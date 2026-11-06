"""回归测试：主页「阿里云认证」三级菜单与配置驱动的页面骨架。

全程使用独立临时数据库，不触碰正在运行的 data/quiz.db。

覆盖：
1. 菜单树层级（一级 / 二级 / 三级）、slug 唯一、页面上下文与面包屑；
2. 5 个入口的页面骨架（标题、简介占位、子栏目卡片、内容建设中）与 404；
3. 导航渲染：三级下拉结构、现有菜单项不受影响、普通用户无导入入口；
4. 未登录拦截与既有页面无回归；
5. 动态扩展 ACP 方向：只改配置即同时生成菜单项与页面。
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

# 必须在 import app 之前重定向数据库：app.py 在模块加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_cert_nav_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

from app import app  # noqa: E402
import auth  # noqa: E402
import navigation  # noqa: E402

URLS = {
    "aliyun-cert": "/certification",
    "aca": "/certification/aca",
    "acp": "/certification/acp",
    "ace": "/certification/ace",
    "acp-cloud-computing": "/certification/acp-cloud-computing",
}


def login(client, username: str, password: str) -> None:
    assert client.post("/login", data={"username": username, "password": password}).status_code == 302


def body_of(client, url: str) -> str:
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    return r.get_data(as_text=True)


try:
    models.init_db()
    auth.create_default_users()

    # 1) 菜单配置树
    root = navigation.CERT_NAV
    assert root.label == "阿里云认证" and root.slug == "aliyun-cert"
    assert [c.label for c in root.children] == ["阿里云ACA", "阿里云ACP", "阿里云ACE"]
    acp = root.children[1]
    assert [c.label for c in acp.children] == ["云计算高级工程师"]
    pages = navigation.all_pages()
    assert set(pages) == set(URLS), f"页面配置缺失：{set(URLS) ^ set(pages)}"
    assert pages["aliyun-cert"].depth == 1
    assert all(pages[s].depth == 2 for s in ("aca", "acp", "ace"))
    assert pages["acp-cloud-computing"].depth == 3
    assert [c.label for c in pages["acp-cloud-computing"].crumbs] == [
        "阿里云认证", "阿里云ACP", "云计算高级工程师",
    ]
    assert navigation.find_page("not-exist") is None
    print("1) 菜单树：1 级 / 3 个二级 / 1 个三级，页面上下文与面包屑正确 ✅")

    admin = app.test_client()
    login(admin, "admin", "admin123")

    # 2) 五个入口的页面骨架
    for slug, url in URLS.items():
        page = pages[slug]
        body = body_of(admin, url)
        assert f'<h1 class="page-title">{page.node.label}</h1>' in body
        assert page.node.intro in body, f"{url} 缺少简介占位"
        assert "阿里云认证" in body
    assert "下属栏目" in body_of(admin, "/certification")
    assert "云计算高级工程师" in body_of(admin, "/certification/acp")
    assert "内容建设中" in body_of(admin, "/certification/aca")
    assert "内容建设中" in body_of(admin, "/certification/acp-cloud-computing")
    assert admin.get("/certification/unknown-direction").status_code == 404
    print("2) 总览 / ACA / ACP / ACE / 云计算高级工程师 5 个页面骨架可访问，未知 slug 404 ✅")

    # 3) 导航渲染：三级下拉 + 其它菜单不受影响
    nav_html = body_of(admin, "/")
    for label in ("阿里云认证", "阿里云ACA", "阿里云ACP", "阿里云ACE", "云计算高级工程师"):
        assert label in nav_html, f"导航缺少 {label}"
    assert 'class="nav-submenu nav-level-2"' in nav_html
    assert re.search(r'nav-level-3[\s\S]{0,400}云计算高级工程师', nav_html), "三级菜单未嵌套在 ACP 下"
    for old in ("首页", "错题本", "排行榜", "导入题目", "后台管理"):
        assert old in nav_html, f"原有菜单项被破坏：{old}"
    assert "历史日期" not in nav_html, "「历史日期」栏目已下线，不应再出现在导航"
    assert 'src="/static/nav.js"' in nav_html
    print("3) 导航含三级下拉，其它原有菜单完整，历史日期栏目已移除 ✅")

    normal = app.test_client()
    login(normal, "user", "user123")
    user_nav = body_of(normal, "/")
    assert "阿里云认证" in user_nav and "云计算高级工程师" in user_nav
    assert "导入题目" not in user_nav, "普通用户不应出现导入入口"
    print("4) 普通用户可见认证菜单，仍无导入入口 ✅")

    anon = app.test_client()
    assert anon.get("/certification").status_code == 302
    assert anon.get("/certification/acp").status_code == 302
    assert "阿里云认证" not in anon.get("/login").get_data(as_text=True)
    for url in ("/", "/leaderboard", "/wrong"):
        assert normal.get(url).status_code == 200, f"既有页面异常：{url}"
    for url in ("/import", "/admin"):
        assert admin.get(url).status_code == 200, f"管理员页面异常：{url}"
    print("5) 未登录被拦截；普通用户 / 管理员既有页面无回归 ✅")

    # 6) 扩展：注册一个新 ACP 方向，菜单 + 页面自动生效，无需改模板或路由
    original_tree = navigation.CERT_NAV
    try:
        navigation.register_acp_direction(
            "acp-bigdata", "大数据高级工程师", "ACP 大数据方向（扩展测试占位简介）。"
        )
        new_body = body_of(admin, "/certification/acp-bigdata")
        assert "大数据高级工程师" in new_body and "扩展测试占位简介" in new_body
        assert "大数据高级工程师" in body_of(admin, "/")
        assert "acp-bigdata" in body_of(admin, "/certification/acp")
        try:
            navigation.register_acp_direction("acp-bigdata", "重复方向")
        except ValueError:
            pass
        else:
            raise AssertionError("重复 slug 应被拒绝")
        print("6) 新增 ACP 方向只改配置即生成菜单项与页面，重复 slug 被拒绝 ✅")
    finally:
        navigation.CERT_NAV = original_tree
    assert admin.get("/certification/acp-bigdata").status_code == 404, "恢复配置后应下线"
    assert "大数据高级工程师" not in body_of(admin, "/"), "恢复配置后菜单应还原"

    print("\n全部通过：阿里云认证三级菜单与配置驱动页面骨架验证完成。")
finally:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
