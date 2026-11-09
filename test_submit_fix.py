"""回归测试：修复「提交答题 500 / FOREIGN KEY constraint failed」。

全程使用独立临时数据库，不会触碰正在运行的 data/quiz.db，
因此可以在 Flask 服务仍在运行时安全执行。

覆盖：
1. 正常用户提交答题（含错题）成功，成绩与错题按 user_id 正确落库；
2. 陈旧 session（数据库重建后残留的孤儿 user_id）被自动识别并引导重新登录；
3. 直接以孤儿 user_id 调用判分时抛出清晰异常，而不是数据库 IntegrityError；
4. 判分写入为单事务：中途失败时错题本与成绩整体回滚，不留半写数据；
5. 满分提交不产生错题；错题重练可正常判分。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

# 必须在 import app 之前重定向数据库，因为 app.py 在模块加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_submit_fix_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

import auth  # noqa: E402
import parser as md_parser  # noqa: E402
import quiz_logic  # noqa: E402
import score_manager  # noqa: E402
import wrong_book  # noqa: E402

DATE = "2026-11-04"


def setup_database() -> list[dict]:
    """在临时库中建表、播种账号并导入 2026-11-04 的 20 道题。"""
    models.init_db()
    auth.create_default_users()
    src = Path(__file__).resolve().parent / "data" / "ACP模拟题20道_2026-11-04.md"
    parsed = md_parser.parse_markdown(src.read_text(encoding="utf-8"))
    assert parsed.total_questions == 20, "示例题库应解析出 20 道题"
    quiz_logic.import_day(DATE, "提交修复测试题库", parsed, overwrite=True)
    conn = models.get_conn()
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, answer FROM questions WHERE daily_set_id = "
            "(SELECT id FROM daily_sets WHERE date = ?) ORDER BY set_index, id",
            (DATE,),
        )
    ]
    conn.close()
    return rows


def _answer_form(questions: list[dict], wrong_ids: set[int] | None = None) -> dict:
    """构造提交表单：默认全部答对，wrong_ids 中的题故意答错。"""
    wrong_ids = wrong_ids or set()
    form: dict = {"duration": "120"}
    for q in questions:
        correct = json.loads(q["answer"])
        if q["id"] in wrong_ids:
            wrong_letter = "A" if correct != ["A"] else "B"
            form[f"q_{q['id']}"] = [wrong_letter]
        else:
            form[f"q_{q['id']}"] = list(correct)
    return form


def _login(client, username: str = "user", password: str = "user123"):
    import test_support  # 延迟导入：app 需在 DB_PATH 重定向之后再加载
    return client.post(
        "/login",
        data={"username": username, "password": password,
              **test_support.captcha_fields()},
    )


def main() -> None:
    questions = setup_database()
    from app import app  # noqa: PLC0415  需在 DB_PATH 重定向之后导入

    app.config["TESTING"] = True
    user = models.get_user_by_username("user")
    assert user is not None
    uid = user["id"]

    # ---- 1. 正常提交（故意答错 2 题）----
    client = app.test_client()
    assert _login(client).status_code == 302
    wrong_two = {questions[0]["id"], questions[1]["id"]}
    resp = client.post(f"/quiz/{DATE}", data=_answer_form(questions, wrong_two))
    assert resp.status_code == 302, f"提交应重定向，实际 {resp.status_code}"
    loc = resp.headers["Location"]
    assert "/result/" in loc, loc
    assert client.get(loc).status_code == 200
    rec = score_manager.get_record_by_set_user(
        models.get_daily_set_by_date(DATE)["id"], uid
    )
    assert rec and rec["total"] == 20 and rec["correct"] == 18 and rec["wrong"] == 2
    assert rec["user_id"] == uid
    wrongs = wrong_book.get_wrong_by_date(DATE, uid)
    assert len(wrongs) == 2, f"应有 2 条错题，实际 {len(wrongs)}"
    assert all(w["explanation"] for w in wrongs), "错题应带解析字段"
    print("1) 正常提交成功：20 题 / 18 对 / 2 错，成绩与错题归属正确 ✅")

    # ---- 2. 陈旧 session（孤儿 user_id）被拦截，不再 500 ----
    ghost = app.test_client()
    with ghost.session_transaction() as sess:
        sess["user_id"] = 9999  # 模拟数据库重建前签发的旧 cookie
        sess["username"] = "ghost"
        sess["role"] = "user"
    r = ghost.get("/")
    assert r.status_code == 200, "首页已公开，陈旧 session 不影响游客浏览"
    home = r.get_data(as_text=True)
    assert "浏览题库无需登录" in home or "登录后答题" in home, "首页应说明浏览与答题的登录边界"
    with ghost.session_transaction() as sess:
        assert not sess.get("user_id"), "失效的登录态应被清空"
    r = ghost.post(f"/quiz/{DATE}", data=_answer_form(questions))
    assert r.status_code == 302 and "/login" in r.headers["Location"], (
        f"陈旧 session 提交应跳转登录页，实际 {r.status_code} {r.headers.get('Location')}"
    )
    print("2) 陈旧 session 自动失效并被引导重新登录，提交不再 500 ✅")

    # ---- 3. 孤儿 user_id 直接调用判分 → 清晰 ValueError ----
    try:
        quiz_logic.grade_and_save(DATE, 9999, _answer_form(questions), 10)
        raise AssertionError("应以 ValueError 拒绝孤儿 user_id")
    except ValueError as exc:
        assert "登录状态已失效" in str(exc)
    print("3) 孤儿 user_id 提前拦截为可读异常，不触发外键 IntegrityError ✅")

    # ---- 4. 单事务：写成绩失败时，错题本与成绩整体回滚 ----
    original = score_manager.save_record

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟写成绩失败")

    score_manager.save_record = _boom
    try:
        quiz_logic.grade_and_save(DATE, uid, _answer_form(questions), 30)
        raise AssertionError("模拟失败应抛出异常")
    except RuntimeError:
        pass
    finally:
        score_manager.save_record = original

    rec_after = score_manager.get_record_by_set_user(
        models.get_daily_set_by_date(DATE)["id"], uid
    )
    wrongs_after = wrong_book.get_wrong_by_date(DATE, uid)
    assert rec_after["correct"] == 18, "成绩不应被半写入（应回滚）"
    assert len(wrongs_after) == 2, "旧错题不应被清空（应回滚）"
    print("4) 单事务回滚验证通过：中途失败不留下半写数据 ✅")

    # ---- 5. 再次提交满分 + 错题重练 ----
    resp = client.post(f"/quiz/{DATE}", data=_answer_form(questions))
    assert resp.status_code == 302 and "/result/" in resp.headers["Location"]
    rec2 = score_manager.get_record_by_set_user(
        models.get_daily_set_by_date(DATE)["id"], uid
    )
    assert rec2["correct"] == 20 and rec2["accuracy"] == 100.0
    assert wrong_book.get_wrong_by_date(DATE, uid) == [], "满分后错题应清空"

    # 制造 1 条错题再重练
    client.post(f"/quiz/{DATE}", data=_answer_form(questions, {questions[3]["id"]}))
    assert len(wrong_book.get_wrong_by_date(DATE, uid)) == 1
    practice_form = {
        f"q_{questions[3]['id']}": list(json.loads(questions[3]["answer"]))
    }
    pr = client.post(f"/wrong/{DATE}/practice", data=practice_form)
    assert pr.status_code == 302
    assert wrong_book.get_wrong_by_date(DATE, uid) == [], "重练答对后应移出错题本"
    print("5) 满分提交无错题、错题重练判分正常 ✅")

    print("\n全部通过：提交答题 500 已修复。")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
