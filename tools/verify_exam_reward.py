"""线上 MySQL 验证：模拟考积分 / 交卷不泄题 / 抽题顺序（跑完自动清理）。

用法（容器内）：
    PYTHONPATH=/app python /tmp/verify_exam_reward.py
"""
from __future__ import annotations

import models
import auth
import bank
import practice_bank

PROBE = "__audit_probe__"


def cleanup(conn, uid: int = 0) -> None:
    conn.execute("DELETE FROM rankings WHERE user_id IN "
                 "(SELECT id FROM users WHERE username = ?)", (PROBE,))
    conn.execute("DELETE FROM users WHERE username = ?", (PROBE,))
    conn.commit()


def main() -> None:
    conn = models.get_conn()
    try:
        cleanup(conn)
        uid = models.create_user(PROBE, auth.hash_password("ProbePass123"))
        print("probe user id =", uid)

        target = practice_bank.get_bank("vpc-architecture")
        assert target, "缺少 vpc-architecture 题库"
        print("bank:", target["slug"], target["total_questions"], "题")

        # 第一场：答 80% 且全对 → 正确率 80%，应入账 160 分
        session = practice_bank.start_session(uid, target, restart=True)
        ids = [q["id"] for q in session["questions"]]
        picked = ids[: max(1, int(len(ids) * 0.8))]
        for qid in picked:
            row = conn.execute("SELECT answer FROM bank_questions WHERE id = ?", (qid,)).fetchone()
            practice_bank.save_answer(uid, session["token"], qid, row["answer"])
        result = practice_bank.submit_session(uid, session["token"])
        conn.rollback()  # 脚本连接从开头就持有旧快照，读榜单前先结束它
        payload = result["result"]
        print("score=%.1f accuracy=%.1f points=%s revealed=%s" % (
            payload["score"], payload["accuracy"], payload["points_awarded"],
            sum(1 for i in payload["items"] if i["revealed"]),
        ))
        assert payload["points_awarded"] == 160, payload["points_awarded"]

        row = conn.execute(
            "SELECT points, quiz_count, best_score FROM rankings "
            "WHERE period_type='day' AND user_id = ?", (uid,)).fetchone()
        print("day ranking:", dict(row))
        assert int(row["points"]) == 160 and int(row["best_score"]) == 80
        assert bank.user_stats(uid)["points"] == 160

        # 第二场同日同题库：不再加分
        session2 = practice_bank.start_session(uid, target, restart=True)
        for q in session2["questions"]:
            a = conn.execute("SELECT answer FROM bank_questions WHERE id = ?", (q["id"],)).fetchone()
            practice_bank.save_answer(uid, session2["token"], q["id"], a["answer"])
        result2 = practice_bank.submit_session(uid, session2["token"])
        conn.rollback()
        print("second attempt points =", result2["result"]["points_awarded"])
        assert result2["result"]["points_awarded"] == 0
        row = conn.execute(
            "SELECT points FROM rankings WHERE period_type='day' AND user_id = ?", (uid,)).fetchone()
        assert int(row["points"]) == 160

        # 交白卷：不泄答案
        blank = practice_bank.start_session(uid, target, restart=True)
        blank_result = practice_bank.submit_session(uid, blank["token"])["result"]
        assert blank_result["points_awarded"] == 0
        assert all(not i["revealed"] and not i["correct_answer"] for i in blank_result["items"])
        print("blank submit: all answers hidden ✅")

        # 抽题顺序：连续开考覆盖不同题号
        seen = set()
        for _ in range(6):
            s = practice_bank.start_session(uid, target, restart=True)
            seen.update(q["id"] for q in s["questions"])
        print("6 次开考覆盖题号数 =", len(seen), "/", target["total_questions"])
        assert len(seen) > target["total_questions"] * 0.8

        cleanup(conn, uid)
        left = conn.execute("SELECT COUNT(*) AS c FROM users WHERE username = ?", (PROBE,)).fetchone()
        print("cleanup ok, probe users left =", int(left["c"]))
        print("LIVE PASS")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
