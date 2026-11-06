"""管理员题库维护业务逻辑。

职责（与 quiz_logic 平级，位于路由与数据访问之间）：
- list_sets：后台题库列表（含题目 / 成绩 / 错题数量）。
- build_set_editor：组装编辑页所需的题库与逐题数据。
- save_edit：校验编辑表单并在**单个事务**内保存日期、标题、逐题修改与单题删除。
- build_delete_preview / delete_set：删除确认页数据与级联删除。

设计要点：
- 所有写操作共用一个连接 + `with conn:`，中途失败整体回滚，避免半写题库。
- 表单校验复用 parser.py 的规则（答案字母必须存在、单选唯一、多选至少两个、
  判断为 A/B），错误以 `EditError` 抛出，由路由层转成 flash 提示。
- 修改题目不重算历史成绩：成绩与错题是当时的作答快照，编辑页会明确提示管理员。
"""
from __future__ import annotations

import json
import re
from datetime import date as date_cls
from typing import Optional

import db
import models
import navigation

TYPE_LABELS: dict[str, str] = {
    "single": "单选题",
    "multiple": "多选题",
    "judge": "判断题",
}

_LETTERS = "ABCDEFGH"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ANSWER_CLEAN_RE = re.compile(r"[\s,，、;；/|]+")
_OPTION_LETTER_RE = re.compile(r"^[A-Z]$")
_TRUE_VALUES = {"1", "on", "true", "yes"}


class EditError(ValueError):
    """编辑表单校验失败，消息可直接展示给管理员。"""


def list_sets() -> list[dict]:
    """返回后台题库列表（含题目、成绩、错题数量）。"""
    return models.list_sets_with_stats()


def _option_letters(options: dict[str, str]) -> list[str]:
    """生成编辑页要渲染的选项字母：至少 4 个，额外留 1 个空位供新增选项。

    Args:
        options: 题目现有选项字典。

    Returns:
        字母列表（A~H，最多 8 个），并保留题库中已有的异常字母以免丢数据。
    """
    existing = [k for k in options if _OPTION_LETTER_RE.match(str(k))]
    slots = min(len(_LETTERS), max(4, len(existing) + 1))
    letters = list(_LETTERS[:slots])
    for key in sorted(existing):
        if key not in letters:
            letters.append(key)
    return letters


def build_set_editor(set_date: str) -> Optional[dict]:
    """组装题库编辑页数据。

    Args:
        set_date: 题库日期（YYYY-MM-DD）。

    Returns:
        含 set / questions / stats / type_labels 的字典；题库不存在返回 None。
    """
    s = models.get_daily_set_by_date(set_date)
    if not s:
        return None

    questions: list[dict] = []
    for row in models.get_all_questions_by_set(s["id"]):
        options = json.loads(row["options"])
        questions.append(
            {
                "id": row["id"],
                "set_index": row["set_index"],
                "type": row["type"],
                "question": row["question"],
                "options": options,
                "answer": json.loads(row["answer"]),
                "explanation": row["explanation"] if "explanation" in row.keys() else "",
                "letters": _option_letters(options),
            }
        )
    return {
        "set": dict(s),
        "date": set_date,
        "selected_category": s["category_slug"] if "category_slug" in s.keys() else "",
        "questions": questions,
        "stats": models.get_set_stats(s["id"]),
        "type_labels": TYPE_LABELS,
    }


def build_delete_preview(set_date: str) -> Optional[dict]:
    """组装删除确认页数据（含将被级联删除的数据量）。

    Args:
        set_date: 题库日期。

    Returns:
        含 set / stats 的字典；题库不存在返回 None。
    """
    s = models.get_daily_set_by_date(set_date)
    if not s:
        return None
    return {"set": dict(s), "date": set_date, "stats": models.get_set_stats(s["id"])}


def _parse_answer(raw: str, options: dict[str, str], q_type: str, label: str) -> list[str]:
    """校验并规范化正确答案文本（与 parser._validate_question 规则一致）。

    Args:
        raw: 表单中输入的答案，如 "B" / "A、B" / "ABD"。
        options: 已填写的非空选项。
        q_type: 题型（single / multiple / judge）。
        label: 出错提示中使用的题号描述。

    Returns:
        答案字母列表（大写、去重）。

    Raises:
        EditError: 答案为空、含重复字母、引用不存在的选项或与题型不符。
    """
    cleaned = _ANSWER_CLEAN_RE.sub("", (raw or "")).upper()
    if not cleaned:
        raise EditError(f"{label}：请填写正确答案")
    answer = list(cleaned)
    if len(set(answer)) != len(answer):
        raise EditError(f"{label}：答案含重复字母，如 ABD 请勿写成 AAD")
    for letter in answer:
        if letter not in options:
            raise EditError(f"{label}：答案「{letter}」没有对应的选项内容，请检查选项是否留空")
    if q_type == "single" and len(answer) != 1:
        raise EditError(f"{label}：单选题答案应只有 1 个选项")
    if q_type == "multiple" and len(answer) < 2:
        raise EditError(f"{label}：多选题答案应至少包含 2 个选项")
    if q_type == "judge" and (len(answer) != 1 or answer[0] not in ("A", "B")):
        raise EditError(f"{label}：判断题答案应为 A（正确）或 B（错误）")
    return answer


