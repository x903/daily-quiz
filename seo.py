"""SEO 工具：绝对地址、页面元信息与 JSON-LD 结构化数据。

设计要点
--------
1. 所有对外 URL（canonical / og:url / sitemap / 结构化数据）统一走 ``absolute()``，
   优先读 ``SITE_BASE_URL``，避免反向代理后拿到 ``http://127.0.0.1:5000`` 这类内网地址。
2. 结构化数据用 schema.org 的 ``@graph`` 组合，一个页面只输出一个 ``script`` 标签。
3. 只做「页面事实」描述，不堆砌关键词；题库类页面输出 LearningResource +
   BreadcrumbList，首页输出 Organization + WebSite。

本模块不依赖 Flask 的 app 对象，只用到请求上下文，便于脚本与测试复用。
"""
from __future__ import annotations

import json
import os
from typing import Iterable, Optional

from flask import request

SITE_NAME = "认证题库网"
SITE_TAGLINE = "阿里云 / 华为 / AWS / 思科认证题库与每日刷题"
DEFAULT_DESCRIPTION = (
    "认证题库网提供阿里云 ACA/ACP/ACE、华为 HCIA/HCIP/HCIE、AWS、思科 CCNA/CCNP/CCIE "
    "认证介绍与题库练习，每日 10 题随机刷题，积分排行榜与个人备考记录。"
)
DEFAULT_KEYWORDS = "认证题库,阿里云题库,华为题库,AWS题库,思科题库,每日刷题,认证考试"
DEFAULT_IMAGE = "/static/og-cover.png"

# 不收录：管理端、接口、登录态页面、一次性操作页
_NOINDEX_PREFIXES = (
    "/admin", "/api/", "/profile", "/quiz/", "/result/", "/wrong", "/score/",
    "/date/", "/import", "/export/", "/leaderboard", "/reset-password",
)
# 不收录但保留链接权重：登录注册、找回密码、具体试卷（含登录态与动态答题数据）
_NOINDEX_FOLLOW_PREFIXES = (
    "/login", "/register", "/forgot-password", "/practice/",
)


def robots_for_path(path: str) -> str:
    """按路径给出 robots 策略，页面 meta 与响应头共用同一套规则。"""
    value = path or "/"
    if value.startswith(_NOINDEX_PREFIXES):
        return "noindex,nofollow"
    if value.startswith(_NOINDEX_FOLLOW_PREFIXES):
        return "noindex,follow"
    return "index,follow,max-image-preview:large"


def base_url() -> str:
    """站点根地址（不带结尾斜杠）。

    生产环境请在 .env / compose 里设置 ``SITE_BASE_URL=https://your-domain``；
    未设置时退回当前请求的 host，仅适合本地开发。
    """
    value = (os.environ.get("SITE_BASE_URL") or "").strip().rstrip("/")
    if value:
        return value
    return request.url_root.rstrip("/")


def absolute(path: str = "") -> str:
    """把站内路径转成绝对地址；已带协议的原样返回。"""
    value = (path or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    if not value:
        return base_url() + "/"
    if not value.startswith("/"):
        value = "/" + value
    return base_url() + value


def page(
    title: str,
    description: str,
    path: str,
    *,
    keywords: Optional[str] = None,
    robots: Optional[str] = None,
    image: Optional[str] = None,
    og_type: str = "website",
    jsonld: Optional[dict] = None,
    canonical: Optional[str] = None,
) -> dict:
    """构造模板用的 seo 字典（base.html 只认识这几个键）。"""
    return {
        "title": title,
        "description": description,
        "path": path,
        # 传空字符串表示「本页不输出 canonical」（如 404）
        "canonical": path if canonical is None else canonical,
        "keywords": keywords,
        "robots": robots,
        "image": image or DEFAULT_IMAGE,
        "og_type": og_type,
        "jsonld": dump(jsonld) if jsonld else "",
    }


def dump(data: Optional[dict]) -> str:
    """序列化 JSON-LD，并切断 ``</script>`` 逃逸可能。"""
    if not data:
        return ""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("</", "<\\/")


def graph(*nodes: Optional[dict]) -> dict:
    """把多个节点合成一个 @graph，丢弃空节点。"""
    items = [n for n in nodes if n]
    if not items:
        return {}
    return {"@context": "https://schema.org", "@graph": items}


# ---------------------------------------------------------------------------
# 结构化数据节点
# ---------------------------------------------------------------------------

def organization_node() -> dict:
    root = base_url()
    return {
        "@type": "Organization",
        "@id": root + "/#organization",
        "name": SITE_NAME,
        "url": root + "/",
        "logo": {
            "@type": "ImageObject",
            "url": absolute(DEFAULT_IMAGE),
            "width": 1200,
            "height": 630,
        },
    }


def website_node(description: str = DEFAULT_DESCRIPTION) -> dict:
    root = base_url()
    return {
        "@type": "WebSite",
        "@id": root + "/#website",
        "url": root + "/",
        "name": SITE_NAME,
        "alternateName": "认证题库网 · 每日刷题",
        "description": description,
        "inLanguage": "zh-CN",
        "publisher": {"@id": root + "/#organization"},
    }


def website_graph(description: str = DEFAULT_DESCRIPTION) -> dict:
    """首页用：Organization + WebSite。"""
    return graph(organization_node(), website_node(description))


def breadcrumb_node(crumbs: Iterable[dict]) -> Optional[dict]:
    """面包屑：crumbs 形如 [{'label': '首页', 'url': '/'}, ...]。"""
    items = []
    for index, crumb in enumerate(crumbs or (), start=1):
        items.append({
            "@type": "ListItem",
            "position": index,
            "name": crumb["label"],
            "item": absolute(crumb.get("url") or "/"),
        })
    if not items:
        return None
    return {"@type": "BreadcrumbList", "itemListElement": items}


def learning_resource_node(
    name: str,
    description: str,
    path: str,
    *,
    level: Optional[str] = None,
    questions: int = 0,
    vendor: Optional[str] = None,
) -> dict:
    """认证题库页用：LearningResource（题库 = 学习资源）。"""
    node = {
        "@type": "LearningResource",
        "name": name,
        "description": (description or "")[:300],
        "url": absolute(path),
        "inLanguage": "zh-CN",
        "learningResourceType": "认证题库",
        "isAccessibleForFree": True,
        "provider": {"@id": base_url() + "/#organization"},
    }
    if level:
        node["educationalLevel"] = level
    if questions:
        node["numberOfItems"] = questions
    if vendor:
        node["about"] = {"@type": "Thing", "name": vendor}
    return node


def collection_node(name: str, description: str, path: str) -> dict:
    """厂商 / 方向频道页用：CollectionPage + ItemList 由子页面自然形成。"""
    return {
        "@type": "CollectionPage",
        "name": name,
        "description": (description or "")[:300],
        "url": absolute(path),
        "inLanguage": "zh-CN",
        "isPartOf": {"@id": base_url() + "/#website"},
    }


def webpage_node(name: str, description: str, path: str) -> dict:
    return {
        "@type": "WebPage",
        "name": name,
        "description": (description or "")[:300],
        "url": absolute(path),
        "inLanguage": "zh-CN",
        "isPartOf": {"@id": base_url() + "/#website"},
    }
