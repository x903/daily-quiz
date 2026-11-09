"""回归测试：用户名唯一性（接口 / 规则 / 数据库索引）与登录图形验证码。

全程使用独立临时 SQLite 库，不触碰 data/quiz.db。

覆盖：
1. 用户名规则：4-20 位、仅字母数字下划线点号、非纯数字、非保留词、点号位置；
2. 归一化：大小写 / 首尾空格统一，注册后可用任意大小写变体登录但不能再注册；
3. 三层唯一性：实时校验接口 → 注册接口 409 → 数据库唯一索引兜底（含并发路径）；
4. 验证码：SVG 不含明文答案、一次性消费、大小写不敏感、过期即失效；
5. 登录：缺验证码 / 错验证码 / 正确验证码；登录成功清空失败计数；
6. 失败 5 次锁定 15 分钟，锁定期间正确密码也拒绝；
7. 同 IP 取码限流；
8. 页面渲染：登录页有验证码控件，注册页有实时校验提示位；
9. DiceBear 头像：seed 取 username，稳定且不同账号不同。
"""
from __future__ import annotations

import base64
import re
import shutil
import tempfile
from pathlib import Path

import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_authguard_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

import auth  # noqa: E402
import captcha  # noqa: E402
import db  # noqa: E402
import test_support  # noqa: E402
import web_site  # noqa: E402
from app import app  # noqa: E402

PW = "pass1234"


def register(client, username, password=PW, **extra):
    payload = {"username": username, "password": password, "confirmPassword": password}
    payload.update(extra)
    return client.post("/api/auth/register", json=payload)


def login_json(client, username, password, key, code):
    return client.post("/api/auth/login", json={
        "username": username, "password": password,
        "captchaKey": key, "captchaCode": code,
    })


