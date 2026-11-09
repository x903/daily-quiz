"""CSRF 防护：SameSite + Origin / Referer / Sec-Fetch-Site 三重校验。

为什么不是「每个表单塞 token」：
本站同时有服务端渲染表单（登录、注册、答题、导入、删除确认）和多套 fetch SPA
（管理端、题库答题、个人中心）。给十几个模板和上百个 fetch 调用逐个加 token，
改动面大、漏一处就等于没防护。浏览器在**所有**跨站写请求上都会带 Origin
（表单、fetch、XHR 一致），且页面脚本无法伪造该头，因此在服务端统一校验
来源更彻底，也不需要改任何前端代码。

校验规则（仅针对 POST / PUT / PATCH / DELETE）：

1. ``Sec-Fetch-Site`` 若存在，只接受 ``same-origin`` / ``none``；
   ``cross-site`` 与 ``same-site``（子域）一律拒绝。
2. ``Origin`` 若存在，必须命中允许来源集合（当前 Host、``SITE_BASE_URL``、
   ``CSRF_TRUSTED_ORIGINS``）。
3. 没有 ``Origin`` 时退回校验 ``Referer`` 的 origin。
4. 两者都没有（curl / 服务端到服务端 / 老浏览器）时放行，由
   ``SESSION_COOKIE_SAMESITE=Lax`` 兜底 —— 浏览器不会在跨站 POST 中携带
   Lax Cookie，因此这类请求即便到达也没有登录态。

反向代理如果改写了 Host，请把对外域名写进 ``SITE_BASE_URL``
（邮件链接本来也需要它），否则同源 POST 会被判为跨站。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from flask import abort, current_app, jsonify, request

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SAFE_FETCH_SITES = frozenset({"same-origin", "none"})


def _origin_of(url: str) -> str:
    """从 URL 中取出 ``scheme://host[:port]``，非法值返回空串。"""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def allowed_origins() -> set[str]:
    """本次请求允许的来源集合（含当前 Host，兼容反代后的对外域名）。"""
    origins = {_origin_of(request.host_url)}
    configured = (
        str(current_app.config.get("SITE_BASE_URL") or "")
        + ","
        + str(current_app.config.get("CSRF_TRUSTED_ORIGINS") or "")
    )
    for item in configured.split(","):
        origin = _origin_of(item)
        if origin:
            origins.add(origin)
    origins.discard("")
    return origins


def _reject():
    """拒绝跨站写请求：接口返回 JSON，页面返回 403。"""
    if request.path.startswith("/api/") or request.is_json:
        resp = jsonify({
            "ok": False,
            "error": "请求来源校验失败，请刷新页面后重试",
            "code": "CSRF_REJECTED",
        })
        resp.status_code = 403
        return resp
    abort(403)


def check_request():
    """在 ``before_request`` 中调用；返回 None 表示放行。"""
    if request.method not in UNSAFE_METHODS:
        return None

    fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site and fetch_site not in _SAFE_FETCH_SITES:
        return _reject()

    allowed = allowed_origins()

    origin = (request.headers.get("Origin") or "").strip()
    if origin:
        if origin.lower() == "null" or _origin_of(origin) not in allowed:
            return _reject()
        return None

    referer = (request.headers.get("Referer") or "").strip()
    if referer:
        if _origin_of(referer) not in allowed:
            return _reject()

    return None


def init_app(app) -> None:
    """注册全局 CSRF 校验与 403 处理。"""
    app.before_request(check_request)

    @app.errorhandler(403)
    def _handle_403(_error):
        if request.path.startswith("/api/") or request.is_json:
            resp = jsonify({
                "ok": False,
                "error": "没有权限执行该操作",
                "code": "FORBIDDEN",
            })
            resp.status_code = 403
            return resp
        return (
            "<!doctype html><meta charset=utf-8><title>403 禁止访问</title>"
            "<h1>403 禁止访问</h1><p>请求来源校验失败，请返回上一页刷新后重试。</p>",
            403,
            {"Content-Type": "text/html; charset=utf-8"},
        )
