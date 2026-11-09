"""布局回归：全站容器宽度不随内容长短变化。

背景：`body` 是 `display:flex; flex-direction:column`，而 `main.container` 用
`margin:0 auto` 居中。在 flex 容器里，横向 auto margin 会覆盖 `align-items:stretch`，
让 main 按「内容宽度」收缩——题干短时整页只有 480px 宽，题干长时才撑满，
同一页面在不同题目下宽度会跳变。

修正方式：给 `.container` 显式 `width:100%`，再由 `max-width` 收敛居中。
这里锁死该规则，避免后续有人把它删回 `max-width + margin:auto`。

直接 `python test_layout.py`，只读静态文件与模板，不依赖数据库。
"""
from __future__ import annotations

import io
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
checks: list[str] = []


def read(rel: str) -> str:
    with io.open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


def check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)
    checks.append(message)


def rule_body(css: str, selector: str) -> str | None:
    # 必须匹配行首的完整选择器，避免把 `main.container` 当成 `.container`
    match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return match.group(1) if match else None


# 1) .container 必须显式撑满，并保留 max-width 与居中
style = read("static/style.css")
container = rule_body(style, ".container")
check(container is not None, "style.css 缺少 .container 规则")
check("width: 100%" in container, ".container 必须写 width:100%，否则 flex 下按内容宽度收缩")
check("max-width: 1200px" in container, ".container 的 max-width 应为 1200px")
check("margin: 0 auto" in container, ".container 仍需 margin:0 auto 居中")
print("1) .container = width:100% + max-width:1200px + margin:0 auto  ✅")

# 2) 不允许别处再覆盖宽度（site.css 之前有一条 max-width:1200px 的重复规则）
site = read("static/site.css")
check(re.search(r"^\.container\s*\{", site, re.M) is None,
      "site.css 不应再覆盖 .container（宽度只保留 style.css 一处定义）")
print("2) site.css 不再重复覆盖 .container  ✅")

# 3) 答题页所在的主栏不能有独立的窄宽度约束
practice = read("static/practice.css")
exam = rule_body(practice, ".bank-exam")
check(exam is not None and "width: 100%" in exam, ".bank-exam 应显式 width:100%")
for selector in (".bank-exam", ".bank-exam-layout", ".bank-question-panel"):
    body = rule_body(practice, selector)
    check(body is not None, "practice.css 缺少 %s 规则" % selector)
    check("max-width" not in body, "%s 不应设窄 max-width" % selector)
print("3) .bank-exam 撑满主栏，答题区无额外窄宽度  ✅")

# 4) 答题页头部：面包屑不重复认证名、答题卡计数有「已答」前缀
quiz = read("templates/bank_quiz.html")
crumb = re.search(r'<div class="bank-exam-crumb">(.*?)</div>', quiz, re.S)
check(crumb is not None, "答题页缺少面包屑")
crumb_html = crumb.group(1)
check(crumb_html.count("{{ bank.cert_name }}") == 1,
      "面包屑只应出现一次认证名（返回链接里），第二处改为「模拟答题」")
check("模拟答题" in crumb_html, "面包屑应标注当前层级「模拟答题」")
check("已答 0/{{ bank.question_count }}" in quiz, "答题卡计数应带「已答」前缀，避免与题号混淆")
print("4) 面包屑不重复认证名；答题卡「已答 N/M」语义明确  ✅")

# 5) 答题页脚本同步使用「已答」前缀
script = read("static/bank_quiz.js")
check("'已答 ' + answered + '/' + total" in script, "bank_quiz.js 的进度文案应与模板一致")
print("5) 脚本与模板的答题卡文案一致  ✅")

print()
print("RESULT: PASS（%d 项）" % len(checks))
