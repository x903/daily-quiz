"""回归测试：管理员登录图形验证码 + 管理员改密 / 重置密码。

全程使用独立临时 SQLite 库，不触碰 data/quiz.db；直接 `python test_admin_password.py`。

覆盖：
1. 管理端验证码接口：只回 captchaKey + SVG、不回明文、禁止缓存；
2. 登录强制验证码：缺码 / 错码 400，正确码 200；
3. 作用域隔离：用户端验证码不能登管理端，管理端验证码也不能登用户端；
4. 管理端失败 5 次锁 15 分钟，且不影响普通用户登录（计数键隔离）；
5. 自助改密：原密码错误 / 强度不足 / 两次不一致 / 与原密码相同全部拒绝；
6. 改密成功：本人会话立即退出，其他设备的旧会话因指纹不匹配同步失效，
   旧密码失效、新密码可登录；
7. 重置他人密码：需操作者密码、不能重置自己、目标会话失效、旧密码失效；
8. 页面渲染：管理登录页含验证码控件，后台有「修改密码」入口，且不回显默认凭据。
"""
from __future__ import annotations

import base64
import shutil
import tempfile
from pathlib import Path

import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_admin_pwd_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

import auth  # noqa: E402
import captcha  # noqa: E402
import security  # noqa: E402
import test_support  # noqa: E402
from app import app  # noqa: E402

ADMIN_NAME, ADMIN_PW = "pwd_probe_admin", "probePwd2026"
USER_NAME, USER_PW = auth.DEFAULT_USER
NEW_PW = "adminPwd2026"
OPS_NAME, OPS_PW = "pwd_probe_ops", "ops123456"
OPS_NEW_PW = "opsNew2026"


def purge(*usernames: str) -> None:
    """清理本测试创建的管理员，保证可重复执行、也不污染真实管理员账号。"""
    conn = models.get_conn()
    try:
        with conn:
            for name in usernames:
                conn.execute("DELETE FROM admins WHERE username = ?", (name,))
    finally:
        conn.close()


def seed_probe() -> None:
    """建一个专用探针管理员，绝不改动真实 admin 账号的密码。"""
    purge(ADMIN_NAME, OPS_NAME)
    models.create_admin(ADMIN_NAME, auth.hash_password(ADMIN_PW))


def admin_login(client, password=ADMIN_PW, scope="admin"):
    """用正确的验证码走一次管理端表单登录，返回响应。"""
    return client.post("/admin/login", data={
        "username": ADMIN_NAME, "password": password,
        **test_support.captcha_fields(scope),
    })


def login_json(client, password, key, code):
    return client.post("/api/admin/login", json={
        "username": ADMIN_NAME, "password": password,
        "captchaKey": key, "captchaCode": code,
    })


def change(client, old_pw, new_pw, confirm=None):
    return client.post("/api/admin/password", json={
        "old_password": old_pw, "new_password": new_pw,
        "confirm_password": new_pw if confirm is None else confirm,
    })


