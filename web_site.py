"""认证题库网 · 用户端页面与 JSON API。

页面（全部服务端渲染，利于 SEO）：
    /                       首页：左侧认证分类、右侧排行榜
    /cert/<vendor>          厂商频道
    /cert/<vendor>/<dir>    方向频道；单级方向直接渲染认证详情
    /cert/<vendor>/<dir>/<level>  认证详情（介绍 + 题库 + 每日答题入口）
    /daily-quiz             每日答题（选认证 → 10 题 → 得分与错题解析）
    /ranking                排行榜（日/周/月）
    /profile                个人中心：账号 + 荣誉档案 + 排名 + 历史记录（需登录）
    /honors                 旧地址，301 到 /profile

接口：
    GET  /api/cert/tree
    GET  /api/cert/<path>/intro
    POST /api/quiz/start
    POST /api/quiz/submit
    GET  /api/ranking
    GET  /api/profile/me
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   url_for)

import account
import bank
import catalog
import practice_bank
import quiz_service
import security
import seo as seo_util

site = Blueprint("site", __name__)

# DiceBear 默认头像：用 username 作 seed，同一账号永远得到同一张图，且不同账号不同
AVATAR_STYLE = "initials"
AVATAR_BACKGROUNDS = "2f6bff,4f8bff,7aa5ff,5b8def,3d7bfd"


def dicebear_avatar(seed: str, size: int = 96) -> str:
    """生成稳定的默认头像地址；前端加载失败时回退到昵称首字母。"""
    value = quote(str(seed or "guest"), safe="")
    return (
        f"https://api.dicebear.com/9.x/{AVATAR_STYLE}/svg"
        f"?seed={value}&size={size}"
        f"&backgroundColor={AVATAR_BACKGROUNDS}"
        f"&backgroundType=gradientLinear&fontWeight=700&radius=50&scale=92"
    )


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------

TYPE_LABELS = {"single": "单选题", "multiple": "多选题", "judge": "判断题"}
POOL_LABELS = {"category": "本认证题库", "direction": "同方向题库",
               "vendor": "同厂商题库", "global": "全站题库"}


def _json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _tree_with_counts() -> list[dict]:
    counts = bank.counts_by_path()
    tree = catalog.nav_tree()
    for vendor in tree:
        vendor_total = 0
        for direction in vendor["directions"]:
            direction_total = 0
            for leaf in direction["children"]:
                leaf["count"] = counts.get(leaf["path"], 0)
                direction_total += leaf["count"]
            if direction["single"]:
                # 单级方向的 children 为空，题量直接取它自己
                leaf_path = f"{vendor['slug']}/{direction['slug']}"
                direction_total = counts.get(leaf_path, 0)
                direction["count"] = direction_total
            direction["question_count"] = direction_total
            vendor_total += direction_total
        vendor["question_count"] = vendor_total
    return tree


def _platform_stats() -> dict:
    """首页顶部的平台统计。

    口径说明（均只统计 active 数据）：
    - categories：有可练习内容的认证方向数。只有「每日答题」或「模拟题库」任一里有题的
      active 分类才算，避免把 30 个空方向也算进来虚高数字。
    - questions：全站可练习题目，口径与每日答题抽题源一致：分类有已发布模拟题库时
      按模拟题库计数，否则回退旧题库。后台导入双写不会重复计数，重复导入也不会膨胀，
      并与首页认证树完全一致。
    - quizzes：累计答题次数 = 每日答题成绩 + 模拟答题场次。只数 quiz_records 会漏掉
      用户在模拟题库里的练习。
    """
    questions = int(sum(bank.counts_by_path().values()))
    conn = bank._conn()
    try:
        categories = conn.execute(
            "SELECT COUNT(*) AS c FROM cert_categories c WHERE c.status='active' "
            "AND ("
            "  (SELECT COUNT(*) FROM questions q WHERE q.cert_category_id=c.id AND q.status='active')>0"
            "  OR (SELECT COUNT(*) FROM bank_questions bq JOIN question_banks b ON bq.bank_id=b.id "
            "      WHERE b.cert_path=c.path_slug AND bq.status='active' AND b.status='published')>0"
            ")"
        ).fetchone()["c"]
        quizzes = int(conn.execute("SELECT COUNT(*) AS c FROM quiz_records").fetchone()["c"]) \
            + int(conn.execute("SELECT COUNT(*) AS c FROM bank_practice_sessions").fetchone()["c"])
        users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        return {
            "questions": int(questions),
            "quizzes": int(quizzes),
            "users": int(users),
            "categories": int(categories),
        }
    finally:
        conn.close()


def _period() -> str:
    period = (request.args.get("period") or "day").strip()
    return period if period in ("day", "week", "month") else "day"


def _ranking_block(period: str | None = None, limit: int = 8) -> dict:
    period = period or _period()
    user = security.resolve_current_user()
    data = bank.ranking_payload(period, limit, user["id"] if user else None)
    data["labels"] = {"day": "日榜", "week": "周榜", "month": "月榜"}
    return data


def _related_leaves(leaf: dict, limit: int = 4) -> list[dict]:
    counts = bank.counts_by_path()
    same = [x for x in catalog.all_leaves()
            if x["vendor_slug"] == leaf["vendor_slug"] and x["path"] != leaf["path"]]
    same.sort(key=lambda x: (x["direction_slug"] != leaf["direction_slug"], x["path"]))
    for item in same:
        item["count"] = counts.get(item["path"], 0)
    return same[:limit]


# ---------------------------------------------------------------------------
# 首页
# ---------------------------------------------------------------------------

def render_home():
    tree = _tree_with_counts()
    stats = _platform_stats()
    ranking = _ranking_block("day", 8)
    meta = catalog.home_seo()
    seo = seo_util.page(
        meta["title"], meta["description"], "/",
        keywords=meta["keywords"],
        jsonld=seo_util.website_graph(meta["description"]),
    )
    # 累计答题属于登录后功能，游客页不展示该统计
    logged_in = bool(security.resolve_current_user())
    return render_template(
        "home.html",
        seo=seo,
        tree=tree,
        stats=stats,
        ranking=ranking,
        today=date.today().isoformat(),
        logged_in=logged_in,
    )


@site.route("/api/home")
def api_home():
    return jsonify({"ok": True, "data": {
        "tree": _tree_with_counts(),
        "stats": _platform_stats(),
        "ranking": _ranking_block("day", 8),
    }})


# ---------------------------------------------------------------------------
# 认证频道 / 认证详情
# ---------------------------------------------------------------------------

@site.route("/cert/<vendor_slug>")
def vendor_page(vendor_slug: str):
    vendor = catalog.get_vendor(vendor_slug)
    if not vendor:
        return render_template("404.html", seo={"title": "页面不存在 - 认证题库网"}), 404
    tree = next(v for v in _tree_with_counts() if v["slug"] == vendor_slug)
    leaves = catalog.vendor_leaves(vendor_slug)
    counts = bank.counts_by_path()
    total = sum(counts.get(l["path"], 0) for l in leaves)
    meta = catalog.vendor_seo(vendor)
    crumbs = [{"label": "首页", "url": "/"}, {"label": vendor["name"], "url": f"/cert/{vendor_slug}"}]
    seo = seo_util.page(
        meta["title"], meta["description"], meta["canonical"],
        keywords=meta["keywords"],
        jsonld=seo_util.graph(
            seo_util.breadcrumb_node(crumbs),
            seo_util.collection_node(
                f"{vendor['name']}认证题库", meta["description"], meta["canonical"],
            ),
        ),
    )
    return render_template(
        "cert.html",
        seo=seo,
        mode="vendor",
        vendor=tree,
        directions=tree["directions"],
        leaves=leaves,
        total_questions=total,
        ranking=_ranking_block("day", 5),
        crumbs=crumbs,
    )


@site.route("/cert/<vendor_slug>/<direction_slug>")
def direction_page(vendor_slug: str, direction_slug: str):
    vendor = catalog.get_vendor(vendor_slug)
    direction = catalog.get_direction(vendor_slug, direction_slug)
    if not vendor or not direction:
        return render_template("404.html", seo={"title": "页面不存在 - 认证题库网"}), 404
    if catalog.is_single_level(vendor_slug, direction_slug):
        # 单级方向本身就是认证详情页
        return render_leaf(f"{vendor_slug}/{direction_slug}")
    tree = next(v for v in _tree_with_counts() if v["slug"] == vendor_slug)
    direction_view = next(d for d in tree["directions"] if d["slug"] == direction_slug)
    meta = catalog.direction_seo(vendor, direction)
    crumbs = catalog.breadcrumbs_for_path(f"{vendor_slug}/{direction_slug}")
    seo = seo_util.page(
        meta["title"], meta["description"], meta["canonical"],
        keywords=meta["keywords"],
        jsonld=seo_util.graph(
            seo_util.breadcrumb_node(crumbs),
            seo_util.collection_node(
                f"{vendor['name']}{direction['name']}题库",
                meta["description"], meta["canonical"],
            ),
        ),
    )
    return render_template(
        "cert.html",
        seo=seo,
        mode="direction",
        vendor=tree,
        direction=direction_view,
        leaves=[l for l in catalog.direction_leaves(vendor_slug, direction_slug)],
        total_questions=direction_view["question_count"],
        ranking=_ranking_block("day", 5),
        crumbs=crumbs,
    )


@site.route("/cert/<vendor_slug>/<direction_slug>/<level_slug>")
def leaf_page(vendor_slug: str, direction_slug: str, level_slug: str):
    return render_leaf(f"{vendor_slug}/{direction_slug}/{level_slug}")


def render_leaf(path: str):
    leaf = catalog.get_leaf(path)
    if not leaf:
        return render_template("404.html", seo={"title": "页面不存在 - 认证题库网"}), 404
    category = bank.get_category_by_path(path)
    if not category:
        return render_template("404.html", seo={"title": "认证暂未开放 - 认证题库网"}), 404

    intro = bank.get_intro(category["id"]) or {
        "positioning": leaf["positioning"], "audience": leaf["audience"],
        "exam_topics": leaf["topics"], "difficulty": leaf["difficulty"],
        "advice": leaf["advice"], "summary": leaf["summary"],
    }
    user = security.resolve_current_user()
    summary = quiz_service.category_summary(category["id"], user["id"] if user else None)

    page = max(1, int(request.args.get("page") or 1))
    listing = bank.list_questions(category["id"], page=page, page_size=8)
    for item in listing["questions"]:
        item["type_label"] = TYPE_LABELS.get(item["type"], item["type"])

    # 该认证下已发布的模拟题库：管理员在后台建好、发布后立即出现在这里
    banks = practice_bank.list_banks(
        user_id=int(user["id"]) if user else 0,
        cert_path=path,
    )
    bank_total = sum(int(b.get("question_count") or b.get("total_questions") or 0) for b in banks)

    title = category.get("seo_title") or leaf["seo_title"]
    keywords = category.get("seo_keywords") or leaf["keywords"]
    description = (category.get("seo_description") or leaf["seo_description"]
                   or (intro.get("summary") or "")[:110])
    crumbs = catalog.breadcrumbs_for_path(path)
    seo = seo_util.page(
        title, description, f"/cert/{path}",
        keywords=keywords,
        jsonld=seo_util.graph(
            seo_util.breadcrumb_node(crumbs),
            seo_util.learning_resource_node(
                f"{category['cert_name']}题库", description, f"/cert/{path}",
                level=intro.get("difficulty") or category.get("difficulty"),
                questions=listing["total"],
                vendor=leaf["vendor_name"],
            ),
        ),
    )
    return render_template(
        "cert.html",
        seo=seo,
        mode="leaf",
        leaf=leaf,
        category=category,
        intro=intro,
        summary=summary,
        listing=listing,
        banks=banks,
        bank_total=bank_total,
        difficulty=intro.get("difficulty") or category.get("difficulty") or "进阶",
        related=_related_leaves(leaf),
        ranking=_ranking_block("day", 5),
        crumbs=crumbs,
    )


# ---------------------------------------------------------------------------
# 每日答题
# ---------------------------------------------------------------------------

@site.route("/daily-quiz")
def daily_quiz_page():
    tree = _tree_with_counts()
    preselect = (request.args.get("category") or "").strip()
    preselect_id = ""
    if preselect:
        cat = bank.get_category_by_path(preselect) or (
            bank.get_category(int(preselect)) if preselect.isdigit() else None
        )
        if cat:
            preselect_id = cat["id"]
            preselect = cat["path_slug"]
    seo = seo_util.page(
        "每日答题 - 认证题库随机 10 题在线练习 - 认证题库网",
        "选择厂商、方向与等级，从对应认证题库随机抽取 10 道题，每题 10 分，"
        "交卷后立即查看得分、正确率与错题解析。",
        "/daily-quiz",
        keywords="每日答题,认证题库,在线刷题,随机10题,认证考试模拟",
        jsonld=seo_util.graph(seo_util.webpage_node(
            "每日答题 - 认证题库随机 10 题在线练习",
            "从阿里云、华为、AWS、思科认证题库随机抽 10 题，交卷后查看得分与错题解析。",
            "/daily-quiz",
        )),
    )
    return render_template(
        "daily_quiz.html",
        seo=seo,
        tree=tree,
        preselect=preselect,
        preselect_id=preselect_id,
        type_labels=TYPE_LABELS,
        ranking=_ranking_block("day", 5),
    )


@site.route("/api/cert/tree")
def api_tree():
    return jsonify({"ok": True, "data": {"tree": _tree_with_counts()}})


@site.route("/api/cert/<path:path_slug>/intro")
def api_intro(path_slug: str):
    category = bank.get_category_by_path(path_slug)
    if not category:
        return _json_error("认证分类不存在", 404)
    intro = bank.get_intro(category["id"]) or {}
    return jsonify({"ok": True, "data": {
        "category": category,
        "intro": intro,
        "question_count": bank.count_questions(category["id"]),
    }})


@site.route("/api/quiz/start", methods=["POST"])
@security.api_user_required
def api_quiz_start():
    payload = request.get_json(silent=True) or {}
    raw = payload.get("category_id") or payload.get("category") or payload.get("path")
    category_id = 0
    if str(raw).isdigit():
        category_id = int(raw)
    elif isinstance(raw, str) and raw:
        cat = bank.get_category_by_path(raw)
        category_id = cat["id"] if cat else 0
    if not category_id:
        return _json_error("请选择认证分类")
    try:
        data = quiz_service.start(category_id)
    except ValueError as exc:
        return _json_error(str(exc), 409)
    return jsonify({"ok": True, "data": data})


@site.route("/api/quiz/submit", methods=["POST"])
@security.api_user_required
def api_quiz_submit():
    payload = request.get_json(silent=True) or {}
    try:
        category_id = int(payload.get("category_id") or 0)
    except (TypeError, ValueError):
        category_id = 0
    if not category_id:
        return _json_error("缺少认证分类")
    try:
        data = quiz_service.grade(
            category_id,
            payload.get("question_ids") or [],
            payload.get("answers") or {},
            payload.get("duration") or 0,
            payload.get("started_at") or "",
            source=str(payload.get("source") or ""),
        )
    except PermissionError as exc:
        return _json_error(str(exc), 401)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return jsonify({"ok": True, "data": data})


# ---------------------------------------------------------------------------
# 排行榜 / 个人荣誉
# ---------------------------------------------------------------------------

@site.route("/ranking")
def ranking_page():
    period = _period()
    data = _ranking_block(period, 50)
    period_label = data["labels"][period]
    path = "/ranking" if period == "day" else f"/ranking?period={period}"
    seo = seo_util.page(
        f"认证题库排行榜 - {period_label} - 认证题库网",
        "认证题库网积分排行榜：按日榜、周榜、月榜查看昵称、积分、答题次数与最高分，"
        "每日答题每题 10 分，满分 100 分。",
        path,
        keywords="认证题库排行榜,刷题积分,每日答题排名,学习榜",
        jsonld=seo_util.graph(seo_util.webpage_node(
            f"认证题库排行榜 - {period_label}",
            "查看认证题库网日榜、周榜、月榜的积分、答题次数与最高分。",
            path,
        )),
    )
    trend_owner = "我的"
    trend = data.get("trend") or []
    if not trend and data["rows"]:
        trend = bank.user_trend(data["rows"][0]["user_id"])
        trend_owner = data["rows"][0]["display_name"]
    return render_template(
        "ranking.html",
        seo=seo,
        period=period,
        labels=data["labels"],
        rows=data["rows"],
        me=data.get("me"),
        trend=trend,
        trend_owner=trend_owner,
        period_key=data["period_key"],
    )


@site.route("/api/ranking")
def api_ranking():
    limit = request.args.get("limit", "20")
    try:
        limit = max(1, min(100, int(limit)))
    except ValueError:
        limit = 20
    user = security.resolve_current_user()
    data = bank.ranking_payload(_period(), limit, user["id"] if user else None)
    return jsonify({"ok": True, "data": data})


@site.route("/profile")
@security.login_required
def profile_page():
    """个人中心：账号信息 + 荣誉档案 + 排名 + 历史记录（原 /honors 的内容已并入）。"""
    user = security.resolve_current_user()
    data = bank.my_honors(user["id"], 50)
    ranks = {p: bank.user_rank(p, user["id"]) for p in ("day", "week", "month")}
    profile = account.profile_payload(user["id"])
    seo = {
        "title": "个人中心 - 我的答题记录与荣誉 - 认证题库网",
        "keywords": "个人中心,答题记录,积分,荣誉徽章,认证刷题",
        "description": "个人中心汇总我的累计积分、最佳成绩、答题次数、荣誉徽章、日/周/月排名与历史答题记录。",
        "canonical": "/profile",
        "robots": "noindex,follow",
    }
    return render_template(
        "profile.html",
        seo=seo,
        user=user,
        profile=profile,
        avatar_url=dicebear_avatar(user["username"]),
        stats=data["stats"],
        badges=data["badges"],
        records=data["records"],
        recent=data["recent"],
        earned=[b for b in data["badges"] if b["earned"]],
        ranks=ranks,
    )


@site.route("/honors")
def honors_legacy():
    """个人荣誉已并入个人中心，保留旧地址做 301，避免历史链接与收藏失效。"""
    return redirect(url_for("site.profile_page"), code=301)


@site.route("/api/profile/me")
@site.route("/api/honors/me")  # 兼容旧调用方
@security.api_user_required
def api_profile_me():
    user = security.resolve_current_user()
    data = bank.my_honors(user["id"], 50)
    data["ranks"] = {p: bank.user_rank(p, user["id"]) for p in ("day", "week", "month")}
    data["avatar"] = dicebear_avatar(user["username"])
    return jsonify({"ok": True, "data": data})


# ---------------------------------------------------------------------------
# SEO 辅助
# ---------------------------------------------------------------------------

@site.route("/robots.txt")
def robots():
    """只放行内容页；登录态、接口、一次性操作页全部挡在爬虫之外。"""
    base = seo_util.base_url()
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /api/",
        "Disallow: /profile",
        "Disallow: /quiz/",
        "Disallow: /result/",
        "Disallow: /wrong",
        "Disallow: /score/",
        "Disallow: /date/",
        "Disallow: /import",
        "Disallow: /export/",
        "Disallow: /leaderboard",
        "Disallow: /login",
        "Disallow: /register",
        "Disallow: /forgot-password",
        "Disallow: /reset-password",
        "",
        "Sitemap: " + base + "/sitemap.xml",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


@site.route("/sitemap.xml")
def sitemap():
    """公开可索引页面清单：首页 + 每日答题 + 排行榜 + 厂商/方向/认证三级页。"""
    base = seo_util.base_url()
    today = date.today().isoformat()
    urls = [
        (base + "/", "1.0", "daily", today),
        (base + "/daily-quiz", "0.9", "daily", today),
        (base + "/ranking", "0.8", "daily", today),
    ]
    for vendor in catalog.VENDORS:
        urls.append((base + f"/cert/{vendor['slug']}", "0.9", "weekly", ""))
        for direction in vendor["directions"]:
            urls.append((base + f"/cert/{vendor['slug']}/{direction['slug']}", "0.8", "weekly", ""))
            for level in direction.get("levels") or ():
                urls.append((
                    base + f"/cert/{vendor['slug']}/{direction['slug']}/{level['slug']}",
                    "0.7", "weekly", "",
                ))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, priority, freq, lastmod in urls:
        item = f"  <url><loc>{loc}</loc>"
        if lastmod:
            item += f"<lastmod>{lastmod}</lastmod>"
        item += f"<changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        parts.append(item)
    parts.append("</urlset>")
    return "\n".join(parts), 200, {"Content-Type": "application/xml; charset=utf-8"}


# ---------------------------------------------------------------------------
# 旧地址兼容（301 到新结构，避免死链）
# ---------------------------------------------------------------------------

LEGACY_MAP = {
    "aca": "aliyun/aca",
    "acp": "aliyun/acp",
    "acp-cloud-computing": "aliyun/acp/cloud",
    "ace": "aliyun/ace",
    "hcip-datacom": "huawei/datacom/hcip",
    "hcip-cloud": "huawei/cloud/hcip",
    "hcip-security": "huawei/security/hcip",
    "hcip-storage": "huawei/storage/hcip",
    "aws-solutions-architect": "aws/solutions-architect/associate",
    "aws-cloud-practitioner": "aws/cloud-practitioner",
    "aws-devops-engineer": "aws/devops",
    "ccna": "cisco/ccna",
    "ccnp": "cisco/ccnp/enterprise",
}


@site.route("/certification")
@site.route("/certification/<slug>")
def legacy_cert(slug: str = ""):
    if not slug:
        return redirect("/cert/aliyun", code=301)
    target = LEGACY_MAP.get(slug, "")
    if target and catalog.get_leaf(target):
        return redirect(f"/cert/{target}", code=301)
    return redirect("/", code=301)


@site.route("/practice")
def practice_page():
    """独立题库目录已下线：题库改为直接展示在各认证详情页。

    保留这条 301 只是为了让旧书签和外链不至于 404。
    """
    return redirect("/", code=301)


@site.route("/practice/<slug>")
def practice_bank_page(slug: str):
    """模拟答题页：题面与选项由前端按场次拉取，答案在交卷前不下发。"""
    bank_row = practice_bank.get_bank(slug)
    if not bank_row or bank_row.get("status") == "draft":
        return render_template("404.html", seo={"title": "题库不存在 - 认证题库网"}), 404
    user = security.resolve_current_user()
    user_id = int(user["id"]) if user else 0
    state = practice_bank.user_bank_state(user_id, bank_row)
    title = f"{bank_row['name']} · 模拟答题 - 认证题库网"
    seo = seo_util.page(
        title,
        f"{bank_row['name']}：共 {bank_row['total_questions']} 题，"
        f"满分 {int(float(bank_row['total_score']))} 分，"
        f"{int(float(bank_row['pass_score']))} 分合格，限时 {bank_row['duration_minutes']} 分钟。",
        f"/practice/{slug}",
        robots="noindex,follow",
    )
    return render_template(
        "bank_quiz.html",
        seo=seo,
        bank=state,
        bank_status=bank_row.get("status"),
        session_token=(request.args.get("session") or "").strip(),
        user_id=user_id,
    )
