"""验证「显示答案时一并显示解析」的全链路。

覆盖：
1. 解析器收集三种写法的解析块（**解析** / **解析：** / 解析：），且不误收文末汇总；
2. explanation_to_html 的加粗、项目符号、缩进与 HTML 转义（XSS 防护）；
3. 四个显示答案的页面（题目回顾 / 结果页 / 成绩详情 / 错题本）均渲染解析；
4. 答题页与错题重练页不泄露答案与解析；
5. 导出 JSON 携带解析；
6. 测试结束后清理产生的成绩与错题，保持交付库干净。
"""
import json
import re

import models
import parser as md_parser
import quiz_logic
from app import app

client = app.test_client()
DATE = "2026-11-04"

# ---------------------------------------------------------------------------
# 1) 解析器
# ---------------------------------------------------------------------------
real = open("data/ACP模拟题20道_2026-11-04.md", encoding="utf-8").read()
r = md_parser.parse_markdown(real)
assert r.total_questions == 20 and not r.failed, "真实文件应解析出 20 题且无失败"
assert all(q.explanation.strip() for qs in r.sets.values() for q in qs), "每题都应有解析"
assert "网关终端节点" in r.sets[5][0].explanation, "用户举例的第 5 题解析应完整"
assert r.sets[5][0].answer == ["B"]
for idx, qs in r.sets.items():
    for q in qs:
        assert "答案速查" not in q.explanation, f"第{idx}套误收答案速查表"
        assert "作答建议" not in q.explanation, f"第{idx}套误收作答建议"
        assert "答案分布" not in q.explanation, f"第{idx}套误收答案分布"
print("1) 解析：2026-11-04 共 20 题，全部带解析，文末汇总未混入 ✅")

# 11-03 使用 `**解析**`（无冒号）
r3 = md_parser.parse_markdown(
    open(r"C:/Users/haoran/WorkBuddy/automation-2026-10-31-23-26-33/output/"
         "ACP模拟题20道_2026-11-03.md", encoding="utf-8").read()
)
assert r3.total_questions == 20 and all(q.explanation.strip() for qs in r3.sets.values() for q in qs)
print("   11-03 的 `**解析**`（无冒号）写法同样兼容 ✅")

# 用户消息里给出的简化示例
sample = """5. 某 VPC 内的 ECS 均无公网 IP，需要以内网方式访问同地域 OSS？
- A. 为 ECS 绑定 EIP
- B. 创建 OSS 网关终端节点（Gateway Endpoint）
- C. 通过 NAT 网关做 SNAT
- D. 使用 CDN 回源 OSS
答案：B
解析：
- 考点：网关终端节点用于 VPC 私网直接访问 OSS。
- 易错点：
- ① 用 NAT 网关会产生费用；
- ② 绑定 EIP 走公网会产生流量费。
- 延伸：私网路径有三种。
"""
rs = md_parser.parse_markdown(sample)
qs5 = rs.sets[5][0]
assert qs5.answer == ["B"] and len(qs5.options) == 4 and len(qs5.explanation.splitlines()) == 5
print("   用户示例的简化格式（5. / 答案：B / 解析：）解析正确 ✅")

# 格式一（无解析块）向后兼容
r1 = md_parser.parse_markdown("第1套\n单选题\n题目？\nA. 甲\nB. 乙\n答案：A")
assert r1.total_questions == 1 and r1.sets[1][0].explanation == ""
print("   格式一（无解析）不产生解析块，向后兼容 ✅")

# ---------------------------------------------------------------------------
# 2) 安全渲染
# ---------------------------------------------------------------------------
h = quiz_logic.explanation_to_html("- **考点**：<script>alert(1)</script> & \"x\"")
assert "<strong>考点</strong>" in h, "加粗应转为 strong"
assert "<script>" not in h and "&lt;script&gt;" in h, "必须转义 HTML"
assert "&amp;" in h
assert "•" in h
assert "&nbsp;&nbsp;• ① 子项" in quiz_logic.explanation_to_html("  - ① 子项")
assert quiz_logic.explanation_to_html("") == ""
assert quiz_logic.explanation_to_html(None) == ""
print("2) 渲染：加粗 / 项目符号 / 缩进 / HTML 转义（XSS 防护）全部正确 ✅")

# ---------------------------------------------------------------------------
# 3~5) 页面与导出
# ---------------------------------------------------------------------------
try:
    assert client.post("/login", data={"username": "user", "password": "user123"}).status_code == 302

    # 故意全部不作答 → 20 道错题，用于验证错题类页面
    html_quiz = client.get(f"/quiz/{DATE}").get_data(as_text=True)
    qids = re.findall(r'name="q_(\d+)"', html_quiz)
    assert len(qids) == 80, f"应有 80 个选项控件，实际 {len(qids)}"
    resp = client.post(f"/quiz/{DATE}", data={"duration": "42"}, follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    result_html = client.get(loc).get_data(as_text=True)

    # 四个显示答案的页面
    pages = {
        "答题结果页": result_html,
        "成绩详情页": client.get(f"/score/{DATE}").get_data(as_text=True),
        "错题本": client.get("/wrong").get_data(as_text=True),
        "题目回顾页": client.get(f"/date/{DATE}").get_data(as_text=True),
    }
    for name, body in pages.items():
        assert 'class="explanation"' in body, f"{name} 未渲染解析块"
        assert "网关终端节点" in body, f"{name} 未包含第 5 题解析内容"
        print(f"3) {name}：显示答案处已渲染解析 ✅")

    # 防泄题：答题页与重练页不应出现解析块或解析标题
    # （不能用「网关终端节点」判断，因为第 5 题的选项文字本身含该词）
    for name, body in [("答题页", html_quiz),
                       ("错题重练页", client.get(f"/wrong/{DATE}/practice").get_data(as_text=True))]:
        assert 'class="explanation"' not in body, f"{name} 不应显示解析块"
        assert "📖 解析" not in body, f"{name} 不应出现解析标题"
    print("4) 答题页 / 错题重练页均不泄露解析（防泄题）✅")

    # 导出 JSON
    qjson = json.loads(client.get(f"/export/questions/{DATE}").get_data(as_text=True))
    exps = [q["explanation"] for s in qjson["sets"] for q in s["questions"]]
    assert len(exps) == 20 and all(e.strip() for e in exps)
    wjson = json.loads(client.get("/export/wrong").get_data(as_text=True))
    assert wjson["count"] == 20 and all(w["explanation"].strip() for w in wjson["wrong_questions"])
    print("5) 导出题目 / 错题 JSON 均携带解析 ✅")

finally:
    # 清理本次测试产生的成绩与错题，保持交付库干净
    models.init_db()
    u = models.get_user_by_username("user")
    conn = models.get_conn()
    conn.execute("DELETE FROM daily_records WHERE user_id = ?", (u["id"],))
    conn.execute("DELETE FROM wrong_questions WHERE user_id = ?", (u["id"],))
    conn.execute("UPDATE daily_sets SET status = 'pending' WHERE date = ?", (DATE,))
    conn.commit()
    conn.close()
    print("   已清理测试产生的成绩 / 错题 ✅")

print("\n✅ 全部通过：凡显示答案之处均显示解析，且无答案的页面不泄露解析。")
