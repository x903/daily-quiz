"""注册 / 登录的 JSON 接口（前端 fetch 调用，服务端渲染表单共用同一套逻辑）。

接口：
    GET  /api/auth/check-username?username=xxx   注册页实时校验用户名
    POST /api/auth/register                      注册（用户名唯一性后端二次校验）
    GET  /api/auth/captcha                       取一张登录图形验证码
    POST /api/auth/login                         登录（先验验证码，再验账号密码）

约定：
- 成功 `{"ok": true, ...}`，失败 `{"ok": false, "error": 文案, "code": 业务码}`；
- 用户名被占用返回 409，验证码错误 400，账号锁定 423，触发限流 429；
- 验证码明文只存在 Redis / 服务端缓存，接口只返回 captchaKey 与 SVG；
- 所有错误都不回显用户输入的密码。
"""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request

import auth
import captcha
import models
import security

bp = Blueprint("auth_api", __name__)

CHECK_USERNAME_WINDOW = 60
CHECK_USERNAME_LIMIT = 60
# 注册限流：同一 IP 每小时最多 60 个账号。注册无需登录、也无需验证码，
# 不设上限时脚本可以无限刷垃圾账号（占用用户名、写爆 users 表）。
REGISTER_RATE_LIMIT = 60
REGISTER_RATE_WINDOW = 3600


def _body() -> dict:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return request.form.to_dict()


def _redirect_target(raw: str) -> str:
    """只允许站内相对路径，防止 next 被用来做开放重定向。

    与 ``app._safe_next`` 保持同一套规则：拒绝 ``//host``、反斜杠（浏览器会把
    ``/\\evil.com`` 当成协议相对地址）以及控制字符。
    """
    target = (raw or "").strip()
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    if any(ord(ch) < 32 for ch in target):
        return "/"
    return target


def api_action(view):
    """统一异常 → JSON，避免每个路由重复写 try/except。"""

    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except auth.AccountValidationError as exc:
            payload = {"ok": False, "error": exc.message, "code": exc.code}
            if isinstance(exc, auth.LoginError):
                payload["captcha_required"] = True
                if exc.attempts_left is not None:
                    payload["attempts_left"] = exc.attempts_left
                if exc.locked_seconds:
                    payload["locked_seconds"] = exc.locked_seconds
            return jsonify(payload), exc.status

    return wrapper


# ---------------------------------------------------------------------------
# 用户名实时校验
# ---------------------------------------------------------------------------
@bp.route("/api/auth/check-username", methods=["GET"])
@api_action
def check_username():
    limited, retry_after = captcha.hit_check_rate(security.client_ip())
    if limited:
        return jsonify({
            "ok": False,
            "error": "校验过于频繁，请稍后再试",
            "code": "RATE_LIMITED",
            "retry_after": retry_after,
        }), 429

    result = auth.username_available(request.args.get("username", ""))
    payload = {
        "ok": True,
        "available": result["available"],
        "username": result["username"],
        "code": result["code"],
        "message": result["message"],
    }
    return jsonify(payload)


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
@bp.route("/api/auth/register", methods=["POST"])
@api_action
def register():
    limited, retry_after = captcha.hit_action_rate(
        security.client_ip(), "register", REGISTER_RATE_LIMIT, REGISTER_RATE_WINDOW
    )
    if limited:
        return jsonify({
            "ok": False,
            "error": "注册过于频繁，请稍后再试",
            "code": "RATE_LIMITED",
            "retry_after": retry_after,
        }), 429

    data = _body()
    username = data.get("username", "")
    password = data.get("password", "")
    confirm = data.get("confirmPassword") or data.get("confirm_password") or data.get("confirm") or ""
    if password != confirm:
        raise auth.AccountValidationError("两次输入的密码不一致", code="PASSWORD_MISMATCH")

    created = auth.register_user(
        username,
        password,
        email=data.get("email", ""),
        phone=data.get("phone", ""),
    )
    row = models.get_user(created["id"])
    security.set_user_session(row)

    return jsonify({
        "ok": True,
        "data": {
            "user": {"id": created["id"], "username": created["username"]},
            "redirect": _redirect_target(data.get("next", "")),
        },
    })


# ---------------------------------------------------------------------------
# 登录验证码
# ---------------------------------------------------------------------------
@bp.route("/api/auth/captcha", methods=["GET"])
def issue_captcha():
    limited, retry_after = captcha.hit_captcha_rate(security.client_ip())
    if limited:
        return jsonify({
            "ok": False,
            "error": "验证码获取过于频繁，请稍后再试",
            "code": "CAPTCHA_RATE_LIMITED",
            "retry_after": retry_after,
        }), 429

    key, _code, image = captcha.issue()
    return jsonify({
        "ok": True,
        "data": {
            "captchaKey": key,
            "image": image,          # data:image/svg+xml;base64,... 明文答案不在其中
            "expiresIn": captcha.CAPTCHA_TTL_SECONDS,
        },
    })


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
@bp.route("/api/auth/login", methods=["POST"])
@api_action
def login():
    data = _body()
    user = auth.login_user(
        data.get("username", ""),
        data.get("password", ""),
        data.get("captchaKey") or data.get("captcha_key") or "",
        data.get("captchaCode") or data.get("captcha_code") or "",
    )
    security.set_user_session(models.get_user(user["id"]))
    return jsonify({
        "ok": True,
        "data": {
            "user": {"id": user["id"], "username": user["username"]},
            "redirect": _redirect_target(data.get("next", "")),
        },
    })
