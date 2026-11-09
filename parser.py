"""Markdown 题目解析模块。

负责把用户每天提供的 Markdown 题目文本解析为结构化数据，
并在格式不完整（缺题干 / 缺选项 / 缺答案 / 答案与选项不匹配等）
时给出清晰的错误提示，标记导入失败的题目。
"""
from __future__ import annotations

import json
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
# 选项：支持 `A.` / `- A.`（项目符号）/ `(A)` / `（A）` 四种前缀
_OPTION_RE = re.compile(
    r"^\s*(?:[-*]\s+)?"
    r"(?:[（(]([A-Za-z])[)）]"          # (A) / （A）
    r"|([A-Za-z])\s*[\.．、])"           # A. / A、 / - A.
    r"\s*(.*)$"
)
# 章节/题型标题：`# 第一部分　单项选择题（第 1–70 题）`。
# 仅匹配 Markdown 标题行（避免命中正文）；命中后推断后续题型并把题目归入同一套。
_SECTION_TYPE_RE = re.compile(
    r"^\s*#{1,6}\s+.*?(单项选择|多项选择|单选题|多选题|判断题)"
)
# 题号标题（格式三）：`### 第 1 题` / `第 1 题` / `第1题`，题干在后续行
_HEAD_Q_RE = re.compile(r"^\s*(?:#{1,6}\s*)?第\s*(\d+)\s*题\s*[:：]?\s*(.*)$")
# 无编号题干（格式四）：`### 题目：xxx` / `题目：xxx`
_TITLE_Q_RE = re.compile(r"^\s*(?:#{1,6}\s*)?题目\s*[:：]\s*(.*)$")
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
    if "多项" in t or "多选" in t:
        return TYPE_MULTIPLE
    if "判断" in t:
        return TYPE_JUDGE
    return TYPE_SINGLE


def _map_section_type(word: str) -> str:
    """根据章节标题里的题型词推断内部题型常量。"""
    w = word.replace("*", "")
    if "多项" in w or "多选" in w:
        return TYPE_MULTIPLE
    if "判断" in w:
        return TYPE_JUDGE
    return TYPE_SINGLE


