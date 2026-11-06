"""错题管理模块（按用户隔离）。

负责 wrong_questions 表的写入、查询、清空与练习后的移除。
错题本以「每道题当前最新一次答错状态」为准：重新答题答对后自动移出错错本。
多用户版本：错题与具体用户绑定（question_id + user_id 唯一确定一条错题）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

from models import get_conn


def add_wrong(
    set_id: int,
    question_id: int,
    user_id: int,
    user_answer: list[str],
    correct_answer: list[str],
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """记录某用户的错题（若已存在则更新用户答案）。

    唯一键为 (question_id, user_id)，保证每个用户每道题只保留一条最新错题。

    Args:
        set_id: 题目集 id。
        question_id: 题目 id。
        user_id: 用户 id（必须已存在于 users 表，否则外键约束会拒绝写入）。
        user_answer: 用户作答字母列表。
        correct_answer: 正确答案字母列表。
        conn: 外部连接。传入时由调用方统一管理事务，这里不提交也不关闭。
    """
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM wrong_questions WHERE question_id = ? AND user_id = ?",
            (question_id, user_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE wrong_questions SET user_answer=?, correct_answer=?, created_at=? "
                "WHERE question_id = ? AND user_id = ?",
                (
                    json.dumps(user_answer, ensure_ascii=False),
                    json.dumps(correct_answer, ensure_ascii=False),
                    created_at,
                    question_id,
                    user_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO wrong_questions "
                "(daily_set_id, question_id, user_id, user_answer, correct_answer, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    set_id,
                    question_id,
                    user_id,
                    json.dumps(user_answer, ensure_ascii=False),
                    json.dumps(correct_answer, ensure_ascii=False),
                    created_at,
                ),
            )
        if owns_conn:
            conn.commit()
    finally:
        # 异常时也必须关闭连接，避免未提交事务残留导致 database is locked
        if owns_conn:
            conn.close()


def remove_wrong(
    question_id: int, user_id: int, conn: Optional[sqlite3.Connection] = None
) -> None:
    """移出某用户某道题的错题记录（答对后调用）。

    Args:
        question_id: 题目 id。
        user_id: 用户 id。
        conn: 外部连接。传入时由调用方统一管理事务。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        conn.execute(
            "DELETE FROM wrong_questions WHERE question_id = ? AND user_id = ?",
            (question_id, user_id),
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def get_wrong_by_date(date: str, user_id: int) -> list[dict]:
    """按日期获取某用户的该日错题，并关联题目内容。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT w.*, q.question, q.type, q.options, q.set_index, q.explanation "
        "FROM wrong_questions w "
        "JOIN questions q ON q.id = w.question_id "
        "JOIN daily_sets ds ON ds.id = w.daily_set_id "
        "WHERE ds.date = ? AND w.user_id = ? ORDER BY w.id",
        (date, user_id),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_all_wrong(user_id: Optional[int] = None) -> list[dict]:
    """获取错题列表（跨日期）。

    Args:
        user_id: 传具体 id 时只返回该用户错题；传 None（管理员）返回全部。
    """
    conn = get_conn()
    if user_id is None:
        rows = conn.execute(
            "SELECT w.*, q.question, q.type, q.options, q.set_index, q.explanation, ds.date, ds.title, u.username "
            "FROM wrong_questions w "
            "JOIN questions q ON q.id = w.question_id "
            "JOIN daily_sets ds ON ds.id = w.daily_set_id "
            "JOIN users u ON u.id = w.user_id "
            "ORDER BY ds.date DESC, w.id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT w.*, q.question, q.type, q.options, q.set_index, q.explanation, ds.date, ds.title "
            "FROM wrong_questions w "
            "JOIN questions q ON q.id = w.question_id "
            "JOIN daily_sets ds ON ds.id = w.daily_set_id "
            "WHERE w.user_id = ? ORDER BY ds.date DESC, w.id",
            (user_id,),
        ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def clear_all(user_id: Optional[int] = None) -> int:
    """清空错题记录。

    Args:
        user_id: 传具体 id 时只清空该用户错题；传 None（管理员）清空全部。

    Returns:
        被删除的条数。
    """
    conn = get_conn()
    if user_id is None:
        cur = conn.execute("DELETE FROM wrong_questions")
    else:
        cur = conn.execute("DELETE FROM wrong_questions WHERE user_id = ?", (user_id,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["user_answer"] = json.loads(d["user_answer"])
    d["correct_answer"] = json.loads(d["correct_answer"])
    d["options"] = json.loads(d["options"])
    # 兼容尚未迁移 / 无解析的旧题目，保证模板侧字段始终存在
    d.setdefault("explanation", "")
    return d
