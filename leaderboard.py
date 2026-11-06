"""排行榜业务逻辑：日榜 / 周榜 / 月榜的周期计算、聚合与排名。

数据来源：`points_ledger`（积分流水，见 points.py）。

统计口径（重要）：
- 统计日期按**答题发生日** `earned_date`（不是题库日期），补做历史题计入补做当天。
- 每个用户每个自然日只取**当日最佳一次**答题得分 `MAX(points)`，周榜 / 月榜为
  这些每日得分之和。原因：现有系统允许无限重答 / 补做，若把流水直接求和，
  反复提交即可刷榜；取每日最佳既保留完整流水历史（可审计、可换口径），又保证公平。
- 并列积分使用「竞争排名」（1, 2, 2, 4 ...），同分按答对题数、活跃天数、用户名排序展示。
- 只统计 `points > 0` 的记录，0 分用户不占榜位；已删除的用户由 JOIN users 自动排除。
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional

import models

PERIOD_LABELS: dict[str, str] = {"day": "日榜", "week": "周榜", "month": "月榜"}
_WEEKDAY_CN = "一二三四五六日"

_BOARD_SQL = """
SELECT u.id AS user_id,
       u.username,
       SUM(d.best_points)  AS points,
       SUM(d.best_correct) AS correct,
       COUNT(*)            AS active_days,
       MAX(d.best_points)  AS best_day
FROM (
    SELECT p.user_id,
           p.earned_date,
           MAX(p.points)  AS best_points,
           MAX(p.correct) AS best_correct
    FROM points_ledger p
    WHERE p.earned_date BETWEEN ? AND ?
      AND p.points > 0
    GROUP BY p.user_id, p.earned_date
) d
JOIN users u ON u.id = d.user_id
GROUP BY u.id, u.username
ORDER BY points DESC, correct DESC, active_days DESC, u.username ASC
"""


def period_bounds(period: str, on_date: Optional[date] = None) -> tuple[date, date]:
    """计算榜单周期的起止日期（含首尾）。

    Args:
        period: `day` / `week` / `month`。
        on_date: 基准日期，默认今天。周榜为周一至周日，月榜为自然月 1 日至月末。

    Returns:
        (start, end)，均为 datetime.date。

    Raises:
        ValueError: period 非法。
    """
    if period not in PERIOD_LABELS:
        raise ValueError(f"未知榜单周期：{period}")
    on = on_date or date.today()
    if period == "day":
        return on, on
    if period == "week":
        start = on - timedelta(days=on.weekday())  # weekday(): 周一=0
        return start, start + timedelta(days=6)
    start = on.replace(day=1)
    last_day = calendar.monthrange(on.year, on.month)[1]
    return start, on.replace(day=last_day)


def shift_period(period: str, on_date: date, delta: int) -> date:
    """按周期平移基准日期，用于「上一期 / 下一期」导航。

    Args:
        period: `day` / `week` / `month`。
        on_date: 当前基准日期。
        delta: 平移的周期数（正为向后，负为向前）。

    Returns:
        平移后的基准日期（月榜会按目标月份天数夹紧，如 1 月 31 日 → 2 月 28 日）。

    Raises:
        ValueError: period 非法。
    """
    if period == "day":
        return on_date + timedelta(days=delta)
    if period == "week":
        return on_date + timedelta(weeks=delta)
    if period == "month":
        month_index = on_date.year * 12 + (on_date.month - 1) + delta
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        day = min(on_date.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    raise ValueError(f"未知榜单周期：{period}")


def _range_label(period: str, start: date, end: date) -> str:
    """生成给页面展示的周期文案。"""
    if period == "day":
        return f"{start.isoformat()}（周{_WEEKDAY_CN[start.weekday()]}）"
    return f"{start.isoformat()} ~ {end.isoformat()}"


def get_leaderboard(period: str = "day", on_date: Optional[date] = None) -> dict:
    """查询指定周期的排行榜。

    Args:
        period: `day` / `week` / `month`。
        on_date: 基准日期，默认今天；可传历史日期查看往期榜单。

    Returns:
        含 period / period_label / start / end / range_label / on_date /
        participants / items 的字典。items 每项含 rank / user_id / username /
        points / correct / active_days / best_day。

    Raises:
        ValueError: period 非法。
    """
    on = on_date or date.today()
    start, end = period_bounds(period, on)

    conn = models.get_conn()
    try:
        rows = conn.execute(_BOARD_SQL, (start.isoformat(), end.isoformat())).fetchall()
    finally:
        conn.close()

    items: list[dict] = []
    rank = 0
    prev_points: Optional[int] = None
    for position, row in enumerate(rows, 1):
        current_points = int(row["points"])
        if current_points != prev_points:
            rank = position  # 同分并列，跳过被占用的名次（1, 2, 2, 4 ...）
            prev_points = current_points
        items.append(
            {
                "rank": rank,
                "user_id": int(row["user_id"]),
                "username": row["username"],
                "points": current_points,
                "correct": int(row["correct"]),
                "active_days": int(row["active_days"]),
                "best_day": int(row["best_day"]),
            }
        )

    return {
        "period": period,
        "period_label": PERIOD_LABELS[period],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "on_date": on.isoformat(),
        "range_label": _range_label(period, start, end),
        "participants": len(items),
        "items": items,
    }


def find_user(items: list[dict], user_id: int) -> Optional[dict]:
    """从榜单结果中找出指定用户的条目（不在榜上返回 None）。"""
    return next((item for item in items if item["user_id"] == user_id), None)