def _parse_question(form, row: db.Row) -> Optional[dict]:
    """解析单道题的编辑表单。

    Args:
        form: Flask request.form（或普通 dict）。
        row: 数据库中该题目的原始行。

    Returns:
        更新数据字典；勾选「删除本题」时返回 None。

    Raises:
        EditError: 题型非法、题干为空、无选项或答案不合法。
    """
    qid = row["id"]
    label = f"第 {row['set_index']} 题"

    if str(form.get(f"q_{qid}_delete", "")).lower() in _TRUE_VALUES:
        return None

    q_type = (form.get(f"q_{qid}_type") or "").strip()
    if q_type not in TYPE_LABELS:
        raise EditError(f"{label}：题型不合法")

    question = (form.get(f"q_{qid}_question") or "").strip()
    if not question:
        raise EditError(f"{label}：题干不能为空")

    # 从表单里收集所有选项输入，而不是只遍历页面渲染出的字母，
    # 这样即使题库里有 A~H 之外的历史选项也不会被静默丢弃。
    prefix = f"q_{qid}_option_"
    options: dict[str, str] = {}
    for key in form:
        if not key.startswith(prefix):
            continue
        letter = key[len(prefix):]
        text = (form.get(key) or "").strip()
        if text and _OPTION_LETTER_RE.match(letter):
            options[letter] = text
    if not options:
        raise EditError(f"{label}：至少需要填写 1 个选项")

    answer = _parse_answer(form.get(f"q_{qid}_answer"), options, q_type, label)
    explanation = (form.get(f"q_{qid}_explanation") or "").strip()
    return {
        "id": qid,
        "type": q_type,
        "question": question,
        "options": {k: options[k] for k in sorted(options)},
        "answer": answer,
        "explanation": explanation,
    }


def save_edit(set_date: str, form) -> dict:
    """校验并保存题库编辑（日期、标题、逐题修改、单题删除），全部在单事务内完成。

    Args:
        set_date: 当前题库日期（路由上的日期）。
        form: Flask request.form。

    Returns:
        含 date / title / updated / deleted / total 的结果字典。

    Raises:
        EditError: 表单校验不通过或目标日期已被其他题库占用（此时不会写入任何数据）。
    """
    s = models.get_daily_set_by_date(set_date)
    if not s:
        raise EditError("该日期题库不存在")

    new_date = (form.get("set_date") or "").strip()
    title = (form.get("set_title") or "").strip()
    if not _DATE_RE.match(new_date):
        raise EditError("日期格式应为 YYYY-MM-DD")
    try:
        date_cls.fromisoformat(new_date)
    except ValueError as exc:
        raise EditError(f"日期不合法：{new_date}") from exc
    if not title:
        raise EditError("标题不能为空")
    if len(title) > 200:
        raise EditError("标题不能超过 200 个字符")

    # 栏目为可选修改：留空表示保持原归属（兼容脚本与旧表单），
    # 非空则必须是通过导航配置树校验的合法 slug，并同步刷新路径快照。
    category_slug = (form.get("category_slug") or "").strip()
    category_path = None
    if category_slug:
        if not navigation.is_valid_category(category_slug):
            raise EditError("所属栏目不合法，请重新选择")
        category_path = navigation.category_path_label(category_slug)

    rows = models.get_all_questions_by_set(s["id"])
    updates: list[dict] = []
    delete_ids: list[int] = []
    for row in rows:
        parsed = _parse_question(form, row)
        if parsed is None:
            delete_ids.append(row["id"])
        else:
            updates.append(parsed)
    if not updates:
        raise EditError("题库至少需要保留 1 道题；若要移除整个题库，请使用「删除题库」")

    conn = models.get_conn()
    try:
        conflict = conn.execute(
            "SELECT id FROM daily_sets WHERE date = ? AND id <> ?",
            (new_date, s["id"]),
        ).fetchone()
        if conflict:
            raise EditError(f"日期 {new_date} 已存在题库，请先删除该题库或改用其他日期")

        with conn:
            for qid in delete_ids:
                # wrong_questions 通过外键 ON DELETE CASCADE 一并清理
                models.delete_question(qid, conn=conn)
            for item in updates:
                models.update_question(
                    item["id"],
                    item["type"],
                    item["question"],
                    json.dumps(item["options"], ensure_ascii=False),
                    json.dumps(item["answer"], ensure_ascii=False),
                    item["explanation"],
                    conn=conn,
                )
            total = models.recount_total_sets(s["id"], conn=conn)
            models.update_daily_set(
                s["id"], new_date, title, conn=conn,
                category_slug=category_slug or None,
                category_path=category_path,
            )

        return {
            "date": new_date,
            "title": title,
            "updated": len(updates),
            "deleted": len(delete_ids),
            "total": total,
        }
    except db.IntegrityError as exc:
        # date 唯一约束等数据库级冲突：转成可读提示，事务已由 with conn 回滚
        raise EditError(f"保存失败（数据冲突）：{exc}") from exc
    finally:
        conn.close()


def delete_set(set_date: str) -> dict:
    """删除整个题库及其题目、成绩、错题。

    Args:
        set_date: 题库日期。

    Returns:
        含 date / title / question_count / record_count / wrong_count 的结果字典。

    Raises:
        EditError: 该日期题库不存在。
    """
    s = models.get_daily_set_by_date(set_date)
    if not s:
        raise EditError("该日期题库不存在")
    stats = models.get_set_stats(s["id"])
    models.delete_set_cascade(set_date)
    return {"date": set_date, "title": s["title"], **stats}
