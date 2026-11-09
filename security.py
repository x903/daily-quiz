"""会话与权限：普通用户端、管理端两条完全独立的认证链路。

session 中同时允许存在两套键，互不干扰：
    user_id / username / role            -> 用户端（users 表）
    admin_id / admin_username / admin_role -> 管理端（admin 表）

每次请求都回查数据库（cookie 里的 id 可能因为重建库 / 删号变成孤儿），
并且角色一律以数据库为准，不信任 cookie。管理端 JSON 接口统一返回 401 JSON，
页面路由则 302 到各自独立的登录页。

管理员会话还带一个「密码指纹」（password 哈希的摘要）：改密或被其他管理员
重置后指纹不匹配，所有旧会话立即失效，不需要等服务端 session 过期。
"""
from __future__ import annotations

import hashlib
import os
from functools import wraps
from typing import Optional

from flask import flash, g, jsonify, redirect, request, session, url_for

import models

USER_KEYS = ("user_id", "username", "role")
ADMIN_KEYS = ("admin_id", "admin_username", "admin_role", "admin_pwd_sig")


def _admin_signature(admin: dict) -> str:
    """会话指纹：只取密码哈希的摘要，不把哈希本身写进 cookie。"""
    return hashlib.sha256(str(admin.get("password") or "").encode("utf-8")).hexdigest()[:16]


def resolve_current_user() -> Optional[dict]:
    """解析当前普通用户；管理员 session 不会被当成用户身份。"""
    if hasattr(g, "auth_user"):
        return g.auth_user

    user: Optional[dict] = None
    raw_id = session.get("user_id")
    if raw_id:
        try:
            row = models.get_user(int(raw_id))
        except (TypeError, ValueError):
            row = None
        if row and row["role"] != "admin":
            user = dict(row)
            session["username"] = user["username"]
            session["role"] = "user"
        else:
            for key in USER_KEYS:
                session.pop(key, None)

    g.auth_user = user
    return user


def resolve_current_admin() -> Optional[dict]:
    """解析当前管理员；只读 admin 表，与 users 表无关。"""
    if hasattr(g, "auth_admin"):
        return g.auth_admin

    admin: Optional[dict] = None
    raw_id = session.get("admin_id")
    if raw_id:
        try:
            row = models.get_admin(int(raw_id))
        except (TypeError, ValueError):
            row = None
        if row and row["status"] == "active":
            current = dict(row)
            signature = _admin_signature(current)
            saved = session.get("admin_pwd_sig")
            # 指纹必须存在且匹配。旧写法是 `if saved and saved != signature`，
            # 意味着「没有指纹」的会话被无条件信任 —— 一旦 SECRET_KEY 泄露（旧版本
            # 代码里是写死的），攻击者伪造一个只含 admin_id 的 Cookie 就能接管后台。
            if saved != signature:
                for key in ADMIN_KEYS:
                    session.pop(key, None)
                admin = None
            else:
                admin = current
                session["admin_username"] = admin["username"]
                session["admin_role"] = admin["role"]
                session["admin_pwd_sig"] = signature
        else:
            for key in ADMIN_KEYS:
                session.pop(key, None)

    g.auth_admin = admin
    return admin


def set_user_session(user: dict) -> None:
    """写入用户端登录态（不影响管理端登录态）。"""
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = "user"


def set_admin_session(admin: dict) -> None:
    """写入管理端登录态（不影响用户端登录态），并记录密码指纹。"""
    session["admin_id"] = admin["id"]
    session["admin_username"] = admin["username"]
    session["admin_role"] = admin.get("role") or "admin"
    session["admin_pwd_sig"] = _admin_signature(dict(admin))


def clear_user_session() -> None:
    for key in USER_KEYS:
        session.pop(key, None)


def clear_admin_session() -> None:
    for key in ADMIN_KEYS:
        session.pop(key, None)


def client_ip() -> str:
    """客户端 IP：默认只信 TCP 对端地址，绝不盲信 ``X-Forwarded-For``。

    登录失败计数、验证码取码限流都以 IP 为键。如果直接取 XFF 的第一段，攻击者
    每次请求换一个伪造值就能永久绕过账号锁定与限流（等于没有防爆破）。

    只有部署在反向代理后面、且显式设置 ``TRUST_PROXY_COUNT=N``（受信代理层数）
    时才读取 XFF，并取从右往左第 N 段 —— 这一段由最近的一层受信代理写入，客户端
    无法伪造。单层 nginx 用 ``proxy_set_header X-Forwarded-For
    $proxy_add_x_forwarded_for`` 时配置 1 即可。
    """
    try:
        count = int((os.environ.get("TRUST_PROXY_COUNT") or "0").strip())
    except ValueError:
        count = 0
    if count > 0:
        forwarded = [
            part.strip()
            for part in (request.headers.get("X-Forwarded-For") or "").split(",")
            if part.strip()
        ]
        if len(forwarded) >= count:
            return forwarded[-count]
    return request.remote_addr or "unknown"


def login_required(view):
    """用户端**写操作/个人数据**页面：必须登录普通用户账号。

    浏览类页面（首页、排行榜、认证栏目、题目查看、每日答题选栏）不加此装饰器，
    未登录也能看；只有答题、成绩、错题、导出等动作才要求登录。
    未登录时带上 ``next`` 参数，登录成功后回到原页面，避免用户重复点击。
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not resolve_current_user():
            flash("请先登录普通用户账号后再继续", "error")
            return redirect(url_for("login", next=current_path()))
        return view(*args, **kwargs)
    return wrapper


def current_path() -> str:
    """当前请求的站内路径（含查询串），用于登录后回跳。"""
    path = request.path or "/"
    query = request.query_string.decode("utf-8", "ignore")
    return f"{path}?{query}" if query else path


def admin_required(view):
    """管理端页面：只认 admin 表的登录态，未登录跳 /admin/login。"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not resolve_current_admin():
            flash("请使用管理员账号登录后台", "error")
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapper


def api_admin_required(view):
    """管理端 JSON 接口：未登录返回 401 JSON，不跳转、不返回 HTML。"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not resolve_current_admin():
            return jsonify(ok=False, error="未登录或管理员会话已失效", code="ADMIN_AUTH_REQUIRED"), 401
        return view(*args, **kwargs)
    return wrapper


def api_user_required(view):
    """用户端 JSON 接口（每日答题等）：未登录返回 401 JSON，不跳转。"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not resolve_current_user():
            return jsonify(ok=False, error="未登录或用户会话已失效", code="USER_AUTH_REQUIRED"), 401
        return view(*args, **kwargs)
    return wrapper
