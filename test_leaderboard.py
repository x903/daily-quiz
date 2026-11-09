"""回归测试：排行榜模块（日榜 / 周榜 / 月榜）与积分流水。

全程使用独立临时数据库，不触碰正在运行的 data/quiz.db。

覆盖：
1. 正式答题自动写积分流水（与成绩同一事务），0 分也留痕；
2. 同一天重复答题按「当日最佳一次」计分，错题重练不计分；
3. 日 / 周 / 月周期边界与「上一期 / 下一期」平移（含月末夹紧）；
4. 跨周 / 跨月聚合正确性（上周但同月、上月的分数不串榜）与同分并列排名；
5. 历史成绩回填（幂等）、删除题库时积分流水级联清理；
6. 页面 / JSON 接口 / 入口与权限（未登录可浏览，登录用户与管理员正常）。
"""
from __future__ import annotations

import calendar
import json
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

# 必须在 import app 之前重定向数据库：app.py 在模块加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_leaderboard_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

import auth  # noqa: E402
import leaderboard  # noqa: E402
import parser as md_parser  # noqa: E402
import points  # noqa: E402
import quiz_logic  # noqa: E402
import score_manager  # noqa: E402
import wrong_book  # noqa: E402

TODAY = date.today()
QUIZ_DATE = TODAY.isoformat()          # 正式答题用的题库日期
OTHER_SET_DATE = "2026-01-02"          # 用于级联删除测试的题库
ON = date(2026, 6, 18)                 # 周期边界固定基准日（周四，远离运行日）
USERS = ("alice", "bobby", "carol")


def setup_database() -> None:
    """临时库建表、播种账号并导入两份题库。"""
    models.init_db()
    auth.create_default_users()
    src = Path(__file__).resolve().parent / "data" / "ACP模拟题20道_2026-11-04.md"
    parsed = md_parser.parse_markdown(src.read_text(encoding="utf-8"))
    assert parsed.total_questions == 20, "示例题库应解析出 20 道题"
    quiz_logic.import_day(QUIZ_DATE, "排行榜测试题库", parsed, overwrite=True)
    quiz_logic.import_day(OTHER_SET_DATE, "待删除题库", parsed, overwrite=True)
    for name in USERS:
        ok, msg = auth.register(name, "pass1234", role="user")
        assert ok, msg


def _user_id(username: str) -> int:
    return int(models.get_user_by_username(username)["id"])


def _award(username: str, when: str, correct: int, *, set_date: str = QUIZ_DATE) -> None:
    """直接写入指定时间的积分流水（模拟历史数据 / 确定性测试）。"""
    set_id = models.get_daily_set_by_date(set_date)["id"]
    conn = models.get_conn()
    try:
        with conn:
            points.award_for_quiz(
                conn,
                user_id=_user_id(username),
                set_id=set_id,
                record_id=0,
                set_date=set_date,
                correct=correct,
                total=20,
                earned_at=when,
            )
    finally:
        conn.close()


def _ledger_rows(where: str = "", params: tuple = ()) -> list[dict]:
    conn = models.get_conn()
    try:
        sql = "SELECT * FROM points_ledger" + (f" WHERE {where}" if where else "")
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _board(period: str, on: date = ON) -> dict:
    return leaderboard.get_leaderboard(period, on)


def _by_name(board: dict, username: str) -> dict | None:
    return next((i for i in board["items"] if i["username"] == username), None)


def _answer_form(questions: list[dict], wrong_ids: set[int] | None = None) -> dict:
    wrong_ids = wrong_ids or set()
    form: dict = {"duration": "60"}
    for q in questions:
        correct = json.loads(q["answer"])
        form[f"q_{q['id']}"] = ("A" if correct[0] != "A" else "B") if q["id"] in wrong_ids else correct[0]
    return form


def _login(client, username: str, password: str):
    import test_support
    return test_support.login(client, username, password)