try:
    captcha.reset_for_tests()   # 强制使用进程内后端，避免本机 Redis 里的历史键干扰
    models.init_db()
    auth.create_default_users()
    seed_probe()

    # ---------------------------------------------------------------- 1) 验证码接口
    anon = app.test_client()
    resp = anon.get("/api/admin/captcha")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.headers.get("Cache-Control") == "no-store", "验证码响应必须禁止缓存"
    data = resp.get_json()["data"]
    assert data["captchaKey"] and data["image"].startswith("data:image/svg+xml;base64,")
    assert "captchaCode" not in data and "code" not in data, "接口不能回显验证码明文"
    svg = base64.b64decode(data["image"].split(",", 1)[1]).decode("utf-8")
    assert svg.startswith("<svg") and svg.count("<text") == captcha.CAPTCHA_LENGTH
    assert captcha.CAPTCHA_TTL_SECONDS == 300, "验证码有效期应为 5 分钟"
    print("1) 管理端验证码接口：key + SVG、无明文、no-store、5 分钟有效  ✅")

    # ---------------------------------------------------------------- 2) 登录必须带验证码
    no_code = app.test_client().post("/api/admin/login", json={
        "username": ADMIN_NAME, "password": ADMIN_PW})
    assert no_code.status_code == 400 and no_code.get_json()["code"] == "CAPTCHA_INVALID"

    fields = test_support.captcha_fields("admin")
    wrong = login_json(app.test_client(), ADMIN_PW, fields["captcha_key"], "ZZZZ")
    assert wrong.status_code == 400 and wrong.get_json()["code"] == "CAPTCHA_INVALID"

    fields = test_support.captcha_fields("admin")
    good = login_json(app.test_client(), ADMIN_PW, fields["captcha_key"], fields["captcha_code"])
    assert good.status_code == 200 and good.get_json()["ok"], good.get_json()
    print("2) 管理端登录：缺验证码 / 错验证码 400，正确验证码 200  ✅")

    # ---------------------------------------------------------------- 3) 作用域隔离
    user_fields = test_support.captcha_fields("user")
    cross = login_json(app.test_client(), ADMIN_PW,
                       user_fields["captcha_key"], user_fields["captcha_code"])
    assert cross.status_code == 400 and cross.get_json()["code"] == "CAPTCHA_INVALID", \
        "用户端验证码不能用于管理端登录"

    admin_fields = test_support.captcha_fields("admin")
    cross_user = app.test_client().post("/api/auth/login", json={
        "username": USER_NAME, "password": USER_PW,
        "captchaKey": admin_fields["captcha_key"], "captchaCode": admin_fields["captcha_code"],
    })
    assert cross_user.status_code == 400 and cross_user.get_json()["code"] == "CAPTCHA_INVALID", \
        "管理端验证码不能用于用户端登录"
    print("3) 验证码按作用域隔离：两端互不通用  ✅")

    # ---------------------------------------------------------------- 4) 失败锁定隔离
    captcha.clear_failures(ADMIN_NAME, scope="admin")
    lock_client = app.test_client()
    for attempt in range(1, captcha.LOGIN_MAX_FAILURES + 1):
        fields = test_support.captcha_fields("admin")
        resp = login_json(lock_client, "definitely-wrong",
                          fields["captcha_key"], fields["captcha_code"])
        body = resp.get_json()
        if attempt < captcha.LOGIN_MAX_FAILURES:
            assert resp.status_code == 401 and body["code"] == "BAD_CREDENTIALS", body
            assert body["attempts_left"] == captcha.LOGIN_MAX_FAILURES - attempt, body
        else:
            assert resp.status_code == 423 and body["code"] == "ACCOUNT_LOCKED", body
    assert captcha.lock_remaining(ADMIN_NAME, scope="admin") > 0

    # 锁定期间即使密码 + 验证码都正确也必须拒绝
    fields = test_support.captcha_fields("admin")
    still_locked = login_json(lock_client, ADMIN_PW, fields["captcha_key"], fields["captcha_code"])
    assert still_locked.status_code == 423

    # 用户端完全不受影响：失败计数与锁定键各自独立
    assert captcha.lock_remaining(ADMIN_NAME, scope="user") == 0
    user_client = app.test_client()
    user_ok = user_client.post("/api/auth/login", json={
        "username": USER_NAME, "password": USER_PW,
        "captchaKey": test_support.captcha_fields("user")["captcha_key"],
        "captchaCode": "",
    })
    assert user_ok.status_code == 400, "用户端也应校验自己的验证码"
    fields = test_support.captcha_fields("user")
    user_ok = user_client.post("/api/auth/login", json={
        "username": USER_NAME, "password": USER_PW,
        "captchaKey": fields["captcha_key"], "captchaCode": fields["captcha_code"],
    })
    assert user_ok.status_code == 200 and user_ok.get_json()["ok"]
    captcha.clear_failures(ADMIN_NAME, scope="admin")
    print("4) 管理端失败 5 次锁 15 分钟；用户端计数与登录不受牵连  ✅")

    # ---------------------------------------------------------------- 5) 自助改密校验
    c1 = app.test_client()
    assert admin_login(c1).status_code == 302
    assert c1.get("/api/admin/session").get_json()["data"]["logged_in"] is True

    wrong_old = change(c1, "not-my-password", NEW_PW)
    assert wrong_old.status_code == 400 and wrong_old.get_json()["code"] == "INVALID_CREDENTIALS"
    assert "还可尝试" in wrong_old.get_json()["error"], wrong_old.get_json()

    for bad_new, code in (("short1", "PASSWORD_LENGTH"), ("onlyletters", "PASSWORD_WEAK"),
                          ("has space 123", "PASSWORD_SPACE")):
        resp = change(c1, ADMIN_PW, bad_new)
        assert resp.status_code == 400 and resp.get_json()["code"] == code, (bad_new, resp.get_json())

    mismatch = change(c1, ADMIN_PW, NEW_PW, confirm="anotherPwd2026")
    assert mismatch.status_code == 400 and mismatch.get_json()["code"] == "PASSWORD_MISMATCH"

    same = change(c1, ADMIN_PW, ADMIN_PW)
    assert same.status_code == 400 and same.get_json()["code"] == "PASSWORD_SAME"
    print("5) 改密校验：原密码错误 / 长度 / 弱口令 / 空格 / 不一致 / 与原密码相同 全部拒绝  ✅")

    # ---------------------------------------------------------------- 6) 改密成功 + 旧会话失效
    c2 = app.test_client()
    assert admin_login(c2).status_code == 302
    assert c2.get("/api/admin/session").get_json()["data"]["logged_in"] is True

    done = change(c1, ADMIN_PW, NEW_PW)
    body = done.get_json()
    assert done.status_code == 200 and body["ok"], body
    assert body["data"]["relogin"] is True and body["redirect"] == "/admin/login?pwd=1"
    assert c1.get("/api/admin/session").get_json()["data"]["logged_in"] is False, \
        "改密的当前会话应立即退出"
    assert c2.get("/api/admin/session").get_json()["data"]["logged_in"] is False, \
        "其他设备的旧会话应因密码指纹变化而失效"

    old_client = app.test_client()
    assert admin_login(old_client, ADMIN_PW).status_code == 302
    assert old_client.get("/api/admin/session").get_json()["data"]["logged_in"] is False, \
        "旧密码必须失效"

    rejoin = app.test_client()
    assert admin_login(rejoin, NEW_PW).status_code == 302
    assert rejoin.get("/api/admin/session").get_json()["data"]["logged_in"] is True, \
        "新密码应可登录"
    print("6) 改密成功：本人与其他设备会话全部失效，旧密码失效、新密码可登录  ✅")

    # ---------------------------------------------------------------- 7) 重置他人密码
    assert rejoin.post("/api/admin/admins", json={
        "username": OPS_NAME, "password": OPS_PW}).get_json()["ok"]
    ops = app.test_client()
    assert test_support.login(ops, OPS_NAME, OPS_PW, admin=True).status_code == 302
    assert ops.get("/api/admin/session").get_json()["data"]["logged_in"] is True
    ops_id = models.get_admin_by_username(OPS_NAME)["id"]
    admin_id = models.get_admin_by_username(ADMIN_NAME)["id"]

    unauth = app.test_client().post(f"/api/admin/admins/{ops_id}/password",
                                    json={"new_password": OPS_NEW_PW,
                                          "confirm_password": OPS_NEW_PW,
                                          "operator_password": NEW_PW})
    assert unauth.status_code == 401, "未登录不得重置任何管理员密码"

    self_reset = rejoin.post(f"/api/admin/admins/{admin_id}/password", json={
        "new_password": "selfReset2026", "confirm_password": "selfReset2026",
        "operator_password": NEW_PW})
    assert self_reset.status_code == 400 and self_reset.get_json()["code"] == "SELF_RESET_FORBIDDEN"

    bad_operator = rejoin.post(f"/api/admin/admins/{ops_id}/password", json={
        "new_password": OPS_NEW_PW, "confirm_password": OPS_NEW_PW,
        "operator_password": "wrong-operator"})
    assert bad_operator.status_code == 403 and bad_operator.get_json()["code"] == "INVALID_CREDENTIALS"

    missing = rejoin.post("/api/admin/admins/99999/password", json={
        "new_password": OPS_NEW_PW, "confirm_password": OPS_NEW_PW,
        "operator_password": NEW_PW})
    assert missing.status_code == 404

    weak = rejoin.post(f"/api/admin/admins/{ops_id}/password", json={
        "new_password": "weak", "confirm_password": "weak", "operator_password": NEW_PW})
    assert weak.status_code == 400

    reset = rejoin.post(f"/api/admin/admins/{ops_id}/password", json={
        "new_password": OPS_NEW_PW, "confirm_password": OPS_NEW_PW,
        "operator_password": NEW_PW})
    assert reset.status_code == 200 and reset.get_json()["ok"], reset.get_json()
    assert ops.get("/api/admin/session").get_json()["data"]["logged_in"] is False, \
        "被重置账号的旧会话应立即失效"
    ops_old = app.test_client()
    assert test_support.login(ops_old, OPS_NAME, OPS_PW, admin=True).status_code == 302
    assert ops_old.get("/api/admin/session").get_json()["data"]["logged_in"] is False, \
        "被重置账号的旧密码必须失效"
    ops_new = app.test_client()
    assert test_support.login(ops_new, OPS_NAME, OPS_NEW_PW, admin=True).status_code == 302
    assert ops_new.get("/api/admin/session").get_json()["data"]["logged_in"] is True
    print("7) 重置他人密码：需本人密码、禁止重置自己、目标旧会话与旧密码失效  ✅")

    # ---------------------------------------------------------------- 8) 页面与入口
    assert anon.head("/admin/login").status_code == 200, "HEAD 也应返回登录页而不是走登录分支"
    login_page = anon.get("/admin/login")
    assert "no-cache" in (login_page.headers.get("Cache-Control") or ""), \
        "管理登录页不能长缓存，否则发版后仍是旧页面"
    login_html = login_page.get_data(as_text=True)
    assert 'id="captcha-img"' in login_html and 'id="captcha-refresh"' in login_html
    assert "看不清，换一张" in login_html and "/api/admin/captcha" in login_html
    assert ADMIN_PW not in login_html and "admin123" not in login_html, "登录页不得回显默认密码"
    assert "密码已修改" in login_html, "改密后应能提示重新登录"

    dash_html = rejoin.get("/admin/dashboard").get_data(as_text=True)
    assert 'data-section="password"' in dash_html and "修改密码" in dash_html
    assert "账号安全" not in dash_html, "管理端不应植入用户端个人中心文案"
    admin_js_resp = anon.get("/static/admin/admin.js")
    admin_js = admin_js_resp.get_data(as_text=True)
    assert "no-cache" in (admin_js_resp.headers.get("Cache-Control") or ""), \
        "管理端脚本同样不能长缓存"
    assert "'/password'" in admin_js or '"/password"' in admin_js
    assert "/password" in admin_js and "重置密码" in admin_js
    print("8) 登录页含验证码控件、后台含修改密码入口、无默认凭据回显、管理端禁用长缓存  ✅")

    print("\n全部通过：管理员验证码登录、失败锁定、自助改密与他人密码重置回归完成。")
finally:
    # 探针账号用完即删：即使在真实 MySQL 上跑，也不会留下多余管理员
    try:
        purge(ADMIN_NAME, OPS_NAME)
    except Exception as exc:  # noqa: BLE001 - 清理失败不应掩盖测试结论
        print(f"[cleanup] 探针账号清理失败：{exc}")
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