def parse_markdown(text: str) -> ParseResult:
    """解析整份 Markdown 题目（含题目解析）。

    兼容多种常见排版，通用匹配规则如下：

    - 格式一（套卷式）：``第N套`` / ``单选题`` / ``A. 选项`` / ``答案：B``
    - 格式二（ACP 风格）：``**1.** 题干`` / ``- A. 选项`` / ``**答案：C``，题型由
      ``**题型**：单项选择题`` 这类提示行推断（缺省视为单选）
    - 格式三（题号标题式）：``### 第 1 题`` / ``第 1 题``，题干写在标题**下方**
      的独立行，选项 ``- A.`` 或 ``(A)``，答案 ``**答案：B**``；题型由文件中的
      ``# 第一部分　单项选择题`` / ``# 第二部分　多项选择题`` 这类章节标题推断，
      同一章节的题目自动归入同一套
    - 格式四（无编号式）：``### 题目：题干`` / ``题目：题干``

    选项前缀支持 ``A.`` / ``- A.`` / ``(A)`` / ``（A）`` 四种；答案支持
    ``答案：B`` 与 ``**答案：B**``。多选题答案可写成 ``AB`` / ``ACD``。

    答案之后的 ``**解析：**``（或 ``解析：``）块会被收集为题目的 explanation，
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
    section_set_index: int = 0      # 当前「大题/部分」对应的套序号
    in_section: bool = False        # 是否处于题型章节内（其下题目归入同一套）

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

        # 章节/题型标题（# 开头且含题型词）：设置缺省题型，并把其下题目归入同一套
        m_sect = _SECTION_TYPE_RE.match(stripped)
        if m_sect:
            default_type = _map_section_type(m_sect.group(1))
            section_set_index += 1
            in_section = True
            flush_question()
            explanation_target = None
            current_set_index = section_set_index
            continue

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

        # 题号标题（格式三）：### 第 1 题 / 第 1 题，题干在后续行
        m_head = _HEAD_Q_RE.match(line)
        if m_head:
            flush_question()
            explanation_target = None
            inline = m_head.group(2).strip()
            current_set_index = section_set_index if in_section else 1
            current_question = Question(
                set_index=current_set_index,
                q_type=default_type,
                question=inline,
                options={},
                answer=[],
                raw_answer="",
            )
            # 题干在后续非选项/答案行续接
            continue

        # 无编号题干（格式四）：### 题目：xxx / 题目：xxx
        m_title = _TITLE_Q_RE.match(line)
        if m_title:
            flush_question()
            explanation_target = None
            current_set_index = section_set_index if in_section else 1
            current_question = Question(
                set_index=current_set_index,
                q_type=default_type,
                question=m_title.group(1).strip(),
                options={},
                answer=[],
                raw_answer="",
            )
            continue

        m_option = _OPTION_RE.match(line)
        if m_option and current_question is not None:
            letter = (m_option.group(1) or m_option.group(2) or "").upper()
            if letter:
                text_opt = m_option.group(3).strip()
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

        # 防御：未被上述任一规则匹配的 Markdown 标题行直接忽略，避免污染题干
        if stripped.startswith("#"):
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


# ---------------------------------------------------------------------------
# JSON 题库导入（与 /export/questions/<date> 导出的格式互通）
# ---------------------------------------------------------------------------

_JSON_TYPE_MAP = {
    "single": TYPE_SINGLE, "单选题": TYPE_SINGLE, "单项选择": TYPE_SINGLE,
    "单项选择题": TYPE_SINGLE, "单选": TYPE_SINGLE,
    "multiple": TYPE_MULTIPLE, "多选题": TYPE_MULTIPLE, "多项选择题": TYPE_MULTIPLE,
    "多选": TYPE_MULTIPLE,
    "judge": TYPE_JUDGE, "判断题": TYPE_JUDGE, "判断": TYPE_JUDGE,
}

_JUDGE_ANSWER_MAP = {
    "正确": "A", "错误": "B", "对": "A", "错": "B", "√": "A", "×": "B",
    "true": "A", "false": "B", "t": "A", "f": "B", "a": "A", "b": "B",
}


def _json_field(raw: dict, *names: str):
    """按优先级取第一个非空字段（兼容中英文字段名 / 导出格式）。"""
    for name in names:
        value = raw.get(name)
        if value is not None and str(value).strip():
            return value
    return None


def _json_options(raw) -> dict[str, str]:
    """把 dict / 字符串数组 / 对象数组统一成 {"A": "...", "B": "..."}。"""
    result: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            letter = str(key).strip().upper()[:1]
            if letter:
                result[letter] = str(value)
    elif isinstance(raw, (list, tuple)):
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                letter = str(_json_field(item, "key", "letter", "label", "option") or "").strip().upper()[:1]
                value = _json_field(item, "value", "text", "content", "label")
                if not letter:
                    letter = chr(ord("A") + i)
                result[letter] = str(value or "")
            else:
                result[chr(ord("A") + i)] = str(item)
    return {k: v for k, v in result.items() if k and k.isalpha()}


def _json_answer(raw, q_type: str) -> str:
    """把答案统一成无分隔符的大写字母串，供 _validate_question 复用清洗逻辑。"""
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        letters = []
        for item in raw:
            text = str(item).strip()
            if text.upper() in ("TRUE", "FALSE") or text in _JUDGE_ANSWER_MAP:
                letters.append(_JUDGE_ANSWER_MAP.get(text.lower(), _JUDGE_ANSWER_MAP.get(text, text)))
            else:
                letters.append(text[:1].upper())
        return "".join(letters)
    text = str(raw).strip()
    if q_type == TYPE_JUDGE and text.lower() in ("true", "false", "t", "f"):
        return _JUDGE_ANSWER_MAP[text.lower()]
    if text in _JUDGE_ANSWER_MAP and q_type == TYPE_JUDGE:
        return _JUDGE_ANSWER_MAP[text]
    return re.sub(r"[\s,，、;；/|]", "", text).upper()


def _json_questions(payload) -> list[dict]:
    """从多种 JSON 结构里提取题目字典列表。

    支持：顶层数组；{"questions": [...]}；
    {"sets": [{"set_index": 1, "questions": [...]}]}（导出格式，可原样再导入）。
    """
    if isinstance(payload, list):
        return [q for q in payload if isinstance(q, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("questions"), list):
        return [q for q in payload["questions"] if isinstance(q, dict)]
    collected: list[dict] = []
    for group in payload.get("sets") or []:
        if not isinstance(group, dict):
            continue
        for q in group.get("questions") or []:
            if isinstance(q, dict):
                collected.append(dict(q))
    return collected


def parse_json_questions(text: str) -> ParseResult:
    """解析 JSON 题库文本为与 Markdown 一致的结构化结果。

    单道题格式：
        {"type": "single|multiple|judge", "question": "题干",
         "options": {"A": "...", "B": "..."}, "answer": ["B"],
         "explanation": "解析（可选）"}

    兼容中文题型、字符串答案（"AB" / "A,B"）、判断题语义答案（正确/错误）、
    数组选项，以及 ``/export/questions/<date>`` 导出的 ``sets`` 结构。
    校验规则与 Markdown 完全一致（复用 ``_validate_question``）。

    Args:
        text: UTF-8 JSON 文本。

    Returns:
        ParseResult；格式错误时 failed 中给出明确原因，不会抛异常。
    """
    result = ParseResult()
    if not text or not text.strip():
        return result
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        result.failed.append({
            "set_index": 0, "question": "JSON 文本",
            "errors": [f"不是合法的 JSON：{exc}"],
        })
        return result

    raw_questions = _json_questions(payload)
    if not raw_questions:
        result.failed.append({
            "set_index": 0, "question": "JSON 结构",
            "errors": ["未找到题目：需要 questions 数组、顶层数组或 sets[].questions"],
        })
        return result

    for index, raw in enumerate(raw_questions, 1):
        q_type = _JSON_TYPE_MAP.get(
            str(_json_field(raw, "type", "q_type", "题型") or "single").strip().lower(),
            None,
        ) or _JSON_TYPE_MAP.get(
            str(_json_field(raw, "type", "q_type", "题型") or "single").strip(),
            TYPE_SINGLE,
        )
        options = _json_options(_json_field(raw, "options", "选项"))
        if q_type == TYPE_JUDGE and not options:
            options = {"A": "正确", "B": "错误"}
        q = Question(
            set_index=index,
            q_type=q_type,
            question=str(_json_field(raw, "question", "stem", "title", "题干") or "").strip(),
            options=options,
            raw_answer=_json_answer(_json_field(raw, "answer", "correct_answer", "答案"), q_type),
            explanation=str(_json_field(raw, "explanation", "analysis", "解析") or "").strip(),
        )
        valid, errors = _validate_question(q)
        if valid:
            result.sets.setdefault(index, []).append(q)
        else:
            result.failed.append({
                "set_index": index,
                "question": q.question[:60],
                "errors": errors,
            })
    return result


def parse_auto(text: str, fmt: str = "markdown") -> ParseResult:
    """按格式选择解析器；未知格式回退 Markdown。"""
    return parse_json_questions(text) if fmt.lower() in ("json", "js") else parse_markdown(text)
