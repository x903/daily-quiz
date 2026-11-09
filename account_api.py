"""个人中心 / 账号安全接口 + 找回密码页面。

JSON 前缀：
    GET  /api/user/profile          当前用户资料（手机 / 邮箱为脱敏值）
    PUT  /api/user/profile          更新昵称、性别、个人简介
    PUT  /api/user/password         修改密码（校验原密码，成功后强制重新登录）
    POST /api/user/bind-phone       发送短信验证码
    POST /api/user/verify-phone     校验并绑定手机号
    POST /api/user/bind-email       发送邮箱验证码
    POST /api/user/verify-email     校验并绑定邮箱
    POST /api/auth/forgot-password  提交邮箱，发送重置邮件（公开）
    POST /api/auth/reset-password   用一次性 Token 重置密码（公开）

页面：
    GET /forgot-password            找回密码第 1 步
    GET /reset-password?token=...   设置新密码

普通用户接口一律走 `api_user_required`：管理员 session 在这里是"未登录"，
因此管理后台不会被牵进个人中心，也不会出现越权改普通用户资料的口子。
"""
from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, jsonify, render_template, request

import account
import bank
import captcha
import security

bp = Blueprint("account", __name__)


def api_action(view):
    """统一成功 / 失败响应，避免每个路由重复写 try/except。"""

    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            data = view(*args, **kwargs)
            return jsonify({"ok": True, "data": data})
        except account.AccountError as exc:
            payload = {"ok": False, "error": exc.message, "code": exc.code}
            payload.update(exc.extra)
            return jsonify(payload), exc.status

    return wrapper


def _body() -> dict:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _base_url() -> str:
    """邮件里的绝对地址：优先 SITE_BASE_URL（反代 / 容器场景），否则取当前站点。"""
    configured = (os.environ.get("SITE_BASE_URL") or "").strip()
    return configured or request.url_root


# ---------------------------------------------------------------------------
# 个人资料
# ---------------------------------------------------------------------------

@bp.route("/api/user/profile", methods=["GET"])
@security.api_user_required
@api_action
def api_get_profile():
    user = security.resolve_current_user()
    profile = account.profile_payload(user["id"])
    profile["stats"] = bank.user_stats(user["id"])
    profile["ranks"] = {p: bank.user_rank(p, user["id"]) for p in ("day", "week", "month")}
    return profile


@bp.route("/api/user/profile", methods=["PUT", "POST"])
@security.api_user_required
@api_action
def api_update_profile():
    user = security.resolve_current_user()
    body = _body()
    return account.update_profile(
        user["id"],
        body.get("nickname", ""),
        body.get("gender", ""),
        body.get("bio", ""),
    )


# ---------------------------------------------------------------------------
# 修改密码
# ---------------------------------------------------------------------------

@bp.route("/api/user/password", methods=["PUT", "POST"])
@security.api_user_required
@api_action
def api_change_password():
    user = security.resolve_current_user()
    body = _body()
    result = account.change_password(
        user["id"],
        body.get("old_password", ""),
        body.get("new_password", ""),
        body.get("confirm_password", ""),
    )
    # 密码已变更：立刻注销当前会话，前端引导重新登录
    security.clear_user_session()
    return result


# ---------------------------------------------------------------------------
# 手机号 / 邮箱绑定
# ---------------------------------------------------------------------------

@bp.route("/api/user/bind-phone", methods=["POST"])
@security.api_user_required
@api_action
def api_bind_phone():
    user = security.resolve_current_user()
    return account.send_phone_code(user["id"], _body().get("phone", ""))


@bp.route("/api/user/verify-phone", methods=["POST"])
@security.api_user_required
@api_action
def api_verify_phone():
    user = security.resolve_current_user()
    body = _body()
    return account.verify_phone(user["id"], body.get("phone", ""), body.get("code", ""))


@bp.route("/api/user/bind-email", methods=["POST"])
@security.api_user_required
@api_action
def api_bind_email():
    user = security.resolve_current_user()
    return account.send_email_code(user["id"], _body().get("email", ""))


@bp.route("/api/user/verify-email", methods=["POST"])
@security.api_user_required
@api_action
def api_verify_email():
    user = security.resolve_current_user()
    body = _body()
    return account.verify_email(user["id"], body.get("email", ""), body.get("code", ""))


# ---------------------------------------------------------------------------
# 邮箱找回密码（公开）
# ---------------------------------------------------------------------------

FORGOT_RATE_LIMIT = 10
FORGOT_RATE_WINDOW = 3600


@bp.route("/api/auth/forgot-password", methods=["POST"])
@api_action
def api_forgot_password():
    # 该接口公开且会触发发信：按 IP 限流，避免被当作「邮箱是否注册」的批量探测
    # 工具，也避免脚本刷爆 SMTP 配额。
    limited, retry_after = captcha.hit_action_rate(
        security.client_ip(), "forgot", FORGOT_RATE_LIMIT, FORGOT_RATE_WINDOW
    )
    if limited:
        return jsonify({
            "ok": False,
            "error": "操作过于频繁，请稍后再试",
            "code": "RATE_LIMITED",
            "retry_after": retry_after,
        }), 429
    return account.request_password_reset(_body().get("email", ""), _base_url())


@bp.route("/api/auth/reset-password", methods=["POST"])
@api_action
def api_reset_password():
    body = _body()
    return account.reset_password(
        body.get("token", ""), body.get("password", ""), body.get("confirm_password", "")
    )


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

@bp.route("/forgot-password")
def forgot_password_page():
    seo = {
        "title": "找回密码 - 认证题库网",
        "keywords": "找回密码,重置密码,认证题库网",
        "description": "通过已绑定邮箱找回认证题库网账号密码，重置链接 30 分钟内有效。",
        "canonical": "/forgot-password",
    }
    return render_template("forgot_password.html", seo=seo)


@bp.route("/reset-password")
def reset_password_page():
    token = (request.args.get("token") or "").strip()
    state = account.inspect_reset_token(token)
    seo = {
        "title": "设置新密码 - 认证题库网",
        "keywords": "重置密码,设置新密码",
        "description": "使用一次性链接设置认证题库网账号的新密码。",
        "canonical": "/reset-password",
        "robots": "noindex,nofollow",
    }
    return render_template("reset_password.html", seo=seo, token=token, state=state)
