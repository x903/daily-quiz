"""成绩管理模块。

负责每日答题成绩（daily_records）的写入与查询。
多用户版本：成绩与具体用户绑定，一条 (题目集, 用户) 对应一条成绩记录
（再次完成时覆盖更新，保证每个用户每天只有一份最新成绩）。
"""
from __future__ import annotations

from typing import Optional

import db
from models import get_conn


def save_record(
    set_id: int,
    user_id: int,
    total: int,
    correct: int,
    wrong: int,
    accuracy: float,
    duration: int,
    finished_at: str,
    conn: Optional[db.Connection] = None,
) -> int:
    """保存（覆盖）某用户某日的成绩记录，返回记录 id。

    Args:
        set_id: 题目集 id。
        user_id: 用户 id（必须已存在于 users 表，否则外键约束会拒绝写入）。
        total / correct / wrong / accuracy / duration / finished_at: 成绩字段。
        conn: 外部连接。传入时由调用方统一管理事务，这里不提交也不关闭。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM daily_records WHERE daily_set_id = ? AND user_id = ?",
            (set_id, user_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE daily_records SET total=?, correct=?, wrong=?, accuracy=?, duration=?, finished_at=? "
                "WHERE daily_set_id = ? AND user_id = ?",
                (total, correct, wrong, accuracy, duration, finished_at, set_id, user_id),
            )
            record_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO daily_records "
                "(daily_set_id, user_id, total, correct, wrong, accuracy, duration, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (set_id, user_id, total, correct, wrong, accuracy, duration, finished_at),
            )
            record_id = cur.lastrowid
        if owns_conn:
            conn.commit()
        return record_id
    finally:
        # 异常时也必须关闭连接，避免未提交事务残留导致 database is locked
        if owns_conn:
            conn.close()


def get_record_by_set_user(set_id: int, user_id: int) -> Optional[dict]:
    """按（题目集 id, 用户 id）获取成绩记录。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM daily_records WHERE daily_set_id = ? AND user_id = ?",
        (set_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_record(record_id: int) -> Optional[dict]:
    """按记录 id 获取成绩记录（含 user_id 供权限校验）。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM daily_records WHERE id = ?", (record_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_records_by_user(user_id: int) -> list[dict]:
    """获取某用户的全部成绩记录（按完成时间倒序），并关联日期与标题。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT dr.*, ds.date, ds.title, ds.total_sets "
        "FROM daily_records dr "
        "JOIN daily_sets ds ON ds.id = dr.daily_set_id "
        "WHERE dr.user_id = ? ORDER BY dr.finished_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_all_records() -> list[dict]:
    """获取全部用户的成绩记录（管理员视角），并关联用户名。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT dr.*, ds.date, ds.title, ds.total_sets, u.username "
        "FROM daily_records dr "
        "JOIN daily_sets ds ON ds.id = dr.daily_set_id "
        "JOIN users u ON u.id = dr.user_id "
        "ORDER BY dr.finished_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
