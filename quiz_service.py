"""每日答题业务逻辑：判分、落库、榜单更新。

原则：
- 抽题与判分全部在服务端完成，HTTP 响应永远不带正确答案；
- 交卷时回查数据库校验题目归属，不信任浏览器提交的题目范围；
- 一次交卷 = quiz_records 一行 + user_honors 一行 + rankings 三条（日/周/月）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import bank
import practice_bank

PASS_TOTAL = 10
POINTS_PER_QUESTION = 10


def _prefer_bank_pool(category_id: int) -> bool:
    """该认证的模拟题库是否已够抽一次题。

    够 10 题就直接用模拟题库；否则回退到旧题库（含同方向/同厂商/全站兜底），
    保证历史上只导入过 questions 的分类仍能答题。
    """
    return practice_bank.count_bank_questions(category_id) >= PASS_TOTAL


def _normalize_answers(raw) -> list[str]:
    """把提交的答案统一成大写字母列表，兼容 "A" / "A,B" / ["A","B"]。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.replace("，", ",").split(",")
    if not isinstance(raw, (list, tuple, set)):
        raw = [raw]
    result = []
    for item in raw:
        text = str(item).strip().upper()
        if text:
            result.append(text)
    return sorted(set(result))


def public_question(row: dict) -> dict:
    """下发给浏览器的题面（绝不含 answer / explanation）。"""
    return {
        "id": int(row["id"]),
        "type": row.get("type") or "single",
        "question": row.get("question") or "",
        "options": row.get("options") or {},
        "difficulty": row.get("difficulty") or "中",
    }


def start(category_id: int) -> dict:
    category = bank.get_category(category_id)
    if not category or category.get("status") != "active":
        raise ValueError("认证分类不存在或已下线")
    # 优先从「模拟题库」抽题；该认证还没建够题时回退旧题库
    if _prefer_bank_pool(category["id"]):
        rows, pool = practice_bank.random_questions_for_category(category["id"], PASS_TOTAL)
        source = "bank"
    else:
        rows, pool = bank.random_questions(category["id"], PASS_TOTAL)
        source = "legacy"
    if len(rows) < PASS_TOTAL:
        raise ValueError(
            f"「{category['cert_name']}」题库仅有 {len(rows)} 道题，不足 {PASS_TOTAL} 道，请先在后台导入题目"
        )
    return {
        "category": {
            "id": category["id"],
            "cert_name": category["cert_name"],
            "path": category["path_slug"],
        },
        "source": source,
        "pool": pool,
        "pool_label": {
            ("bank", "category"): "本认证模拟题库",
            ("bank", "global"): "全站模拟题库",
            ("legacy", "category"): "本认证题库",
            ("legacy", "direction"): "同方向题库",
            ("legacy", "vendor"): "同厂商题库",
            ("legacy", "global"): "全站题库",
        }.get((source, pool), "题库"),
        "total": PASS_TOTAL,
        "points_per_question": POINTS_PER_QUESTION,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "questions": [public_question(row) for row in rows],
    }


def grade(
    category_id: int,
    question_ids: list,
    answers: dict,
    duration: int = 0,
    started_at: str = "",
    source: str = "",
) -> dict:
    category = bank.get_category(category_id)
    if not category:
        raise ValueError("认证分类不存在")
    # 题源由开考时下发给前端，交卷再回传；缺失时按当前题量重新推断
    if source not in ("bank", "legacy"):
        source = "bank" if _prefer_bank_pool(category_id) else "legacy"

    clean_ids: list[int] = []
    for raw in question_ids or []:
        try:
            qid = int(raw)
        except (TypeError, ValueError):
            continue
        if qid not in clean_ids:
            clean_ids.append(qid)
    if len(clean_ids) != PASS_TOTAL:
        raise ValueError(f"题目数量异常：应为 {PASS_TOTAL} 道，实际 {len(clean_ids)} 道")

    rows = (practice_bank.questions_by_ids(clean_ids) if source == "bank"
            else bank.questions_by_ids(clean_ids))
    if len(rows) != len(clean_ids):
        raise ValueError("题目不存在或已被删除，请重新开始答题")

    review = []
    correct_count = 0
    for qid in clean_ids:
        row = rows[qid]
        user_answer = _normalize_answers(answers.get(str(qid)) or answers.get(qid))
        right_answer = sorted(row.get("answer_list") or [])
        is_correct = bool(user_answer) and user_answer == right_answer
        if is_correct:
            correct_count += 1
        review.append({
            "id": qid,
            "type": row.get("type"),
            "question": row.get("question"),
            "options": row.get("options"),
            "your_answer": user_answer,
            "correct_answer": right_answer,
            "is_correct": is_correct,
            "explanation": row.get("explanation") or "",
        })

    total = len(clean_ids)
    score = correct_count * POINTS_PER_QUESTION
    accuracy = round(correct_count / total, 4) if total else 0
    try:
        duration = max(0, min(int(duration or 0), 7200))
    except (TypeError, ValueError):
        duration = 0

    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record_id = bank.save_quiz_record(
        user_id=_current_user_id(),
        category=category,
        question_ids=clean_ids,
        answers={str(k): _normalize_answers(v) for k, v in (answers or {}).items()},
        total=total,
        correct=correct_count,
        score=score,
        accuracy=accuracy,
        duration=duration,
        started_at=started_at,
        finished_at=finished_at,
    )

    return {
        "record_id": record_id,
        "score": score,
        "correct": correct_count,
        "total": total,
        "accuracy": accuracy,
        "duration": duration,
        "finished_at": finished_at,
        "category": {"id": category["id"], "cert_name": category["cert_name"]},
        "review": review,
    }


def _current_user_id() -> int:
    """延迟导入 Flask session，避免本模块在纯逻辑测试中依赖请求上下文。"""
    from flask import session
    uid = session.get("user_id")
    if not uid:
        raise PermissionError("请先登录后再交卷")
    return int(uid)


def category_summary(category_id: int, user_id: Optional[int] = None) -> dict:
    """认证页需要的统计：题量、我的最佳成绩、最近一次成绩。"""
    category = bank.get_category(category_id)
    if not category:
        return {}
    conn = bank._conn()
    try:
        count = bank.count_questions(category_id)
        mine = None
        recent = None
        if user_id:
            row = conn.execute(
                "SELECT MAX(score) AS best, COUNT(*) AS times FROM quiz_records "
                "WHERE user_id = ? AND category_id = ?",
                (int(user_id), int(category_id)),
            ).fetchone()
            if row and int(row["times"] or 0) > 0:
                mine = {"best": int(row["best"] or 0), "times": int(row["times"])}
            last = conn.execute(
                "SELECT score, finished_at FROM quiz_records WHERE user_id = ? "
                "AND category_id = ? ORDER BY finished_at DESC LIMIT 1",
                (int(user_id), int(category_id)),
            ).fetchone()
            if last:
                recent = {"score": int(last["score"]), "finished_at": last["finished_at"]}
        return {"question_count": count, "mine": mine, "recent": recent}
    finally:
        conn.close()
