"""管理端 REST API（统一前缀 /api/admin/，全部返回 JSON）。

前端 /admin/dashboard 是纯静态页面，所有数据都通过这里的接口获取与提交；
服务端不再为管理端渲染任何业务页面，实现前后端分离。

统一响应格式：
    成功 {"ok": true,  "message": "...", "data": ...}
    失败 {"ok": false, "error": "...", "reason": "..."}，HTTP 4xx/5xx
鉴权：除 login / logout / session / captcha 外，全部要求独立的管理端 session，
      未登录返回 401 JSON（不是 302，也不是 HTML 登录页）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal

from flask import Blueprint, jsonify, request

import admin_logic
import bank
import admin_service
import auth
import captcha
import db
import models
import navigation
import practice_bank
import score_manager
import security
import wrong_book

api_admin = Blueprint("api_admin", __name__, url_prefix="/api/admin")

# 管理端登录页是静态页，每次打开都会取一张码；比用户端放宽到 20 张 / 5 分钟，
# 既能挡住批量刷码，也不会让正常刷新页面的人被自己的验证码限流卡住。
ADMIN_CAPTCHA_RATE_LIMIT = 20


def ok(data=None, message: str = "", **extra):
    payload = {"ok": True, "message": message, "data": jsonable(data)}
    payload.update(jsonable(extra))
    return jsonify(payload)


def fail(message: str, status: int = 400, reason: str = "", **extra):
    """统一失败响应：reason 是内部业务码，同时以 code 暴露给前端使用。

    历史上管理端用 reason、用户端用 code，前端两套都要读；这里保持向后兼容，
    一份响应同时给出两个字段，避免调用方因为记错字段名而拿不到错误码。
    """
    payload = {"ok": False, "error": message, "reason": reason}
    if reason:
        payload["code"] = reason
    payload.update(jsonable(extra))
    return jsonify(payload), status


def jsonable(value):
    """把 MySQL 的 Decimal / datetime / RowDict 等转成可 JSON 序列化的类型。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict) or hasattr(value, "keys"):
        return {str(k): jsonable(v) for k, v in dict(value).items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return value


def _payload():
    """兼容 JSON 与表单两种提交方式；JSON 支持平铺或 {"fields": {...}}。"""
    if request.is_json:
        data = request.get_json(silent=True) or {}
        return data.get("fields", data) if isinstance(data, dict) else {}
    return request.form


def _str(key: str, default: str = "") -> str:
    value = _payload().get(key, default)
    return str(value).strip() if value is not None else ""


def _client_ip() -> str:
    """客户端 IP：统一走 security.client_ip（默认不信 X-Forwarded-For）。"""
    return security.client_ip()


# ---------------------------------------------------------------------------
# 会话（公开）
# ---------------------------------------------------------------------------

@api_admin.route("/captcha", methods=["GET"])
def captcha_image():
    """签发管理员登录验证码。

    与管理端登录共用 scope="admin"：用户端签发的验证码无法在这里通过校验，
    两端的取码限流、失败计数、账号锁定也完全隔离。
    """
    limited, retry_after = captcha.hit_captcha_rate(
        _client_ip(), scope="admin", limit=ADMIN_CAPTCHA_RATE_LIMIT
    )
    if limited:
        resp = jsonify({
            "ok": False,
            "error": "验证码获取过于频繁，请稍后再试",
            "code": "CAPTCHA_RATE_LIMITED",
            "retry_after": retry_after,
        })
        resp.status_code = 429
        resp.headers["Cache-Control"] = "no-store"
        return resp

    key, _code, image = captcha.issue(scope="admin")
    resp = jsonify({
        "ok": True,
        "data": {
            "captchaKey": key,
            "image": image,          # data:image/svg+xml;base64,... 明文答案不在其中
            "expiresIn": captcha.CAPTCHA_TTL_SECONDS,
        },
    })
    # 验证码必须每次回源，不能被浏览器或中间缓存复用
    resp.headers["Cache-Control"] = "no-store"
    return resp


@api_admin.route("/login", methods=["POST"])
def login():
    """管理员登录：验证码 → 账号密码，成功后写入独立的管理端 session。"""
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    captcha_key = str(payload.get("captchaKey") or payload.get("captcha_key") or "")
    captcha_code = str(payload.get("captchaCode") or payload.get("captcha_code") or "")
    try:
        admin = auth.login_admin(username, password, captcha_key, captcha_code)
    except auth.LoginError as exc:
        extra = {"captcha_required": True}
        if exc.attempts_left is not None:
            extra["attempts_left"] = exc.attempts_left
        if exc.locked_seconds:
            extra["locked_seconds"] = exc.locked_seconds
        return fail(exc.message, exc.status, exc.code, **extra)

    security.set_admin_session(admin)
    models.touch_admin_login(admin["id"])
    return ok(
        {"id": admin["id"], "username": admin["username"],
         "role": admin.get("role") or "admin"},
        "登录成功",
        redirect="/admin/dashboard",
    )


@api_admin.route("/logout", methods=["POST"])
def logout():
    security.clear_admin_session()
    return ok(message="已退出管理后台", redirect="/admin/login")


@api_admin.route("/session", methods=["GET"])
def session_info():
    admin = security.resolve_current_admin()
    return ok(
        {
            "logged_in": bool(admin),
            "admin": ({"id": admin["id"], "username": admin["username"],
                       "role": admin.get("role") or "admin"} if admin else None),
            "login_url": "/admin/login",
            "dashboard_url": "/admin/dashboard",
        }
    )


# ---------------------------------------------------------------------------
# 账号安全：修改自己的密码 / 重置其他管理员密码
# ---------------------------------------------------------------------------

@api_admin.route("/password", methods=["POST", "PUT"])
@security.api_admin_required
def change_password():
    """修改当前登录管理员的密码：需原密码，成功后立刻注销本人会话。"""
    admin = security.resolve_current_admin()
    payload = _payload()
    try:
        result = auth.change_admin_password(
            admin["id"],
            str(payload.get("old_password") or ""),
            str(payload.get("new_password") or ""),
            str(payload.get("confirm_password") or ""),
        )
    except auth.AccountValidationError as exc:
        extra = {}
        if isinstance(exc, auth.LoginError):
            if exc.attempts_left is not None:
                extra["attempts_left"] = exc.attempts_left
            if exc.locked_seconds:
                extra["locked_seconds"] = exc.locked_seconds
        return fail(exc.message, exc.status, exc.code, **extra)

    # 强制重新登录：新密码立即生效，旧会话（含其他设备）因指纹不匹配同步失效
    security.clear_admin_session()
    return ok(result, result["message"], redirect="/admin/login?pwd=1")


@api_admin.route("/admins/<int:admin_id>/password", methods=["POST", "PUT"])
@security.api_admin_required
def reset_password(admin_id):
    """重置指定管理员的密码（不能重置自己，需输入操作者本人的密码确认）。"""
    admin = security.resolve_current_admin()
    payload = _payload()
    try:
        result = auth.reset_admin_password(
            admin,
            admin_id,
            str(payload.get("new_password") or ""),
            str(payload.get("confirm_password") or ""),
            str(payload.get("operator_password") or ""),
        )
    except auth.AccountValidationError as exc:
        return fail(exc.message, exc.status, exc.code)
    return ok(result, result["message"])


# ---------------------------------------------------------------------------
# 元数据 / 概览
# ---------------------------------------------------------------------------

@api_admin.route("/meta", methods=["GET"])
@security.api_admin_required
def meta():
    admin = security.resolve_current_admin()
    return ok({
        "admin": {"id": admin["id"], "username": admin["username"]},
        "sections": [
            {"key": "overview", "label": "控制台概览"},
            {"key": "sets", "label": "题库管理"},
            {"key": "import", "label": "题库导入"},
            {"key": "intros", "label": "认证介绍管理"},
            {"key": "users", "label": "用户与权限"},
            {"key": "records", "label": "答题记录"},
        ],
        "categories": navigation.category_tree_payload(),
        "type_labels": admin_logic.TYPE_LABELS,
    })


@api_admin.route("/overview", methods=["GET"])
@security.api_admin_required
def overview():
    users = models.list_users_with_stats()
    sets = admin_logic.list_sets()
    records = score_manager.list_all_records()
    wrong = wrong_book.get_all_wrong(user_id=None)
    return ok({
        "stats": {
            "user_count": len(users),
            "set_count": len(sets),
            "record_count": len(records),
            "wrong_count": len(wrong),
            "admin_count": models.count_admins(),
            "intro_count": len(models.list_cert_intros()),
        },
        "sets": sets,
        "recent_records": records[:10],
    })


@api_admin.route("/users", methods=["GET"])
@security.api_admin_required
def users():
    return ok({"users": models.list_users_with_stats(),
               "admins": models.list_admins()})


@api_admin.route("/admins", methods=["POST"])
@security.api_admin_required
def create_admin():
    created, message = auth.register_admin(_str("username"), str(_payload().get("password") or ""))
    if not created:
        return fail(message, 400, "VALIDATION")
    return ok(message=message)


@api_admin.route("/records", methods=["GET"])
@security.api_admin_required
def records():
    return ok({"records": score_manager.list_all_records()})


@api_admin.route("/categories", methods=["GET"])
@security.api_admin_required
def categories():
    return ok({"categories": navigation.category_tree_payload()})


# ---------------------------------------------------------------------------
# 题库
# ---------------------------------------------------------------------------

@api_admin.route("/sets", methods=["GET"])
@security.api_admin_required
def sets():
    return ok({"sets": admin_logic.list_sets()})


@api_admin.route("/sets/<set_date>", methods=["GET"])
@security.api_admin_required
def set_detail(set_date):
    view = admin_logic.build_set_editor(set_date)
    if not view:
        return fail("该日期题库不存在", 404, "NOT_FOUND")
    return ok(view)


@api_admin.route("/sets/<set_date>", methods=["POST", "PUT"])
@security.api_admin_required
def save_set(set_date):
    """保存题库编辑。表单字段名与旧编辑器一致：set_date / set_title /
    category_slug / q_<id>_type|question|option_<字母>|answer|explanation|delete。
    """
    try:
        saved = admin_logic.save_edit(set_date, _payload())
    except admin_logic.EditError as exc:
        return fail(str(exc), 400, "VALIDATION")
    except db.DatabaseError:
        return fail("保存失败，请稍后重试", 500, "DATABASE")
    return ok(saved, f"已保存「{saved['title']}」")


@api_admin.route("/sets/<set_date>/delete-preview", methods=["GET"])
@security.api_admin_required
def delete_preview(set_date):
    preview = admin_logic.build_delete_preview(set_date)
    if not preview:
        return fail("该日期题库不存在", 404, "NOT_FOUND")
    return ok(preview)


def _delete_set(set_date):
    payload = _payload()
    confirm = str(payload.get("confirm_text") or "").strip()
    if confirm != set_date:
        return fail(f"请准确输入日期 {set_date} 后再删除", 400, "CONFIRM_REQUIRED")
    try:
        deleted = admin_logic.delete_set(set_date)
    except admin_logic.EditError as exc:
        return fail(str(exc), 404, "NOT_FOUND")
    except db.DatabaseError:
        return fail("删除失败，请稍后重试", 500, "DATABASE")
    return ok(deleted, f"已删除 {set_date}「{deleted['title']}」")


@api_admin.route("/sets/<set_date>/delete", methods=["POST"])
@api_admin.route("/sets/<set_date>", methods=["DELETE"])
@security.api_admin_required
def delete_set(set_date):
    return _delete_set(set_date)


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

@api_admin.route("/import", methods=["POST"])
@security.api_admin_required
def import_markdown():
    """上传 Markdown 导入题库；支持 multipart 文件与 JSON 文本两种方式。

    multipart 字段：md_file / date / title / category_slug / confirm_overwrite /
    pending_content（覆盖确认时回传上一轮文本）。
    JSON 字段：markdown（或 pending_content）/ date / title / category_slug /
    confirm_overwrite / filename。
    """
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    uploaded_file = request.files.get("md_file")
    text, filename, detected_fmt, error = admin_service.read_uploaded_file(uploaded_file)
    if error:
        return fail(error, 400, "ENCODING")
    if not text:
        text = str(payload.get("markdown") or payload.get("pending_content") or "")
    filename = filename or str(payload.get("filename") or "")
    # 覆盖确认时浏览器无法回填文件，前端会显式带 fmt；缺省按文件名 / 正文自动识别
    fmt = str(payload.get("fmt") or "").strip() or detected_fmt
    fmt = fmt or admin_service.detect_format(filename, text)
    result = admin_service.import_markdown(
        text,
        import_date=str(payload.get("date") or "").strip(),
        title=str(payload.get("title") or "").strip(),
        category_slug=str(payload.get("category_slug") or "").strip(),
        filename=filename,
        overwrite=str(payload.get("confirm_overwrite") or "") == "1",
        fmt=fmt,
    )
    if not result["ok"]:
        # exists 是需要二次确认，不算失败；其余为 400 校验错误
        status = 409 if result["reason"] == "exists" else 400
        return fail(result["message"], status, result["reason"], **{
            k: v for k, v in result.items() if k not in ("ok", "message", "reason")
        })
    return ok(result, result["message"])


# ---------------------------------------------------------------------------
# 认证介绍管理（CMS）
# ---------------------------------------------------------------------------

@api_admin.route("/cert-intros", methods=["GET"])
@security.api_admin_required
def cert_intros():
    """列出 / 搜索认证百科介绍。

    查询参数 ``q`` 按认证名称、厂商、方向、等级或 slug 模糊匹配。
    """
    keyword = (request.args.get("q") or "").strip()
    items = models.list_cert_intros(keyword)
    return ok({
        "items": items,
        "keyword": keyword,
        "total": len(items) if not keyword else len(models.list_cert_intros()),
        "presets": navigation.intro_preset_slugs(),
    })


@api_admin.route("/cert-intros/<slug>", methods=["POST", "PUT"])
@security.api_admin_required
def save_cert_intro(slug):
    """保存管理员对某条认证百科介绍的修改（正文、显示名、资料来源）。"""
    payload = _payload()
    intro = str(payload.get("intro") or "").strip()
    if not intro:
        return fail("认证介绍正文不能为空", 400, "VALIDATION")
    admin = security.resolve_current_admin()
    cert_name = payload.get("cert_name")
    source = payload.get("source")
    saved = models.update_cert_intro(
        slug,
        intro,
        updated_by=(admin["username"] if admin else ""),
        cert_name=None if cert_name is None else str(cert_name).strip() or None,
        source=None if source is None else str(source).strip(),
    )
    if not saved:
        return fail("该认证介绍不存在，请刷新后重试", 404, "NOT_FOUND")
    row = models.get_cert_intro(slug) or {}
    return ok(row, f"已保存「{row.get('cert_name') or slug}」的认证介绍")


# ---------------------------------------------------------------------------
# 认证题库网 v2：分类 / 题库 / 五段式介绍 / 排行榜
# ---------------------------------------------------------------------------

@api_admin.route("/cert-categories", methods=["GET"])
@security.api_admin_required
def categories_v2():
    """认证分类列表：含题量、上架状态与 SEO 字段。"""
    keyword = (request.args.get("q") or "").strip()
    vendor = (request.args.get("vendor") or "").strip()
    items = bank.list_categories(keyword=keyword, vendor=vendor)
    return ok({"items": items, "total": len(items),
               "stats": bank.category_stats()})


@api_admin.route("/cert-categories/<int:category_id>", methods=["POST", "PUT"])
@security.api_admin_required
def save_category(category_id):
    payload = _payload()
    fields = {}
    for key in ("cert_name", "difficulty", "status", "sort_order",
                "seo_title", "seo_keywords", "seo_description", "exam_code"):
        if key in payload:
            value = payload.get(key)
            if key == "sort_order":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = 0
            fields[key] = "" if value is None else str(value).strip() if key != "sort_order" else value
    if not fields:
        return fail("没有可更新的字段", 400, "VALIDATION")
    saved = bank.update_category(category_id, fields)
    if not saved:
        return fail("认证分类不存在或未发生变化", 404, "NOT_FOUND")
    return ok({"category": bank.get_category(category_id)}, "已保存")


@api_admin.route("/questions", methods=["GET"])
@security.api_admin_required
def questions_list():
    try:
        category_id = int(request.args.get("category_id") or 0) or None
    except ValueError:
        category_id = None
    try:
        page = int(request.args.get("page") or 1)
    except ValueError:
        page = 1
    keyword = (request.args.get("q") or "").strip()
    data = bank.list_questions(category_id=category_id, keyword=keyword, page=page, page_size=12)
    return ok(data)


def _question_fields(payload) -> tuple[dict, str]:
    qtype = str(payload.get("type") or "single").strip()
    if qtype not in ("single", "multiple", "judge"):
        return {}, "题型必须是 single / multiple / judge"
    question = str(payload.get("question") or "").strip()
    if not question:
        return {}, "题干不能为空"
    options = payload.get("options")
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except ValueError:
            return {}, "选项必须是 JSON 对象"
    if not isinstance(options, dict) or not options:
        return {}, "至少需要一个选项"
    clean_options = {str(k).strip().upper(): str(v).strip() for k, v in options.items() if str(v).strip()}
    if len(clean_options) < 2 and qtype != "judge":
        return {}, "至少需要两个选项"
    answer = payload.get("answer")
    if isinstance(answer, (list, tuple)):
        answer = ",".join(str(a) for a in answer)
    answer = str(answer or "").replace("，", ",").upper().strip()
    if not answer:
        return {}, "答案不能为空"
    letters = [a.strip() for a in answer.split(",") if a.strip()]
    if any(a not in clean_options for a in letters):
        return {}, "答案必须出现在选项中"
    if qtype != "multiple" and len(letters) > 1:
        return {}, "单选题 / 判断题只能有一个答案"
    return {
        "type": qtype,
        "question": question,
        "options": clean_options,
        "answer": ",".join(sorted(set(letters))),
        "explanation": str(payload.get("explanation") or "").strip(),
        "difficulty": str(payload.get("difficulty") or "中").strip(),
        "status": str(payload.get("status") or "active").strip(),
    }, ""


@api_admin.route("/questions", methods=["POST"])
@security.api_admin_required
def question_create():
    payload = _payload()
    try:
        category_id = int(payload.get("category_id") or 0)
    except (TypeError, ValueError):
        category_id = 0
    if not category_id or not bank.get_category(category_id):
        return fail("请选择有效的认证分类", 400, "VALIDATION")
    fields, error = _question_fields(payload)
    if error:
        return fail(error, 400, "VALIDATION")
    question_id = bank.create_question(fields, category_id)
    return ok({"id": question_id}, "题目已新增")


@api_admin.route("/questions/<int:question_id>", methods=["POST", "PUT"])
@security.api_admin_required
def question_update(question_id):
    payload = _payload()
    existing = bank.get_question(question_id)
    if not existing:
        return fail("题目不存在", 404, "NOT_FOUND")
    merged = {
        "type": payload.get("type", existing["type"]),
        "question": payload.get("question", existing["question"]),
        "options": payload.get("options", existing["options"]),
        "answer": payload.get("answer", existing["answer"]),
        "explanation": payload.get("explanation", existing["explanation"]),
        "difficulty": payload.get("difficulty", existing["difficulty"]),
        "status": payload.get("status", existing["status"]),
    }
    fields, error = _question_fields(merged)
    if error:
        return fail(error, 400, "VALIDATION")
    bank.update_question(question_id, fields)
    return ok({"question": bank.get_question(question_id)}, "已保存")


@api_admin.route("/questions/<int:question_id>/delete", methods=["POST", "DELETE"])
@security.api_admin_required
def question_delete(question_id):
    if not bank.delete_question(question_id):
        return fail("题目不存在", 404, "NOT_FOUND")
    return ok(message="题目已删除")


@api_admin.route("/bank/import", methods=["POST"])
@security.api_admin_required
def bank_import():
    """认证题库导入：必须选择三级认证分类（category_id）。"""
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    try:
        category_id = int(payload.get("category_id") or 0)
    except (TypeError, ValueError):
        category_id = 0
    category = bank.get_category(category_id) if category_id else None
    if not category:
        return fail("请选择要导入的认证分类", 400, "VALIDATION")

    uploaded = request.files.get("file")
    text, filename, fmt, error = admin_service.read_uploaded_file(uploaded)
    if error:
        return fail(error, 400, "ENCODING")
    if not text:
        text = str(payload.get("markdown") or "")
    if not text.strip():
        return fail("请上传文件或粘贴题目内容", 400, "EMPTY")
    fmt = str(payload.get("fmt") or "").strip() or fmt or admin_service.detect_format(filename, text)
    overwrite = str(payload.get("overwrite") or "") in ("1", "true", "on")
    try:
        result = bank.import_questions(text, category_id, fmt=fmt, overwrite=overwrite)
    except ValueError as exc:
        return fail(str(exc), 400, "VALIDATION")

    # 同步生成「模拟题库」：每日答题从模拟题库抽题，用户也可针对性练习。
    # 旧题库保留写入，保证历史上只依赖 questions 的分类与记录不受影响。
    practice = {"created": False, "inserted": 0, "failed": 0, "name": "", "reason": ""}
    try:
        if overwrite:
            practice_bank.delete_auto_import_banks(category_id)
        mirrored = practice_bank.import_paper_as_bank(
            category, text, fmt=fmt, filename=filename,
        )
        practice = {
            "created": bool(mirrored.get("created")),
            "inserted": int(mirrored.get("inserted") or 0),
            "failed": len(mirrored.get("failed") or []),
            "name": (mirrored.get("bank") or {}).get("name", ""),
            "reason": mirrored.get("reason", ""),
            "publish_errors": mirrored.get("publish_errors") or [],
        }
    except Exception as exc:  # 模拟题库失败不回滚旧题库导入，但要把原因回传
        practice["reason"] = str(exc)
    result["practice_bank"] = practice

    if practice["created"]:
        message = (
            f"已向「{category['cert_name']}」导入 {result['imported']} 道题，"
            f"并生成模拟题库「{practice['name']}」（{practice['inserted']} 题）"
        )
    else:
        message = f"已向「{category['cert_name']}」导入 {result['imported']} 道题"
        if practice["reason"]:
            message += f"；模拟题库生成失败：{practice['reason']}"
    return ok(result, message)


@api_admin.route("/intros", methods=["GET"])
@security.api_admin_required
def intros_v2():
    keyword = (request.args.get("q") or "").strip()
    items = bank.list_intros(keyword)
    return ok({"items": items, "total": len(items)})


@api_admin.route("/intros/<int:category_id>", methods=["POST", "PUT"])
@security.api_admin_required
def save_intro_v2(category_id):
    payload = _payload()
    # 五段式介绍是认证页首屏主体，核心四块留空会直接把页面写坏
    missing = [
        label for key, label in (
            ("positioning", "认证定位"), ("audience", "适合人群"),
            ("exam_topics", "考试方向"), ("advice", "备考建议"),
        ) if not str(payload.get(key) or "").strip()
    ]
    if missing:
        return fail("以下字段不能为空：" + "、".join(missing), 400, "VALIDATION")
    admin = security.resolve_current_admin()
    saved = bank.save_intro(category_id, payload, updated_by=admin["username"] if admin else "")
    if not saved:
        return fail("认证分类不存在", 404, "NOT_FOUND")
    return ok({"intro": bank.get_intro(category_id)}, "认证介绍已保存")


@api_admin.route("/rankings", methods=["GET"])
@security.api_admin_required
def rankings_v2():
    period = (request.args.get("period") or "day").strip()
    if period not in ("day", "week", "month"):
        period = "day"
    try:
        limit = max(1, min(100, int(request.args.get("limit") or 30)))
    except ValueError:
        limit = 30
    return ok({
        "period": period,
        "period_key": bank.period_key(period),
        "rows": bank.top_ranking(period, limit),
    })


# ---------------------------------------------------------------------------
# 自由练习：题库管理 / 题目维护 / 考试规则
# ---------------------------------------------------------------------------

def _bank_id_or_404(bank_id: int) -> dict:
    row = practice_bank.get_bank(bank_id, by_id=True)
    if not row:
        raise practice_bank.PracticeError("题库不存在", 404, "NOT_FOUND")
    return row


@api_admin.route("/practice-banks", methods=["GET"])
@security.api_admin_required
def practice_banks_list():
    """题库列表：带实际题量、练习人次、平均分与发布校验结果。"""
    status = (request.args.get("status") or "").strip()
    keyword = (request.args.get("q") or "").strip()
    conn = models.get_conn()
    try:
        items = practice_bank.admin_list(conn, status=status, keyword=keyword)
    finally:
        conn.close()
    attempts = sum(int(item.get("attempts") or 0) for item in items)
    weighted = sum(float(item.get("avg_score") or 0) * int(item.get("attempts") or 0) for item in items)
    return ok({
        "banks": items,
        "summary": {
            "total": len(items),
            "published": sum(1 for item in items if item["status"] == "published"),
            "draft": sum(1 for item in items if item["status"] == "draft"),
            "disabled": sum(1 for item in items if item["status"] == "disabled"),
            "attempts": attempts,
            "avg_score": round(weighted / attempts, 1) if attempts else 0,
        },
        "rules": practice_bank.list_rules(),
    })


@api_admin.route("/practice-banks/<int:bank_id>", methods=["GET"])
@security.api_admin_required
def practice_bank_detail(bank_id):
    try:
        bank = _bank_id_or_404(bank_id)
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code)
    return ok({
        "bank": bank,
        "questions": practice_bank.list_questions(bank_id),
        "stats": practice_bank.bank_stats(bank_id),
        "publish_errors": practice_bank.publish_check(bank) if bank["status"] != "published" else [],
    })


