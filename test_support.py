"""回归测试公共辅助：适配「独立 admin 表 + 双登录入口 + 角色路由隔离」架构。

历史用例里有不少「用管理员账号访问用户端页面」的写法。新架构下管理员
登录 /admin/login 只写管理端 session，用户端页面需要普通用户 session；
真实浏览器可以同时持有两套 session，因此这里提供 with_user_session 选项，
让这批用例在不破坏隔离语义的前提下继续回归用户端页面。
"""
from __future__ import annotations

import hashlib

import models

ADMIN_NAME = "admin"
USER_NAME = "user"


def captcha_fields(scope: str = "user") -> dict:
    """签发一张登录验证码，并把明文一并返回（仅测试内部使用）。

    线上接口只返回 captchaKey 与 SVG，明文只存在 Redis / 服务端缓存；
    测试与服务端同进程，所以可以直接拿到答案，避免为了测试开后门。
    scope="admin" 用于管理端登录页（两端验证码不通用）。
    """
    import captcha

    key, code, _svg = captcha.issue(scope=scope)
    return {"captcha_key": key, "captcha_code": code}


def login(client, username: str, password: str, *, with_user_session: bool = False,
          admin: bool | None = None):
    """按账号类型选择正确的登录入口，返回登录响应。

    两端登录都已启用图形验证码，这里按入口签发对应作用域的验证码。
    ``admin`` 显式指定入口，用于 admin 表里除默认账号之外的其他管理员
    （默认按 ADMIN_NAME 判断）。
    """
    is_admin = (username == ADMIN_NAME) if admin is None else bool(admin)
    url = "/admin/login" if is_admin else "/login"
    data = {"username": username, "password": password,
            **captcha_fields("admin" if is_admin else "user")}
    response = client.post(url, data=data)
    if with_user_session and username == ADMIN_NAME and response.status_code == 302:
        attach_user_session(client)
    return response


def attach_user_session(client, username: str = USER_NAME) -> None:
    """给 client 额外挂一个普通用户会话（模拟同一浏览器同时登录两端）。"""
    row = models.get_user_by_username(username)
    if not row:
        return
    with client.session_transaction() as sess:
        sess["user_id"] = row["id"]
        sess["username"] = row["username"]
        sess["role"] = "user"


def set_session(sess, row, *, is_admin: bool = False) -> None:
    """按账号类型写入对应的 session 键。

    管理端会话必须同时写入密码指纹（security._admin_signature）：线上逻辑要求
    「没有指纹的会话一律失效」，防止伪造 admin_id 的 Cookie 直接接管后台。
    """
    if is_admin:
        sess["admin_id"] = row["id"]
        sess["admin_username"] = row["username"]
        # sqlite3.Row / RowDict 不一定有 .get，用 keys() 判断更通用
        sess["admin_role"] = row["role"] if "role" in row.keys() else "admin"
        password = row["password"] if "password" in row.keys() else ""
        sess["admin_pwd_sig"] = hashlib.sha256(str(password).encode("utf-8")).hexdigest()[:16]
    else:
        sess["user_id"] = row["id"]
        sess["username"] = row["username"]
        sess["role"] = "user"
