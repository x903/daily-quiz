"""管理端业务服务层：与 HTTP 传输格式（页面表单 / JSON API）解耦。

/admin/dashboard 的静态前端通过 /api/admin/* 调用这里；遗留的服务端视图也
复用同一份实现，保证两种入口的导入/编辑行为完全一致，不会出现逻辑分叉。
"""
from __future__ import annotations

import json
import re
from typing import Optional

import navigation
import parser as md_parser
import quiz_logic

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def detect_format(filename: str, text: str = "") -> str:
    """判断上传题库格式：显式 .json 或正文以 { / [ 开头视为 JSON，其余 Markdown。"""
    name = (filename or "").lower()
    if name.endswith(".json"):
        return "json"
    if (text or "").lstrip().startswith(("{", "[")):
        return "json"
    return "markdown"


def read_uploaded_file(uploaded_file) -> tuple[str, str, str, Optional[str]]:
    """读取上传文件，返回 (文本, 文件名, 格式, 错误信息)。

    同时支持 UTF-8 Markdown（.md / .markdown / .txt）与 JSON（.json）；
    非 UTF-8 时错误信息非空，调用方据此返回 400 而不是 500。
    """
    if uploaded_file is None or not uploaded_file.filename:
        return "", "", "markdown", None
    try:
        text = uploaded_file.read().decode("utf-8")
    except UnicodeDecodeError:
        return "", uploaded_file.filename, "markdown", "文件不是有效的 UTF-8 文本，请另存为 UTF-8 编码的 Markdown / JSON 文件"
    return text, uploaded_file.filename, detect_format(uploaded_file.filename, text), None


def guess_date(md_text: str, title: str, filename: str) -> str:
    """从文件名、标题、正文头部探测 YYYY-MM-DD 日期，找不到返回空串。"""
    for candidate in (filename or "", title or "", (md_text or "")[:2000]):
        match = _DATE_RE.search(candidate)
        if match:
            return match.group(1)
    return ""


def import_markdown(
    md_text: str,
    *,
    import_date: str = "",
    title: str = "",
    category_slug: str = "",
    filename: str = "",
    overwrite: bool = False,
    fmt: str = "markdown",
) -> dict:
    """解析并导入一份 Markdown / JSON 题库，返回结构化结果（不抛 HTTP 异常）。

    Args:
        md_text: UTF-8 原文（Markdown 或 JSON 文本）。
        import_date: 指定日期；为空时从文件名/标题/正文（或 JSON 的 date 字段）自动探测。
        title: 题库标题；为空时用文件名、JSON 的 title 或「日期 + 每日答题」。
        category_slug: 三级栏目节点 slug，必须是导航配置中的合法值。
        filename: 上传文件名，用于自动探测日期、格式与默认标题。
        overwrite: 同日题库已存在时是否覆盖。
        fmt: ``markdown``（默认，兼容既有调用）或 ``json``。

    Returns:
        {"ok": bool, "reason": str, "message": str, ...}。reason == "exists"
        表示需要前端二次确认（把 md_text 放进 pending_content 再调用一次）。
    """
    fmt = "json" if str(fmt).lower() in ("json", "js") else "markdown"
    def failure(reason: str, message: str) -> dict:
        return {
            "ok": False, "reason": reason, "message": message,
            "date": import_date, "title": title,
            "category_slug": category_slug,
            "category_path": "", "fmt": fmt,
            "total_sets": 0, "total_questions": 0, "failed": [],
            "pending_content": md_text,
        }

    if not (md_text or "").strip():
        return failure(
            "empty",
            "请先选择要导入的 JSON 题库文件" if fmt == "json"
            else "请先选择要导入的 Markdown 文件",
        )

    # JSON 题库允许在文件里自带 date / title，未填写表单时作为默认值
    if fmt == "json":
        try:
            payload = json.loads(md_text)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict):
            import_date = import_date or str(payload.get("date") or "").strip()
            title = title or str(payload.get("title") or "").strip()

    if not title:
        title = filename.rsplit(".", 1)[0] if filename else ""
    if not import_date:
        import_date = guess_date(md_text, title, filename)
    if not import_date:
        return failure(
            "invalid_date",
            "请选择导入日期（或从文件名/标题中识别到形如 2026-11-04 的日期）",
        )
    if not navigation.is_valid_category(category_slug):
        return failure(
            "invalid_category",
            "请选择完整的导入栏目（例如 华为认证 › 数通 › HCIP-数通，"
            "或 阿里云认证 › 阿里云ACP › 云计算高级工程师）",
        )
    category_path = navigation.category_path_label(category_slug)

    parsed = md_parser.parse_auto(md_text, fmt)
    if parsed.total_questions == 0 and not parsed.failed:
        return failure("no_questions", "未能解析出任何题目，请检查格式")
    if not title:
        title = f"{import_date} 每日答题"

    imported = quiz_logic.import_day(
        import_date, title, parsed,
        overwrite=overwrite,
        category_slug=category_slug,
        category_path=category_path,
    )
    if not imported["ok"] and imported.get("reason") == "exists":
        return {
            "ok": False, "reason": "exists",
            "message": "该日期题库已存在，请确认是否覆盖",
            "date": import_date, "title": title,
            "category_slug": category_slug, "category_path": category_path,
            "fmt": fmt,
            "total_sets": 0, "total_questions": 0, "failed": [],
            "pending_content": md_text,
        }

    failed_count = len(imported.get("failed") or [])
    message = (
        f"导入成功：{imported['total_sets']} 套 / {imported['total_questions']} 题"
        + (f"，{failed_count} 题导入失败" if failed_count else "")
    )
    # 保留 import_day 的原始结果（distribution / set_id 等），遗留导入结果页
    # 与 JSON API 都能拿到同一份完整数据。
    result = dict(imported)
    result.update({
        "ok": True, "reason": "", "message": message,
        "date": import_date, "title": title,
        "category_slug": category_slug, "category_path": category_path,
        "fmt": fmt, "pending_content": "",
    })
    return result
