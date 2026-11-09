"""每日答题逻辑模块。

串联题目解析、出题、判分、成绩与错题：
- import_day：解析 Markdown 并落库（支持覆盖）。
- get_quiz_data：按日期取出当日题目（不含答案）供前端渲染。
- grade_and_save：判分、写成绩、刷新错题本（均绑定到具体用户）。
- practice_wrong：错题重练，答对即从该用户错题本移除。
"""
from __future__ import annotations

import html as _html
import json
import re as _re
from datetime import datetime
from typing import Optional

import models
import points
import score_manager
import wrong_book
from parser import ParseResult


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

def import_day(
    date: str,
    title: str,
    parse_result: ParseResult,
    overwrite: bool = False,
    category_slug: str = "",
    category_path: str = "",
) -> dict:
    """将解析结果导入为某日期的题库，并标记所属栏目。

    Args:
        date: 日期（YYYY-MM-DD）。
        title: 标题。
        parse_result: 解析结果。
        overwrite: 若该日期已存在，是否覆盖。
        category_slug: 栏目节点 slug；由路由层校验后传入，空串表示未分类
            （脚本直接调用时保持向后兼容）。
        category_path: 栏目路径文案快照。

    Returns:
        结果字典，ok=False 且 reason="exists" 表示需用户确认覆盖。
    """
    existing = models.get_daily_set_by_date(date)
    if existing and not overwrite:
        return {"ok": False, "reason": "exists", "set_id": existing["id"]}
    if existing and overwrite:
        models.delete_set_cascade(date)

    set_id = models.create_daily_set(
        date, title, parse_result.total_sets, "pending",
        category_slug, category_path,
    )
    for set_index in sorted(parse_result.sets.keys()):
        for q in parse_result.sets[set_index]:
            models.insert_question(
                set_id,
                q.set_index,
                q.q_type,
                q.question,
                json.dumps(q.options, ensure_ascii=False),
                json.dumps(q.answer, ensure_ascii=False),
                getattr(q, "explanation", "") or "",
            )

    return {
        "ok": True,
        "set_id": set_id,
        "total_sets": parse_result.total_sets,
        "total_questions": parse_result.total_questions,
        "distribution": parse_result.type_distribution(),
        "failed": parse_result.failed,
    }


# ---------------------------------------------------------------------------
# 出题
# ---------------------------------------------------------------------------

def get_quiz_data(date: str) -> Optional[dict]:
    """按日期取出当日答题数据（不含答案）。

    Returns:
        含 set_id / title / sets 的字典；日期未导入则返回 None。
    """
    s = models.get_daily_set_by_date(date)
    if not s:
        return None

    rows = models.get_quiz_questions_by_set(s["id"])
    sets: dict[int, list[dict]] = {}
    for r in rows:
        sets.setdefault(r["set_index"], []).append(
            {
                "id": r["id"],
                "type": r["type"],
                "question": r["question"],
                "options": json.loads(r["options"]),
            }
        )
    return {
        "set_id": s["id"],
        "date": s["date"],
        "title": s["title"],
        "sets": [{"set_index": k, "questions": v} for k, v in sorted(sets.items())],
        "total": len(rows),
    }


# ---------------------------------------------------------------------------
# 判分与保存
# ---------------------------------------------------------------------------

def judge(q_type: str, correct: list[str], user: list[str]) -> bool:
    """判断用户作答是否正确（与顺序无关、大小写无关）。"""
    return sorted(a.upper() for a in user) == sorted(c.upper() for c in correct)


def _load_submitted(form, total_questions: int) -> dict[int, list[str]]:
    """从表单中提取作答：{question_id: [字母, ...]}。

    同时兼容 Flask 的 ImmutableMultiDict（HTTP 请求）与普通 dict（脚本 / 测试直接调用）。
    """
    submitted: dict[int, list[str]] = {}
    for key in form:
        if not key.startswith("q_"):
            continue
        try:
            qid = int(key[2:])
        except ValueError:
            continue
        if hasattr(form, "getlist"):
            raw = form.getlist(key)
        else:
            value = form[key]
            raw = value if isinstance(value, (list, tuple)) else [value]
        submitted[qid] = [str(v).upper() for v in raw if str(v).strip()]
    return submitted


