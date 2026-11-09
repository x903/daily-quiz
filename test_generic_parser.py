"""回归测试：Markdown 题库解析器的通用匹配规则。

覆盖 parser.py 在「套卷式 / ACP 风格 / 题号标题式 / 无编号式」四类排版下的解析，
确保：
1. 既有 ACP 风格（**N.** 同行题干）不被破坏；
2. 题号标题式（### 第 N 题，题干在下方）能被识别；
3. 章节标题（# 第一部分　单项选择题 / # 第二部分　多项选择题）能推断题型并把
   整段题目归入同一套；
4. 选项前缀 A. / - A. / (A) / （A） 全部兼容；
5. 真实 100 题标准模拟卷能完整解析为 70 单选 + 30 多选、解析不丢、文末汇总不混入。
"""
from __future__ import annotations

import parser as md_parser

SAMPLE_20 = "data/ACP模拟题20道_2026-11-04.md"
PAPER_100 = "data/ACP云计算高级工程师_标准模拟卷100题_2026-11-08.md"


def test_existing_acp_style_unchanged():
    """既有 ACP 风格样例仍解析为 20 题 / 20 套。"""
    text = open(SAMPLE_20, encoding="utf-8").read()
    r = md_parser.parse_auto(text, "markdown")
    assert r.total_questions == 20, r.total_questions
    assert len(r.sets) == 20, list(r.sets.keys())
    assert not r.failed, r.failed


def test_heading_question_format():
    """### 第 N 题（题干在下一行）应被识别，且选项/答案正确。"""
    text = (
        "### 第 1 题\n"
        "下列属于云产品的是？\n\n"
        "- A. ECS\n- B. OSS\n- C. RDS\n- D. 以上都是\n\n"
        "**答案：D**\n"
    )
    r = md_parser.parse_markdown(text)
    assert r.total_questions == 1 and not r.failed, r.failed
    q = r.sets[1][0]
    assert q.question.startswith("下列属于云产品的是")
    assert list(q.options.keys()) == ["A", "B", "C", "D"]
    assert q.answer == ["D"]


def test_paren_option_format():
    """选项写成 (A) / （A） 括号形式也应被识别。"""
    text = (
        "### 第 1 题\n下列属于云产品的是？\n\n"
        "(A) ECS\n(B) OSS\n(C) RDS\n(D) 以上都是\n\n"
        "**答案：D**\n"
    )
    r = md_parser.parse_markdown(text)
    assert r.total_questions == 1 and not r.failed, r.failed
    assert list(r.sets[1][0].options.keys()) == ["A", "B", "C", "D"]


def test_title_only_format():
    """题目：xxx 无编号形式应被识别。"""
    text = "题目：下面哪个是对象存储？\nA. ECS\nB. OSS\nC. RDS\n答案：B\n"
    r = md_parser.parse_markdown(text)
    assert r.total_questions == 1 and not r.failed, r.failed
    assert r.sets[1][0].question == "下面哪个是对象存储？"
    assert r.sets[1][0].answer == ["B"]


def test_section_type_inference_and_grouping():
    """章节标题应推断题型，并把同章节题目归入同一套（多选题答案>1）。"""
    text = (
        "# 第一部分　单项选择题（第 1–2 题）\n\n"
        "### 第 1 题\n单选题干一\n- A. a\n- B. b\n答案：A\n\n"
        "### 第 2 题\n单选题干二\n- A. a\n- B. b\n答案：B\n\n"
        "# 第二部分　多项选择题（第 3–4 题）\n\n"
        "### 第 3 题\n多选题干一\n- A. a\n- B. b\n- C. c\n答案：AB\n\n"
        "### 第 4 题\n多选题干二\n- A. a\n- B. b\n- C. c\n答案：BC\n\n"
    )
    r = md_parser.parse_markdown(text)
    assert r.total_questions == 4 and not r.failed, r.failed
    # 单选题归入套 1，多选题归入套 2
    assert list(r.sets.keys()) == [1, 2], list(r.sets.keys())
    dist = r.type_distribution()
    assert dist["single"] == 2 and dist["multiple"] == 2, dist
    for q in r.sets[2]:
        assert q.q_type == md_parser.TYPE_MULTIPLE
        assert len(q.answer) >= 2


def test_real_paper_100():
    """真实 100 题标准模拟卷：70 单选 + 30 多选，0 失败，解析齐全。"""
    text = open(PAPER_100, encoding="utf-8").read()
    r = md_parser.parse_auto(text, "markdown")
    assert r.total_questions == 100, r.total_questions
    assert not r.failed, r.failed[:3]
    dist = r.type_distribution()
    assert dist == {"single": 70, "multiple": 30, "judge": 0}, dist
    # 每题都应带解析，且文末「答案速查表」未混入解析
    with_expl = sum(1 for qs in r.sets.values() for q in qs if q.explanation.strip())
    assert with_expl == 100, with_expl


if __name__ == "__main__":
    test_existing_acp_style_unchanged()
    test_heading_question_format()
    test_paren_option_format()
    test_title_only_format()
    test_section_type_inference_and_grouping()
    test_real_paper_100()
    print("✅ test_generic_parser 全部通过")
