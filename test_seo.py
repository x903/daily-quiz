"""回归测试：全站 SEO 输出与可抓取性。

独立临时数据库，不触碰 data/quiz.db。

覆盖：
1. canonical / og:url / og:image 全部为绝对地址，且跟随 SITE_BASE_URL；
2. 公开页 index,follow；登录态、接口、后台 noindex（meta + X-Robots-Tag 双保险）；
3. JSON-LD 可解析：首页 Organization+WebSite，认证页 BreadcrumbList+LearningResource；
4. sitemap.xml 用正式域名、含全部认证页与 lastmod，不收录登录页；
5. robots.txt 屏蔽私有路径并声明 sitemap；
6. 404 返回 404 + h1 + noindex；gzip 生效；
7. 静态资源带版本号并长缓存。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_seo_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

os.environ["SITE_BASE_URL"] = "https://quiz.example.com"

from app import app  # noqa: E402
import catalog  # noqa: E402

PUBLIC_PAGES = ["/", "/daily-quiz", "/ranking", "/cert/aliyun", "/cert/aliyun/acp",
                "/cert/aliyun/acp/cloud", "/cert/huawei/datacom/hcip", "/cert/cisco/ccna"]


def body(path: str, client=None) -> str:
    client = client or app.test_client()
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} 返回 {resp.status_code}"
    return resp.get_data(as_text=True)


def literal(html: str, pattern: str) -> str:
    match = re.search(pattern, html, re.S)
    return match.group(1).strip() if match else ""


def jsonld(html: str) -> dict:
    raw = literal(html, r'<script type="application/ld\+json">(.*?)</script>')
    assert raw, "页面缺少 JSON-LD"
    return json.loads(raw)


def main() -> None:
    anon = app.test_client()

    # ---------------------------------------------------------------- 1) 基础 head
    home = body("/", anon)
    assert '<html lang="zh-CN">' in home
    assert literal(home, r'<link rel="canonical" href="([^"]+)"') == "https://quiz.example.com/"
    assert literal(home, r'<meta property="og:url" content="([^"]+)"') == "https://quiz.example.com/"
    assert literal(home, r'<meta property="og:image" content="([^"]+)"') == \
        "https://quiz.example.com/static/og-cover.png"
    assert 'name="twitter:card"' in home and "summary_large_image" in home
    assert literal(home, r'<meta name="robots" content="([^"]+)"').startswith("index,follow")
    assert literal(home, r'<meta name="description" content="([^"]+)"')
    assert re.search(r'<link rel="icon"[^>]+favicon\.svg', home)
    print("1) 首页 canonical / OG / Twitter / favicon / robots 全部正确 ✅")

    # ---------------------------------------------------------------- 2) 每页唯一 h1
    for path in PUBLIC_PAGES:
        html = body(path, anon)
        count = len(re.findall(r"<h1[\s>]", html))
        assert count == 1, f"{path} 的 h1 数量为 {count}，应为 1"
        title = literal(html, r"<title>(.*?)</title>")
        desc = literal(html, r'<meta name="description" content="([^"]*)"')
        assert 10 <= len(title) <= 70, f"{path} title 长度异常：{len(title)} {title}"
        assert 30 <= len(desc) <= 200, f"{path} description 长度异常：{len(desc)}"
        canonical = literal(html, r'<link rel="canonical" href="([^"]+)"')
        assert canonical.startswith("https://quiz.example.com/"), f"{path} canonical 非绝对地址：{canonical}"
    print("2) 公开页均只有一个 h1，title / description 长度达标 ✅")

    # ---------------------------------------------------------------- 3) 结构化数据
    types = {node.get("@type") for node in jsonld(home).get("@graph", [])}
    assert {"Organization", "WebSite"} <= types, types

    vendor_html = body("/cert/aliyun", anon)
    vendor_types = {n.get("@type") for n in jsonld(vendor_html).get("@graph", [])}
    assert {"BreadcrumbList", "CollectionPage"} <= vendor_types, vendor_types

    leaf_html = body("/cert/aliyun/acp/cloud", anon)
    leaf_ld = jsonld(leaf_html)
    leaf_types = {n.get("@type") for n in leaf_ld.get("@graph", [])}
    assert {"BreadcrumbList", "LearningResource"} <= leaf_types, leaf_types
    resource = next(n for n in leaf_ld["@graph"] if n["@type"] == "LearningResource")
    assert resource["url"] == "https://quiz.example.com/cert/aliyun/acp/cloud"
    assert resource["inLanguage"] == "zh-CN"
    crumbs = next(n for n in leaf_ld["@graph"] if n["@type"] == "BreadcrumbList")
    assert len(crumbs["itemListElement"]) >= 3
    assert crumbs["itemListElement"][1]["item"].startswith("https://quiz.example.com/")
    print("3) JSON-LD：Organization / WebSite / BreadcrumbList / LearningResource 均可解析 ✅")

    # ---------------------------------------------------------------- 4) noindex 策略
    # 重置页带一次性 token，比普通表单更严格：noindex,nofollow
    for path, expected in (("/login", "noindex,follow"), ("/register", "noindex,follow"),
                           ("/forgot-password", "noindex,follow"),
                           ("/reset-password", "noindex,nofollow")):
        resp = anon.get(path)
        robots = literal(resp.get_data(as_text=True), r'<meta name="robots" content="([^"]+)"')
        assert robots == expected, f"{path} robots={robots}"
        assert resp.headers.get("X-Robots-Tag") == expected

    private = {"/profile": "noindex,nofollow", "/wrong": "noindex,nofollow",
               "/admin/login": "noindex,nofollow", "/leaderboard": "noindex,nofollow"}
    for path, expected in private.items():
        resp = anon.get(path, follow_redirects=False)
        assert resp.headers.get("X-Robots-Tag") == expected, f"{path} 头为 {resp.headers.get('X-Robots-Tag')}"
    api = anon.get("/api/cert/tree")
    assert api.headers.get("X-Robots-Tag") == "noindex,nofollow"
    print("4) 登录页 noindex,follow；个人中心 / 后台 / 接口 noindex,nofollow ✅")

    # ---------------------------------------------------------------- 5) sitemap
    sitemap = body("/sitemap.xml", anon)
    assert sitemap.startswith("<?xml") and sitemap.rstrip().endswith("</urlset>")
    assert sitemap.count("<url>") == sitemap.count("</url>")
    for path in ("/", "/daily-quiz", "/ranking"):
        assert f"<loc>https://quiz.example.com{path}</loc>" in sitemap
    for leaf in catalog.all_leaves():
        assert f"<loc>https://quiz.example.com{leaf['url']}</loc>" in sitemap, leaf["url"]
    assert "<lastmod>" in sitemap
    for private_path in ("/login", "/profile", "/admin", "/leaderboard", "/date/"):
        assert private_path not in sitemap, f"sitemap 不应包含 {private_path}"
    print("5) sitemap 覆盖全部认证页、带 lastmod、不含私有页 ✅")

    # ---------------------------------------------------------------- 6) robots
    robots = body("/robots.txt", anon)
    for rule in ("Disallow: /admin", "Disallow: /api/", "Disallow: /profile",
                 "Disallow: /wrong", "Disallow: /score/", "Disallow: /import"):
        assert rule in robots, f"robots 缺少 {rule}"
    assert "Sitemap: https://quiz.example.com/sitemap.xml" in robots
    print("6) robots.txt 屏蔽私有路径并指向正式域名 sitemap ✅")

    # ---------------------------------------------------------------- 7) 404 与压缩
    resp = anon.get("/no-such-page")
    assert resp.status_code == 404
    html = resp.get_data(as_text=True)
    assert "<h1>页面不存在</h1>" in html
    assert 'name="robots" content="noindex,follow"' in html
    assert resp.headers.get("X-Robots-Tag") == "noindex,follow"
    assert '<link rel="canonical"' not in html, "404 不应输出 canonical"

    raw = anon.get("/cert/aliyun/acp/cloud").get_data()
    gz = anon.get("/cert/aliyun/acp/cloud", headers={"Accept-Encoding": "gzip"})
    assert gz.headers.get("Content-Encoding") == "gzip"
    assert len(gz.get_data()) < len(raw) * 0.5, "gzip 压缩率不足"
    assert "Accept-Encoding" in gz.headers.get("Vary", "")
    print("7) 自定义 404（h1 + noindex）；HTML gzip 压缩生效 ✅")

    # ---------------------------------------------------------------- 8) 静态缓存
    css = anon.get("/static/style.css")
    assert css.status_code == 200
    cache = css.headers.get("Cache-Control", "")
    assert "max-age=604800" in cache, cache
    assert "?v=" in literal(home, r'<link rel="stylesheet" href="([^"]*style\.css[^"]*)"')
    print("8) 静态资源带版本号，Cache-Control 7 天 ✅")

    print("\n全部 SEO 断言通过")


if __name__ == "__main__":
    main()
