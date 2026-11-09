"""每日答题系统 —— Flask 应用入口（前后端分离 + 角色路由隔离）。

两套入口彻底分开：
- 普通用户：users 表，/login 登录，/ 为 localhost:5000 主界面（完整导航菜单）。
- 管理员：独立 admin 表，/admin/login 登录，登录后直达 /admin/dashboard，
  该页面是纯静态前端，只调 /api/admin/* JSON 接口，不渲染任何用户端导航。

路由分组：
- 公开浏览（无需登录）：/ /date/<date> /leaderboard /leaderboard/api
                        /certification /certification/<slug> /practice
                        /api/practice/tree /api/cert/intro
- 需登录（答题与个人数据）：/quiz/<date> /result /score /wrong /export/...
                        /api/practice/questions /api/practice/check
- 注册登录：/login /register /logout（用户端）；/admin/login /admin/logout（管理端，独立 URL）
- 管理端入口：/admin/dashboard（/admin 仅做兼容跳转）
- 管理端 API：/api/admin/* 全部 JSON，见 admin_api.py
- 遗留管理视图：/import /admin/users /admin/records /admin/set/... 仍保留，
  但统一改为 admin 表鉴权、且不渲染用户端导航；正式入口是 /admin/dashboard。
"""
from __future__ import annotations

import gzip
import json
import os
import re
import secrets
from datetime import date, datetime
from typing import Optional

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import admin_api
import account_api
import auth_api
import catalog
import admin_logic
import admin_service
import auth
import bank
import captcha
import csrf
import db
import leaderboard
import models
import navigation
import points
import practice_api
import quiz_logic
import runtime
import score_manager
import security
import seo
import web_site as site_module
import wrong_book


def _load_secret_key() -> str:
    """SESSION 签名密钥。

    历史版本在代码里写死了 ``daily-quiz-secret-key``，一旦随源码公开，任何人都能
    伪造 ``admin_id`` 的会话 Cookie 直接接管后台。这里做三件事：

    - 生产环境：只接受显式注入且足够长的密钥，缺失/占位/过短就随机生成并告警
      （宁可重启后掉登录态，也不能让全站会话可被伪造）；
    - 本地开发：允许使用固定密钥，避免每次热重载都把本地登录态清掉；
    - 已知占位值一律视为未配置。
    """
    raw = (os.environ.get("SECRET_KEY") or "").strip()
    placeholders = {
        "daily-quiz-secret-key",
        "please-change-this-secret-in-production",
        "changeme",
        "change-me",
        "secret",
    }
    if raw and raw.lower() not in placeholders and len(raw) >= 16:
        return raw

    if runtime.is_production():
        print(
            "[security] 未检测到有效的 SECRET_KEY，已为本次进程随机生成。\n"
            "[security] 这会导致重启后所有登录态失效，请在 .env 中设置：\n"
            "[security]   SECRET_KEY=$(python -c \"import secrets;print(secrets.token_hex(32))\")\n",
            flush=True,
        )
        return secrets.token_hex(32)

    return "daily-quiz-dev-secret-key"


app = Flask(__name__)
app.secret_key = _load_secret_key()

# ---- Cookie 安全属性 -------------------------------------------------------
# HttpOnly：JS 读不到会话 Cookie，降低 XSS 后的会话窃取面。
# SameSite=Lax：跨站 POST 不携带 Cookie，是 CSRF 的最后一道兜底。
# Secure：站点走 HTTPS 时自动开启（SITE_BASE_URL 以 https:// 开头即为 HTTPS 部署），
#         也可用 COOKIE_SECURE=1/0 显式覆盖。
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = runtime.env_flag(
    "COOKIE_SECURE", (os.environ.get("SITE_BASE_URL") or "").strip().lower().startswith("https://")
)
app.config["SITE_BASE_URL"] = (os.environ.get("SITE_BASE_URL") or "").strip()
app.config["CSRF_TRUSTED_ORIGINS"] = (os.environ.get("CSRF_TRUSTED_ORIGINS") or "").strip()

# 生产环境强制关闭 debug：Flask 在构造时会读取 FLASK_DEBUG 环境变量，一旦它被写进
# .env 或 compose（本地调试后忘记删），gunicorn 下同样会打开异常透传与调试中间件。
# 这里按「生产判定」（DB_BACKEND=mysql / APP_ENV=production）兜底关掉，不给线上留口子。
if runtime.is_production():
    app.debug = False
    app.config["DEBUG"] = False

# 请求体上限：题库导入是纯 Markdown，8MB 足够；未设上限时单个超大请求就能把小内存
# 容器（256MB）打到 OOM。表单字段单独放宽到 2MB，兼容「覆盖导入」回填的 pending_content。
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB") or 8) * 1024 * 1024
app.config["MAX_FORM_MEMORY_SIZE"] = 2 * 1024 * 1024

# 解析文本渲染过滤器：所有显示答案的页面统一通过它输出（内部已做 HTML 转义）
app.jinja_env.filters["exp_html"] = quiz_logic.explanation_to_html


def _asset_version() -> str:
    """静态资源指纹：取 static/ 下最新 mtime。

    模板引用静态文件时带上 `?v=`，这样可以把缓存时间拉长到 7 天，
    发版后 URL 变化又会立刻失效，兼顾首屏速度与更新及时性。
    """
    static_dir = os.path.join(app.root_path, "static")
    newest = 0
    for root, _dirs, files in os.walk(static_dir):
        for name in files:
            try:
                newest = max(newest, int(os.path.getmtime(os.path.join(root, name))))
            except OSError:
                continue
    return str(newest or 1)


