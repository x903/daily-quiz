"""回归测试：认证页内嵌题库 + 模拟答题（判分 / 超时 / 管理端约束）。

使用独立临时 SQLite 库，不触碰 data/quiz.db。直接运行：
    python test_practice_bank.py

覆盖：
1. 认证详情页只展示该认证的题库卡片，不泄漏题干、不串认证；
2. 卡片按用户进度渲染「已练 N/M / 错题 K / 继续练习」；
3. 开始练习返回 100 题且不下发答案，支持继续练习；
4. 逐题保存、单选/多选完全匹配判分、交卷后逐题解析；
5. 超时由服务端强制交卷，duration 按考试时长封顶；
6. 管理端题库创建：ACP 规则强制 100 题（70 单选 + 30 多选）；
7. 发布校验：题量不足禁止发布；补齐后可发布；停用后前端隐藏但成绩保留；
8. 管理端练习人次 / 平均分 / 合格率统计；考试规则可修改并影响发布校验；
9. 登录页不再展示默认演示账号。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_practice_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

import auth  # noqa: E402
import practice_bank  # noqa: E402
import seed_practice  # noqa: E402
import test_support  # noqa: E402
from app import app  # noqa: E402


def setup() -> None:
    models.init_db()
    auth.create_default_users()
    seed_practice.main()


def user_client():
    client = app.test_client()
    response = test_support.login(client, "user", "user123")
    assert response.status_code == 302, "普通用户登录失败"
    return client


def answer_of(question_id: int) -> list[str]:
    conn = models.get_conn()
    try:
        row = conn.execute(
            "SELECT answer FROM bank_questions WHERE id = ?", (int(question_id),)
        ).fetchone()
        return json.loads(row["answer"])
    finally:
        conn.close()


def bank_id(slug: str) -> int:
    row = practice_bank.get_bank(slug)
    assert row, f"题库 {slug} 不存在"
    return int(row["id"])


def check_page_and_seed() -> None:
    client = app.test_client()
    # 题库不再有独立目录页，直接嵌在对应认证详情页里
    page = client.get("/cert/aliyun/acp/cloud")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for text in ("模拟题库", "阿里云 ACP 云计算高级工程师 · 全真模拟卷", "ECS 弹性计算专项题库",
                 "VPC 网络架构模拟卷", "共 <b>100</b> 题", "70 单选 · 30 多选",
                 "120 分钟", "80 分合格", "开始练习", "尚未开始"):
        assert text in html, f"认证页题库区块缺少：{text}"
    assert "共 3 套 · 155 道题" in html, "认证页应汇总该认证的题库套数与总题量"
    # 认证页不得平铺题干
    conn = models.get_conn()
    stem = conn.execute("SELECT question FROM bank_questions LIMIT 1").fetchone()["question"]
    conn.close()
    assert stem[:24] not in html, "认证页不应平铺具体题目"
    assert "rel=\"canonical\"" in html

    # 其他认证不串库：没有题库时给空态
    other = client.get("/cert/aliyun/acp/llm").get_data(as_text=True)
    assert "暂时还没有模拟题库" in other, "无题库的认证页应展示空态"
    assert "ECS 弹性计算专项题库" not in other, "题库不能串到其他认证页"

    # 旧的独立目录页已下线，只保留 301 兼容旧书签
    legacy = client.get("/practice")
    assert legacy.status_code == 301 and legacy.headers["Location"].endswith("/")
    print("1) 认证详情页展示该认证的 3 套题库（155 题），不平铺题目、不串认证  ✅")

    rules = {r["code"]: r for r in practice_bank.list_rules()}
    cloud = rules["aliyun-acp-cloud"]
    assert (cloud["total_questions"], cloud["single_count"], cloud["multiple_count"]) == (100, 70, 30)
    assert (cloud["total_score"], cloud["pass_score"], cloud["duration_minutes"]) == (100, 80, 120)
    llm = rules["aliyun-acp-llm"]
    assert (llm["total_questions"], llm["total_score"], llm["pass_score"]) == (75, 100, 80)
    print("2) 预置 ACP 云计算 100 题（70+30）/120 分钟/80 分，ACP 大模型 75 题/80 分  ✅")


def check_practice_flow() -> None:
    client = user_client()
    bank = practice_bank.get_bank("aliyun-acp-cloud-mock")
    assert bank["question_count"] == 100

    started = client.post(
        "/api/practice/banks/aliyun-acp-cloud-mock/start", json={"restart": True}
    ).get_json()["data"]
    assert started["status"] == "ongoing" and started["total"] == 100
    assert len(started["questions"]) == 100
    for q in started["questions"]:
        assert "answer" not in q and "explanation" not in q, "交卷前不得下发答案/解析"
        assert len(q["options"]) == 4
    token = started["token"]
    print("3) 开始练习返回 100 题，题面/选项完整且答案与解析未下发  ✅")

    single = next(q for q in started["questions"] if q["type"] == "single")
    multiple = next(q for q in started["questions"] if q["type"] == "multiple")
    another = next(
        q for q in started["questions"]
        if q["type"] == "single" and q["id"] not in (single["id"], multiple["id"])
    )
    single_answer = answer_of(single["id"])
    another_answer = answer_of(another["id"])
    multiple_answer = answer_of(multiple["id"])
    wrong_letter = next(
        letter for letter in multiple["options"] if letter not in multiple_answer
    )

    assert client.post(
        f"/api/practice/sessions/{token}/answer",
        json={"question_id": single["id"], "answer": single_answer},
    ).status_code == 200
    assert client.post(
        f"/api/practice/sessions/{token}/answer",
        json={"question_id": another["id"], "answer": another_answer},
    ).status_code == 200
    assert client.post(
        f"/api/practice/sessions/{token}/answer",
        json={"question_id": multiple["id"], "answer": [wrong_letter]},
    ).status_code == 200

    # 继续练习：同一 token、答案与已答数原样返回
    resumed = client.post(
        "/api/practice/banks/aliyun-acp-cloud-mock/start", json={}
    ).get_json()["data"]
    assert resumed["token"] == token and resumed["answered"] == 3
    assert resumed["answers"][str(single["id"])] == single_answer
    print("4) 逐题保存后「继续练习」恢复同一场次、已答数与作答内容  ✅")

    progress = next(
        b for b in client.get("/api/practice/banks").get_json()["data"]["banks"]
        if b["slug"] == "aliyun-acp-cloud-mock"
    )["progress"]
    assert (progress["answered_count"], progress["correct_count"], progress["wrong_count"]) == (3, 2, 1)
    listed = client.get("/cert/aliyun/acp/cloud").get_data(as_text=True)
    assert "已练 3/100" in listed and "继续练习" in listed
    assert "错题 1" in listed, "认证页卡片应显示错题数"
    print("5) 认证页卡片显示「已练 3/100 / 错题 1 / 继续练习」  ✅")

    result = client.post(
        f"/api/practice/sessions/{token}/submit", json={"reason": "manual"}
    ).get_json()["data"]
    assert result["status"] == "submitted"
    assert result["result"]["correct"] == 2 and result["result"]["score"] == 2.0
    assert result["result"]["passed"] is False
    assert len(result["result"]["items"]) == 100
    assert result["result"]["wrong_count"] == 98
    first = result["result"]["items"][0]
    assert {"your_answer", "correct_answer", "correct_text", "explanation_html"} <= set(first)
    again = client.get(f"/api/practice/sessions/{token}/result").get_json()["data"]["result"]
    assert again["score"] == 2.0 and len(again["items"]) == 100
    print("6) 交卷判分 2/100、未合格，返回 100 道逐题解析且可重复查看  ✅")

    # 重新练习后，错题答对应从 wrong_count 中移除
    second = client.post(
        "/api/practice/banks/aliyun-acp-cloud-mock/start", json={"restart": True}
    ).get_json()["data"]
    assert second["token"] != token
    client.post(
        f"/api/practice/sessions/{second['token']}/answer",
        json={"question_id": multiple["id"], "answer": multiple_answer},
    )
    progress2 = next(
        b for b in client.get("/api/practice/banks").get_json()["data"]["banks"]
        if b["slug"] == "aliyun-acp-cloud-mock"
    )["progress"]
    assert progress2["wrong_count"] == 0, "错题在后续答对后应移出错题数"
    print("7) 重新练习答对后，错题数自动归零  ✅")


def check_timeout() -> None:
    client = user_client()
    started = client.post(
        "/api/practice/banks/ecs-essentials/start", json={"restart": True}
    ).get_json()["data"]
    token = started["token"]
    q = started["questions"][0]
    client.post(
        f"/api/practice/sessions/{token}/answer",
        json={"question_id": q["id"], "answer": answer_of(q["id"])},
    )
    conn = models.get_conn()
    try:
        with conn:
            conn.execute(
                "UPDATE bank_practice_sessions SET deadline_at = '2020-01-01 00:00:00' "
                "WHERE token = ?", (token,)
            )
    finally:
        conn.close()
    expired = client.get(f"/api/practice/sessions/{token}").get_json()["data"]
    assert expired["status"] == "submitted"
    assert expired["result"]["submit_reason"] == "timeout"
    assert expired["result"]["duration"] == 45 * 60, "超时用时应按考试时长封顶"
    assert expired["result"]["score"] == 1.0
    print("8) 超过截止时间后服务端自动交卷，reason=timeout、用时封顶  ✅")


def admin_client():
    client = app.test_client()
    assert test_support.login(client, "admin", "admin123").status_code == 302
    return client


def check_admin_constraints() -> None:
    admin = admin_client()
    listing = admin.get("/api/admin/practice-banks").get_json()["data"]
    assert listing["summary"]["total"] == 3
    assert listing["summary"]["attempts"] >= 2
    assert listing["summary"]["avg_score"] > 0
    print("9) 管理端题库列表带练习人次、平均分与状态统计  ✅")

    # 选择真实规则时，错误题量被强制校正
    created = admin.post("/api/admin/practice-banks", json={
        "name": "规则校正测试卷", "slug": "rule-fix-test",
        "cert_path": "aliyun/acp/cloud", "level_tag": "基础", "difficulty": "简单",
        "rule_code": "aliyun-acp-cloud",
        "total_questions": 30, "single_count": 20, "multiple_count": 10,
        "total_score": 30, "pass_score": 24, "duration_minutes": 45,
    }).get_json()["data"]["bank"]
    assert (created["total_questions"], created["single_count"], created["multiple_count"]) == (100, 70, 30)
    assert (created["total_score"], created["pass_score"], created["duration_minutes"]) == (100, 80, 120)
    failed = admin.post(f"/api/admin/practice-banks/{created['id']}/status",
                        json={"status": "published"})
    assert failed.status_code == 409
    details = failed.get_json()["details"]
    assert any("100" in item for item in details)
    print("10) 关联 ACP 规则后题量被校正为 100（70+30），空题库禁止发布  ✅")

    # 自定义 3 题小卷，补齐后发布
    small = admin.post("/api/admin/practice-banks", json={
        "name": "发布流程测试卷", "slug": "publish-flow-test",
        "cert_path": "aliyun/acp/cloud", "level_tag": "进阶", "difficulty": "中等",
        "rule_code": "custom", "total_questions": 3, "single_count": 2,
        "multiple_count": 1, "total_score": 3, "pass_score": 2, "duration_minutes": 10,
    }).get_json()["data"]["bank"]
    questions = [
        ("single", "测试单选题一，以下哪个选项正确？", "A"),
        ("single", "测试单选题二，以下哪个选项正确？", "B"),
        ("multiple", "测试多选题，以下哪些选项正确？", "A,C"),
    ]
    for qtype, stem, answer in questions:
        assert admin.post(f"/api/admin/practice-banks/{small['id']}/questions", json={
            "type": qtype, "question": stem,
            "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
            "answer": answer, "explanation": "测试解析",
        }).status_code == 200
    assert admin.post(f"/api/admin/practice-banks/{small['id']}/status",
                      json={"status": "published"}).status_code == 200
    assert "发布流程测试卷" in app.test_client().get("/cert/aliyun/acp/cloud").get_data(as_text=True)

    # 用户完成这场小卷，管理端统计应 +1
    user = user_client()
    session = user.post("/api/practice/banks/publish-flow-test/start",
                        json={"restart": True}).get_json()["data"]
    for q in session["questions"]:
        user.post(f"/api/practice/sessions/{session['token']}/answer",
                  json={"question_id": q["id"], "answer": answer_of(q["id"])})
    result = user.post(f"/api/practice/sessions/{session['token']}/submit",
                       json={"reason": "manual"}).get_json()["data"]["result"]
    assert result["score"] == 3.0 and result["passed"] is True
    detail = admin.get(f"/api/admin/practice-banks/{small['id']}").get_json()["data"]
    assert detail["stats"]["attempts"] == 1 and detail["stats"]["participants"] == 1
    assert detail["stats"]["avg_score"] == 3.0 and detail["stats"]["pass_rate"] == 100.0
    print("11) 3 题小卷补满后发布成功；交卷 3 分合格，统计人次/均分/合格率  ✅")

    # 停用：前端隐藏，成绩仍可查
    assert admin.post(f"/api/admin/practice-banks/{small['id']}/status",
                      json={"status": "disabled"}).status_code == 200
    assert "发布流程测试卷" not in app.test_client().get("/practice").get_data(as_text=True)
    assert user.get(f"/api/practice/sessions/{session['token']}/result").status_code == 200
    blocked = admin.post(f"/api/admin/practice-banks/{small['id']}/delete")
    assert blocked.status_code == 409 and "答题记录" in blocked.get_json()["error"]
    print("12) 停用后前端隐藏、历史成绩仍可查看；有记录的题库禁止删除  ✅")

    # 考试规则可改：新规则 30 题 → 创建题库 → 改规则后发布校验提示不同步
    assert admin.post("/api/admin/exam-rules/test-rule-30", json={
        "name": "测试规则 30 题", "total_questions": 30, "single_count": 20,
        "multiple_count": 10, "total_score": 30, "pass_score": 24,
        "duration_minutes": 45, "description": "测试用",
    }).status_code == 200
    rule_bank = admin.post("/api/admin/practice-banks", json={
        "name": "规则同步测试卷", "slug": "rule-sync-test",
        "cert_path": "aliyun/acp/cloud", "level_tag": "基础", "difficulty": "简单",
        "rule_code": "test-rule-30",
    }).get_json()["data"]["bank"]
    assert rule_bank["total_questions"] == 30
    assert admin.post("/api/admin/exam-rules/test-rule-30", json={
        "name": "测试规则 30 题", "total_questions": 30, "single_count": 18,
        "multiple_count": 12, "total_score": 30, "pass_score": 24,
        "duration_minutes": 45, "description": "测试用",
    }).status_code == 200
    sync_fail = admin.post(f"/api/admin/practice-banks/{rule_bank['id']}/status",
                           json={"status": "published"})
    assert sync_fail.status_code == 409
    assert any("规则已更新" in item for item in sync_fail.get_json()["details"])
    print("13) 考试规则可在线修改；题库快照不同步时发布被拦截  ✅")

    # 清理无记录题库（small 已有答题记录，只能停用）
    for bid in (created["id"], rule_bank["id"]):
        assert admin.post(f"/api/admin/practice-banks/{bid}/delete").status_code == 200
    print("14) 无答题记录的测试题库可正常删除  ✅")


def check_login_page() -> None:
    html = app.test_client().get("/login").get_data(as_text=True)
    assert "默认演示账号" not in html
    assert "user / user123" not in html
    assert "user123" not in html
    print("15) 登录页不再展示默认演示账号  ✅")


def check_seo() -> None:
    client = app.test_client()
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    # 独立目录页已下线，不再占用 sitemap；题库内容挂在认证页上被索引
    assert "<loc>http://localhost/practice</loc>" not in sitemap
    assert "<loc>http://localhost/cert/aliyun/acp/cloud</loc>" in sitemap
    cert_page = client.get("/cert/aliyun/acp/cloud")
    assert cert_page.headers.get("X-Robots-Tag") in (None, "index,follow,max-image-preview:large")
    assert "模拟题库" in cert_page.get_data(as_text=True)
    page = client.get("/practice/aliyun-acp-cloud-mock")
    assert page.headers.get("X-Robots-Tag") == "noindex,follow"
    assert "noindex,follow" in page.get_data(as_text=True)
    print("16) 认证页可索引并含题库卡片；试卷页 noindex,follow；sitemap 不再有 /practice  ✅")


def main() -> None:
    setup()
    try:
        check_page_and_seed()
        check_practice_flow()
        check_timeout()
        check_admin_constraints()
        check_login_page()
        check_seo()
        print("\nRESULT: PASS — 题库列表 / 自由练习 / 考试规则约束 全部通过")
    finally:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
