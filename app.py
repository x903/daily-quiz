"""每日答题系统 —— Flask 应用入口（含用户与权限）。

角色与权限：
- 未登录：仅可访问 /login 与 /register。
- 普通用户（user）：答题、查看个人成绩与错题、按认证栏目浏览题库、查看排行榜、导出个人数据。
- 管理员（admin）：在普通用户能力之上，可进入后台：导入题库（仅支持上传
  Markdown 文件）、编辑题库（日期/标题/逐题内容）、删除题库、
  查看所有用户的答题记录与用户统计。

路由分组：
- 公开：/login /register
- 需登录：/ /quiz /result /date /score /wrong /leaderboard /leaderboard/api
           /certification（阿里云认证栏目，配置驱动）/export/...
- 仅管理员：/import /admin /admin/users /admin/records
             /admin/set/<date>/edit（编辑题库）、/admin/set/<date>/delete（删除确认与执行）
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from functools import wraps
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

import admin_logic
import auth
import db
import leaderboard
import models
import navigation
import parser as md_parser
import points
import quiz_logic
import score_manager
import wrong_book

app = Flask(__name__)
# 生产部署（Docker）务必通过 SECRET_KEY 注入随机值；本地开发用固定默认值
app.secret_key = os.environ.get("SECRET_KEY") or "daily-quiz-secret-key"
# 解析文本渲染过滤器：所有显示答案的页面统一通过它输出（内部已做 HTML 转义）
app.jinja_env.filters["exp_html"] = quiz_logic.explanation_to_html

models.init_db()
auth.create_default_users()

# 日期探测：用于在管理员上传 MD 文件时，从文件名、标题或文件头自动推断导入日期
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _guess_date(md_text: str, title: str, filename: str) -> str:
    """从文件名、标题、正文头部探测 YYYY-MM-DD 日期。

    优先级：标题/文件名（含日期）→ 正文头部（如「生成日期：2026-11-04」）。
    返回匹配到的第一个日期字符串，未找到返回空串。
    """
    candidates = [filename or "", title or "", (md_text or "")[:2000]]
    for c in candidates:
        m = _DATE_RE.search(c)
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# 权限装饰器 & 上下文
# ---------------------------------------------------------------------------

def _resolve_current_user() -> Optional[dict]:
    """解析并校验当前登录用户（结果按请求缓存到 flask.g）。

    session cookie 里只保存 user_id，数据库可能被重建 / 账号可能被删除，
    因此每次请求都必须回查 users 表。若该 id 已不存在，立即清空登录态，
    避免用孤儿 user_id 去写带外键的成绩 / 错题表而触发 500。
    角色一律以数据库为准，不信任 cookie 中的 role。

    Returns:
        用户字典；未登录或登录态已失效时返回 None。
    """
    if hasattr(g, "auth_user"):
        return g.auth_user

    user: Optional[dict] = None
    raw_id = session.get("user_id")
    if raw_id:
        try:
            row = models.get_user(int(raw_id))
        except (TypeError, ValueError):
            row = None
        if row:
            user = dict(row)
            # 同步 role / username，保证模板与权限判断使用最新的数据库值
            session["username"] = user["username"]
            session["role"] = user["role"]
        else:
            session.clear()

    g.auth_user = user
    return user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _resolve_current_user():
            flash("登录状态已失效，请重新登录", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = _resolve_current_user()
        if not user:
            flash("登录状态已失效，请重新登录", "error")
            return redirect(url_for("login"))
        if user["role"] != "admin":
            flash("需要管理员权限", "error")
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_user():
    """向所有模板注入当前登录用户（含角色），未登录为 None。"""
    user = _resolve_current_user()
    if user:
        return {
            "current_user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
            }
        }
    return {"current_user": None}


@app.context_processor
def inject_navigation():
    """向所有模板注入「阿里云认证」菜单树、当前栏目与 URL 生成函数。

    每次请求都读取 `navigation.CERT_NAV`，因此运行时注册新的 ACP 方向会
    立即反映到菜单与页面上，无需重启服务或修改模板。
    """
    slug = (request.view_args or {}).get("slug")
    in_section = request.endpoint in ("cert_index", "cert_detail")
    if request.endpoint == "cert_index":
        slug = navigation.CERT_NAV.slug

    def cert_url(target_slug: str) -> str:
        if target_slug == navigation.CERT_NAV.slug:
            return url_for("cert_index")
        return url_for("cert_detail", slug=target_slug)

    return {
        "cert_nav": navigation.CERT_NAV,
        "cert_active_slug": slug if in_section else None,
        "cert_url": cert_url,
        # 导入页 / 编辑页的三级栏目选择器共用同一份配置树；每次请求重新读取，
        # 运行时新增 ACP 方向会立即出现在选择器中。
        "category_tree": navigation.category_tree_payload(),
        "category_label": navigation.category_label,
    }


def _current_user_id() -> int:
    user = _resolve_current_user()
    return int(user["id"]) if user else 0


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
    if _resolve_current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = auth.authenticate(username, password)
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash(f"欢迎回来，{user['username']}", "success")
            return redirect(url_for("index"))
        flash("用户名或密码错误", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if _resolve_current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if password != confirm:
            flash("两次输入的密码不一致", "error")
        else:
            ok, msg = auth.register(username, password, role="user")
            if ok:
                user = auth.authenticate(username, password)
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["role"] = user["role"]
                flash("注册成功，已自动登录", "success")
                return redirect(url_for("index"))
            flash(msg, "error")
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# 首页：今日答题首页（需登录）
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    today = date.today().strftime("%Y-%m-%d")
    today_set = models.get_daily_set_by_date(today)
    today_record = None
    if today_set:
        today_record = score_manager.get_record_by_set_user(today_set["id"], _current_user_id())
    recent = models.list_daily_sets(user_id=_current_user_id())[:7]
    wrong_count = len(wrong_book.get_all_wrong(user_id=_current_user_id()))
    # 首页卡片展示「我的今日积分 / 日榜排名」，点击可进入完整榜单
    day_board = leaderboard.get_leaderboard("day", date.today())
    my_day = leaderboard.find_user(day_board["items"], _current_user_id())
    return render_template(
        "index.html",
        today=today,
        today_set=today_set,
        today_record=today_record,
        recent=recent,
        wrong_count=wrong_count,
        my_day=my_day,
    )


# ---------------------------------------------------------------------------
# 导入（仅管理员）
# ---------------------------------------------------------------------------

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
    import_date = request.form.get("date", "").strip()
    title = request.form.get("title", "").strip()
    category_slug = request.form.get("category_slug", "").strip()
    confirm_overwrite = request.form.get("confirm_overwrite") == "1"

    uploaded_file = request.files.get("md_file")
    uploaded_filename = ""
    md_text = ""
    if uploaded_file and uploaded_file.filename:
        uploaded_filename = uploaded_file.filename
        try:
            md_text = uploaded_file.read().decode("utf-8")
        except UnicodeDecodeError:
            flash("文件不是有效的 UTF-8 文本，请另存为 UTF-8 编码的 Markdown 文件", "error")
            return redirect(url_for("import_page"))
        if not title:
            title = uploaded_filename.rsplit(".", 1)[0]
    elif confirm_overwrite:
        md_text = request.form.get("pending_content", "")

    # 日期未手动填写时，依次从文件名、标题、正文头部自动探测
    if not import_date:
        import_date = _guess_date(md_text, title, uploaded_filename)

    if not md_text.strip():
        flash("请先选择要导入的 Markdown 文件", "error")
        return redirect(url_for("import_page"))
    if not import_date:
        flash("请选择导入日期（或从文件名/标题中识别到形如 2026-11-04 的日期）", "error")
        return redirect(url_for("import_page"))
    if not navigation.is_valid_category(category_slug):
        flash("请选择完整的导入栏目（例如 阿里云认证 › 阿里云ACP › 云计算高级工程师）", "error")
        return redirect(url_for("import_page"))
    category_path = navigation.category_path_label(category_slug)

    result = md_parser.parse_markdown(md_text)
    if result.total_questions == 0 and not result.failed:
        flash("未能解析出任何题目，请检查格式", "error")
        return redirect(url_for("import_page"))

    if not title:
        title = f"{import_date} 每日答题"

    imported = quiz_logic.import_day(
        import_date, title, result,
        overwrite=confirm_overwrite,
        category_slug=category_slug,
        category_path=category_path,
    )
    if not imported["ok"] and imported.get("reason") == "exists":
        return render_template(
            "import.html",
            today=import_date,
            title=title,
            need_confirm=True,
            pending_content=md_text,
            selected_category=category_slug,
        )

    flash(
        f"导入成功：{imported['total_sets']} 套 / {imported['total_questions']} 题"
        + (f"，{len(imported['failed'])} 题导入失败" if imported["failed"] else ""),
        "success",
    )
    return render_template(
        "import_result.html",
        date=import_date,
        result=imported,
        category_slug=category_slug,
        category_path=category_path,
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
            return redirect(url_for("login"))
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
# 题目查看 & 成绩（需登录；历史日期栏目已下线，题目从首页最近列表与栏目页进入）
# ---------------------------------------------------------------------------

@app.route("/date/<date>")
@login_required
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
# 排行榜（需登录，全员可见）
# ---------------------------------------------------------------------------

@app.route("/leaderboard")
@login_required
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
@login_required
def leaderboard_api():
    """榜单 JSON 接口，便于前端异步刷新或外部集成（数据与页面同源）。"""
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
# 阿里云认证栏目（配置驱动：菜单树 / 面包屑 / 页面骨架共用 navigation.py）
# ---------------------------------------------------------------------------

def _render_cert_page(slug: str):
    """渲染栏目页：菜单配置 + 该栏目（含子栏目）下已导入的题库。

    父栏目会聚合所有子孙栏目的题目，例如「阿里云ACP」页能看到「云计算
    高级工程师」下的题库，「阿里云认证」总览能看到全部认证题库。未分类的
    历史题库不出现在栏目页，只在首页「最近题目」中展示。
    """
    page = navigation.find_page(slug)
    if page is None:
        abort(404)
    quiz_sets = models.list_sets_by_category(
        navigation.descendant_slugs(slug), user_id=_current_user_id()
    )
    return render_template("certification.html", page=page, quiz_sets=quiz_sets)


@app.route("/certification")
@login_required
def cert_index():
    """一级菜单「阿里云认证」的总览介绍页。"""
    return _render_cert_page(navigation.CERT_NAV.slug)


@app.route("/certification/<slug>")
@login_required
def cert_detail(slug):
    """ACA / ACP / ACE 及 ACP 各方向共用的介绍页，新增方向无需新增路由。"""
    return _render_cert_page(slug)


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
# 管理员后台
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    users = models.list_users_with_stats()
    # 后台列表需要题目 / 成绩 / 错题数量，用一次性聚合查询，避免 N+1 查询
    sets = admin_logic.list_sets()
    total_records = len(score_manager.list_all_records())
    total_wrong = len(wrong_book.get_all_wrong(user_id=None))
    return render_template(
        "admin.html",
        users=users,
        sets=sets,
        total_records=total_records,
        total_wrong=total_wrong,
    )


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
    # Docker 部署用 gunicorn（见 Dockerfile / docker-compose.yml），这里仅供本地开发
    app.run(
        host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_RUN_PORT", 5000)),
        debug=True,
    )
