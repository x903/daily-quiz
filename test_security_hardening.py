"""上线前安全加固回归：CSRF、会话伪造、安全响应头、IP 伪造、默认凭据。

运行：python test_security_hardening.py
不依赖 MySQL / Redis，使用临时 SQLite 库。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_security_"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-hardening-check")

import models  # noqa: E402

models.DB_PATH = _TMP_DIR / "quiz.db"

import security  # noqa: E402
import test_support  # noqa: E402
from app import app  # noqa: E402

PASS = 0


def check(condition, message: str) -> None:
    global PASS
    assert condition, message
    PASS += 1
    print(f"  ✅ {message}")


def main() -> None:
    client = app.test_client()

    print("1) 安全响应头齐全（防点击劫持 / MIME 嗅探 / 被第三方框套）")
    resp = client.get("/")
    check(resp.headers.get("X-Frame-Options") == "SAMEORIGIN", "X-Frame-Options=SAMEORIGIN")
    check("frame-ancestors 'self'" in (resp.headers.get("Content-Security-Policy") or ""),
          "CSP 包含 frame-ancestors 'self'")
    check(resp.headers.get("X-Content-Type-Options") == "nosniff", "X-Content-Type-Options=nosniff")
    check("geolocation=()" in (resp.headers.get("Permissions-Policy") or ""), "Permissions-Policy 已下发")

    print("2) CSRF：跨站来源的写请求一律拒绝，同源放行")
    evil = {"Origin": "https://evil.example.com"}
    r = client.post("/login", data={"username": "user", "password": "user123"}, headers=evil)
    check(r.status_code == 403, "跨站 POST /login 被拒绝（403）")
    r = client.post("/api/auth/forgot-password", json={"email": "a@b.com"}, headers=evil)
    check(r.status_code == 403 and r.get_json()["code"] == "CSRF_REJECTED",
          "跨站 JSON 写接口被拒绝并返回 CSRF_REJECTED")
    r = client.post("/api/admin/login", json={"username": "admin", "password": "x"}, headers=evil)
    check(r.status_code == 403, "跨站 POST /api/admin/login 被拒绝")

    r = client.post("/login", data={"username": "user", "password": "user123"},
                    headers={"Origin": "http://localhost"})
    check(r.status_code == 200, "同源 POST 正常进入登录流程（不是 403）")

    r = client.post("/login", data={"username": "user", "password": "user123"},
                    headers={"Sec-Fetch-Site": "cross-site"})
    check(r.status_code == 403, "Sec-Fetch-Site: cross-site 被拒绝")

    r = client.get("/", headers=evil)
    check(r.status_code == 200, "GET 不受 CSRF 校验影响")

    print("3) 管理端会话必须带密码指纹（伪造 admin_id 无效）")
    admin_row = models.get_admin_by_username("admin")
    with app.test_client() as forged:
        with forged.session_transaction() as sess:
            sess["admin_id"] = admin_row["id"]
            sess["admin_username"] = admin_row["username"]
            sess["admin_role"] = "admin"
            # 故意不写 admin_pwd_sig
        r = forged.get("/admin")
        check(r.status_code == 302, "只有 admin_id 的会话被判定为未登录")

    with app.test_client() as valid:
        with valid.session_transaction() as sess:
            test_support.set_session(sess, admin_row, is_admin=True)
        check(valid.get("/admin").status_code == 200, "带正确指纹的会话可正常进入后台")

    with app.test_client() as wrong_sig:
        with wrong_sig.session_transaction() as sess:
            test_support.set_session(sess, admin_row, is_admin=True)
            sess["admin_pwd_sig"] = "0" * 16
        check(wrong_sig.get("/admin").status_code == 302, "指纹错误的会话被拒绝")

    print("4) 客户端 IP 不可伪造（默认忽略 X-Forwarded-For）")
    with app.test_request_context("/", headers={"X-Forwarded-For": "1.2.3.4"}):
        ip = security.client_ip()
        check(ip != "1.2.3.4", f"默认忽略伪造的 XFF（实际取对端地址：{ip}）")

    old = os.environ.get("TRUST_PROXY_COUNT")
    os.environ["TRUST_PROXY_COUNT"] = "1"
    try:
        with app.test_request_context("/", headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}):
            check(security.client_ip() == "10.0.0.1", "显式信任一层代理时取最右侧地址")
    finally:
        if old is None:
            os.environ.pop("TRUST_PROXY_COUNT", None)
        else:
            os.environ["TRUST_PROXY_COUNT"] = old

    print("5) 生产环境默认不回显验证码 / 重置链接")
    import notify

    env_backup = os.environ.get("DB_BACKEND")
    dev_backup = os.environ.get("ACCOUNT_DEV_MODE")
    try:
        os.environ["DB_BACKEND"] = "mysql"
        os.environ.pop("ACCOUNT_DEV_MODE", None)
        check(notify.dev_mode() is False, "生产环境（DB_BACKEND=mysql）默认关闭 dev_mode")
        os.environ["ACCOUNT_DEV_MODE"] = "1"
        check(notify.dev_mode() is True, "显式 ACCOUNT_DEV_MODE=1 时仍可用于验收")
    finally:
        if env_backup is None:
            os.environ.pop("DB_BACKEND", None)
        else:
            os.environ["DB_BACKEND"] = env_backup
        os.environ.pop("ACCOUNT_DEV_MODE", None)
        if dev_backup is not None:
            os.environ["ACCOUNT_DEV_MODE"] = dev_backup

    print("6) 生产环境不再用 admin123 作为初始管理员密码")
    import auth

    try:
        os.environ["DB_BACKEND"] = "mysql"
        os.environ.pop("ADMIN_PASSWORD", None)
        password = auth._initial_admin_password()
        check(password != "admin123" and len(password) >= 12,
              "未配置 ADMIN_PASSWORD 时随机生成强密码")
    finally:
        if env_backup is None:
            os.environ.pop("DB_BACKEND", None)
        else:
            os.environ["DB_BACKEND"] = env_backup

    print("7) 登录回跳地址只允许站内相对路径")
    import app as app_module

    for bad in ("//evil.com", "/\\evil.com", "https://evil.com", "/\t/evil.com", "javascript:alert(1)"):
        check(app_module._safe_next(bad) == "", f"拒绝非法 next：{bad!r}")
    check(app_module._safe_next("/quiz/2026-11-01") == "/quiz/2026-11-01", "放行正常站内路径")

    print("8) 未登录访问管理接口返回 401 JSON（不泄漏页面）")
    r = client.get("/api/admin/overview")
    check(r.status_code == 401 and r.get_json()["code"] == "ADMIN_AUTH_REQUIRED",
          "管理接口未登录返回 401")

    print(f"\nRESULT: PASS（{PASS} 项断言全部通过）")


if __name__ == "__main__":
    main()