ASSET_VERSION = _asset_version()
app.jinja_env.globals["asset_version"] = ASSET_VERSION
app.jinja_env.globals["absolute_url"] = seo.absolute
app.jinja_env.globals["site_name"] = seo.SITE_NAME
app.jinja_env.globals["default_description"] = seo.DEFAULT_DESCRIPTION
app.jinja_env.globals["default_image"] = seo.DEFAULT_IMAGE
app.jinja_env.globals["robots_for_path"] = seo.robots_for_path
# 静态资源带版本号，可安全长缓存；HTML 本身不缓存，保证发版即时生效
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 7 * 24 * 3600
# 所有管理端数据接口统一挂在 /api/admin/ 前缀下
app.register_blueprint(admin_api.api_admin)
# 认证题库网用户端（页面 + JSON API）
app.register_blueprint(site_module.site)
# 个人中心 / 账号安全 / 找回密码（JSON + 公开页面）
app.register_blueprint(account_api.bp)
# 注册 / 登录 JSON 接口（用户名校验、验证码、登录限流）
app.register_blueprint(auth_api.bp)
# 自由练习：题库列表 / 模拟答题 / 交卷解析
app.register_blueprint(practice_api.bp)
# 全局 CSRF 防护：校验 Origin / Referer / Sec-Fetch-Site，覆盖表单与 JSON 两类写请求
csrf.init_app(app)


# ---------------------------------------------------------------------------
# 全站 SEO 响应头 + 页面压缩
# ---------------------------------------------------------------------------

# 只压缩文本类响应；图片等二进制不碰
_GZIP_MIMETYPES = {
    "text/html", "text/css", "text/plain", "text/xml", "application/xml",
    "application/json", "application/javascript", "text/javascript",
    "image/svg+xml",
}


