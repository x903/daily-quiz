"""回归测试：二次审计 B0 项（FIX-16 / FIX-17 / FIX-18 / FIX-20）。

直接运行：``python test_exam_audit.py``

覆盖：
1. FIX-16 抽题覆盖：题库题量 > 每卷题量时，连续开考应覆盖大部分题目，
   而不是永远只考前 N 题；
2. FIX-20 交卷不泄题：交白卷时所有题目 `correct_answer` / `explanation_html`
   为空；已作答的题正常给答案解析；完成率不足不计分；
3. FIX-17 模拟考积分：完成率达标后按成绩折算积分，写入排行榜与个人统计；
   同题库同日重复练习不叠加；并发重复结算（stale session）不重复加分；
4. FIX-18 超时回收：全局 sweep 能把超时场次结算为 submitted，且幂等。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_exam_audit_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

import auth  # noqa: E402
import bank  # noqa: E402
import practice_bank  # noqa: E402
import seed_practice  # noqa: E402
import test_support  # noqa: E402
from app import app  # noqa: E402

CERT_PATH = "aliyun/acp/cloud"


def setup() -> None:
    models.init_db()
    auth.create_default_users()
    seed_practice.main()


def make_bank(slug: str, count: int, *, declared: int = 0, duration: int = 60) -> dict:
    """建一个已发布的全单选题库。

    ``declared`` 小于 ``count`` 时模拟「题库实际题量 > 每卷题量」的场景
    （发布后再扩充题目 / 后台调小每卷题量）。
    """
    created = practice_bank.create_bank({
        "name": "审计测试题库 " + slug,
        "slug": slug,
        "cert_path": CERT_PATH,
        "level_tag": "基础",
        "difficulty": "中等",
        "rule_code": "custom",
        "total_questions": count,
        "single_count": count,
        "multiple_count": 0,
        "total_score": 100,
        "pass_score": 60,
        "duration_minutes": duration,
    })
    for index in range(count):
        practice_bank.add_question(created["id"], {
            "type": "single",
            "question": f"{slug} 第 {index + 1} 题：以下哪个选项是正确答案？",
            "options": {"A": "选项甲", "B": "选项乙", "C": "选项丙", "D": "选项丁"},
            "answer": ["A"],
            "explanation": "解析：本测试题库正确答案统一为 A。",
        })
    practice_bank.set_bank_status(created["id"], "published")
    if declared and declared != count:
        conn = models.get_conn()
        try:
            conn.execute("UPDATE question_banks SET total_questions = ? WHERE id = ?",
                         (declared, created["id"]))
            conn.commit()
        finally:
            conn.close()
    return practice_bank.get_bank(created["id"], by_id=True)


def user_client():
    client = app.test_client()
    assert test_support.login(client, "user", "user123").status_code == 302
    return client


def user_id() -> int:
    return int(models.get_user_by_username("user")["id"])


def start(client, slug: str) -> dict:
    response = client.post(f"/api/practice/banks/{slug}/start", json={"restart": True})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]


def answer(client, token: str, question_id: int, letters=("A",)) -> None:
    response = client.post(
        f"/api/practice/sessions/{token}/answer",
        json={"question_id": question_id, "answer": list(letters)},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def submit(client, token: str) -> dict:
    response = client.post(f"/api/practice/sessions/{token}/submit", json={"reason": "manual"})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["result"]


def check_sampling_covers_whole_bank() -> None:
    """FIX-16：先截断再洗牌会让后 90 题永远考不到。"""
    target = make_bank("audit-sampling", 100, declared=10)
    seen: set[int] = set()
    for _ in range(15):
        data = practice_bank.start_session(user_id(), target, restart=True)
        ids = [int(q["id"]) for q in data["questions"]]
        assert len(ids) == 10, "每卷题量应等于声明的 10 题"
        seen.update(ids)
    assert len(seen) > 50, f"15 次开考只覆盖 {len(seen)} 道题，抽题仍集中在前 10 题"
    print(f"1) 100 题题库声明每卷 10 题，15 次开考覆盖 {len(seen)} 道题（修复前恒为 10）  ✅")


def check_blank_submit_hides_answers() -> None:
    """FIX-20：交白卷不得拿到答案与解析。"""
    target = make_bank("audit-blank", 10)
    client = user_client()
    session = start(client, target["slug"])
    result = submit(client, session["token"])

    assert result["unanswered_count"] == 10
    assert result["points_awarded"] == 0, "完成率为 0 不应产生积分"
    for item in result["items"]:
        assert item["revealed"] is False
        assert item["correct_answer"] == [], "未作答的题不得下发正确答案"
        assert item["correct_text"] == ""
        assert item["explanation_html"] == "", "未作答的题不得下发解析"

    # 只答 1 题：只有这道题给答案，其余仍隐藏
    session = start(client, target["slug"])
    first = session["questions"][0]
    answer(client, session["token"], first["id"])
    result = submit(client, session["token"])
    revealed = [item for item in result["items"] if item["revealed"]]
    hidden = [item for item in result["items"] if not item["revealed"]]
    assert len(revealed) == 1 and len(hidden) == 9
    assert revealed[0]["correct_answer"] == ["A"]
    assert revealed[0]["explanation_html"], "已作答的题应保留解析"
    print("2) 交白卷 0 条答案/解析，只答 1 题仅该题可见答案  ✅")


def rankings_row(uid: int) -> dict:
    key = practice_bank._now()[:10]
    conn = models.get_conn()
    try:
        row = conn.execute(
            "SELECT points, quiz_count, best_score FROM rankings "
            "WHERE period_type = 'day' AND period_key = ? AND user_id = ?",
            (key, int(uid)),
        ).fetchone()
        return dict(row) if row else {"points": 0, "quiz_count": 0, "best_score": 0}
    finally:
        conn.close()


def check_exam_points_and_idempotency() -> None:
    """FIX-17：模拟考计分入榜，同题库同日不叠加，重复结算不重复加分。"""
    target = make_bank("audit-points", 10)
    client = user_client()
    uid = user_id()

    session = start(client, target["slug"])
    for question in session["questions"][:8]:  # 80% 完成率
        answer(client, session["token"], question["id"])
    result = submit(client, session["token"])
    assert result["correct"] == 8 and result["score"] == 80.0
    assert result["points_awarded"] == 160, result["points_awarded"]

    row = rankings_row(uid)
    assert row["points"] == 160 and row["best_score"] == 80, row
    assert bank.user_stats(uid)["points"] >= 160
    assert bank.user_stats(uid)["exam_times"] >= 1

    # 重复结算（模拟双击交卷 / 超时回收撞车）：拿一份「还没交卷」的旧快照再结算
    conn = models.get_conn()
    try:
        stale = dict(conn.execute(
            "SELECT * FROM bank_practice_sessions WHERE token = ?", (session["token"],)
        ).fetchone())
    finally:
        conn.close()
    stale["status"] = "ongoing"
    practice_bank.finalize(stale)
    assert rankings_row(uid)["points"] == 160, "重复结算不得重复加分"

    # 同一天再认真做一遍（全对）：分数更高，但当天积分不再叠加
    again = start(client, target["slug"])
    for question in again["questions"]:
        answer(client, again["token"], question["id"])
    result2 = submit(client, again["token"])
    assert result2["score"] == 100.0
    assert result2["points_awarded"] == 0, "同一题库同日第二场不应再加分"
    assert rankings_row(uid)["points"] == 160

    # 完成率不足 60%：不计分
    half = make_bank("audit-half", 10)
    session = start(client, half["slug"])
    for question in session["questions"][:5]:
        answer(client, session["token"], question["id"])
    result3 = submit(client, session["token"])
    assert result3["score"] == 50.0 and result3["points_awarded"] == 0
    print("3) 模拟考 80 分入账 160 积分；同日重复/重复结算/完成率不足均不加分  ✅")


def check_sweep_expired_sessions() -> None:
    """FIX-18：超时场次可被全局回收，且重复执行幂等。"""
    target = make_bank("audit-sweep", 5, duration=1)
    tokens = [practice_bank.start_session(user_id(), target, restart=True)["token"]
              for _ in range(3)]
    conn = models.get_conn()
    try:
        marks = ",".join("?" for _ in tokens)
        conn.execute(
            f"UPDATE bank_practice_sessions SET deadline_at = '2000-01-01 00:00:00' "
            f"WHERE token IN ({marks})",
            tuple(tokens),
        )
        conn.commit()
    finally:
        conn.close()

    assert practice_bank.sweep_expired() == 3
    assert practice_bank.sweep_expired() == 0, "重复回收必须幂等"

    conn = models.get_conn()
    try:
        marks = ",".join("?" for _ in tokens)
        rows = conn.execute(
            f"SELECT status FROM bank_practice_sessions WHERE token IN ({marks})",
            tuple(tokens),
        ).fetchall()
    finally:
        conn.close()
    assert [r["status"] for r in rows] == ["submitted"] * 3
    print("4) 3 场超时练习被全局回收为 submitted，再扫一次返回 0  ✅")


def main() -> None:
    setup()
    check_sampling_covers_whole_bank()
    check_blank_submit_hides_answers()
    check_exam_points_and_idempotency()
    check_sweep_expired_sessions()
    print("\n全部通过：FIX-16 / FIX-17 / FIX-18 / FIX-20 回归  ✅")


if __name__ == "__main__":
    main()