def main() -> None:
    setup_database()
    from app import app  # noqa: PLC0415  需在 DB_PATH 重定向之后导入

    app.config["TESTING"] = True
    set_id = models.get_daily_set_by_date(QUIZ_DATE)["id"]
    questions = [dict(r) for r in models.get_all_questions_by_set(set_id)]
    assert len(questions) == 20
    uid = _user_id("user")

    # ---- 1. 正式答题自动记分 ----
    client = app.test_client()
    assert _login(client, "user", "user123").status_code == 302
    assert client.post(f"/quiz/{QUIZ_DATE}", data=_answer_form(questions)).status_code == 302
    rows = _ledger_rows("user_id = ? AND source = 'quiz'", (uid,))
    assert len(rows) == 1, "一次提交应只写 1 条积分流水"
    assert rows[0]["points"] == 20 * points.POINTS_PER_CORRECT
    assert rows[0]["correct"] == 20 and rows[0]["earned_date"] == TODAY.isoformat()
    assert rows[0]["set_date"] == QUIZ_DATE
    day_board = _board("day", TODAY)
    assert _by_name(day_board, "user")["points"] == 20
    print("1) 正式答题自动累计积分（20 题 -> 20 分），流水与日榜一致 ✅")

    # ---- 2. 重答取当日最佳 + 错题重练不计分 ----
    wrong_ids = {q["id"] for q in questions[:5]}
    assert client.post(
        f"/quiz/{QUIZ_DATE}", data=_answer_form(questions, wrong_ids)
    ).status_code == 302
    assert len(_ledger_rows("user_id = ? AND source = 'quiz'", (uid,))) == 2
    assert _by_name(_board("day", TODAY), "user")["points"] == 20, "重答不得叠加刷分"
    assert len(wrong_book.get_wrong_by_date(QUIZ_DATE, uid)) == 5
    assert client.post(f"/wrong/{QUIZ_DATE}/practice", data={}).status_code == 302
    assert len(_ledger_rows("user_id = ? AND source = 'quiz'", (uid,))) == 2, "重练不得计分"
    print("2) 同日重答取最佳一次（仍 20 分），错题重练不计分 ✅")

    # ---- 3. 周期边界与平移 ----
    assert leaderboard.period_bounds("day", ON) == (ON, ON)
    ws, we = leaderboard.period_bounds("week", ON)
    assert ws.weekday() == 0 and we.weekday() == 6 and (we - ws).days == 6 and ws <= ON <= we
    ms, me = leaderboard.period_bounds("month", ON)
    assert ms == ON.replace(day=1) and me.day == calendar.monthrange(ON.year, ON.month)[1]
    assert leaderboard.shift_period("day", ON, -1) == ON - timedelta(days=1)
    assert leaderboard.shift_period("week", ON, 1) == ON + timedelta(days=7)
    assert leaderboard.shift_period("month", date(2026, 1, 31), 1) == date(2026, 2, 28)
    try:
        leaderboard.period_bounds("year")
        raise AssertionError("非法周期应抛 ValueError")
    except ValueError:
        pass
    print("3) 日 / 周（周一至周日）/ 月（自然月）边界与周期平移正确 ✅")

    # ---- 4. 跨周期聚合 + 并列排名 ----
    assert ms < ws, "基准月应包含本周之前的日期，便于验证跨周聚合"
    _award("alice", f"{ON.isoformat()} 09:00:00", 7)
    _award("alice", f"{ON.isoformat()} 20:00:00", 3)          # 同日第二次，取 7
    _award("alice", f"{ws.isoformat()} 09:00:00", 5)           # 本周内另一天
    _award("alice", f"{(ws - timedelta(days=1)).isoformat()} 09:00:00", 9)   # 上周、同月
    _award("alice", f"{(ms - timedelta(days=1)).isoformat()} 09:00:00", 11)  # 上月
    _award("bobby", f"{ON.isoformat()} 09:00:00", 7)
    _award("carol", f"{ON.isoformat()} 09:00:00", 2)

    day = _board("day", ON)
    assert _by_name(day, "alice")["points"] == 7
    assert [_by_name(day, n)["rank"] for n in ("alice", "bobby", "carol")] == [1, 1, 3], "同分应并列"
    week = _board("week", ON)
    assert _by_name(week, "alice")["points"] == 12, "周榜 = 7 + 5，不含上周的 9 分"
    month = _board("month", ON)
    assert _by_name(month, "alice")["points"] == 21, "月榜 = 7 + 5 + 9，不含上月的 11 分"
    prev_month = _board("month", ms - timedelta(days=1))
    assert _by_name(prev_month, "alice")["points"] == 11
    assert _by_name(month, "bobby")["points"] == 7 and _by_name(month, "carol")["points"] == 2
    print("4) 日榜 7 / 周榜 12 / 月榜 21 聚合准确，上周与上月数据不串榜，同分并列 ✅")

    # ---- 5. 页面 / 接口 / 入口 / 权限 ----
    anon = app.test_client()
    assert anon.get("/leaderboard").status_code == 200, "未登录也应能浏览排行榜"
    assert anon.get("/leaderboard/api").status_code == 200
    page = client.get("/leaderboard")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert all(t in html for t in ("日榜", "周榜", "月榜", "积分", "排行榜"))
    hist_page = client.get(f"/leaderboard?period=week&date={ON.isoformat()}")
    assert hist_page.status_code == 200 and "alice" in hist_page.get_data(as_text=True)
    assert client.get("/leaderboard?period=bogus").status_code == 200, "非法周期应回退日榜"
    assert client.get("/leaderboard?period=day&date=not-a-date").status_code == 200
    api = client.get(f"/leaderboard/api?period=month&date={ON.isoformat()}")
    assert api.status_code == 200
    payload = api.get_json()
    assert payload["start"] == ms.isoformat() and payload["end"] == me.isoformat()
    assert payload["items"][0]["username"] == "alice" and payload["items"][0]["points"] == 21
    assert "排行榜" in client.get("/").get_data(as_text=True), "首页应有榜单入口"
    admin = app.test_client()
    assert _login(admin, "admin", "admin123").status_code == 302
    admin_body = admin.get("/admin").get_data(as_text=True)
    for user_only in ("每日答题", "个人中心", "错题本", "阿里云认证"):
        assert user_only not in admin_body, f"管理后台不应出现用户端菜单：{user_only}"
    assert admin.get("/api/admin/overview").get_json()["ok"]
    print("5) 榜单页面 / JSON 接口 / 导航与首页入口正常，未登录可浏览、答题需登录 ✅")

    # ---- 6. 历史成绩回填（幂等）----
    conn = models.get_conn()
    try:
        record = score_manager.get_record_by_set_user(set_id, uid)
        assert record is not None
        with conn:
            conn.execute(
                "DELETE FROM points_ledger WHERE record_id = ? AND source = 'quiz'",
                (record["id"],),
            )
        added = models.backfill_points_ledger(conn)
        conn.commit()
        assert added == 1, f"应回填 1 条历史积分，实际 {added}"
        assert models.backfill_points_ledger(conn) == 0, "回填必须幂等"
        conn.commit()
    finally:
        conn.close()
    backfilled = _ledger_rows("source = 'backfill'")
    assert len(backfilled) == 1 and backfilled[0]["record_id"] is not None
    print("6) 升级前的历史成绩可幂等回填为积分流水 ✅")

    # ---- 7. 删除题库级联清理积分 ----
    _award("alice", "2026-01-02 10:00:00", 4, set_date=OTHER_SET_DATE)
    other_id = models.get_daily_set_by_date(OTHER_SET_DATE)["id"]
    assert len(_ledger_rows("daily_set_id = ?", (other_id,))) == 1
    before = len(_ledger_rows())
    models.delete_set_cascade(OTHER_SET_DATE)
    assert len(_ledger_rows("daily_set_id = ?", (other_id,))) == 0, "题库删除应清掉对应积分"
    assert len(_ledger_rows()) == before - 1, "其他题库积分不应受影响"
    print("7) 删除题库时积分流水级联清理，其他题库数据保留 ✅")

    print("\n全部通过：日 / 周 / 月榜与积分链路验证完成。")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