@app.after_request
def _seo_and_perf_headers(resp):
    path = request.path or "/"
    robots = seo.robots_for_path(path)
    if resp.status_code == 404:
        robots = "noindex,follow"
    if robots != "index,follow,max-image-preview:large":
        resp.headers.setdefault("X-Robots-Tag", robots)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # 管理后台是纯静态 SPA，被第三方站点 iframe 嵌套后会遭遇点击劫持
    # （诱导管理员点击「删除题库」「重置密码」）。禁止任何站点嵌套本站页面。
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'self'; object-src 'none'; base-uri 'self'",
    )
    resp.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
    )
    # 只在 HTTPS 部署时下发 HSTS，避免本地 http 调试被浏览器强制升级
    if request.is_secure or (request.headers.get("X-Forwarded-Proto") or "").lower() == "https":
        resp.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )

    # 管理端页面与静态资源不走长缓存：登录页 / SPA 一旦被浏览器缓存 7 天，
    # 发版后旧页面仍会请求旧接口（例如缺少验证码字段），会直接登不进去。
    # 关掉强缓存后靠 ETag / Last-Modified 协商，命中时只是一个 304，开销可忽略。
    if path.startswith("/admin") or path.startswith("/static/admin/"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        resp.headers.pop("Expires", None)

    if resp.mimetype not in _GZIP_MIMETYPES:
        return resp
    # HEAD 与 GET 的头要保持一致，否则中间缓存可能把两份响应混用
    if "Accept-Encoding" not in (resp.headers.get("Vary") or ""):
        resp.headers.add("Vary", "Accept-Encoding")
    if request.method == "HEAD":
        return resp
    # 1 核机器只压缩动态文本（HTML/JSON/XML），静态资源交给浏览器缓存
    if (
        "gzip" in (request.headers.get("Accept-Encoding") or "")
        and not resp.direct_passthrough
        and "Content-Encoding" not in resp.headers
        and resp.status_code in (200, 404)
    ):
        raw = resp.get_data()
        if len(raw) >= 860:
            body = gzip.compress(raw, 5)
            if len(body) < len(raw):
                resp.set_data(body)
                resp.headers["Content-Encoding"] = "gzip"
                resp.headers["Content-Length"] = str(len(body))
    return resp


@app.errorhandler(404)
def _handle_404(_error):
    """统一 404 页面：带 noindex 与站内导航，避免爬虫抓到裸错误页。"""
    meta = seo.page(
        "页面不存在 - 认证题库网",
        "该页面不存在或已下线。返回首页可继续浏览阿里云、华为、AWS、思科认证题库与每日答题。",
        request.path,
        robots="noindex,follow",
        canonical="",
    )
    return render_template("404.html", seo=meta), 404


@app.errorhandler(413)
def _handle_413(_error):
    """请求体超过 MAX_CONTENT_LENGTH：接口返回 JSON，页面给出可读提示。"""
    limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({
            "ok": False,
            "error": f"提交内容过大，单次不得超过 {limit_mb}MB",
            "code": "PAYLOAD_TOO_LARGE",
        }), 413
    return (
        f"<!doctype html><meta charset=utf-8><title>413 内容过大</title>"
        f"<h1>提交内容过大</h1><p>单次上传不得超过 {limit_mb}MB，请拆分后重试。</p>",
        413,
        {"Content-Type": "text/html; charset=utf-8"},
    )


models.init_db()
auth.create_default_users()
# 生产环境兜底：演示密码（admin123 / user123）在公网等同于「无密码」，
# 启动时检测并替换为随机密码，只打印一次到服务日志。
auth.harden_default_accounts()

# 日期探测：用于在管理员上传 MD 文件时，从文件名、标题或文件头自动推断导入日期
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _guess_date(md_text: str, title: str, filename: str) -> str:
    """兼容别名：日期探测已统一放到 admin_service.guess_date。"""
    return admin_service.guess_date(md_text, title, filename)


# ---------------------------------------------------------------------------
# 权限装饰器 & 上下文
# ---------------------------------------------------------------------------

# 权限与会话解析统一放在 security.py：普通用户端与管理端各一条独立链路。
# 这里保留模块级别名，兼容既有调用与测试脚本。
_resolve_current_user = security.resolve_current_user
_resolve_current_admin = security.resolve_current_admin
login_required = security.login_required
admin_required = security.admin_required


@app.context_processor
def inject_user():
    """向模板注入两套互不干扰的身份：用户端 current_user / 管理端 current_admin。"""
    user = _resolve_current_user()
    admin = _resolve_current_admin()
    return {
        "current_user": (
            {"id": user["id"], "username": user["username"], "role": "user"}
            if user else None
        ),
        "current_admin": (
            {"id": admin["id"], "username": admin["username"],
             "role": admin.get("role") or "admin"}
            if admin else None
        ),
    }


@app.context_processor
def inject_site_nav():
    """新版「认证题库网」三级菜单：厂商 → 方向 → 认证（配置来自 catalog.py）。"""
    return {"site_nav": catalog.nav_tree()}


@app.context_processor
def inject_navigation():
    """向遗留管理视图注入栏目辅助函数与配置树。

    正式导航由 `site_nav`（catalog.nav_tree）渲染；这里的 `cert_url` 只服务于
    导入结果、旧编辑页等遗留视图的「查看栏目页」链接，需要把历史栏目 slug
    （acp-cloud-computing）映射到新版三级地址（/cert/aliyun/acp/cloud）。
    """

    def cert_url(target_slug: str) -> str:
        if not target_slug:
            return "/"
        if "/" in target_slug and catalog.get_leaf(target_slug):
            return f"/cert/{target_slug}"
        target = site_module.LEGACY_MAP.get(target_slug, "")
        if target and catalog.get_leaf(target):
            return f"/cert/{target}"
        return "/cert/aliyun"

    return {
        "cert_url": cert_url,
        # 导入页 / 编辑页 / 每日答题的三级栏目选择器共用同一份配置树；
        # 每次请求重新读取，运行时新增方向会立即出现在选择器中。
        "category_tree": navigation.category_tree_payload(),
        "practice_tree": navigation.practice_tree_payload(),
        "category_label": navigation.category_label,
    }


def _current_user_id() -> int:
    user = _resolve_current_user()
    return int(user["id"]) if user else 0


def _safe_next(raw: Optional[str]) -> str:
    """校验登录后回跳地址，只允许站内相对路径，防止开放重定向。

    除 ``//evil.com`` 外，浏览器会把 ``/\\evil.com``、``/\\t/evil.com`` 之类的
    变形也解析成外部地址，因此反斜杠与控制字符一律拒绝。
    """
    value = (raw or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return ""
    if any(ord(ch) < 32 for ch in value):
        return ""
    return value


def _is_admin() -> bool:
    user = _resolve_current_user()
    return bool(user and user["role"] == "admin")


def _board_args() -> tuple[str, date, str]:
    """解析排行榜查询参数，非法值一律回退到默认值（今日日榜）。

    Returns:
        (period, on_date, raw_date)。raw_date 用于 tab 链接中原样保留基准日期；
        非法或为空时返回空串（链接回落到本期）。
    """
    period = (request.args.get("period") or "day").strip()
    if period not in leaderboard.PERIOD_LABELS:
        period = "day"
    raw_date = (request.args.get("date") or "").strip()
    on_date = date.today()
    if raw_date:
        try:
            on_date = date.fromisoformat(raw_date)
        except ValueError:
            raw_date = ""
    return period, on_date, raw_date


# ---------------------------------------------------------------------------
# 公开：登录 / 注册 / 登出
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    """普通用户入口：只校验 users 表，管理员账号在这里无法登录。

    登录页不再展示管理员入口——管理员始终从独立的 /admin/login 进入。
    未登录浏览被拦截时，会带 next 参数，登录成功后回到原来的页面。
    每次渲染都会签发一张新验证码；POST 走与 /api/auth/login 相同的服务函数，
    所以关掉 JS 也不会绕过验证码。
    """
    next_url = _safe_next(request.values.get("next"))
    if _resolve_current_user():
        return redirect(next_url or url_for("index"))

    error = ""
    status = 200
    if request.method == "POST":
        try:
            user = auth.login_user(
                request.form.get("username", ""),
                request.form.get("password", ""),
                request.form.get("captcha_key", ""),
                request.form.get("captcha_code", ""),
            )
        except auth.LoginError as exc:
            error = exc.message
            status = 200  # 页面仍渲染登录表单，错误在卡片内展示
        else:
            security.set_user_session(user)
            flash(f"欢迎回来，{user['username']}", "success")
            return redirect(next_url or url_for("index"))

    captcha_key, _code, captcha_svg = captcha.issue()
    return render_template(
        "login.html",
        seo=seo.page(
            "登录 - 认证题库网",
            "登录后可保存答题成绩、错题记录与积分排名；浏览认证题库无需登录。",
            "/login",
            robots="noindex,follow",
        ),
        next_url=next_url,
        captcha_key=captcha_key,
        captcha_svg=captcha_svg,
        error=error,
    ), status


@app.route("/register", methods=["GET", "POST"])
def register():
    """注册普通用户：用户名 4-20 位、唯一、非保留词，后端二次校验 + 唯一索引兜底。"""
    next_url = _safe_next(request.values.get("next"))
    if _resolve_current_user():
        return redirect(next_url or url_for("index"))

    error = ""
    form = {"username": "", "email": "", "phone": ""}
    if request.method == "POST":
        form = {
            "username": (request.form.get("username") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "phone": (request.form.get("phone") or "").strip(),
        }
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if password != confirm:
            error = "两次输入的密码不一致"
        else:
            try:
                created = auth.register_user(
                    form["username"], password, email=form["email"], phone=form["phone"]
                )
            except auth.AccountValidationError as exc:
                error = exc.message
            else:
                security.set_user_session(models.get_user(created["id"]))
                flash("注册成功，已自动登录", "success")
                return redirect(next_url or url_for("index"))

    return render_template(
        "register.html",
        seo=seo.page(
            "注册 - 认证题库网",
            "注册认证题库网账号，开始每日 10 题练习并记录成绩、错题与排行榜积分。",
            "/register",
            robots="noindex,follow",
        ),
        next_url=next_url,
        error=error,
        form=form,
    )


@app.route("/logout")
def logout():
    """只退出用户端，不影响同一浏览器里的管理端登录态。"""
    security.clear_user_session()
    flash("已退出登录", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# 管理端独立入口（与上面的用户端完全隔离）
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """管理员独立登录 URL：验证码 + admin 表校验。

    GET 返回纯静态登录页（不经过 Jinja 渲染），POST 同时支持表单和 JSON；
    表单登录成功 302 到 /admin/dashboard，JSON 登录成功返回 redirect 字段。
    验证码与失败锁定走 admin 作用域，与用户端完全隔离。
    """
    if request.method in ("GET", "HEAD"):
        if _resolve_current_admin():
            return redirect(url_for("admin_dashboard"))
        return app.send_static_file("admin/login.html")

    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    captcha_key = str(payload.get("captchaKey") or payload.get("captcha_key") or "")
    captcha_code = str(payload.get("captchaCode") or payload.get("captcha_code") or "")
    try:
        admin = auth.login_admin(username, password, captcha_key, captcha_code)
    except auth.LoginError as exc:
        if request.is_json:
            return jsonify(
                ok=False, error=exc.message, code=exc.code, captcha_required=True,
                attempts_left=exc.attempts_left, locked_seconds=exc.locked_seconds,
            ), exc.status
        return redirect(url_for("admin_login", error="1", code=exc.code))

    security.set_admin_session(admin)
    models.touch_admin_login(admin["id"])
    if request.is_json:
        return jsonify(ok=True, message="登录成功", redirect=url_for("admin_dashboard"))
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/logout")
def admin_logout():
    """只退出管理端，不影响用户端登录态。"""
    security.clear_admin_session()
    flash("已退出管理后台", "success")
    return redirect(url_for("admin_login"))


_ADMIN_ASSET_RE = re.compile(r"(/static/admin/[A-Za-z0-9_./-]+\.(?:css|js))(?=\")")


def _admin_shell():
    """返回管理端 SPA 外壳，并给静态资源引用打上版本指纹。

    管理端脚本曾经被浏览器按 7 天强缓存，发版后旧脚本会缺少新菜单的渲染分支，
    表现为「点了菜单地址栏变了、右侧内容不动」。给 URL 加 `?v=` 后，脚本一变
    地址就变，浏览器只能重新拉取，不需要用户手动清缓存。
    """
    path = os.path.join(app.static_folder, "admin", "index.html")
    with open(path, encoding="utf-8") as fp:
        html = fp.read()
    html = _ADMIN_ASSET_RE.sub(lambda m: "%s?v=%s" % (m.group(1), ASSET_VERSION), html)
    resp = app.response_class(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    """独立管理后台：返回纯静态 SPA 外壳，全部数据由 /api/admin/* 提供。"""
    return _admin_shell()


@app.route("/admin")
def admin_entry():
    """兼容旧书签：已登录管理员直接进后台，否则跳独立登录页。"""
    if not _resolve_current_admin():
        return redirect(url_for("admin_login"))
    return _admin_shell()


# ---------------------------------------------------------------------------
# 首页：公开浏览。未登录可看今日题目 / 最近题目 / 排行榜，
# 只有「开始答题」等动作会跳转登录。
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """首页：左侧认证分类入口 + 右侧排行榜（新版「认证题库网」）。"""
    return site_module.render_home()


# ---------------------------------------------------------------------------
# 导入（仅管理员）
# ---------------------------------------------------------------------------

def _resolve_category_id(slug: str) -> int:
    """把旧版栏目 slug（acp-cloud-computing）映射到 cert_categories.id。"""
    value = (slug or "").strip()
    if not value:
        return 0
    path = site_module.LEGACY_MAP.get(value, value if "/" in value else "")
    if not path:
        return 0
    category = bank.get_category_by_path(path)
    return int(category["id"]) if category else 0


def _attach_set_questions(set_date: str, slug: str, only_unattached: bool = True) -> int:
    """把「按日期导入」的题目挂到三级认证分类。

    旧版 /import 走 daily_sets + questions(daily_set_id)；新版认证页与每日答题
    只读 cert_category_id。挂接后旧入口导入的题目同样进入认证页题库与抽题池，
    避免管理员在两个入口导入却只看到一半效果。
    """
    category_id = _resolve_category_id(slug)
    row = models.get_daily_set_by_date(set_date) if set_date else None
    if not category_id or not row:
        return 0
    sql = "UPDATE questions SET cert_category_id = ? WHERE daily_set_id = ?"
    args = [category_id, row["id"]]
    if only_unattached:
        sql += " AND cert_category_id IS NULL"
    conn = models.get_conn()
    try:
        with conn:
            cursor = conn.execute(sql, tuple(args))
            return int(cursor.rowcount or 0)
    finally:
        conn.close()


@app.route("/import", methods=["GET"])
@admin_required
def import_page():
    today = date.today().strftime("%Y-%m-%d")
    return render_template("import.html", today=today, selected_category="")


@app.route("/import", methods=["POST"])
@admin_required
def import_do():
    """导入题目：仅接受上传的 Markdown 文件，并必须指定所属栏目。

    覆盖确认时浏览器无法回填已选文件，因此确认页用隐藏字段 `pending_content`
    暂存上一轮上传的内容；该字段仅在 `confirm_overwrite=1` 时读取，
    不作为独立的导入入口（即已取消的「直接粘贴」方式）。
    栏目 slug 一律用 navigation.is_valid_category 校验，防止伪造值入库；
    同时把完整路径文案作为快照写入题库，菜单调整后仍能显示归属。
    """
    confirm_overwrite = request.form.get("confirm_overwrite") == "1"
    md_text, uploaded_filename, upload_fmt, error = admin_service.read_uploaded_file(
        request.files.get("md_file")
    )
    if error:
        flash(error, "error")
        return redirect(url_for("import_page"))
    # 覆盖确认时浏览器无法回填已选文件，用隐藏字段暂存上一轮的内容
    if not md_text and confirm_overwrite:
        md_text = request.form.get("pending_content", "")
    # Markdown / JSON 由文件扩展名或正文自动识别；覆盖确认时表单会显式带 fmt
    fmt = (
        request.form.get("fmt", "").strip()
        or upload_fmt
        or admin_service.detect_format(uploaded_filename, md_text)
    )

    imported = admin_service.import_markdown(
        md_text,
        import_date=request.form.get("date", "").strip(),
        title=request.form.get("title", "").strip(),
        category_slug=request.form.get("category_slug", "").strip(),
        filename=uploaded_filename,
        overwrite=confirm_overwrite,
        fmt=fmt,
    )
    if not imported["ok"] and imported["reason"] == "exists":
        return render_template(
            "import.html",
            today=imported["date"],
            title=imported["title"],
            need_confirm=True,
            pending_content=imported["pending_content"],
            selected_category=imported["category_slug"],
            fmt=imported.get("fmt", "markdown"),
        )
    if not imported["ok"]:
        flash(imported["message"], "error")
        return redirect(url_for("import_page"))

    flash(imported["message"], "success")
    # 同步挂到三级认证分类：新版认证页与每日答题只认 cert_category_id
    _attach_set_questions(imported["date"], imported["category_slug"])
    return render_template(
        "import_result.html",
        date=imported["date"],
        result=imported,
        category_slug=imported["category_slug"],
        category_path=imported["category_path"],
    )


# ---------------------------------------------------------------------------
# 答题（需登录）
# ---------------------------------------------------------------------------

@app.route("/quiz/<date>", methods=["GET"])
@login_required
def quiz(date):
    data = quiz_logic.get_quiz_data(date)
    if not data:
        flash("该日期暂无题目", "error")
        if _is_admin():
            return redirect(url_for("import_page"))
        return redirect(url_for("index"))
    record = score_manager.get_record_by_set_user(data["set_id"], _current_user_id())
    return render_template("quiz.html", data=data, record=record)


@app.route("/quiz/<date>", methods=["POST"])
@login_required
def quiz_submit(date):
    try:
        duration = int(request.form.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0
    try:
        result = quiz_logic.grade_and_save(
            date, _current_user_id(), request.form, duration
        )
    except ValueError as exc:
        message = str(exc)
        if "登录状态" in message:
            # 极端场景（如请求过程中账号被删）：清理登录态并引导重新登录
            session.clear()
            flash(message, "error")
            return redirect(url_for("login", next=url_for("quiz", date=date)))
        flash(message, "error")
        return redirect(url_for("index"))
    except db.DatabaseError:
        # grade_and_save 使用单事务，异常时已整体回滚，不会留下半写数据
        app.logger.exception("保存答题结果失败: date=%s", date)
        flash("保存答题结果失败，请稍后重试", "error")
        return redirect(url_for("quiz", date=date))
    flash("答题完成，成绩已保存", "success")
    return redirect(url_for("result", record_id=result["record_id"]))


@app.route("/result/<int:record_id>", methods=["GET"])
@login_required
def result(record_id):
    rec = score_manager.get_record(record_id)
    if not rec:
        flash("未找到该成绩记录", "error")
        return redirect(url_for("index"))
    # 权限校验：仅本人或管理员可查看
    if rec["user_id"] != _current_user_id() and not _is_admin():
        flash("无权查看该成绩", "error")
        return redirect(url_for("index"))
    s = models.get_daily_set_by_id(rec["daily_set_id"])
    if not s:
        flash("未找到对应的题目集", "error")
        return redirect(url_for("index"))
    wrongs = wrong_book.get_wrong_by_date(s["date"], rec["user_id"])
    for w in wrongs:
        w["user_text"] = quiz_logic.answer_to_text(w["options"], w["user_answer"])
        w["correct_text"] = quiz_logic.answer_to_text(w["options"], w["correct_answer"])
    return render_template("result.html", rec=rec, set_info=s, wrongs=wrongs)


# ---------------------------------------------------------------------------
# 题目查看（公开）& 成绩（需登录；历史日期栏目已下线，题目从首页最近列表与栏目页进入）
# ---------------------------------------------------------------------------

@app.route("/date/<date>")
def date_detail(date):
    s = models.get_daily_set_by_date(date)
    if not s:
        flash("该日期尚未导入题目", "error")
        return redirect(url_for("index"))
    rows = models.get_all_questions_by_set(s["id"])
    sets: dict[int, list[dict]] = {}
    for r in rows:
        sets.setdefault(r["set_index"], []).append(
            {
                "id": r["id"],
                "type": r["type"],
                "question": r["question"],
                "options": json.loads(r["options"]),
                "answer": json.loads(r["answer"]),
                "explanation": r["explanation"] if "explanation" in r.keys() else "",
            }
        )
    grouped = [{"set_index": k, "questions": v} for k, v in sorted(sets.items())]
    return render_template("date_detail.html", set_info=s, grouped=grouped)


@app.route("/score/<date>")
@login_required
def score_detail(date):
    view = quiz_logic.build_score_view(date, _current_user_id())
    if not view:
        flash("该日期尚未导入题目", "error")
        return redirect(url_for("index"))
    if not view["record"]:
        flash("你还没有完成该日期的答题", "error")
        return redirect(url_for("index"))
    for w in view["wrongs"]:
        w["user_text"] = quiz_logic.answer_to_text(w["options"], w["user_answer"])
        w["correct_text"] = quiz_logic.answer_to_text(w["options"], w["correct_answer"])
    return render_template("score_detail.html", view=view)


# ---------------------------------------------------------------------------
# 错题本（需登录，仅本人）
# ---------------------------------------------------------------------------

@app.route("/wrong")
@login_required
def wrong_list():
    wrongs = wrong_book.get_all_wrong(user_id=_current_user_id())
    for w in wrongs:
        w["user_text"] = quiz_logic.answer_to_text(w["options"], w["user_answer"])
        w["correct_text"] = quiz_logic.answer_to_text(w["options"], w["correct_answer"])
    return render_template("wrong.html", wrongs=wrongs)


@app.route("/wrong/<date>/practice", methods=["GET"])
@login_required
def wrong_practice(date):
    wrongs = wrong_book.get_wrong_by_date(date, _current_user_id())
    if not wrongs:
        flash("该日期没有错题啦 🎉", "success")
        return redirect(url_for("wrong_list"))
    return render_template("wrong_practice.html", date=date, wrongs=wrongs)


@app.route("/wrong/<date>/practice", methods=["POST"])
@login_required
def wrong_practice_submit(date):
    try:
        stats = quiz_logic.practice_wrong(date, _current_user_id(), request.form)
    except ValueError as exc:
        if "登录状态" in str(exc):
            session.clear()
        flash(str(exc), "error")
        return redirect(url_for("login") if "登录状态" in str(exc) else url_for("wrong_list"))
    except db.DatabaseError:
        app.logger.exception("保存错题重练结果失败: date=%s", date)
        flash("保存重练结果失败，请稍后重试", "error")
        return redirect(url_for("wrong_list"))
    flash(
        f"重练完成：共 {stats['practiced']} 题，答对 {stats['right']} 题，"
        f"仍错 {stats['still_wrong']} 题",
        "success",
    )
    return redirect(url_for("wrong_list"))


@app.route("/wrong/clear", methods=["POST"])
@login_required
def wrong_clear():
    # 普通用户只清空自己的错题；管理员清空全部（来自后台时）
    user_id = None if _is_admin() and request.form.get("scope") == "all" else _current_user_id()
    deleted = wrong_book.clear_all(user_id=user_id)
    flash(f"已清空错题（共删除 {deleted} 条）", "success")
    return redirect(url_for("wrong_list"))


# ---------------------------------------------------------------------------
# 排行榜（公开浏览，全员可见；登录用户额外看到「我的排名」）
# ---------------------------------------------------------------------------

@app.route("/leaderboard")
def leaderboard_page():
    """日榜 / 周榜 / 月榜页面，支持 ?period=day|week|month 与 ?date=YYYY-MM-DD。"""
    period, on_date, raw_date = _board_args()
    board = leaderboard.get_leaderboard(period, on_date)
    uid = _current_user_id()
    return render_template(
        "leaderboard.html",
        board=board,
        period=period,
        periods=leaderboard.PERIOD_LABELS,
        date_param=raw_date,
        prev_date=leaderboard.shift_period(period, on_date, -1).isoformat(),
        next_date=leaderboard.shift_period(period, on_date, 1).isoformat(),
        my=leaderboard.find_user(board["items"], uid),
        current_uid=uid,
        top=board["items"][:50],
        points_per_correct=points.POINTS_PER_CORRECT,
    )


@app.route("/leaderboard/api")
def leaderboard_api():
    """榜单 JSON 接口，便于前端异步刷新或外部集成（数据与页面同源，公开只读）。"""
    period, on_date, _ = _board_args()
    board = leaderboard.get_leaderboard(period, on_date)
    return jsonify(
        period=board["period"],
        period_label=board["period_label"],
        start=board["start"],
        end=board["end"],
        range_label=board["range_label"],
        participants=board["participants"],
        items=board["items"][:100],
    )


# ---------------------------------------------------------------------------
# 认证栏目与每日答题已迁移到 site.py（/cert/*、/daily-quiz）；
# 这里只保留旧版练习 API，供既有脚本与错题本复用。
# ---------------------------------------------------------------------------

@app.route("/api/practice/tree")
def practice_tree_api():
    """三级联动配置（厂商 / 方向 / 等级），与导入页共用同一份导航树（公开）。"""
    return jsonify(ok=True, data={"tree": navigation.practice_tree_payload()})


@app.route("/api/cert/intro")
def cert_intro_api():
    """读取某认证的数据库介绍（公开只读，后台 CMS 修改后立即生效）。"""
    slug = (request.args.get("slug") or "").strip()
    page = navigation.find_page(slug)
    if page is None:
        return jsonify(ok=False, error="认证栏目不存在"), 404
    row = models.get_cert_intro(slug) or {}
    return jsonify(ok=True, data={
        "slug": slug,
        "cert_name": row.get("cert_name") or page.node.label,
        "intro": row.get("intro") or page.node.intro,
        "source": row.get("source") or "",
        "updated_at": row.get("updated_at") or "",
        "path": navigation.category_path_label(slug),
    })


@app.route("/api/practice/questions")
@security.api_user_required
def practice_questions_api():
    """从选中栏目（含子孙栏目）的题库中随机抽取题目，默认 10 道。

    Query: slug（必填，必须是导航树中的合法节点）、limit（1~50，默认 10）。
    返回题面与选项，**不含答案**。
    """
    slug = (request.args.get("slug") or "").strip()
    if not navigation.is_valid_category(slug):
        return jsonify(ok=False, error="请选择完整有效的认证栏目"), 400
    try:
        limit = int(request.args.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))
    slugs = navigation.descendant_slugs(slug)
    rows = models.random_questions_by_category(slugs, limit)
    items = [
        {
            "id": r["id"],
            "type": r["type"],
            "type_label": _TYPE_LABELS.get(r["type"], r["type"]),
            "question": r["question"],
            "options": json.loads(r["options"]),
            "set_title": r["set_title"],
            "set_date": r["set_date"],
            "category_path": r["category_path"],
        }
        for r in rows
    ]
    return jsonify(ok=True, data={
        "slug": slug,
        "path": navigation.category_path_label(slug),
        "triplet": navigation.category_triplet(slug),
        "total_available": models.count_questions_by_category(slugs),
        "count": len(items),
        "items": items,
    })


def _normalize_practice_answer(raw) -> list[str]:
    """把前端提交的答案归一成大写字母列表。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        values = [str(v).strip().upper()[:1] for v in raw]
    else:
        text = re.sub(r"[\s,，、;；/|]", "", str(raw).strip().upper())
        values = list(text)
    return [v for v in values if v]


@app.route("/api/practice/check", methods=["POST"])
@security.api_user_required
def practice_check_api():
    """判分：按题目 id 回查数据库答案，返回对错、正确答案与解析。"""
    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers") or {}
    if not isinstance(answers, dict) or not answers:
        return jsonify(ok=False, error="没有收到作答内容"), 400
    rows = models.get_questions_by_ids(list(answers.keys()))
    results = []
    correct_count = 0
    for r in rows:
        options = json.loads(r["options"])
        correct_ans = json.loads(r["answer"])
        user_ans = _normalize_practice_answer(answers.get(str(r["id"])))
        is_correct = quiz_logic.judge(r["type"], correct_ans, user_ans)
        if is_correct:
            correct_count += 1
        results.append({
            "id": r["id"],
            "question": r["question"],
            "correct": is_correct,
            "your_answer": user_ans,
            "correct_answer": correct_ans,
            "correct_text": quiz_logic.answer_to_text(options, correct_ans),
            "explanation_html": quiz_logic.explanation_to_html(r["explanation"]),
        })
    return jsonify(ok=True, data={
        "total": len(results),
        "correct": correct_count,
        "accuracy": round(correct_count / len(results) * 100, 2) if results else 0.0,
        "results": results,
    })


# ---------------------------------------------------------------------------
# 导出（需登录，仅本人数据）
# ---------------------------------------------------------------------------

def _json_download(data: dict, filename: str) -> Response:
    body = json.dumps(data, ensure_ascii=False, indent=2)
    resp = Response(body, mimetype="application/json")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


@app.route("/export/questions/<date>")
@login_required
def export_questions(date):
    s = models.get_daily_set_by_date(date)
    if not s:
        flash("该日期尚未导入题目", "error")
        return redirect(url_for("index"))
    rows = models.get_all_questions_by_set(s["id"])
    sets: dict[int, list[dict]] = {}
    for r in rows:
        sets.setdefault(r["set_index"], []).append(
            {
                "id": r["id"],
                "type": r["type"],
                "question": r["question"],
                "options": json.loads(r["options"]),
                "answer": json.loads(r["answer"]),
                "explanation": r["explanation"] if "explanation" in r.keys() else "",
            }
        )
    payload = {
        "date": date,
        "title": s["title"],
        "total_sets": s["total_sets"],
        "category_slug": s["category_slug"],
        "category_path": s["category_path"],
        "sets": [{"set_index": k, "questions": v} for k, v in sorted(sets.items())],
    }
    return _json_download(payload, f"questions_{date}.json")


@app.route("/export/score/<date>")
@login_required
def export_score(date):
    s = models.get_daily_set_by_date(date)
    if not s:
        flash("该日期尚未导入题目", "error")
        return redirect(url_for("index"))
    rec = score_manager.get_record_by_set_user(s["id"], _current_user_id())
    if not rec:
        flash("你还没有完成该日期的答题，暂无成绩可导出", "error")
        return redirect(url_for("index"))
    payload = {
        "date": date,
        "title": s["title"],
        "category_slug": s["category_slug"],
        "category_path": s["category_path"],
        "total": rec["total"],
        "correct": rec["correct"],
        "wrong": rec["wrong"],
        "accuracy": rec["accuracy"],
        "duration": rec["duration"],
        "finished_at": rec["finished_at"],
    }
    return _json_download(payload, f"score_{date}.json")


@app.route("/export/wrong")
@login_required
def export_wrong():
    wrongs = wrong_book.get_all_wrong(user_id=_current_user_id())
    payload = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(wrongs),
        "wrong_questions": [
            {
                "date": w.get("date"),
                "set_index": w["set_index"],
                "question": w["question"],
                "options": w["options"],
                "user_answer": w["user_answer"],
                "correct_answer": w["correct_answer"],
                "explanation": w.get("explanation", ""),
            }
            for w in wrongs
        ],
    }
    return _json_download(payload, "wrong_questions.json")


# ---------------------------------------------------------------------------
# 遗留管理视图（兼容旧书签与自动化回归）
#
# 正式入口是 /admin/dashboard（纯静态前端 + /api/admin/* JSON）。这些页面只做
# 展示，统一使用 admin 表鉴权与 admin_base.html（不含任何用户端导航），
# 业务逻辑与管理端 API 共用 admin_service / admin_logic，不会出现两套规则。
# ---------------------------------------------------------------------------

@app.route("/admin/users")
@admin_required
def admin_users():
    users = models.list_users_with_stats()
    return render_template("admin_users.html", users=users)


@app.route("/admin/records")
@admin_required
def admin_records():
    records = score_manager.list_all_records()
    return render_template("admin_records.html", records=records)


@app.route("/admin/set/<date>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_set(date):
    """编辑题库：GET 展示表单，POST 校验并保存（含日期、标题、逐题内容）。

    保存成功后重定向到「可能已修改的新日期」的编辑页；校验失败时用 flash
    提示并重新渲染表单，admin_logic.save_edit 在失败前不会写入任何数据。
    """
    if request.method == "POST":
        try:
            saved = admin_logic.save_edit(date, request.form)
        except admin_logic.EditError as exc:
            flash(str(exc), "error")
        except db.DatabaseError:
            app.logger.exception("保存题库修改失败: date=%s", date)
            flash("保存失败，请稍后重试", "error")
        else:
            row = models.get_daily_set_by_date(saved["date"])
            if row:
                # 编辑页可能改了所属栏目，题目的 cert_category_id 跟着走
                _attach_set_questions(
                    saved["date"], row["category_slug"] or "", only_unattached=False
                )
            flash(
                f"已保存「{saved['title']}」：更新 {saved['updated']} 题、"
                f"删除 {saved['deleted']} 题，当前共 {saved['total']} 题",
                "success",
            )
            return redirect(url_for("admin_edit_set", date=saved["date"]))

    view = admin_logic.build_set_editor(date)
    if not view:
        flash("该日期题库不存在", "error")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_set_edit.html", view=view, date=date)


@app.route("/admin/set/<date>/delete", methods=["GET", "POST"])
@admin_required
def admin_delete_set(date):
    """删除题库：GET 展示影响范围并要求输入日期确认，POST 执行级联删除。

    用「手工输入日期」做二次确认，避免误点链接或脚本自动提交导致不可逆删除。
    """
    preview = admin_logic.build_delete_preview(date)
    if not preview:
        flash("该日期题库不存在", "error")
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        if (request.form.get("confirm_text") or "").strip() != date:
            flash("确认失败：请准确输入该题库的日期后再删除", "error")
            return render_template("admin_set_delete.html", preview=preview, date=date)
        try:
            deleted = admin_logic.delete_set(date)
        except admin_logic.EditError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin_dashboard"))
        except db.DatabaseError:
            app.logger.exception("删除题库失败: date=%s", date)
            flash("删除失败，请稍后重试", "error")
            return render_template("admin_set_delete.html", preview=preview, date=date)
        flash(
            f"已删除 {deleted['date']}「{deleted['title']}」："
            f"{deleted['question_count']} 道题、{deleted['record_count']} 条成绩、"
            f"{deleted['wrong_count']} 条错题",
            "success",
        )
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_set_delete.html", preview=preview, date=date)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Docker 部署用 gunicorn（见 Dockerfile / docker-compose.yml），这里仅供本地开发。
    # debug 必须显式开启：Werkzeug 调试器能在页面上执行任意 Python 代码，
    # 一旦有人用 `python app.py` 把服务跑在公网，debug=True 等于直接交出服务器。
    app.run(
        host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_RUN_PORT", 5000)),
        debug=runtime.env_flag("FLASK_DEBUG"),
    )
