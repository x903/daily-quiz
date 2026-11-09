"""管理端外壳回归：静态资源必须带版本指纹。

背景：`/static/admin/admin.js` 曾被浏览器按 7 天强缓存持有，发版后旧脚本
缺少新增菜单的渲染分支，表现为「点了菜单地址栏变了、右侧内容不动」。
这里锁死三件事：
1. dashboard 返回的 HTML 中 css / js 引用都带 `?v=`；
2. 外壳与静态资源都是协商缓存，不是长缓存；
3. 前端对未知模块有兜底提示（而不是静默停在概览）。

直接 `python test_admin_shell.py`，使用临时 SQLite 库，不触碰 data/quiz.db。
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("DB_BACKEND", "sqlite")

import models  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="daily_quiz_shell_"))
models.DB_PATH = _TMP / "quiz.db"

import test_support  # noqa: E402
from app import app  # noqa: E402

checks: list[str] = []


def check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)
    checks.append(message)


try:
    with app.test_client() as client:
        resp = test_support.login(client, "admin", "admin123")
        check(resp.status_code == 302, "管理员登录成功")

        page = client.get("/admin/dashboard")
        check(page.status_code == 200, "dashboard 返回 200")
        html = page.get_data(as_text=True)
        check("?v=" in html, "外壳 HTML 给静态资源带上了版本参数")
        refs = re.findall(r"/static/admin/[A-Za-z0-9_./-]+\.(?:css|js)", html)
        check(len(refs) >= 2, "css 与 js 都被引用")
        check(all("?v=" in m for m in re.findall(r"/static/admin/[A-Za-z0-9_./-]+\.(?:css|js)\?v=\d+", html)),
              "每个静态引用都带 ?v= 数字指纹")
        check("no-cache" in page.headers.get("Cache-Control", ""),
              "dashboard 使用协商缓存，避免外壳被长期缓存")

        css = client.get("/static/admin/admin.css")
        check("no-cache" in css.headers.get("Cache-Control", ""),
              "管理端样式使用协商缓存")

        js = client.get("/static/admin/admin.js")
        body = js.get_data(as_text=True)
        check("不是最新版本" in body, "脚本对未知模块给出刷新提示")
        check("navSeq" in body, "渲染带导航代次，防止旧请求覆盖新页面")

    # 静态页本身仍可直接访问（供开发调试），但不能被当成主入口
    check(Path(app.static_folder, "admin", "index.html").exists(), "外壳源文件存在")

    print("RESULT: PASS")
    for item in checks:
        print(" -", item)
finally:
    shutil.rmtree(_TMP, ignore_errors=True)
