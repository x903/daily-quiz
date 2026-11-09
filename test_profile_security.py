"""回归测试：个人中心资料、修改密码、手机号 / 邮箱绑定、邮箱找回密码。

全程使用独立临时 SQLite 库，不触碰正在运行的 data/quiz.db。

覆盖：
1. users 表新增字段、sms_codes / email_tokens 建表与唯一索引；
2. 鉴权隔离：游客 401/302，管理员会话不能调用户端接口；
3. 基础信息：昵称 2-20、性别枚举、简介 200 字上限、保存后页面同步；
4. 修改密码：原密码校验、强度规则、新旧不同、哈希存储、改后强制重新登录；
5. 手机号绑定：验证码下发 / 冷却 429 / 错误码计数 / 唯一性 409 / 脱敏展示；
6. 邮箱绑定：同上，且邮箱大小写归一、标记 email_verified；
7. 找回密码：未绑定提示、一次性 Token、30 分钟时效、复用失败、频率限制；
8. 页面：/profile 三段式、/forgot-password、/reset-password 正常，管理端不含个人中心。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

os.environ["ACCOUNT_DEV_MODE"] = "1"  # 测试依赖调试验证码，显式覆盖环境值

# 必须在 import app 之前重定向数据库：app.py 加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_profile_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

from app import app  # noqa: E402
import auth  # noqa: E402
import test_support  # noqa: E402

PHONE = "13800001234"
PHONE2 = "13900002222"
EMAIL = "abc@gmail.com"
EMAIL_UPPER = "ABC@Gmail.com"
USER_PW = "user123"


def login(client, username: str = "user", password: str = USER_PW):
    return client.post("/login", data={
        "username": username, "password": password, **test_support.captcha_fields(),
    })


def latest_code(table: str, user_id: int = 1) -> str:
    """测试侧直接读库拿验证码明文（生产接口仅在开发模式回显）。"""
    conn = models.get_conn()
    row = conn.execute(
        f"SELECT code_hash FROM {table} WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["code_hash"] if row else ""


def main() -> None:
    models.init_db()
    auth.create_default_users()
    models.create_user("second", auth.hash_password("second123"), "user")

    # ---------------------------------------------------------------- 1) 迁移
    conn = models.get_conn()
    columns = {c["name"] for c in conn.execute("PRAGMA table_info(users)")}
    expected = {
        "nickname", "gender", "bio", "phone", "phone_verified", "email",
        "email_verified", "password_hash", "reset_token", "reset_token_expire_at", "updated_at",
    }
    assert expected <= columns, f"users 缺少字段：{expected - columns}"
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sms_codes", "email_tokens"} <= tables, "验证码表未创建"
    indexes = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'uk_users%'")}
    assert {"uk_users_phone", "uk_users_email"} <= indexes, "手机号 / 邮箱唯一索引缺失"
    conn.close()
    print("1) users 字段、sms_codes / email_tokens、唯一索引齐备 ✅")

    # ---------------------------------------------------------------- 2) 鉴权
    anon = app.test_client()
    assert anon.get("/api/user/profile").status_code == 401
    page = anon.get("/profile")
    assert page.status_code == 302 and "/login" in page.headers["Location"]
    assert anon.get("/forgot-password").status_code == 200
    assert anon.get("/reset-password?token=bad").status_code == 200

    admin = app.test_client()
    admin.post("/admin/login", data={
        "username": "admin", "password": "admin123",
        **test_support.captcha_fields("admin"),
    })
    assert admin.get("/api/user/profile").status_code == 401, "管理员会话不应被当成普通用户"
    assert admin.get("/profile").status_code == 302, "管理端不展示个人中心"
    assert "账号安全" not in admin.get("/admin/dashboard").get_data(as_text=True)
    print("2) 游客 / 管理员双端隔离，管理员无法进入个人中心 ✅")

    # ---------------------------------------------------------------- 3) 基础信息
    user = app.test_client()
    assert login(user).status_code == 302

    r = user.put("/api/user/profile", json={"nickname": "x", "gender": "男", "bio": ""})
    assert r.status_code == 400 and "昵称" in r.get_json()["error"]
    r = user.put("/api/user/profile", json={"nickname": "x" * 21, "gender": "男", "bio": ""})
    assert r.status_code == 400
    r = user.put("/api/user/profile", json={"nickname": "正常昵称", "gender": "外星人", "bio": ""})
    assert r.status_code == 400 and "性别" in r.get_json()["error"]
    r = user.put("/api/user/profile", json={"nickname": "正常昵称", "gender": "男", "bio": "字" * 201})
    assert r.status_code == 400 and "200" in r.get_json()["error"]

    r = user.put("/api/user/profile", json={
        "nickname": "刷题小能手", "gender": "男", "bio": "正在备考阿里云 ACP，目标一次通过。",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    payload = r.get_json()["data"]
    assert payload["nickname"] == "刷题小能手" and payload["gender"] == "男"
    assert payload["bio"].startswith("正在备考")

    body = user.get("/profile").get_data(as_text=True)
    assert "刷题小能手" in body and "基础信息" in body and "账号安全" in body and "学习数据" in body
    assert "正在备考阿里云 ACP" in body
    print("3) 基础信息校验与保存生效，页面三段式齐全 ✅")

    # ---------------------------------------------------------------- 4) 修改密码
    r = user.put("/api/user/password", json={
        "old_password": "wrongpass1", "new_password": "user1234", "confirm_password": "user1234"})
    assert r.status_code == 400 and r.get_json()["code"] == "INVALID_CREDENTIALS"
    r = user.put("/api/user/password", json={
        "old_password": USER_PW, "new_password": "abcdefgh", "confirm_password": "abcdefgh"})
    assert r.status_code == 400 and "数字" in r.get_json()["error"], "缺少数字应拒绝"
    r = user.put("/api/user/password", json={
        "old_password": USER_PW, "new_password": "abcd1234", "confirm_password": "abcd9999"})
    assert r.status_code == 400 and "不一致" in r.get_json()["error"]
    r = user.put("/api/user/password", json={
        "old_password": USER_PW, "new_password": USER_PW, "confirm_password": USER_PW})
    assert r.status_code == 400 and "相同" in r.get_json()["error"]

    r = user.put("/api/user/password", json={
        "old_password": USER_PW, "new_password": "user1234", "confirm_password": "user1234"})
    assert r.status_code == 200 and r.get_json()["data"]["relogin"] is True
    assert user.get("/api/user/profile").status_code == 401, "改密后当前会话应立即失效"

    stored = models.get_user(1)["password_hash"]
    assert stored.startswith("pbkdf2:sha256") and "user1234" not in stored, "密码必须哈希存储"

    old_client = app.test_client()
    assert login(old_client).status_code == 200, "旧密码应无法登录（返回登录页而非 302）"
    assert login(old_client, password="user1234").status_code == 302, "新密码应可登录"
    print("4) 改密规则、原密码校验、哈希存储与强制重登全部正确 ✅")

    # ---------------------------------------------------------------- 5) 绑定手机号
    user = app.test_client()
    login(user, password="user1234")

    r = user.post("/api/user/bind-phone", json={"phone": "12345"})
    assert r.status_code == 400 and "手机号" in r.get_json()["error"]

    r = user.post("/api/user/bind-phone", json={"phone": PHONE})
    assert r.status_code == 200, r.get_data(as_text=True)
    code = r.get_json()["data"]["debug_code"]
    assert r.get_json()["data"]["phone"] == "138****1234"
    assert len(latest_code("sms_codes")) == 64 and latest_code("sms_codes") != code, "验证码必须哈希存储"

    r = user.post("/api/user/bind-phone", json={"phone": PHONE})
    assert r.status_code == 429 and r.get_json()["code"] == "RATE_LIMITED", "60 秒内重发应被限流"

    r = user.post("/api/user/verify-phone", json={"phone": PHONE, "code": "000000"})
    assert r.status_code == 400 and "还可尝试" in r.get_json()["error"]

    r = user.post("/api/user/verify-phone", json={"phone": PHONE, "code": code})
    assert r.status_code == 200 and r.get_json()["data"]["phone_verified"] is True

    other = app.test_client()
    login(other, "second", "second123")
    r = other.post("/api/user/bind-phone", json={"phone": PHONE})
    assert r.status_code == 409 and r.get_json()["code"] == "PHONE_TAKEN"

    body = user.get("/profile").get_data(as_text=True)
    assert "138****1234" in body and PHONE not in body, "页面只能展示脱敏手机号"
    print("5) 手机号验证码、冷却限流、错误计数、唯一性与脱敏展示正确 ✅")

    # ---------------------------------------------------------------- 6) 绑定邮箱
    r = user.post("/api/user/bind-email", json={"email": "not-an-email"})
    assert r.status_code == 400
    r = user.post("/api/user/bind-email", json={"email": EMAIL_UPPER})
    assert r.status_code == 200, r.get_data(as_text=True)
    code = r.get_json()["data"]["debug_code"]
    assert r.get_json()["data"]["email"] == "abc***@gmail.com", "邮箱应小写归一后脱敏"

    r = user.post("/api/user/verify-email", json={"email": EMAIL, "code": code})
    assert r.status_code == 200 and r.get_json()["data"]["email_verified"] is True

    row = dict(models.get_user(1))
    assert row["email"] == EMAIL and int(row["email_verified"]) == 1

    other = app.test_client()
    login(other, "second", "second123")
    r = other.post("/api/user/bind-email", json={"email": EMAIL})
    assert r.status_code == 409 and r.get_json()["code"] == "EMAIL_TAKEN", "邮箱必须唯一（忽略大小写）"

    body = user.get("/profile").get_data(as_text=True)
    assert "abc***@gmail.com" in body and EMAIL not in body, "页面只能展示脱敏邮箱"
    print("6) 邮箱格式、大小写归一、唯一性与已验证标记正确 ✅")

    # ---------------------------------------------------------------- 7) 找回密码
    stranger = app.test_client()
    r = stranger.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    # 账号枚举防护：未注册邮箱也返回同样的「已发送」文案，不暴露邮箱是否存在
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["data"]["sent"] is True
    assert "debug_link" not in r.get_json()["data"], "未注册邮箱不应生成重置链接"

    r = stranger.post("/api/auth/forgot-password", json={"email": EMAIL})
    assert r.status_code == 200, r.get_data(as_text=True)
    link = r.get_json()["data"]["debug_link"]
    token = link.split("token=")[1]
    assert "reset-password" in link

    r = stranger.post("/api/auth/forgot-password", json={"email": EMAIL})
    assert r.status_code == 429 and r.get_json()["code"] == "RATE_LIMITED", "重置邮件需限频"

    page = stranger.get(f"/reset-password?token={token}").get_data(as_text=True)
    assert "abc***@gmail.com" in page and "设置新密码" in page

    r = stranger.post("/api/auth/reset-password", json={
        "token": "bad-token", "password": "reset1234", "confirm_password": "reset1234"})
    assert r.status_code == 400 and r.get_json()["code"] == "TOKEN_INVALID"

    r = stranger.post("/api/auth/reset-password", json={
        "token": token, "password": "short1", "confirm_password": "short1"})
    assert r.status_code == 400 and "8-16" in r.get_json()["error"]

    r = stranger.post("/api/auth/reset-password", json={
        "token": token, "password": "reset1234", "confirm_password": "reset1234"})
    assert r.status_code == 200 and r.get_json()["data"]["reset"] is True

    r = stranger.post("/api/auth/reset-password", json={
        "token": token, "password": "reset5678", "confirm_password": "reset5678"})
    assert r.status_code == 400 and ("已被使用" in r.get_json()["error"] or "无效" in r.get_json()["error"]), \
        "Token 必须一次性"

    row = dict(models.get_user(1))
    assert not row["reset_token"] and not row["reset_token_expire_at"], "重置成功应清空 Token"

    # 过期 Token：手动回填一个已过期的凭证
    conn = models.get_conn()
    conn.execute(
        "UPDATE users SET reset_token = ?, reset_token_expire_at = ? WHERE id = 1",
        (__import__("hashlib").sha256(b"expired-token").hexdigest(), "2000-01-01 00:00:00"),
    )
    conn.commit()
    conn.close()
    r = stranger.post("/api/auth/reset-password", json={
        "token": "expired-token", "password": "reset9999", "confirm_password": "reset9999"})
    assert r.status_code == 400 and "过期" in r.get_json()["error"]

    fresh = app.test_client()
    assert login(fresh, password="reset1234").status_code == 302, "重置后的新密码应可登录"
    stale = app.test_client()
    assert login(stale, password="user1234").status_code == 200, "旧密码应失效"
    print("7) 找回密码：未绑定提示、Token 一次性 / 过期、限频与改密全部正确 ✅")

    # ---------------------------------------------------------------- 8) 收尾
    assert "忘记密码" in anon.get("/login").get_data(as_text=True)
    assert "个人中心" not in anon.get("/admin/dashboard").get_data(as_text=True)
    print("8) 登录页找回入口、管理端无个人中心 ✅")
    print("\n全部通过：个人中心资料 / 密码 / 绑定 / 找回密码链路验证完成。")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
