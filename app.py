"""每日答题系统 —— Flask 应用入口（含用户与权限）。

角色与权限：
- 未登录：仅可访问 /login 与 /register。
- 普通用户（user）：答题、查看个人成绩与错题、历史回顾、导出个人数据。
- 管理员（admin）：在普通用户能力之上，可进入后台：导入题库（支持粘贴文本、
  上传 MD 文件、选择 data/ 下已有 MD 文件）、编辑题库（日期/标题/逐题内容）、
  删除题库、查看所有用户的答题记录与用户统计。

路由分组：
- 公开：/login /register
- 需登录：/ /quiz /result /history /date /score /wrong /export/...
- 仅管理员：/import /admin /admin/users /admin/records
             /admin/set/<date>/edit（编辑题库）、/admin/set/<date>/delete（删除确认与执行）
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, datetime
from functools import wraps
from typing import Optional

from flask import (
    Flask,
    Response,
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
import models
import parser as md_parser
import quiz_logic
import score_manager
import wrong_book

app = Flask(__name__)
app.secret_key = "daily-quiz-secret-key"  # 用于 session 与 flash 消息
# 解析文本渲染过滤器：所有显示答案的页面统一通过它输出（内部已做 HTML 转义）
app.jinja_env.filters["exp_html"] = quiz_logic.explanation_to_html

models.init_db()
auth.create_default_users()

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 日期探测：用于在管理员上传 / 读取 MD 文件时，从文件名、标题或文件头自动推断导入日期
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


def _list_md_files() -> list[str]:
    """列出 data/ 目录下的 Markdown 文件（供管理员选择导入）。"""
    files: list[str] = []
    if os.path.isdir(DATA_DIR):
        for f in sorted(os.listdir(DATA_DIR)):
            if f.lower().endswith((".md", ".markdown")):
                files.append(f)
    return files


def _is_safe_md_name(filename: str) -> bool:
    """校验文件名：仅允许纯文件名的 .md/.markdown，防止路径穿越（保留中文名）。"""
    if not filename:
        return False
    if os.path.basename(filename) != filename:
        return False  # 含路径分隔符
    if ".." in filename:
        return False
    return filename.lower().endswith((".md", ".markdown"))


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


def _current_user_id() -> int:
    user = _resolve_current_user()
    return int(user["id"]) if user else 0


def _is_admin() -> bool:
    user = _resolve_current_user()
    return bool(user and user["role"] == "admin")


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
    return render_template(
        "index.html",
        today=today,
        today_set=today_set,
        today_record=today_record,
        recent=recent,
        wrong_count=wrong_count,
    )


# ---------------------------------------------------------------------------
# 导入（仅管理员）
# ---------------------------------------------------------------------------

@app.route("/import", methods=["GET"])
@admin_required
def import_page():
    today = date.today().strftime("%Y-%m-%d")
    return render_template("import.html", today=today, md_files=_list_md_files())


@app.route("/import/api/load", methods=["POST"])
@admin_required
def import_api_load():
    """AJAX 接口：读取 data/ 目录下指定 MD 文件的内容，返回 JSON。"""
    data = request.get_json(silent=True) or {}
    filename = str(data.get("filename", ""))
    if not _is_safe_md_name(filename):
        return jsonify(error="文件名非法或仅支持 Markdown 文件"), 400
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.isfile(filepath):
        return jsonify(error="文件不存在"), 404
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return jsonify(content=content, filename=filename)


@app.route("/import", methods=["POST"])
@admin_required
def import_do():
    md_text = request.form.get("markdown", "")
    import_date = request.form.get("date", "").strip()
    title = request.form.get("title", "").strip()
    confirm_overwrite = request.form.get("confirm_overwrite") == "1"

    # 优先使用上传的 MD 文件（若提供）
    uploaded_file = request.files.get("md_file")
    uploaded_filename = ""
    if uploaded_file and uploaded_file.filename:
        md_text = uploaded_file.read().decode("utf-8")
        uploaded_filename = uploaded_file.filename
        if not title:
            title = uploaded_filename.rsplit(".", 1)[0]

    # 日期未手动填写时，依次从文件名、标题、正文头部自动探测
    if not import_date:
        import_date = _guess_date(md_text, title, uploaded_filename)

    if not import_date:
        flash("请选择导入日期（或从文件名/标题中识别到形如 2026-11-04 的日期）", "error")
        return redirect(url_for("import_page"))
    if not md_text.strip():
        flash("请粘贴 Markdown 题目内容或上传 MD 文件", "error")
        return redirect(url_for("import_page"))

    result = md_parser.parse_markdown(md_text)
    if result.total_questions == 0 and not result.failed:
        flash("未能解析出任何题目，请检查格式", "error")
        return redirect(url_for("import_page"))

    if not title:
        title = f"{import_date} 每日答题"

    imported = quiz_logic.import_day(import_date, title, result, overwrite=confirm_overwrite)
    if not imported["ok"] and imported.get("reason") == "exists":
        return render_template(
            "import.html",
            today=import_date,
            preview=imported,
            md_text=md_text,
            title=title,
            need_confirm=True,
            md_files=_list_md_files(),
        )

    flash(
        f"导入成功：{imported['total_sets']} 套 / {imported['total_questions']} 题"
        + (f"，{len(imported['failed'])} 题导入失败" if imported["failed"] else ""),
        "success",
    )
    return render_template("import_result.html", date=import_date, result=imported)


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
    except sqlite3.Error:
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
        return redirect(url_for("history"))
    # 权限校验：仅本人或管理员可查看
    if rec["user_id"] != _current_user_id() and not _is_admin():
        flash("无权查看该成绩", "error")
        return redirect(url_for("history"))
    s = models.get_daily_set_by_id(rec["daily_set_id"])
    if not s:
        flash("未找到对应的题目集", "error")
        return redirect(url_for("history"))
    wrongs = wrong_book.get_wrong_by_date(s["date"], rec["user_id"])
    for w in wrongs:
        w["user_text"] = quiz_logic.answer_to_text(w["options"], w["user_answer"])
        w["correct_text"] = quiz_logic.answer_to_text(w["options"], w["correct_answer"])
    return render_template("result.html", rec=rec, set_info=s, wrongs=wrongs)


# ---------------------------------------------------------------------------
# 历史 & 题目查看 & 成绩（需登录）
# ---------------------------------------------------------------------------

@app.route("/history")
@login_required
def history():
    sets = models.list_daily_sets(user_id=_current_user_id())
    return render_template("history.html", sets=sets)


@app.route("/date/<date>")
@login_required
def date_detail(date):
    s = models.get_daily_set_by_date(date)
    if not s:
        flash("该日期尚未导入题目", "error")
        return redirect(url_for("history"))
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
        return redirect(url_for("history"))
    if not view["record"]:
        flash("你还没有完成该日期的答题", "error")
        return redirect(url_for("history"))
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
    except sqlite3.Error:
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
        return redirect(url_for("history"))
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
        "sets": [{"set_index": k, "questions": v} for k, v in sorted(sets.items())],
    }
    return _json_download(payload, f"questions_{date}.json")


@app.route("/export/score/<date>")
@login_required
def export_score(date):
    s = models.get_daily_set_by_date(date)
    if not s:
        flash("该日期尚未导入题目", "error")
        return redirect(url_for("history"))
    rec = score_manager.get_record_by_set_user(s["id"], _current_user_id())
    if not rec:
        flash("你还没有完成该日期的答题，暂无成绩可导出", "error")
        return redirect(url_for("history"))
    payload = {
        "date": date,
        "title": s["title"],
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
        except sqlite3.Error:
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
        except sqlite3.Error:
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
    app.run(host="127.0.0.1", port=5000, debug=True)