def grade_and_save(date: str, user_id: int, form, duration: int) -> dict:
    """判分、写成绩、刷新错题本，全部绑定到 user_id 并在**单个事务**内完成。

    Args:
        date: 日期。
        user_id: 当前答题用户 id。必须是 users 表中真实存在的 id。
        form: Flask request.form（MultiDict）。
        duration: 用时（秒）。

    Returns:
        含 record_id 的判分结果字典。

    Raises:
        ValueError: 登录用户已失效，或该日期尚未导入题目。
        db.DatabaseError: 写库失败（事务已整体回滚，不会留下半写数据）。
    """
    duration = max(0, int(duration))
    conn = models.get_conn()
    try:
        # 外键保护：题库重建后残留的旧 session 里的 user_id 可能已不存在，
        # 若直接写入 wrong_questions / daily_records 会触发
        # db.IntegrityError: FOREIGN KEY constraint failed。这里提前拦截，
        # 由路由层清理登录态并给出友好提示。
        if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise ValueError("登录状态已失效，请重新登录")

        s = conn.execute("SELECT * FROM daily_sets WHERE date = ?", (date,)).fetchone()
        if not s:
            raise ValueError("该日期尚未导入题目")
        set_id = s["id"]

        questions = conn.execute(
            "SELECT * FROM questions WHERE daily_set_id = ? ORDER BY set_index, id",
            (set_id,),
        ).fetchall()
        submitted = _load_submitted(form, len(questions))

        total = len(questions)
        correct = 0

        # with conn: 会在正常结束时 commit、异常时 rollback，
        # 避免「旧错题已删、新错题/成绩未写」的不一致状态。
        with conn:
            # 先清空这批题目在该用户错题本中的旧记录，反映“最新一次”作答
            for q in questions:
                wrong_book.remove_wrong(q["id"], user_id, conn=conn)

            for q in questions:
                qid = q["id"]
                correct_ans = json.loads(q["answer"])
                user_ans = submitted.get(qid, [])
                if judge(q["type"], correct_ans, user_ans):
                    correct += 1
                else:
                    wrong_book.add_wrong(
                        set_id, qid, user_id, user_ans, correct_ans, conn=conn
                    )

            wrong = total - correct
            accuracy = round(correct / total * 100, 2) if total else 0.0
            finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            record_id = score_manager.save_record(
                set_id, user_id, total, correct, wrong, accuracy, duration, finished_at,
                conn=conn,
            )
            models.set_set_status(set_id, "completed", conn=conn)
            # 积分与成绩、错题共用同一事务：判分成功则一定有积分，
            # 任何一步失败则三者一起回滚，不会出现「有成绩没积分」
            earned_points = points.award_for_quiz(
                conn,
                user_id=user_id,
                set_id=set_id,
                record_id=record_id,
                set_date=date,
                correct=correct,
                total=total,
                earned_at=finished_at,
            )

        return {
            "record_id": record_id,
            "set_id": set_id,
            "date": date,
            "total": total,
            "correct": correct,
            "wrong": wrong,
            "accuracy": accuracy,
            "duration": duration,
            "points": earned_points,
        }
    finally:
        conn.close()


def practice_wrong(date: str, user_id: int, form) -> dict:
    """错题重练：判分并据此更新该用户错题本（答对移除，答错更新）。

    与 grade_and_save 一致，所有写入放在单个事务内，异常时整体回滚。

    Returns:
        含 practiced / right / still_wrong 的统计字典。

    Raises:
        ValueError: 登录用户已失效。
    """
    conn = models.get_conn()
    try:
        if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise ValueError("登录状态已失效，请重新登录")

        wrongs = wrong_book.get_wrong_by_date(date, user_id)
        submitted = _load_submitted(form, len(wrongs))

        practiced = len(wrongs)
        right = 0
        still_wrong = 0
        with conn:
            for w in wrongs:
                qid = w["question_id"]
                correct_ans = w["correct_answer"]
                user_ans = submitted.get(qid, [])
                if judge(w["type"], correct_ans, user_ans):
                    wrong_book.remove_wrong(qid, user_id, conn=conn)
                    right += 1
                else:
                    still_wrong += 1
                    wrong_book.add_wrong(
                        w["daily_set_id"], qid, user_id, user_ans, correct_ans, conn=conn
                    )

        return {
            "date": date,
            "practiced": practiced,
            "right": right,
            "still_wrong": still_wrong,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 展示辅助
# ---------------------------------------------------------------------------

def answer_to_text(options: dict, letters: list[str]) -> str:
    """把答案字母列表转换为可读文本，如 "B" -> "B. 选项B"。"""
    return "、".join(f"{l}. {options.get(l, '')}" for l in letters)


_BOLD_RE = _re.compile(r"\*\*(.+?)\*\*")
_BULLET_RE = _re.compile(r"^(\s*)[-*]\s+(.*)$")


def explanation_to_html(text: Optional[str]) -> str:
    """把题目解析的原始 Markdown 文本渲染为安全 HTML。

    仅支持解析块中实际出现的两种标记：`**加粗**` 与 `- ` 项目符号（含缩进子项，
    缩进转换为 `&nbsp;` 以保留层级）。其余内容一律 HTML 转义，避免题库文本注入脚本。
    渲染结果需配合模板的 `|safe` 使用。

    Args:
        text: 解析原始文本（可含换行、缩进、加粗标记），允许为 None。

    Returns:
        安全 HTML 字符串；无解析时返回空串。
    """
    if not text or not text.strip():
        return ""
    out: list[str] = []
    for raw in text.splitlines():
        escaped = _html.escape(raw)
        escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
        m = _BULLET_RE.match(escaped)
        if m:
            indent = m.group(1).replace(" ", "&nbsp;")
            out.append(f'<span class="exp-line">{indent}• {m.group(2)}</span>')
        else:
            out.append(f'<span class="exp-line">{escaped}</span>')
    return "<br>".join(out)


def build_score_view(date: str, user_id: int) -> Optional[dict]:
    """组装成绩详情页所需数据：该用户的成绩记录 + 该日错题列表。"""
    s = models.get_daily_set_by_date(date)
    if not s:
        return None
    record = score_manager.get_record_by_set_user(s["id"], user_id)
    wrongs = wrong_book.get_wrong_by_date(date, user_id)
    return {
        "date": date,
        "title": s["title"],
        "record": record,
        "wrongs": wrongs,
    }
