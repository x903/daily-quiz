"""回归测试：题目导入页只保留「上传 Markdown 文件」一种方式。

全程使用独立临时数据库，不触碰正在运行的 data/quiz.db。

覆盖：
1. 导入页不再出现方式二（选择已有文件）与方式三（直接粘贴）的 UI / 脚本；
2. 方式二的读取接口 /import/api/load 已移除；
3. 仅提交 markdown 文本（原方式三）被拒绝，不落库；
4. 上传 MD 文件可导入，日期自动从文件名识别、标题取自文件名；
5. 重复导入的覆盖确认页用隐藏字段暂存文件内容，确认后覆盖成功且无需重选文件；
6. 非 UTF-8 文件不会 500；
7. 未登录 / 普通用户的 GET、POST 均被拦截。
"""
from __future__ import annotations

import html
import io
import re
import shutil
import tempfile
from pathlib import Path

# 必须在 import app 之前重定向数据库：app.py 在模块加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_import_ui_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

from app import app  # noqa: E402
import auth  # noqa: E402

SRC = Path(__file__).resolve().parent / "data" / "ACP模拟题20道_2026-11-04.md"
MD_TEXT = SRC.read_text(encoding="utf-8")
FILENAME = "ACP模拟题20道_2026-11-07.md"   # 文件名带日期，用于验证自动识别
DATE = "2026-11-07"


def login(client, username: str, password: str) -> None:
    import test_support
    r = test_support.login(client, username, password)
    assert r.status_code == 302, f"{username} 登录失败"


def upload(client, filename: str = FILENAME, content: bytes | None = None, **extra):
    """模拟管理员在导入页选择栏目 + 文件并提交（默认不手填日期）。"""
    payload = dict(extra)
    payload.setdefault("category_slug", "acp-cloud-computing")
    payload["md_file"] = (
        io.BytesIO(content if content is not None else MD_TEXT.encode("utf-8")),
        filename,
    )
    return client.post("/import", data=payload, content_type="multipart/form-data")


def set_info(date_str: str):
    s = models.get_daily_set_by_date(date_str)
    if s is None:
        return None
    return s, models.get_all_questions_by_set(s["id"])


try:
    models.init_db()
    auth.create_default_users()
    admin = app.test_client()
    login(admin, "admin", "admin123")

    # 1) 导入页仅剩上传文件的入口
    page = admin.get("/import")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'name="md_file"' in body and "上传 Markdown 文件" in body
    for gone in (
        "方式一", "方式二", "方式三", "选择已有文件", "直接粘贴 Markdown",
        "加载到编辑器", "existing-file-select", "loadExistingFile",
        'name="markdown"', "markdown-textarea", "import/api/load",
    ):
        assert gone not in body, f"导入页仍残留已取消的方式：{gone}"
    # 题目格式帮助仍保留（「格式一/格式二」不是导入方式）
    assert "支持的 Markdown 格式" in body and "格式二：ACP 风格" in body
    assert "autoFillDateFromName" in body, "文件名日期自动识别应保留"
    print("1) 导入页仅保留上传文件；方式二/方式三的 UI 与脚本已清除 ✅")

    # 2) 方式二的读取接口已下线
    assert admin.post("/import/api/load", json={"filename": SRC.name}).status_code == 404
    assert admin.get("/import/api/load").status_code == 404
    print("2) /import/api/load 读取接口已移除（404）✅")

    # 3) 原方式三：仅 POST markdown 文本应被拒绝，且不落库
    r = admin.post("/import", data={"date": DATE, "markdown": MD_TEXT},
                   follow_redirects=True)
    assert r.status_code == 200 and "请先选择要导入的 Markdown 文件" in r.get_data(as_text=True)
    assert set_info(DATE) is None, "粘贴文本不应产生题库"
    print("3) 仅提交粘贴文本被拒绝，未产生任何题库 ✅")

    # 4) 方式一：上传文件正常导入，日期/标题自动识别
    r = upload(admin)
    assert r.status_code == 200 and "导入结果" in r.get_data(as_text=True)
    info = set_info(DATE)
    assert info is not None, "上传后应按文件名日期建库"
    s, questions = info
    assert len(questions) == 20, f"应导入 20 题，实际 {len(questions)}"
    assert "2026-11-07" in s["title"], f"标题应取自文件名，实际 {s['title']}"
    assert s["category_slug"] == "acp-cloud-computing", "应记录选中的栏目 slug"
    assert "云计算高级工程师" in s["category_path"], "应保存栏目完整路径快照"
    print("4) 上传 MD 文件导入 20 题，日期/标题/栏目均正确落库 ✅")

    # 5) 覆盖确认：内容由隐藏字段带回，无需重新选择文件
    r2 = upload(admin)
    confirm_html = r2.get_data(as_text=True)
    assert r2.status_code == 200 and "确认覆盖并导入" in confirm_html
    assert 'name="confirm_overwrite"' in confirm_html
    m = re.search(r'<textarea name="pending_content"[^>]*>(.*?)</textarea>',
                  confirm_html, re.S)
    assert m, "确认页应包含隐藏的 pending_content"
    assert html.unescape(m.group(1)) == MD_TEXT, "暂存内容应与上传文件完全一致"
    assert len(set_info(DATE)[1]) == 20, "未确认前不应改动题库"
    assert 'value="acp-cloud-computing"' in confirm_html, "覆盖确认页应回填已选栏目"
    r3 = admin.post("/import", data={
        "date": DATE,
        "title": s["title"],
        "category_slug": "acp-cloud-computing",
        "confirm_overwrite": "1",
        "pending_content": MD_TEXT,
    })
    assert r3.status_code == 200 and "导入结果" in r3.get_data(as_text=True)
    assert len(set_info(DATE)[1]) == 20, "覆盖后仍应是 20 题"
    assert set_info(DATE)[0]["category_slug"] == "acp-cloud-computing", "覆盖后栏目应保留"
    assert len([x for x in models.list_daily_sets() if x["date"] == DATE]) == 1, "覆盖不应新增题库"
    print("5) 覆盖确认页暂存文件内容与栏目，确认后覆盖成功且不重复建库 ✅")

    # 6) 非 UTF-8 文件友好提示、不 500
    r4 = admin.post(
        "/import",
        data={"date": "2026-11-08",
              "md_file": (io.BytesIO(b"\xff\xfe\x80\x81"), "broken.md")},
        content_type="multipart/form-data", follow_redirects=True,
    )
    assert r4.status_code == 200 and "UTF-8" in r4.get_data(as_text=True)
    assert set_info("2026-11-08") is None
    print("6) 非 UTF-8 文件返回可读提示，不产生 500 ✅")

    # 7) 权限：未登录与普通用户均不能访问 / 通过 POST 导入
    anon = app.test_client()
    assert anon.get("/import").status_code == 302
    assert anon.post("/import", data={"date": "2026-11-09", "markdown": MD_TEXT}).status_code == 302
    normal = app.test_client()
    login(normal, "user", "user123")
    assert normal.get("/import").status_code == 302
    assert upload(normal, filename="hack_2026-11-09.md").status_code == 302
    assert set_info("2026-11-09") is None
    print("7) 未登录 / 普通用户的 GET 与 POST 均被拦截 ✅")

    print("\n全部通过：导入页已取消方式二与方式三，仅保留上传 Markdown 文件。")
finally:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
