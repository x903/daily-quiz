"""新增架构回归：独立 admin 表 + 双登录入口 + /api/admin/ 前后端分离 + 角色隔离。

全程使用独立临时 SQLite 库，不触碰 data/quiz.db；直接 `python test_admin_isolation.py`。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("DB_BACKEND", "sqlite")

import models  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="daily_quiz_isolation_"))
models.DB_PATH = _TMP / "quiz.db"

import auth  # noqa: E402
import test_support  # noqa: E402
from app import app  # noqa: E402

DATE = "2026-11-20"
SLUG = "acp-cloud-computing"
MD_TEXT = """第1套
单选题
云计算的核心特征是什么？
A. 按需自助服务
B. 只能本地部署
答案：A

第2套
单选题
以下哪项属于阿里云产品？
A. ECS
B. 以上都不是
答案：A
"""
# 全站导航只保留公共模块；排行榜 / 错题本入口已收进个人中心
USER_NAV = ("首页", "每日答题", "阿里云认证", "华为认证", "AWS 认证", "思科认证")
PROFILE_ONLY = ("排行榜", "错题本")
# 管理端不得出现的用户端专属文案：「排行榜」在管理端是「排行榜数据」，不能按子串排除
ADMIN_FORBIDDEN = ("首页", "每日答题", "个人中心", "错题本", "阿里云认证",
                   "导入题目", "后台管理")


def upload(client, **extra):
    payload = {
        "date": DATE,
        "title": "隔离测试题库",
        "category_slug": SLUG,
        "md_file": (io.BytesIO(MD_TEXT.encode("utf-8")), "quiz_2026-11-20.md"),
    }
    payload.update(extra)
    return client.post("/api/admin/import", data=payload, content_type="multipart/form-data")


try:
    models.init_db()
    auth.create_default_users()
    app.config["TESTING"] = True

    # 1) admin 表独立存在，users 表不含管理员
    conn = models.get_conn()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(admin)").fetchall()]
    conn.close()
    for expected in ("id", "username", "password", "role", "status", "created_at"):
        assert expected in cols, f"admin 表缺少字段 {expected}"
    assert models.get_admin_by_username("admin") is not None
    assert models.get_user_by_username("admin") is None, "管理员不应残留在 users 表"
    assert models.get_user_by_username("user") is not None
    assert (Path(__file__).parent / "admin_table_mysql.sql").read_text(encoding="utf-8").count("CREATE TABLE") >= 1
    print("1) admin 表独立、字段齐全、默认账号分表；建表 SQL 脚本齐备  ✅")

    # 2) 双入口互不串表
    c = app.test_client()
    assert c.post("/login", data={"username": "admin", "password": "admin123",
                              **test_support.captcha_fields()}).status_code == 200
    assert c.post("/admin/login", data={"username": "user", "password": "user123"}).status_code == 302
    assert c.get("/api/admin/session").get_json()["data"]["logged_in"] is False
    print("2) /login 拒绝管理员，/admin/login 拒绝普通用户  ✅")

    # 3) 管理员登录直达独立后台，且不含任何用户端菜单
    r = test_support.login(c, "admin", "admin123")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/dashboard")
    dash = c.get("/admin/dashboard")
    assert dash.status_code == 200
    body = dash.get_data(as_text=True)
    assert "管理控制台" in body
    for label in ADMIN_FORBIDDEN:
        assert label not in body, f"管理后台仍出现用户端菜单：{label}"
    assert "/static/admin/admin.js" in body and "/static/nav.js" not in body
    print("3) /admin/dashboard 为独立静态前端，彻底不渲染用户导航  ✅")

    # 4) 普通用户界面菜单保持，排行榜 / 错题本只在个人中心
    uc = app.test_client()
    test_support.login(uc, "user", "user123")
    home = uc.get("/").get_data(as_text=True)
    top_nav = home.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
    for label in USER_NAV:
        assert label in top_nav, f"用户端导航缺少：{label}"
    for label in PROFILE_ONLY:
        assert label not in top_nav, f"全站导航不应出现：{label}"
    profile_html = uc.get("/profile").get_data(as_text=True)
    for label in PROFILE_ONLY:
        assert label in profile_html, f"个人中心缺少入口：{label}"
    print("4) 普通用户界面保持，排行榜 / 错题本入口已收进个人中心  ✅")

    # 5) API 鉴权：未登录 / 普通用户 401，管理员 200，且返回的都是 JSON
    assert app.test_client().get("/api/admin/overview").status_code == 401
    denied = uc.get("/api/admin/overview")
    assert denied.status_code == 401 and denied.is_json and denied.get_json()["ok"] is False
    for path in ("/api/admin/session", "/api/admin/meta", "/api/admin/overview",
                 "/api/admin/sets", "/api/admin/users", "/api/admin/records",
                 "/api/admin/categories"):
        resp = c.get(path)
        assert resp.status_code == 200 and resp.is_json and resp.get_json()["ok"], path
    print("5) /api/admin/* 全部 JSON；越权一律 401  ✅")

    # 6) 通过 API 完整走一遍导入 → 编辑 → 删除
    imported = upload(c)
    assert imported.status_code == 200 and imported.get_json()["ok"], imported.get_data(as_text=True)
    assert models.get_daily_set_by_date(DATE) is not None
    detail = c.get(f"/api/admin/sets/{DATE}").get_json()["data"]
    assert len(detail["questions"]) == 2
    form = {"set_date": DATE, "set_title": "隔离测试题库（已改）"}
    for q in detail["questions"]:
        qid = q["id"]
        form[f"q_{qid}_type"] = q["type"]
        form[f"q_{qid}_question"] = q["question"]
        form[f"q_{qid}_answer"] = "".join(q["answer"])
        form[f"q_{qid}_explanation"] = q["explanation"]
        for letter, text in q["options"].items():
            form[f"q_{qid}_option_{letter}"] = text
    saved = c.post(f"/api/admin/sets/{DATE}", data=form)
    assert saved.status_code == 200 and saved.get_json()["ok"], saved.get_data(as_text=True)
    assert models.get_daily_set_by_date(DATE)["title"] == "隔离测试题库（已改）"
    preview = c.get(f"/api/admin/sets/{DATE}/delete-preview")
    assert preview.status_code == 200 and preview.get_json()["ok"]
    bad = c.delete(f"/api/admin/sets/{DATE}", json={"confirm_text": "wrong"})
    assert bad.status_code == 400 and not bad.get_json()["ok"]
    deleted = c.delete(f"/api/admin/sets/{DATE}", json={"confirm_text": DATE})
    assert deleted.status_code == 200 and deleted.get_json()["ok"]
    assert models.get_daily_set_by_date(DATE) is None
    print("6) API 导入 / 逐题编辑 / 删除预览 / 二次确认删除全链路可用  ✅")

    # 7) 管理员账号可通过 API 新增；两套 session 独立退出
    created = c.post("/api/admin/admins", json={"username": "ops01", "password": "ops123456"})
    assert created.status_code == 200 and created.get_json()["ok"]
    assert models.get_admin_by_username("ops01") is not None
    c.get("/admin/logout")
    assert c.get("/api/admin/overview").status_code == 401
    print("7) 管理员账号可新增，退出只清理管理端会话  ✅")

    print("\n✅ 前后端分离与角色路由隔离回归全部通过")
finally:
    shutil.rmtree(_TMP, ignore_errors=True)
