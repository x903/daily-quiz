"""Markdown 题目解析模块。

负责把用户每天提供的 Markdown 题目文本解析为结构化数据，
并在格式不完整（缺题干 / 缺选项 / 缺答案 / 答案与选项不匹配等）
时给出清晰的错误提示，标记导入失败的题目。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 题型常量
TYPE_SINGLE = "single"    # 单选题
TYPE_MULTIPLE = "multiple"  # 多选题
TYPE_JUDGE = "judge"      # 判断题

_TYPE_MAP = {
    "单选题": TYPE_SINGLE,
    "多选题": TYPE_MULTIPLE,
    "判断题": TYPE_JUDGE,
}

_TYPE_LABEL = {
    TYPE_SINGLE: "单选题",
    TYPE_MULTIPLE: "多选题",
    TYPE_JUDGE: "判断题",
}


# 格式一（原始）：第N套 / 单选题 / A. 选项 / 答案：B
_SET_RE = re.compile(r"^第\s*(\d+)\s*套\s*$")
_TYPE_RE = re.compile(r"^\s*(单选题|多选题|判断题)\s*$")
# 格式二（ACP 风格）：**1.** 题干 / - A. 选项 / **答案：C**
# 题型提示行：**题型**：单项选择题（...）
_TYPEHINT_RE = re.compile(r"题型\**\s*[:：]\s*\**\s*(单项选择|多选|判断|单选)")
# 编号题：`**1.** 题干` 或 `1. 题干`（加粗可能为 **1.** 或 *1.*）
_QNUM_RE = re.compile(r"^\s*\*{0,2}\s*(\d+)\.\s*\*{0,2}\s*(.*)$")
# 选项：支持 `A.` 与 `- A.`（带项目符号）两种前缀
_OPTION_RE = re.compile(r"^\s*(?:-\s+)?([A-Za-z])\s*[\.．、]\s*(.*)$")
# 答案：支持 `答案：B` 与 `**答案：B**`（带加粗）
_ANSWER_RE = re.compile(r"答案\s*[:：]\s*(.+)")
# 解析块起始行：兼容 `**解析**`（无冒号）、`解析：`、`**解析：**`、`**解析**：`
_EXP_START_RE = re.compile(r"^\*{0,2}\s*解析\s*\*{0,2}\s*[:：]?\s*\*{0,2}\s*$")
# 解析内容行：项目符号列表（含缩进子项），如 `- **考点**：...` / `  - ① ...`
_EXPL_LINE_RE = re.compile(r"^\s*[-*]\s+\S")


@dataclass
class Question:
    """单道题目的结构化表示（解析阶段，尚未落库）。"""

    set_index: int
    q_type: str
    question: str
    options: dict[str, str] = field(default_factory=dict)
    answer: list[str] = field(default_factory=list)
    raw_answer: str = ""
    explanation: str = ""  # 题目解析：答案之后 `解析：` 块的原始 Markdown 文本

    def to_dict(self) -> dict:
        return {
            "set_index": self.set_index,
            "type": self.q_type,
            "question": self.question,
            "options": self.options,
            "answer": self.answer,
            "explanation": self.explanation,
        }


@dataclass
class ParseResult:
    """整份 Markdown 的解析结果。"""

    sets: dict[int, list[Question]] = field(default_factory=dict)
    failed: list[dict] = field(default_factory=list)

    @property
    def total_questions(self) -> int:
        return sum(len(qs) for qs in self.sets.values())

    @property
    def total_sets(self) -> int:
        return len(self.sets)

    def type_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {TYPE_SINGLE: 0, TYPE_MULTIPLE: 0, TYPE_JUDGE: 0}
        for qs in self.sets.values():
            for q in qs:
                dist[q.q_type] += 1
        return dist


def _type_label(q_type: str) -> str:
    return _TYPE_LABEL.get(q_type, q_type)


def _map_hint(text: str) -> str:
    """把题型提示文本映射为内部题型常量。"""
    t = text.replace("*", "")
    if "多选" in t:
        return TYPE_MULTIPLE
    if "判断" in t:
        return TYPE_JUDGE
    return TYPE_SINGLE


def parse_markdown(text: str) -> ParseResult:
    """解析整份 Markdown 题目（含题目解析）。

    同时兼容两种格式：
    - 格式一：第N套 / 单选题 / A. 选项 / 答案：B
    - 格式二（ACP 风格）：**1.** 题干 / - A. 选项 / **答案：C**，题型由
      `**题型**：单项选择题` 这类提示行推断（缺省视为单选）

    答案之后的 `**解析：**`（或 `解析：`）块会被收集为题目的 explanation，
    收集在遇到下一道题、章节标题、表格、分隔线或汇总行时终止，因此文件末尾的
    「答案速查表」「本期模块分布」「作答建议」等不会混入解析文本。

    Args:
        text: 用户提供的 Markdown 题目文本。

    Returns:
        ParseResult，包含成功解析的题目（按套分组）与失败题目列表。
    """
    result = ParseResult()
    if not text or not text.strip():
        return result

    lines = text.replace("\r\n", "\n").split("\n")
    current_set_index: Optional[int] = None
    current_question: Optional[Question] = None
    default_type: str = TYPE_SINGLE  # 格式二中缺省题型

    # 答案行 flush 之后，记录「待补充解析」的题目对象引用。因为该对象已加入
    # result.sets（同一引用），后续把解析文本追加到它上面会直接反映在结果里。
    explanation_target: Optional[Question] = None
    in_explanation: bool = False

    def flush_question() -> Optional[Question]:
        """结算当前题目；校验通过时返回该对象，供调用方继续补充解析。"""
        nonlocal current_question, current_set_index
        if current_question is None:
            return None
        q = current_question
        current_question = None
        # 既无选项也无答案的片段视为普通文本（如文末「作答建议」等有序列表），
        # 直接跳过，不计入成功题目也不计入失败题目。
        if not q.options and not q.raw_answer:
            return None
        if current_set_index is None:
            # 题目出现在任何套标记之前，统一归入第 1 套
            current_set_index = 1
        ok, errors = _validate_question(q)
        if ok:
            result.sets.setdefault(current_set_index, []).append(q)
            return q
        result.failed.append(
            {
                "set_index": current_set_index,
                "type": _type_label(q.q_type),
                "question": (q.question or "(无题干)")[:60],
                "errors": errors,
            }
        )
        return None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        # ---- 解析块收集：位于答案之后、下一题之前 ----
        # 只收集 `- ` 列表行（含缩进子项），这样「答案速查表」「作答建议」
        # 等文末汇总内容（标题 / 表格 / 有序列表）不会混入解析。
        if in_explanation:
            if _EXPL_LINE_RE.match(line):
                if explanation_target is not None:
                    explanation_target.explanation += (
                        "\n" if explanation_target.explanation else ""
                    ) + line.rstrip()
                continue
            # 非解析行：结束收集，本行继续交由下方主逻辑处理
            in_explanation = False

        # 题型提示（如 **题型**：单项选择题），设置后续缺省题型
        m_hint = _TYPEHINT_RE.search(stripped)
        if m_hint:
            default_type = _map_hint(m_hint.group(1))

        m_set = _SET_RE.match(stripped)
        if m_set:
            flush_question()
            explanation_target = None
            current_set_index = int(m_set.group(1))
            continue

        m_type = _TYPE_RE.match(stripped)
        if m_type:
            flush_question()
            explanation_target = None
            if current_set_index is None:
                current_set_index = 1
            current_question = Question(
                set_index=current_set_index,
                q_type=_TYPE_MAP[m_type.group(1)],
                question="",
                options={},
                answer=[],
                raw_answer="",
            )
            continue

        # 编号题（格式二）：**1.** 题干  → 每题作为一套
        m_qnum = _QNUM_RE.match(line)
        if m_qnum:
            flush_question()
            explanation_target = None
            num = int(m_qnum.group(1))
            current_set_index = num
            current_question = Question(
                set_index=num,
                q_type=default_type,
                question=m_qnum.group(2).strip(),
                options={},
                answer=[],
                raw_answer="",
            )
            # 题干可能跨多行，首行已写入，后续非选项/答案行会续接
            continue

        m_option = _OPTION_RE.match(line)
        if m_option and current_question is not None:
            letter = m_option.group(1).upper()
            text_opt = m_option.group(2).strip()
            current_question.options[letter] = text_opt
            continue

        m_answer = _ANSWER_RE.search(stripped)
        if m_answer and current_question is not None:
            current_question.raw_answer = (
                m_answer.group(1).strip().strip("*").strip("`").strip()
            )
            # 记录该题对象，紧随其后的「解析：」块将补充到它上面
            explanation_target = flush_question()
            continue

        # 解析块起始（必须紧跟一道已成功入库题目的答案）
        if _EXP_START_RE.match(stripped) and explanation_target is not None:
            in_explanation = True
            continue

        # 其余非空行：在选项 / 答案之前视为题干内容（支持多行题干）
        if current_question is not None and not current_question.options and not current_question.raw_answer:
            if current_question.question:
                current_question.question += "\n" + stripped
            else:
                current_question.question = stripped

    flush_question()
    return result


def _validate_question(q: Question) -> tuple[bool, list[str]]:
    """校验单道题目的完整性。

    Returns:
        (是否通过, 错误原因列表)
    """
    errors: list[str] = []

    if not q.question.strip():
        errors.append("缺少题干")

    if not q.options:
        errors.append("缺少选项")

    if not q.raw_answer:
        errors.append("缺少答案")
    else:
        # 清洗答案：去掉空格、逗号、顿号，统一大写，支持 "AB" / "A、B" / "A, B"
        cleaned = re.sub(r"[\s,，、]", "", q.raw_answer).upper()
        if not cleaned:
            errors.append("答案内容为空")
        else:
            q.answer = list(cleaned)
            for letter in q.answer:
                if letter not in q.options:
                    errors.append(f"答案选项「{letter}」在题目中不存在")
            if q.q_type == TYPE_SINGLE and len(q.answer) != 1:
                errors.append("单选题答案应为唯一选项")
            if q.q_type == TYPE_MULTIPLE and len(q.answer) < 2:
                errors.append("多选题答案应至少包含两个选项")
            if q.q_type == TYPE_JUDGE and q.answer and q.answer[0] not in ("A", "B"):
                errors.append("判断题答案应为 A（正确）或 B（错误）")

    return (len(errors) == 0), errors
