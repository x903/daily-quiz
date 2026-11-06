"""积分规则与积分流水写入。

规则：
- 只有**正式答题**（`/quiz/<date>` 提交）计分；错题重练不计分，避免反复制造错题刷分。
- 每答对 1 题得 `POINTS_PER_CORRECT` 分（默认 1 分），0 分也会记一条流水用于审计。
- 每次提交写一条 `points_ledger` 流水，保留完整积分历史；榜单口径见 leaderboard.py
  （每用户每日只取当日最佳一次，防止重答刷榜）。
- 写入必须复用判分所在的连接，与成绩、错题在**同一事务**内提交或回滚。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import db

POINTS_PER_CORRECT: int = 1


def award_for_quiz(
    conn: db.Connection,
    *,
    user_id: int,
    set_id: int,
    record_id: int,
    set_date: str,
    correct: int,
    total: int,
    earned_at: Optional[str] = None,
) -> int:
    """为一次正式答题写入积分流水，返回本次积分。

    Args:
        conn: 判分事务所在的连接（本函数不 commit / 不关闭）。
        user_id: 答题用户 id。
        set_id: 题库 id。
        record_id: 成绩记录 id（仅作追溯，不设外键）。
        set_date: 题库日期（YYYY-MM-DD），仅用于追溯。
        correct: 本次答对题数。
        total: 本次总题数。
        earned_at: 答题完成时间 `YYYY-MM-DD HH:MM:SS`；默认取当前时间。
                   允许显式传入以便补录历史数据或做确定性测试。

    Returns:
        本次获得的积分（correct × POINTS_PER_CORRECT）。

    Raises:
        ValueError: earned_at 格式不合法。
    """
    finished_at = earned_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if len(finished_at) < 10 or finished_at[4] != "-" or finished_at[7] != "-":
        raise ValueError(f"答题完成时间格式不合法：{finished_at}")
    earned_date = finished_at[:10]  # 统计周期一律按「答题发生日」，补做旧题算当天
    correct = max(0, int(correct))
    earned_points = correct * POINTS_PER_CORRECT

    conn.execute(
        """
        INSERT INTO points_ledger
            (user_id, daily_set_id, record_id, earned_date, set_date,
             points, correct, total, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'quiz', ?)
        """,
        (
            user_id,
            set_id,
            record_id,
            earned_date,
            set_date,
            earned_points,
            correct,
            max(0, int(total)),
            finished_at,
        ),
    )
    return earned_points
