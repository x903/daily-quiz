"""回归测试：认证目录（catalog）、频道页与全站 SEO。

全程使用独立临时数据库，不触碰正在运行的 data/quiz.db。

覆盖：
1. 目录配置：4 个厂商 / 34 个认证，path 唯一、层级完整，每个认证都有独立 SEO 文案；
2. 导航树：厂商 → 方向 → 认证三层，单级方向退化为二级链接；
3. 面包屑：厂商 / 方向 / 认证三种深度都正确；
4. 页面可达性：4 个厂商频道、所有方向页、34 个认证页全部 200，未知路径 404；
5. 认证页首屏：介绍五段 + 模拟题库 + 每日答题入口，canonical 指向自身；
6. sitemap.xml / robots.txt 覆盖全部认证页并屏蔽后台；
7. 旧地址 /certification/* 与 /practice 301 到新结构；
8. 首页与导航：标题/描述符合 SEO 要求，左侧分类 + 右侧排行榜，导航无管理端入口。
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
import catalog  # noqa: E402
import test_support  # noqa: E402

# 需求里逐一点名的四个旗舰认证，必须各自有独立 SEO 落地页
FLAGSHIPS = {
    "aliyun/acp/cloud": ("/cert/aliyun/acp/cloud", "阿里云ACP云计算高级工程师题库"),
    "huawei/datacom/hcip": ("/cert/huawei/datacom/hcip", "华为HCIP-数通题库"),
    "aws/solutions-architect/associate": ("/cert/aws/solutions-architect/associate",
                                          "AWS Solutions Architect – Associate题库"),
    "cisco/ccna": ("/cert/cisco/ccna", "思科CCNA题库"),
}


def body_of(client, url: str) -> str:
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    return r.get_data(as_text=True)


try:
    models.init_db()
    auth.create_default_users()

    # ---------------------------------------------------------------- 1) 目录与 SEO
    vendors = catalog.VENDORS
    assert [v["slug"] for v in vendors] == ["aliyun", "huawei", "aws", "cisco"]
    assert [v["name"] for v in vendors] == ["阿里云认证", "华为认证", "AWS 认证", "思科认证"]

    leaves = catalog.all_leaves()
    assert len(leaves) == 34, f"认证数量应为 34，实际 {len(leaves)}"
    paths = [leaf["path"] for leaf in leaves]
    assert len(set(paths)) == len(paths), "认证 path 必须唯一"
    for leaf in leaves:
        assert leaf["url"] == f"/cert/{leaf['path']}", leaf["path"]
        assert leaf["cert_name"] and leaf["menu_name"], f"{leaf['path']} 缺少名称"
        assert leaf["positioning"] and leaf["audience"] and leaf["topics"] and leaf["advice"], \
            f"{leaf['path']} 五段式介绍字段不全"
        assert leaf["difficulty"] in ("入门", "进阶", "高级", "专家"), leaf["path"]
        # 每个认证独立 SEO：标题含认证名 + 站点名，关键词/描述非空
        assert leaf["seo_title"] == f"{leaf['cert_name']}题库 - 认证题库网", leaf["path"]
        assert leaf["cert_name"] in leaf["keywords"], leaf["path"]
        assert "题库" in leaf["keywords"] and len(leaf["seo_description"]) >= 10, leaf["path"]
    assert len({leaf["seo_title"] for leaf in leaves}) == 34, "认证页 title 不得重复"
    for path, (url, title) in FLAGSHIPS.items():
        leaf = catalog.get_leaf(path)
        assert leaf and leaf["url"] == url, path
        assert title in leaf["seo_title"], f"{path} 的 SEO 标题应包含「{title}」"
    # 单级方向（ACA / Cloud Practitioner / CCNA）本身就是认证
    assert catalog.is_single_level("aliyun", "aca")
    assert catalog.is_single_level("cisco", "ccna")
    assert not catalog.is_single_level("aliyun", "acp")
    assert catalog.get_leaf("aliyun/nope") is None
    print("1) 4 厂商 / 34 认证目录唯一且完整，每个认证都有独立 SEO 标题与关键词 ✅")

    # ---------------------------------------------------------------- 2) 导航树
    tree = catalog.nav_tree()
    assert len(tree) == 4
    aliyun, huawei, aws, cisco = tree
    assert [d["slug"] for d in aliyun["directions"]] == ["aca", "acp", "ace"]
    assert [c["label"] for c in aliyun["directions"][1]["children"]] == [
        "云计算高级工程师", "大模型高级工程师", "大数据高级工程师",
    ]
    assert aliyun["directions"][0]["single"] and not aliyun["directions"][0]["children"]
    assert [d["slug"] for d in huawei["directions"]] == ["datacom", "cloud", "security", "storage"]
    assert all(len(d["children"]) == 3 for d in huawei["directions"]), "华为每个方向都应有 HCIA/HCIP/HCIE"
    assert len(aws["directions"]) == 7 and len(cisco["directions"]) == 3
    assert len(cisco["directions"][1]["children"]) == 4, "CCNP 应有 4 个方向"
    assert len(cisco["directions"][2]["children"]) == 4, "CCIE 应有 4 个方向"
    tree_paths = [c["path"] for v in tree for d in v["directions"] for c in d["children"]]
    # 单级方向没有 children，它自己就是认证入口（如 /cert/aliyun/aca、/cert/cisco/ccna）
    tree_paths += [d["url"].split("/cert/", 1)[1] for v in tree
                   for d in v["directions"] if d["single"]]
    assert sorted(tree_paths) == sorted(paths), "导航树必须覆盖全部认证且不重复"
    print("2) 导航树三层结构正确：ACA 退化为二级链接，34 个认证全部挂到对应方向下 ✅")

    # ---------------------------------------------------------------- 3) 面包屑
    assert [c["label"] for c in catalog.breadcrumbs_for_path("aliyun/acp/cloud")] == [
        "首页", "阿里云认证", "阿里云ACP", "云计算高级工程师",
    ]
    assert [c["label"] for c in catalog.breadcrumbs_for_path("huawei/datacom/hcip")] == [
        "首页", "华为认证", "数通 Datacom", "HCIP",
    ]
    assert [c["label"] for c in catalog.breadcrumbs_for_path("aliyun/acp")] == [
        "首页", "阿里云认证", "阿里云ACP",
    ]
    assert [c["label"] for c in catalog.breadcrumbs_for_path("cisco")] == ["首页", "思科认证"]
    print("3) 面包屑按 厂商 / 方向 / 认证 深度逐级生成 ✅")

    anon = app.test_client()
    user = app.test_client()
    assert test_support.login(user, "user", "user123").status_code == 302

    # ---------------------------------------------------------------- 4) 页面可达性
    home = body_of(anon, "/")
    assert "<title>认证题库网 - 阿里云、华为、AWS、思科认证题库与每日刷题</title>" in home
    # canonical 统一输出绝对地址（SEO 规范），测试环境下 host 为 localhost
    assert re.search(r'<link rel="canonical" href="http://localhost/">', home)
    assert "阿里云认证" in home and "思科认证" in home
    assert 'class="home-grid"' in home and 'class="home-main"' in home and 'class="home-side"' in home
    assert "认证分类" in home and "排行榜" in home, "首页应为左侧分类 + 右侧排行榜"

    for vendor in vendors:
        page = body_of(anon, f"/cert/{vendor['slug']}")
        assert f"<h1>{vendor['name']}题库</h1>" in page, vendor["slug"]
        assert "认证方向" in page
        for direction in vendor["directions"]:
            d_url = f"/cert/{vendor['slug']}/{direction['slug']}"
            d_page = body_of(anon, d_url)
            if direction.get("levels"):
                assert "选择认证等级" in d_page, d_url
            else:
                assert "认证介绍" in d_page and "模拟题库" in d_page, f"单级方向应直接是认证页：{d_url}"

    for leaf in leaves:
        page = body_of(anon, leaf["url"])
        assert f"<title>{leaf['seo_title']}</title>" in page, leaf["url"]
        assert f'<link rel="canonical" href="http://localhost{leaf["url"]}">' in page, leaf["url"]
        assert f"<h1>{leaf['cert_name']}题库</h1>" in page, leaf["url"]
        for block in ("认证介绍", "认证定位", "适合人群", "考试方向", "备考建议", "模拟题库"):
            assert block in page, f"{leaf['url']} 缺少「{block}」"
        assert "开始每日答题" in page, f"{leaf['url']} 缺少每日答题入口"
        assert page.find("认证介绍") < page.find("模拟题库"), "首屏介绍应在题库之前"
        assert f"/daily-quiz?category={leaf['path']}" in page, f"{leaf['url']} 答题入口未带上认证参数"

    for bad in ("/cert/nope", "/cert/aliyun/nope", "/cert/aliyun/acp/nope", "/cert/nope/x/y"):
        assert anon.get(bad).status_code == 404, f"{bad} 应返回 404"
    print(f"4) 4 个厂商频道 + 全部方向页 + {len(leaves)} 个认证页可达，未知路径 404 ✅")

    # ---------------------------------------------------------------- 5) sitemap / robots
    sitemap = body_of(anon, "/sitemap.xml")
    for leaf in leaves:
        assert f"<loc>http://localhost{leaf['url']}</loc>" in sitemap, leaf["url"]
    assert "<loc>http://localhost/daily-quiz</loc>" in sitemap
    assert "<loc>http://localhost/ranking</loc>" in sitemap
    assert sitemap.startswith("<?xml") and sitemap.rstrip().endswith("</urlset>")
    robots = body_of(anon, "/robots.txt")
    assert "Disallow: /admin" in robots and "Sitemap:" in robots
    print("5) sitemap.xml 覆盖全部认证页，robots.txt 屏蔽后台并声明站点地图 ✅")

    # ---------------------------------------------------------------- 6) 旧地址 301
    legacy = {
        "/certification": "/cert/aliyun",
        "/certification/ccna": "/cert/cisco/ccna",
        "/certification/hcip-datacom": "/cert/huawei/datacom/hcip",
        "/certification/acp-cloud-computing": "/cert/aliyun/acp/cloud",
        "/certification/aws-solutions-architect": "/cert/aws/solutions-architect/associate",
    }
    for old, new in legacy.items():
        r = anon.get(old)
        assert r.status_code == 301, f"{old} -> {r.status_code}"
        assert r.headers["Location"].endswith(new), f"{old} -> {r.headers['Location']}"
    assert anon.get("/certification/no-such-cert").status_code == 301
    # /practice 独立目录页已下线（题库改为内嵌在认证详情页），保留 301 兼容旧书签
    practice_page = anon.get("/practice")
    assert practice_page.status_code == 301
    assert practice_page.headers["Location"].endswith("/")
    print("6) 旧地址 /certification/* 全部 301；/practice 301 回首页 ✅")

    # ---------------------------------------------------------------- 7) 导航与权限边界
    nav_html = body_of(user, "/")
    # 排行榜 / 错题本已从全站导航栏收起，只在个人中心内提供入口
    top_nav = nav_html.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
    for label in ("首页", "每日答题", "阿里云认证", "华为认证", "AWS 认证", "思科认证"):
        assert label in top_nav, f"用户端导航缺少：{label}"
    for moved in ("排行榜", "错题本"):
        assert moved not in top_nav, f"「{moved}」不应出现在全站导航栏"
    # 入口迁移后，个人中心必须仍能直达两个页面
    profile_html = body_of(user, "/profile")
    for label in ("错题本", "排行榜"):
        assert label in profile_html, f"个人中心缺少「{label}」入口"
    assert 'href="/wrong"' in profile_html and 'href="/ranking"' in profile_html
    # 个人荣誉已并入个人中心：导航不再有独立入口，用户名即入口
    assert "个人荣誉" not in nav_html, "导航栏不应再出现「个人荣誉」"
    assert 'href="/profile"' in top_nav and 'title="进入个人中心"' in top_nav
    assert 'class="nav-submenu nav-level-2"' in nav_html
    assert 'nav-submenu nav-level-3' in nav_html
    assert re.search(r'nav-level-3[\s\S]{0,600}云计算高级工程师', nav_html), "三级菜单未嵌套在 ACP 下"
    assert re.search(r'nav-level-3[\s\S]{0,600}HCIP', nav_html), "华为方向下未渲染 HCIP"
    for admin_only in ("导入题目", "后台管理", "题库导入", "管理台"):
        assert admin_only not in nav_html, f"用户端不应出现管理入口：{admin_only}"
    assert "/admin/login" not in nav_html
    # 未登录也能浏览全部公开内容，只有答题动作需要登录
    anon_nav = body_of(anon, "/")
    anon_top = anon_nav.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
    assert "每日答题" in anon_top
    for moved in ("排行榜", "错题本"):
        assert moved not in anon_top, f"游客导航不应出现「{moved}」"
    assert "登录" in anon_nav and "注册" in anon_nav
    assert 'href="/profile"' not in anon_nav, "游客导航不应出现个人中心入口"
    print("7) 用户端导航（无排行榜/错题本）、个人中心入口、三级下拉与无管理端入口 ✅")

    print("\n全部通过：认证目录、频道页、SEO、旧地址兼容与导航渲染验证完成。")
finally:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