@api_admin.route("/practice-banks", methods=["POST"])
@security.api_admin_required
def practice_bank_create():
    payload = _payload()
    admin = security.resolve_current_admin()
    try:
        bank = practice_bank.create_bank(payload, created_by=admin["username"] if admin else "")
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code, **exc.extra)
    requested = _int_or_zero(payload.get("total_questions"))
    corrected = bool(requested and requested != bank["total_questions"])
    message = "题库已创建（草稿）"
    if corrected:
        message += f"，题量已按「{bank['rule_name']}」校正为 {bank['total_questions']} 题"
    return ok({"bank": bank, "corrected": corrected}, message)


@api_admin.route("/practice-banks/<int:bank_id>", methods=["POST", "PUT"])
@security.api_admin_required
def practice_bank_update(bank_id):
    payload = _payload()
    try:
        bank = practice_bank.update_bank(bank_id, payload)
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code, **exc.extra)
    return ok({"bank": bank, "publish_errors": practice_bank.publish_check(bank)},
              "题库已保存")


@api_admin.route("/practice-banks/<int:bank_id>/status", methods=["POST"])
@security.api_admin_required
def practice_bank_set_status(bank_id):
    status = str(_payload().get("status") or "").strip()
    try:
        bank = practice_bank.set_bank_status(bank_id, status)
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code, **exc.extra)
    labels = {"published": "题库已发布，前端目录立即可见",
              "disabled": "题库已停用，前端不再展示；历史成绩保留",
              "draft": "题库已转为草稿"}
    return ok({"bank": bank}, labels.get(status, "状态已更新"))


