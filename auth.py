"""认证与权限模块。

职责：
- 密码哈希（werkzeug pbkdf2:sha256，自带加盐，不存明文）。
- 用户注册、登录校验。
- 首次启动时播种默认账号（管理员 + 普通用户演示账号）。

本模块只依赖 models，不依赖 Flask，方便在脚本/测试中复用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

import models

# 默认账号（首次启动、库为空时创建）。生产环境请修改或导入后尽快改密。
DEFAULT_ADMIN = ("admin", "admin123")
DEFAULT_USER = ("user", "user123")


def hash_password(password: str) -> str:
    """返回加盐后的密码哈希。"""
    return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password_hash: str, password: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    return check_password_hash(password_hash, password)


def register(username: str, password: str, role: str = "user") -> tuple[bool, str]:
    """注册新用户。

    Returns:
        (ok, message)。
    """
    username = (username or "").strip()
    if not username:
        return False, "用户名不能为空"
    if len(username) < 2 or len(username) > 20:
        return False, "用户名长度需为 2-20 个字符"
    if len(password) < 6:
        return False, "密码长度至少 6 位"
    if models.get_user_by_username(username):
        return False, "该用户名已被占用"
    models.create_user(username, hash_password(password), role)
    return True, "注册成功"


def authenticate(username: str, password: str) -> Optional[dict]:
    """校验登录凭据，成功返回用户字典，失败返回 None。"""
    user = models.get_user_by_username(username)
    if user and verify_password(user["password_hash"], password):
        return dict(user)
    return None


def create_default_users() -> None:
    """若库中无任何用户，则创建默认管理员与普通用户演示账号。"""
    if models.count_users() > 0:
        return
    admin_name, admin_pw = DEFAULT_ADMIN
    user_name, user_pw = DEFAULT_USER
    models.create_user(admin_name, hash_password(admin_pw), "admin")
    models.create_user(user_name, hash_password(user_pw), "user")
    print(
        f"[auth] 已创建默认账号 -> 管理员 {admin_name}/{admin_pw}，普通用户 {user_name}/{user_pw}"
    )
