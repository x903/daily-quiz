"""冒烟测试：覆盖解析、导入、判分、错题、导出、用户隔离等核心逻辑。

注意：为避免污染真实数据，测试使用独立日期 2030-01-01 与独立测试账号。
"""
from pathlib import Path
from werkzeug.datastructures import MultiDict

import auth
import models
import parser as md_parser
import quiz_logic
import score_manager
import wrong_book

BASE = Path(__file__).resolve().parent

TEST_DATE = "2030-01-01"


def _ensure_test_user() -> int:
    """确保存在测试用户，返回其 id。"""
    models.init_db()
    auth.create_default_users()
    ok, _ = auth.register("smoketest", "smoke123", role="user")
    u = models.get_user_by_username("smoketest")
    return u["id"]


def test_parse_good():
    text = (BASE / "sample_questions.md").read_text(encoding="utf-8")
    r = md_parser.parse_markdown(text)
    print("[解析·正常] 套数=%d 题数=%d 分布=%s 失败=%d" %
          (r.total_sets, r.total_questions, r.type_distribution(), len(r.failed)))
    assert r.total_sets == 6 and r.total_questions == 6 and not r.failed


def test_parse_bad():
    text = (BASE / "sample_with_errors.md").read_text(encoding="utf-8")
    r = md_parser.parse_markdown(text)
    print("[解析·异常] 成功题数=%d 失败题数=%d" % (r.total_questions, len(r.failed)))
    for f in r.failed:
        print("   - 第%d套 %s: %s | %s" % (f["set_index"], f["type"], f["question"], "；".join(f["errors"])))
    assert r.total_questions == 1 and len(r.failed) == 4


def test_import_and_grade(user_id: int):
    text = (BASE / "sample_questions.md").read_text(encoding="utf-8")
    r = md_parser.parse_markdown(text)
    res = quiz_logic.import_day(TEST_DATE, f"{TEST_DATE} 每日答题", r, overwrite=True)
    print("[导入] ok=%s set_id=%s 失败=%d" % (res["ok"], res["set_id"], len(res["failed"])))
    assert res["ok"]

    data = quiz_logic.get_quiz_data(TEST_DATE)
    form = MultiDict()
    # 第1题答对(B)，其余故意答错(A)
    first_qid = data["sets"][0]["questions"][0]["id"]
    for st in data["sets"]:
        for q in st["questions"]:
            form.add(f"q_{q['id']}", "B" if q["id"] == first_qid else "A")
    result = quiz_logic.grade_and_save(TEST_DATE, user_id, form, duration=42)
    print("[判分] total=%d correct=%d wrong=%d acc=%s dur=%ds" %
          (result["total"], result["correct"], result["wrong"], result["accuracy"], result["duration"]))
    assert result["wrong"] >= 1

    rec = score_manager.get_record_by_set_user(res["set_id"], user_id)
    print("[成绩] 已落库 record_id=%s" % rec["id"])
    assert rec and rec["duration"] == 42 and rec["user_id"] == user_id

    wrongs = wrong_book.get_wrong_by_date(TEST_DATE, user_id)
    print("[错题] 数量=%d" % len(wrongs))
    assert len(wrongs) == result["wrong"]
    # 用户隔离：另一用户不应看到本题错题
    others = wrong_book.get_all_wrong(user_id=user_id + 999)
    assert others == []


def test_export_dict():
    s = models.get_daily_set_by_date(TEST_DATE)
    rows = models.get_all_questions_by_set(s["id"])
    print("[导出] 题目条数=%d，首题=%s" % (len(rows), rows[0]["question"][:20]))
    assert len(rows) == 6


def test_overwrite_guard():
    r = md_parser.parse_markdown("第1套\n单选题\n题目？\nA. a\nB. b\n答案：A")
    res = quiz_logic.import_day(TEST_DATE, "x", r, overwrite=False)
    print("[覆盖保护] ok=%s reason=%s" % (res["ok"], res.get("reason")))
    assert not res["ok"] and res.get("reason") == "exists"


def test_auth():
    # 两套账号分别校验，互不串表
    assert auth.authenticate_admin("admin", "admin123") is not None
    assert auth.authenticate_admin("admin", "wrong") is None
    assert auth.authenticate_user("user", "user123") is not None
    assert auth.authenticate_user("admin", "admin123") is None, "管理员不能从用户端登录"
    assert auth.authenticate_admin("user", "user123") is None, "普通用户不能登管理端"
    # 重复运行脚本时不因历史用户而失败：已存在则只验证重名被拒绝
    if not models.get_user_by_username("dup_user"):
        ok, msg = auth.register("dup_user", "123456")
        assert ok, msg
    ok2, msg2 = auth.register("dup_user", "123456")
    assert not ok2 and "已被注册" in msg2, f"重名应被拒绝，实际：{msg2}"


if __name__ == "__main__":
    uid = _ensure_test_user()
    models.init_db()
    test_parse_good()
    test_parse_bad()
    test_import_and_grade(uid)
    test_export_dict()
    test_overwrite_guard()
    test_auth()
    print("\n✅ 全部冒烟测试通过")