@api_admin.route("/practice-banks/<int:bank_id>/delete", methods=["POST", "DELETE"])
@security.api_admin_required
def practice_bank_delete(bank_id):
    try:
        practice_bank.delete_bank(bank_id)
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code)
    return ok(message="题库已删除")


@api_admin.route("/practice-banks/<int:bank_id>/questions", methods=["POST"])
@security.api_admin_required
def practice_question_create(bank_id):
    payload = _payload()
    try:
        created = practice_bank.add_question(bank_id, payload)
        bank = _bank_id_or_404(bank_id)
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code)
    return ok({"question_id": created["id"], "bank": bank}, "题目已新增")


@api_admin.route("/practice-questions/<int:question_id>", methods=["POST", "PUT"])
@security.api_admin_required
def practice_question_update(question_id):
    try:
        practice_bank.update_question(question_id, _payload())
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code)
    return ok(message="题目已保存")


@api_admin.route("/practice-questions/<int:question_id>/delete", methods=["POST", "DELETE"])
@security.api_admin_required
def practice_question_delete(question_id):
    try:
        practice_bank.delete_question(question_id)
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code)
    return ok(message="题目已删除")


@api_admin.route("/practice-banks/<int:bank_id>/import", methods=["POST"])
@security.api_admin_required
def practice_question_import(bank_id):
    """批量导入：支持粘贴 Markdown / JSON，或上传同名格式文件。"""
    text = ""
    filename = ""
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("markdown") or "")
    else:
        text = str(request.form.get("markdown") or "")
        upload = request.files.get("file")
        if upload and upload.filename:
            filename = upload.filename
            text = upload.read().decode("utf-8", "replace")
    fmt = "json" if (filename.lower().endswith(".json") or text.lstrip().startswith(("{", "["))) else "markdown"
    try:
        result = practice_bank.import_questions(bank_id, text, fmt)
        bank = _bank_id_or_404(bank_id)
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code)
    message = f"导入完成：新增 {result['inserted']} 题"
    if result["failed"]:
        message += f"，失败 {len(result['failed'])} 题"
    return ok({"result": result, "bank": bank}, message)


@api_admin.route("/exam-rules", methods=["GET"])
@security.api_admin_required
def exam_rules_list():
    rules = practice_bank.list_rules()
    conn = models.get_conn()
    try:
        for rule in rules:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM question_banks WHERE rule_code = ?", (rule["code"],)
            ).fetchone()
            rule["bank_count"] = int(row["n"] or 0) if row else 0
    finally:
        conn.close()
    return ok({"rules": rules})


@api_admin.route("/exam-rules/<code>", methods=["POST", "PUT"])
@security.api_admin_required
def exam_rule_save(code):
    try:
        rule = practice_bank.save_rule(code, _payload())
    except practice_bank.PracticeError as exc:
        return fail(exc.message, exc.status, exc.code)
    return ok({"rule": rule}, "考试规则已保存；已关联的题库需重新保存以同步新的题量/分值")


def _int_or_zero(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
