"""回归测试：管理员界面的题库「修改（含标题）」与「删除」功能。

全程使用独立临时数据库，不触碰正在运行的 data/quiz.db。

覆盖：
1. 未登录 / 普通用户对编辑页与删除接口的越权拦截；
2. 编辑页渲染（标题、日期、逐题字段）与后台列表统计；
3. 修改标题、题干、选项、答案、解析后正确落库，其他题目不受影响；
4. 校验失败（空标题 / 非法日期 / 答案字母不存在 / 题型不符 / 日期冲突 /
   删光所有题）不写入任何数据，且连带修改一起被拒绝（原子性）；
5. 修改日期、删除单题后的题数重算、错题级联清理与成绩保留；
6. 删除确认页、错误确认不删除、正确确认后的整库级联删除。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

# 必须在 import app 之前重定向数据库：app.py 在模块加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_admin_edit_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

import auth  # noqa: E402
import parser as md_parser  # noqa: E402
import quiz_logic  # noqa: E402
import score_manager  # noqa: E402
import wrong_book  # noqa: E402

DATE = "2026-11-04"
OTHER_DATE = "2026-11-03"   # 用于日期冲突校验
NEW_DATE = "2026-11-06"     # 编辑时改成的新日期
ORIGINAL_TITLE = "ACP 每日模拟题（原始标题）"
NEW_TITLE = "ACP 每日模拟题（已编辑标题）"


def setup_database() -> None:
    """临时库建表、播种账号并导入两份题库（含 1 条成绩与 2 条错题）。"""
    models.init_db()
    auth.create_default_users()
    src = Path(__file__).resolve().parent / "data" / "ACP模拟题20道_2026-11-04.md"
    parsed = md_parser.parse_markdown(src.read_text(encoding="utf-8"))
    assert parsed.total_questions == 20, "示例题库应解析出 20 道题"
    quiz_logic.import_day(DATE, ORIGINAL_TITLE, parsed, overwrite=True)
    quiz_logic.import_day(OTHER_DATE, "另一个题库（用于日期冲突）", parsed, overwrite=True)


def _questions(set_date: str) -> list[dict]:
    s = models.get_daily_set_by_date(set_date)
    assert s is not None
    return [dict(r) for r in models.get_all_questions_by_set(s["id"])]


def _edit_form(questions: list[dict], title: str, set_date: str) -> dict:
    """按编辑页字段构造完整表单（必须包含题库中所有题目）。"""
    form: dict = {"set_title": title, "set_date": set_date}
    for q in questions:
        qid = q["id"]
        form[f"q_{qid}_type"] = q["type"]
        form[f"q_{qid}_question"] = q["question"]
        form[f"q_{qid}_answer"] = "".join(json.loads(q["answer"]))
        form[f"q_{qid}_explanation"] = q["explanation"]
        for letter, text in json.loads(q["options"]).items():
            form[f"q_{qid}_option_{letter}"] = text
    return form


def _login(client, username: str, password: str):
    import test_support
    # 用例会用管理员身份顺带访问用户端回顾页，因此同时挂上普通用户会话
    return test_support.login(client, username, password, with_user_session=True)


def _counts() -> dict:
    conn = models.get_conn()
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("daily_sets", "questions", "daily_records", "wrong_questions")
        }
    finally:
        conn.close()


def main() -> None:
    setup_database()
    from app import app  # noqa: PLC0415  需在 DB_PATH 重定向之后导入

    app.config["TESTING"] = True
    questions = _questions(DATE)
    assert len(questions) == 20
    q1 = questions[0]
    uid = models.get_user_by_username("user")["id"]

    # 制造 1 条成绩 + 2 条错题，验证编辑/删除对历史数据的影响
    user_client = app.test_client()
    assert _login(user_client, "user", "user123").status_code == 302
    answer_form: dict = {"duration": "60"}
    for i, q in enumerate(questions):
        correct = json.loads(q["answer"])[0]
        answer_form[f"q_{q['id']}"] = ("A" if correct != "A" else "B") if i < 2 else correct
    assert user_client.post(f"/quiz/{DATE}", data=answer_form).status_code == 302
    assert len(wrong_book.get_wrong_by_date(DATE, uid)) == 2, "应先产生 2 条错题"
    print("0) 准备完成：临时库含 2 个题库、20 题/库、1 条成绩、2 条错题 ✅")

    # ---- 1. 权限拦截 ----
    anon = app.test_client()
    assert anon.get(f"/admin/set/{DATE}/edit").status_code == 302
    assert anon.post(
        f"/admin/set/{DATE}/delete", data={"confirm_text": DATE}
    ).status_code == 302
    assert user_client.get(f"/admin/set/{DATE}/edit").status_code == 302
    assert user_client.get(f"/admin/set/{DATE}/delete").status_code == 302
    assert user_client.post(
        f"/admin/set/{DATE}/delete", data={"confirm_text": DATE}
    ).status_code == 302
    assert models.get_daily_set_by_date(DATE) is not None, "越权删除不应生效"
    print("1) 未登录 / 普通用户的编辑与删除请求均被拦截，数据未变 ✅")

    admin = app.test_client()
    assert _login(admin, "admin", "admin123").status_code == 302

    # ---- 2. 后台列表 + 编辑页渲染 ----
    dashboard = admin.get("/admin")
    assert dashboard.status_code == 200
    dash_html = dashboard.get_data(as_text=True)
    assert "管理控制台" in dash_html
    # 「排行榜」在管理端是「排行榜数据」，不能用子串排除，改用用户端独有文案
    for gone in ("首页", "每日答题", "个人中心", "错题本", "阿里云认证", "导入题目", "后台管理"):
        assert gone not in dash_html, f"管理后台不应出现用户端菜单：{gone}"
    api_sets = admin.get("/api/admin/sets")
    assert api_sets.status_code == 200 and api_sets.get_json()["ok"]
    assert ORIGINAL_TITLE in str(api_sets.get_json()["data"])
    edit_page = admin.get(f"/admin/set/{DATE}/edit")
    assert edit_page.status_code == 200
    edit_html = edit_page.get_data(as_text=True)
    assert 'name="set_title"' in edit_html and 'name="set_date"' in edit_html
    assert ORIGINAL_TITLE in edit_html
    assert f'name="q_{q1["id"]}_question"' in edit_html
    assert f'name="q_{q1["id"]}_explanation"' in edit_html
    print("2) 后台列表与编辑页正常渲染（标题、日期、题干、解析字段齐全） ✅")

    # ---- 3. 修改标题 + 第 1 题内容 ----
    form = _edit_form(questions, NEW_TITLE, DATE)
    form[f"q_{q1['id']}_question"] = "【已修改】这是编辑后的题干"
    form[f"q_{q1['id']}_option_A"] = "【已修改】选项 A 内容"
    new_answer = "B" if json.loads(q1["answer"]) == ["A"] else "A"
    form[f"q_{q1['id']}_answer"] = new_answer
    form[f"q_{q1['id']}_explanation"] = "- **考点**：编辑功能验证\n- **易错点**：答案随题干同步修改"
    resp = admin.post(f"/admin/set/{DATE}/edit", data=form)
    assert resp.status_code == 302, f"保存应重定向，实际 {resp.status_code}"
    s = models.get_daily_set_by_date(DATE)
    assert s["title"] == NEW_TITLE and s["total_sets"] == 20
    updated_q1 = {r["id"]: dict(r) for r in models.get_all_questions_by_set(s["id"])}[q1["id"]]
    assert updated_q1["question"] == "【已修改】这是编辑后的题干"
    assert json.loads(updated_q1["answer"]) == [new_answer]
    assert json.loads(updated_q1["options"])["A"] == "【已修改】选项 A 内容"
    assert "编辑功能验证" in updated_q1["explanation"]
    others = [q for q in _questions(DATE) if q["id"] != q1["id"]]
    assert all(q["question"] == before["question"] for q, before in zip(others, questions[1:]))
    assert NEW_TITLE in admin.get(f"/date/{DATE}").get_data(as_text=True), "用户端回顾页应显示新标题"
    print("3) 标题、题干、选项、答案、解析修改成功且其他 19 题不受影响 ✅")

    # ---- 4. 校验失败必须整体不落库 ----
    current = _questions(DATE)

    def _reject(mutate, label: str) -> None:
        bad = _edit_form(current, NEW_TITLE, DATE)
        mutate(bad)
        before = _counts()
        r = admin.post(f"/admin/set/{DATE}/edit", data=bad)
        assert r.status_code == 200, f"{label} 应留在编辑页，实际 {r.status_code}"
        assert _counts() == before, f"{label} 不应写入任何数据"
        assert models.get_daily_set_by_date(DATE)["title"] == NEW_TITLE

    _reject(lambda f: f.update(set_title=""), "空标题")
    _reject(lambda f: f.update(set_date="2026-13-99"), "非法日期")
    _reject(lambda f: f.update(set_date=OTHER_DATE), "日期冲突")
    _reject(lambda f: f.update({f"q_{q1['id']}_answer": "Z"}), "答案字母无对应选项")
    _reject(lambda f: f.update({f"q_{q1['id']}_type": "multiple"}), "多选题却只有 1 个答案")
    _reject(
        lambda f: f.update({f"q_{q1['id']}_option_{new_answer}": ""}),
        "选项留空但答案引用它",
    )
    _reject(
        lambda f: f.update({f"q_{q['id']}_delete": "1" for q in current}),
        "删光所有题目",
    )
    # 原子性：连带把标题改成新值，但某题校验失败时标题也不得生效
    atomic = _edit_form(current, "不该生效的标题", DATE)
    atomic[f"q_{current[5]['id']}_question"] = ""
    before = _counts()
    assert admin.post(f"/admin/set/{DATE}/edit", data=atomic).status_code == 200
    assert models.get_daily_set_by_date(DATE)["title"] == NEW_TITLE
    assert _counts() == before
    print("4) 7 类校验失败均被拒绝且不写入任何数据（含连带修改的原子性） ✅")

    # ---- 5. 修改日期 + 删除单题 ----
    form = _edit_form(current, NEW_TITLE, NEW_DATE)
    form[f"q_{q1['id']}_delete"] = "1"
    resp = admin.post(f"/admin/set/{DATE}/edit", data=form)
    assert resp.status_code == 302 and NEW_DATE in resp.headers["Location"]
    assert models.get_daily_set_by_date(DATE) is None, "旧日期应不再存在"
    s2 = models.get_daily_set_by_date(NEW_DATE)
    assert s2["id"] == s["id"] and s2["title"] == NEW_TITLE
    assert s2["total_sets"] == 19 and len(_questions(NEW_DATE)) == 19
    assert all(w["question_id"] != q1["id"] for w in wrong_book.get_all_wrong(user_id=None))
    assert len(wrong_book.get_all_wrong(user_id=None)) == 1, "被删题目的错题应级联清理"
    assert len(score_manager.list_all_records()) == 1, "成绩记录应保留（历史快照）"
    assert admin.get(f"/admin/set/{NEW_DATE}/edit").status_code == 200
    print("5) 日期修改成功、单题删除后题数重算、错题级联清理、成绩保留 ✅")

    # ---- 6. 删除确认页与整库级联删除 ----
    confirm_page = admin.get(f"/admin/set/{NEW_DATE}/delete")
    assert confirm_page.status_code == 200
    html = confirm_page.get_data(as_text=True)
    assert NEW_TITLE in html and 'name="confirm_text"' in html and ">19<" in html
    assert admin.post(
        f"/admin/set/{NEW_DATE}/delete", data={}
    ).status_code == 200, "缺少确认输入应退回确认页"
    assert admin.post(
        f"/admin/set/{NEW_DATE}/delete", data={"confirm_text": "2026-11-01"}
    ).status_code == 200, "确认文本不匹配不应删除"
    assert models.get_daily_set_by_date(NEW_DATE) is not None

    before = _counts()
    resp = admin.post(f"/admin/set/{NEW_DATE}/delete", data={"confirm_text": NEW_DATE})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/dashboard")
    assert models.get_daily_set_by_date(NEW_DATE) is None
    assert len(models.list_sets_with_stats()) == 1, "只应剩下另一个题库"
    after = _counts()
    assert after["daily_sets"] == before["daily_sets"] - 1
    assert after["questions"] == before["questions"] - 19
    assert after["daily_records"] == 0 and after["wrong_questions"] == 0
    assert admin.get("/admin").status_code == 200
    print("6) 删除确认页展示影响范围；未确认/错确认不删；确认后整库级联删除 ✅")

    # ---- 7. 单选 / 多选 / 判断题的编辑往返（只改标题，题目内容不得被破坏）----
    sample = Path(__file__).resolve().parent / "sample_questions.md"
    parsed = md_parser.parse_markdown(sample.read_text(encoding="utf-8"))
    assert parsed.total_questions == 6, "样例应解析出 6 道题"
    quiz_logic.import_day("2026-10-01", "题型兼容验证", parsed, overwrite=True)
    mixed = _questions("2026-10-01")
    assert {q["type"] for q in mixed} >= {"single", "multiple", "judge"}
    assert admin.get("/admin/set/2026-10-01/edit").status_code == 200
    mixed_form = _edit_form(mixed, "题型兼容验证（已改标题）", "2026-10-01")
    assert admin.post("/admin/set/2026-10-01/edit", data=mixed_form).status_code == 302
    after_mixed = _questions("2026-10-01")
    assert models.get_daily_set_by_date("2026-10-01")["title"] == "题型兼容验证（已改标题）"
    for before_q, after_q in zip(mixed, after_mixed):
        assert before_q["type"] == after_q["type"]
        assert json.loads(before_q["options"]) == json.loads(after_q["options"])
        assert json.loads(before_q["answer"]) == json.loads(after_q["answer"]), (
            f"题型 {before_q['type']} 的答案不应在编辑往返中丢失"
        )
    print("7) 单选 / 多选 / 判断题编辑往返无损（选项与答案完整保留） ✅")

    print("\n全部通过：管理员题库编辑（含标题）与删除功能验证完成。")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
