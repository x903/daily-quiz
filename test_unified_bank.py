"""回归测试：题库统一为「模拟题库」一条数据流。

覆盖：
1. 后台导入一份试卷后，自动生成一个已发布的模拟题库（题量与试卷一致）；
2. 每日答题优先从模拟题库抽 10 题（source=bank），不再依赖旧 questions 表；
3. 判分按题源查对应表，全对 100 分；
4. 只有旧题库、没有模拟题库的分类自动回退（source=legacy），仍能答题；
5. 首页统计与认证树同口径，双写不重复计数。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_unified_"))
models.DB_PATH = _TMP_DIR / "quiz.db"
models.init_db()

import bank  # noqa: E402
import practice_bank  # noqa: E402
import quiz_service  # noqa: E402
import web_site  # noqa: E402

PAPER = Path(__file__).resolve().parent / "data" / "ACP云计算高级工程师_标准模拟卷100题_2026-11-08.md"


_USER_SEQ = 0


def _create_user() -> int:
    global _USER_SEQ
    _USER_SEQ += 1
    return models.create_user(f"unified_tester_{_USER_SEQ}", "not-a-real-hash", "user")


def _grade(category_id: int, start: dict, lookup) -> dict:
    from flask import Flask, session

    qids = [q["id"] for q in start["questions"]]
    truth = lookup(qids)
    answers = {str(qid): truth[qid]["answer_list"] for qid in qids}
    app = Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context("/"):
        session["user_id"] = _create_user()
        return quiz_service.grade(
            category_id, qids, answers, duration=30, source=start["source"],
        )


def main() -> None:
    text = PAPER.read_text(encoding="utf-8")
    cat = bank.get_category_by_path("aliyun/acp/cloud")
    assert cat, "内置认证分类应存在"
    cat_id = int(cat["id"])

    # ---------------------------------------------------------------- 1) 导入即建模拟题库
    imported = bank.import_questions(text, cat_id, fmt="markdown")
    assert imported["imported"] == 100, imported

    mirror = practice_bank.import_paper_as_bank(
        cat, text, fmt="markdown", filename=PAPER.name,
    )
    assert mirror["created"] and mirror["inserted"] == 100, mirror
    bank_row = mirror["bank"]
    assert bank_row["status"] == "published", "导入后应直接发布"
    assert not mirror["publish_errors"], mirror["publish_errors"]
    assert practice_bank.count_bank_questions(cat_id) == 100, "模拟题库题量应为 100"

    listed = practice_bank.list_banks(cert_path=cat["path_slug"])
    assert len(listed) == 1 and listed[0]["name"].startswith("ACP云计算高级工程师"), listed

    # 同一份文件重复导入：替换旧的自动题库，而不是越堆越多
    again = practice_bank.import_paper_as_bank(
        cat, text, fmt="markdown", filename=PAPER.name,
    )
    assert again["created"] and again["inserted"] == 100, again
    assert practice_bank.count_bank_questions(cat_id) == 100, "重复导入不应翻倍"
    assert len(practice_bank.list_banks(cert_path=cat["path_slug"])) == 1, "同名自动题库应被替换"
    print("1) 后台导入自动生成并发布模拟题库，认证页可见；重复导入不堆积 ✅")

    # ---------------------------------------------------------------- 2) 每日答题从模拟题库抽题
    start = quiz_service.start(cat_id)
    assert start["source"] == "bank", start
    assert start["pool"] == "category" and start["pool_label"] == "本认证模拟题库"
    assert start["total"] == 10 and len(start["questions"]) == 10
    assert len({q["id"] for q in start["questions"]}) == 10, "抽题不得重复"
    for q in start["questions"]:
        assert "answer" not in q and "explanation" not in q, "答案与解析不得提前下发"
        assert q["question"] and len(q["options"]) >= 2
    print("2) 每日答题优先从模拟题库随机抽 10 题，且不下发答案 ✅")

    # ---------------------------------------------------------------- 3) 判分
    result = _grade(cat_id, start, practice_bank.questions_by_ids)
    assert result["score"] == 100 and result["correct"] == 10, result
    assert all(item["is_correct"] for item in result["review"])
    assert all(item["explanation"] for item in result["review"]), "复盘应带解析"
    print("3) 模拟题库题源判分正确（全对 100 分，逐题带解析）✅")

    # ---------------------------------------------------------------- 4) 旧题库回退
    conn = models.get_conn()
    try:
        row = conn.execute(
            "SELECT id, cert_name, path_slug FROM cert_categories WHERE id <> ? ORDER BY id LIMIT 1",
            (cat_id,),
        ).fetchone()
    finally:
        conn.close()
    legacy_id = int(row["id"])
    bank.import_questions(text, legacy_id, fmt="markdown")
    legacy_start = quiz_service.start(legacy_id)
    assert legacy_start["source"] == "legacy", legacy_start
    assert legacy_start["pool_label"] == "本认证题库"
    legacy_result = _grade(legacy_id, legacy_start, bank.questions_by_ids)
    assert legacy_result["score"] == 100, legacy_result
    print("4) 无模拟题库的分类自动回退旧题库，答题与判分正常 ✅")

    # ---------------------------------------------------------------- 5) 统计不重复计数
    paths = bank.counts_by_path()
    stats = web_site._platform_stats()
    assert stats["questions"] == sum(paths.values()), "首页与认证树题量口径应一致"
    assert stats["questions"] == 200, f"双写不得重复计数，期望 200，实际 {stats['questions']}"
    print("5) 首页统计与认证树同口径，导入双写不重复计数 ✅")

    print("\n全部通过：导入 → 模拟题库 → 每日答题抽 10 题 → 判分 全链路验证完成。")


if __name__ == "__main__":
    main()