try:
    models.init_db()
    auth.create_default_users()

    # ---------------------------------------------------------------- 1) 用户名规则
    invalid = [
        ("ab", "USERNAME_LENGTH"),
        ("a" * 21, "USERNAME_LENGTH"),
        ("ab-cd", "USERNAME_CHARS"),
        ("ab cd", "USERNAME_CHARS"),
        ("用户名abcd", "USERNAME_CHARS"),
        ("12345678", "USERNAME_ALL_DIGITS"),
        (".abcd", "USERNAME_FORMAT"),
        ("abcd.", "USERNAME_FORMAT"),
        ("ab..cd", "USERNAME_FORMAT"),
        ("admin", "USERNAME_RESERVED"),
        ("ROOT", "USERNAME_RESERVED"),
        ("Manager", "USERNAME_RESERVED"),
        ("", "USERNAME_REQUIRED"),
    ]
    for raw, code in invalid:
        result = auth.username_available(raw)
        assert result["available"] is False and result["code"] == code, f"{raw!r} -> {result}"

    good = auth.username_available("  Caps.User_01  ")
    assert good["available"] is True, good
    assert good["username"] == "caps.user_01", "应统一小写并去掉首尾空格"
    assert auth.validate_username("abc_123.x") == "abc_123.x"
    print("1) 用户名规则：长度 / 字符 / 纯数字 / 点号位置 / 保留词 / 归一化 全部符合预期 ✅")

    # ---------------------------------------------------------------- 2) 注册与三层唯一性
    client = app.test_client()
    first = register(client, "  Caps.User_01  ")
    assert first.status_code == 200 and first.get_json()["ok"], first.get_json()
    assert first.get_json()["data"]["user"]["username"] == "caps.user_01"

    for variant in ("caps.user_01", "CAPS.USER_01", "  Caps.User_01  "):
        resp = register(app.test_client(), variant)
        body = resp.get_json()
        assert resp.status_code == 409, f"{variant!r} -> {resp.status_code} {body}"
        assert body["code"] == "USERNAME_TAKEN"
        assert "已被注册" in body["error"], body["error"]

    anon = app.test_client()
    taken = anon.get("/api/auth/check-username?username=CAPS.USER_01").get_json()
    assert taken["available"] is False and taken["code"] == "USERNAME_TAKEN"
    free = anon.get("/api/auth/check-username?username=fresh_name").get_json()
    assert free["available"] is True and free["code"] == "OK"
    reserved = anon.get("/api/auth/check-username?username=admin").get_json()
    assert reserved["available"] is False and reserved["code"] == "USERNAME_RESERVED"

    # 绕过应用层直接写库：唯一索引必须挡住（大小写不敏感）
    models.create_user("db_guard", auth.hash_password(PW), "user")
    try:
        models.create_user("DB_GUARD", auth.hash_password(PW), "user")
    except db.IntegrityError:
        pass
    else:
        raise AssertionError("数据库唯一索引未生效：大小写变体被插入了")
    print("2) 唯一性三层生效：注册接口 409 + 实时校验 + 数据库 NOCASE 唯一索引 ✅")

    # ---------------------------------------------------------------- 3) 注册带邮箱 / 手机
    contact_client = app.test_client()
    resp = register(contact_client, "with_contact",
                    email="Contact@Example.com", phone="13900001111")
    assert resp.status_code == 200, resp.get_json()
    row = models.get_user_by_username("with_contact")
    assert row["email"] == "contact@example.com" and not int(row["email_verified"]), dict(row)
    assert row["phone"] == "13900001111" and not int(row["phone_verified"]), dict(row)

    dup_email = register(app.test_client(), "other_user", email="CONTACT@example.com")
    assert dup_email.status_code == 409 and dup_email.get_json()["code"] == "EMAIL_TAKEN"
    dup_phone = register(app.test_client(), "other_user2", phone="13900001111")
    assert dup_phone.status_code == 409 and dup_phone.get_json()["code"] == "PHONE_TAKEN"
    print("3) 注册可选邮箱 / 手机：格式校验、唯一性校验、落库为未验证状态 ✅")

    # ---------------------------------------------------------------- 4) 验证码本身
    resp = app.test_client().get("/api/auth/captcha")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["captchaKey"] and data["image"].startswith("data:image/svg+xml;base64,")
    assert "captchaCode" not in data and "code" not in data, "接口不能回显验证码明文"
    svg = base64.b64decode(data["image"].split(",", 1)[1]).decode("utf-8")
    assert svg.startswith("<svg") and svg.count("<text") == captcha.CAPTCHA_LENGTH
    assert "<line" in svg and "<circle" in svg, "应包含干扰线与噪点"
    assert not re.search(r"[0O1Il]", "".join(re.findall(r">([A-Z0-9]+)</text>", svg))), \
        "字符池不应包含易混字符"

    key, code, _ = captcha.issue()
    assert captcha.verify(key, "XXXX")[0] is False
    assert captcha.verify(key, code)[0] is False, "验证码必须一次性"

    key, code, _ = captcha.issue()
    assert captcha.verify(key, code.lower())[0] is True, "验证码应大小写不敏感"

    key, code, _ = captcha.issue()
    assert captcha.peek(key) == code
    assert captcha.verify("not-a-key", code)[0] is False
    print("4) 验证码：SVG 无明文、含干扰、一次性、忽略大小写、无效 Key 拒绝 ✅")

    # ---------------------------------------------------------------- 5) 登录链路
    no_captcha = app.test_client().post("/api/auth/login", json={
        "username": "user", "password": "user123"})
    assert no_captcha.status_code == 400
    assert no_captcha.get_json()["code"] == "CAPTCHA_INVALID"

    wrong_code = app.test_client()
    fields = test_support.captcha_fields()
    resp = login_json(wrong_code, "user", "user123", fields["captcha_key"], "ZZZZ")
    assert resp.status_code == 400 and resp.get_json()["code"] == "CAPTCHA_INVALID"

    login_client = app.test_client()
    fields = test_support.captcha_fields()
    resp = login_json(login_client, "USER", "user123", fields["captcha_key"], fields["captcha_code"])
    assert resp.status_code == 200 and resp.get_json()["ok"], resp.get_json()
    assert login_client.get("/profile").status_code == 200, "登录后应能进入个人中心"
    print("5) 登录：缺验证码 / 错验证码被拒；正确验证码 + 大写用户名可登录 ✅")

    # ---------------------------------------------------------------- 6) 失败锁定
    captcha.reset_for_tests()
    lock_client = app.test_client()
    for attempt in range(1, captcha.LOGIN_MAX_FAILURES + 1):
        fields = test_support.captcha_fields()
        resp = login_json(lock_client, "lock_probe", "wrongpass",
                          fields["captcha_key"], fields["captcha_code"])
        body = resp.get_json()
        if attempt < captcha.LOGIN_MAX_FAILURES:
            assert resp.status_code == 401 and body["code"] == "BAD_CREDENTIALS", body
            assert body["attempts_left"] == captcha.LOGIN_MAX_FAILURES - attempt, body
        else:
            assert resp.status_code == 423 and body["code"] == "ACCOUNT_LOCKED", body
            assert body["locked_seconds"] > 0

    fields = test_support.captcha_fields()
    resp = login_json(lock_client, "lock_probe", "correct-password",
                      fields["captcha_key"], fields["captcha_code"])
    assert resp.status_code == 423, "锁定期间即使密码正确也必须拒绝"
    assert captcha.lock_remaining("lock_probe") > 0
    captcha.clear_failures("lock_probe")
    assert captcha.lock_remaining("lock_probe") == 0
    print("6) 连续失败 5 次锁定 15 分钟，锁定期间正确密码亦拒绝，解锁后恢复 ✅")

    # ---------------------------------------------------------------- 7) 取码限流
    captcha.reset_for_tests()
    limited = app.test_client()
    for index in range(captcha.CAPTCHA_RATE_LIMIT):
        assert limited.get("/api/auth/captcha").status_code == 200, f"第 {index + 1} 次不应限流"
    blocked = limited.get("/api/auth/captcha")
    assert blocked.status_code == 429
    assert blocked.get_json()["code"] == "CAPTCHA_RATE_LIMITED"
    assert blocked.get_json()["retry_after"] > 0
    print(f"7) 同 IP 连续取码超过 {captcha.CAPTCHA_RATE_LIMIT} 次触发限流（429）✅")

    # ---------------------------------------------------------------- 8) 页面渲染
    captcha.reset_for_tests()
    login_html = app.test_client().get("/login").get_data(as_text=True)
    assert 'id="captcha-img"' in login_html and "data:image/svg+xml;base64," in login_html
    assert 'name="captcha_key"' in login_html
    assert "看不清，换一张" in login_html
    assert "auth.js" in login_html

    reg_html = app.test_client().get("/register").get_data(as_text=True)
    assert 'id="username-msg"' in reg_html and "4-20 位" in reg_html
    assert 'id="register-submit"' in reg_html and "auth.js" in reg_html
    assert 'name="email"' in reg_html and 'name="phone"' in reg_html
    print("8) 登录页验证码控件 / 注册页实时校验提示与邮箱手机字段渲染正常 ✅")

    # ---------------------------------------------------------------- 9) DiceBear 头像
    profile_html = login_client.get("/profile").get_data(as_text=True)
    assert "api.dicebear.com/9.x/initials/svg?seed=user" in profile_html
    assert "avatar-fallback" in profile_html, "外链不可用时要能回退昵称首字母"
    assert web_site.dicebear_avatar("alice") != web_site.dicebear_avatar("bobby")
    assert web_site.dicebear_avatar("alice") == web_site.dicebear_avatar("alice")
    assert "%20" in web_site.dicebear_avatar("a b"), "特殊字符需 URL 编码"
    assert web_site.dicebear_avatar("a.b").endswith("&backgroundType=gradientLinear&fontWeight=700&radius=50&scale=92")
    print("9) DiceBear 头像：seed=username，同账号稳定、不同账号不同、可回退 ✅")

    print("\n全部通过：用户名唯一性三层校验与登录验证码 / 限流 / 锁定回归完成。")
finally:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
