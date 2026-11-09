"""回归测试：认证介绍 CMS、题库绑定与每日答题全链路。

全程使用独立临时 SQLite 库，不触碰正在运行的 data/quiz.db。

覆盖：
1. 目录播种：34 个认证分类 + 34 条五段式介绍，幂等重启不覆盖管理员编辑；
2. 管理端「认证介绍管理」：列表 / 搜索 / 保存 / 校验 / 未知分类 404；
3. 题库导入必须绑定三级认证分类，题目只出现在对应认证页；
4. 管理端题目 CRUD：新增 / 编辑 / 删除，题量同步变化；
5. 每日答题：随机 10 题、答案不下发、服务端判分、错题解析回传；
6. 交卷落库：quiz_records + user_honors + 日 / 周 / 月榜同步累加；
7. 个人中心与排行榜页面展示成绩，未登录不能答题；
8. 用户端 / 管理端接口双向鉴权隔离，管理端 SPA 无用户端导航。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

# 必须在 import app 之前重定向数据库：app.py 在模块加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_cert_intro_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

from app import app  # noqa: E402
import auth  # noqa: E402
import bank  # noqa: E402
import catalog  # noqa: E402
import practice_bank  # noqa: E402
import test_support  # noqa: E402

TARGET = "huawei/datacom/hcip"
SIBLING = "huawei/cloud/hcip"
EMPTY = "cisco/ccna"
MARKER = "【管理员维护】HCIP 数通面向企业网络规划与运维工程师，本段必须优先展示。"

JSON_QUESTIONS = [
    {
        "type": "single" if i % 3 else "multiple",
        "question": f"第 {i} 题：关于企业网络与云架构，下列说法正确的是？",
        "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
        "answer": ["A", "B"] if i % 3 == 0 else "B",
        "explanation": "**考点**：OSPF 多区域与 BGP 选路策略",
    }
    for i in range(1, 13)
]


def admin_client():
    c = app.test_client()
    assert c.post("/admin/login", data={
        "username": "admin", "password": "admin123",
        **test_support.captcha_fields("admin"),
    }).status_code == 302
    return c


def user_client():
    c = app.test_client()
    assert c.post("/login", data={"username": "user", "password": "user123",
                              **test_support.captcha_fields()}).status_code == 302
    return c


try:
    models.init_db()
    auth.create_default_users()

    # ---------------------------------------------------------------- 1) 目录与预置介绍
    cats = bank.list_categories()
    assert len(cats) == 34, f"应播种 34 个认证分类，实际 {len(cats)}"
    assert all(c["has_intro"] == 1 for c in cats), "每个认证分类都应有预置介绍"
    assert all(c["status"] == "active" for c in cats)

    cat = bank.get_category_by_path(TARGET)
    leaf = catalog.get_leaf(TARGET)
    assert cat and cat["cert_name"] == "华为HCIP-数通" == leaf["cert_name"]
    intro = bank.get_intro(cat["id"])
    for key in ("positioning", "audience", "exam_topics", "advice", "summary"):
        assert intro[key], f"预置介绍缺少 {key}"
    assert "BGP" in intro["positioning"] and "MPLS VPN" in intro["exam_topics"]
    # 幂等：再跑一次初始化，分类与介绍数量不变
    models.init_db()
    assert len(bank.list_categories()) == 34 and len(bank.list_intros()) == 34
    print("1) 34 个认证分类与五段式预置介绍落库，重复初始化幂等 ✅")

    admin = admin_client()
    user = user_client()

    # ---------------------------------------------------------------- 2) 介绍 CMS
    r = admin.get("/api/admin/cert-categories")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["total"] == 34 and data["stats"]["categories"] == 34
    assert data["stats"]["questions"] == 0

    items = admin.get("/api/admin/intros?q=HCIP").get_json()["data"]["items"]
    assert len(items) == 4, f"「HCIP」应匹配 4 个认证，实际 {len(items)}"
    assert all("HCIP" in it["cert_name"] for it in items)
    assert admin.get("/api/admin/intros?q=不存在的认证").get_json()["data"]["items"] == []
    assert len(admin.get("/api/admin/intros").get_json()["data"]["items"]) == 34

    bad = admin.post(f"/api/admin/intros/{cat['id']}", json={"positioning": MARKER})
    assert bad.status_code == 400 and "不能为空" in bad.get_json()["error"], "缺字段应被拒绝"
    assert admin.post("/api/admin/intros/999999", json={
        "positioning": "x", "audience": "x", "exam_topics": "x", "advice": "x",
    }).status_code == 404

    saved = admin.post(f"/api/admin/intros/{cat['id']}", json={
        "positioning": MARKER,
        "audience": "企业网络运维、网络规划工程师、系统集成商售前售后。",
        "exam_topics": "OSPF 多区域、IS-IS、BGP 选路、MPLS VPN、VXLAN、SD-WAN、网络割接。",
        "difficulty": "高级",
        "advice": "按「原理 → 实验 → 故障现象 → 定位思路」四步练，BGP 选路是高频难点。",
        "summary": "华为 HCIP 数通认证介绍（管理员维护版）。",
        "source": "华为官方考试大纲 · 测试更新",
    })
    assert saved.status_code == 200 and saved.get_json()["ok"]
    updated = bank.get_intro(cat["id"])
    assert updated["positioning"] == MARKER
    assert updated["difficulty"] == "高级" and updated["updated_by"] == "admin"
    assert updated["source"].startswith("华为官方考试大纲")
    # 重启式幂等播种不得覆盖管理员编辑
    models.init_db()
    assert bank.get_intro(cat["id"])["positioning"] == MARKER
    print("2) 介绍列表 / 搜索 / 保存 / 空字段拦截 / 重启不覆盖管理员编辑 ✅")

    # ---------------------------------------------------------------- 3) 认证页首屏
    page = user.get(f"/cert/{TARGET}").get_data(as_text=True)
    assert MARKER in page, "认证页应展示管理员维护后的介绍"
    assert "高级" in page and "华为官方考试大纲" in page
    assert page.find("认证介绍") < page.find("模拟题库"), "介绍必须在题库之前（首屏）"
    assert "管理员可维护" not in page, "普通用户看到的认证页不应出现后台运营提示"
    assert "题库正在建设中" in page, "未导入题目时应提示建设中"
    assert "/daily-quiz?category=" + TARGET in page, "认证页需提供每日答题入口"
    print("3) 三级认证页首屏优先展示数据库介绍，并提供每日答题入口 ✅")

    # ---------------------------------------------------------------- 4) 导入绑定分类
    # 全站还没有任何题目时，开局应给出可读的 409 提示
    empty_cat = bank.get_category_by_path(EMPTY)
    r = user.post("/api/quiz/start", json={"category_id": empty_cat["id"]})
    assert r.status_code == 409 and "不足" in r.get_json()["error"], "全站题库不足 10 题应拒绝开局"

    r = admin.post("/api/admin/bank/import", json={
        "markdown": json.dumps({"date": "2026-11-22", "title": "HCIP 数通 JSON 题库",
                                "questions": JSON_QUESTIONS}, ensure_ascii=False),
        "filename": "hcip.json", "fmt": "json", "category_id": cat["id"],
    })
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert r.get_json()["data"]["imported"] == 12
    assert bank.count_questions(cat["id"]) == 12
    assert bank.count_questions(bank.get_category_by_path(SIBLING)["id"]) == 0, "兄弟栏目不应串题"

    detail = user.get(f"/cert/{TARGET}").get_data(as_text=True)
    # 认证页只放题库卡片，不再平铺题干；题目内容留到进入答题页才下发
    assert "第 12 题：关于企业网络与云架构" not in detail, "认证页不应平铺题干"
    assert "开始每日答题" in detail and "道题可练" in detail
    assert bank.count_questions(cat["id"]) == 12
    assert "第 12 题：关于企业网络与云架构" not in user.get(f"/cert/{SIBLING}").get_data(as_text=True)

    assert admin.post("/api/admin/bank/import", json={
        "markdown": "随便一段", "category_id": 0,
    }).status_code == 400, "未选认证分类应被拒绝"
    print("4) 导入必须绑定三级认证分类，题目只出现在对应认证页 ✅")

    # ---------------------------------------------------------------- 5) 题目 CRUD
    created = admin.post("/api/admin/questions", json={
        "category_id": cat["id"], "type": "single", "difficulty": "中",
        "question": "【CRUD】BGP 中用于防止环路的路由属性是？",
        "options": {"A": "AS_Path", "B": "MED", "C": "Next_Hop", "D": "Community"},
        "answer": "A", "explanation": "AS_Path 记录途经 AS，收到含本 AS 号的路由会丢弃。",
    })
    assert created.status_code == 200, created.get_json()
    qid = created.get_json()["data"]["id"]
    assert bank.count_questions(cat["id"]) == 13
    assert admin.post(f"/api/admin/questions/{qid}", json={"difficulty": "高级"}).status_code == 200
    assert bank.get_question(qid)["difficulty"] == "高级"
    assert admin.post(f"/api/admin/questions/{qid}", json={"answer": "Z"}).status_code == 400, \
        "答案必须出现在选项中"
    assert admin.post(f"/api/admin/questions/{qid}/delete").status_code == 200
    assert bank.count_questions(cat["id"]) == 12
    print("5) 题目新增 / 编辑 / 删除与题量联动，非法答案被拦截 ✅")

    # ---------------------------------------------------------------- 6) 每日答题
    # 该认证本身没有题目：按「本认证 → 同方向 → 同厂商 → 全站」逐级兜底
    r = user.post("/api/quiz/start", json={"category_id": empty_cat["id"]})
    assert r.status_code == 200, r.get_json()
    fallback = r.get_json()["data"]
    assert fallback["pool"] == "global" and fallback["pool_label"] == "全站题库"
    assert len(fallback["questions"]) == 10, "兜底也应抽满 10 题"

    r = user.post("/api/quiz/start", json={"category": TARGET})
    assert r.status_code == 200, r.get_json()
    start = r.get_json()["data"]
    assert start["total"] == 10 and start["points_per_question"] == 10
    # 后台导入会同步生成模拟题库，每日答题直接复用该题池
    assert start["source"] == "bank", start
    assert start["pool"] == "category" and start["pool_label"] == "本认证模拟题库"
    assert len(start["questions"]) == 10
    assert len({q["id"] for q in start["questions"]}) == 10, "10 道题不得重复"
    for q in start["questions"]:
        assert "answer" not in q and "explanation" not in q, "答案与解析不得提前下发"
        assert q["question"] and len(q["options"]) >= 2

    ids = [q["id"] for q in start["questions"]]
    truth = practice_bank.questions_by_ids(ids)
    right = {str(qid): truth[qid]["answer_list"] for qid in ids}
    wrong = {}
    for qid in ids:
        letters = list(truth[qid]["options"].keys())
        wrong[str(qid)] = [next(l for l in letters if l not in truth[qid]["answer_list"])]

    assert user.post("/api/quiz/submit", json={
        "category_id": cat["id"], "question_ids": ids[:9], "answers": right,
    }).status_code == 400, "题目数量异常应被拒绝"

    r = user.post("/api/quiz/submit", json={
        "category_id": cat["id"], "question_ids": ids, "answers": right,
        "duration": 95, "started_at": start["started_at"],
    })
    assert r.status_code == 200, r.get_json()
    result = r.get_json()["data"]
    assert result["score"] == 100 and result["correct"] == 10 and result["accuracy"] == 1.0
    assert result["duration"] == 95 and result["total"] == 10
    assert len(result["review"]) == 10 and all(x["is_correct"] for x in result["review"])
    assert all(x["explanation"] for x in result["review"]), "错题复盘应带解析"

    r = user.post("/api/quiz/submit", json={
        "category_id": cat["id"], "question_ids": ids, "answers": wrong, "duration": 40,
    })
    assert r.status_code == 200
    second = r.get_json()["data"]
    assert second["score"] == 0 and second["correct"] == 0
    assert all(not x["is_correct"] and x["your_answer"] for x in second["review"])
    assert second["review"][0]["correct_answer"] == truth[ids[0]]["answer_list"]
    print("6) 随机 10 题 / 答案不下发 / 服务端判分：全对 100 分、全错 0 分、逐题解析 ✅")

    # ---------------------------------------------------------------- 7) 落库与榜单
    user_row = models.get_user_by_username("user")
    records = bank.list_user_records(user_row["id"])
    assert len(records) == 2 and records[0]["score"] == 0 and records[1]["score"] == 100
    stats = bank.user_stats(user_row["id"])
    assert stats["times"] == 2 and stats["points"] == 100 and stats["best"] == 100
    assert stats["correct"] == 10 and stats["total"] == 20
    honors = bank.my_honors(user_row["id"])
    assert len(honors["records"]) == 2
    assert any(b["code"] == "perfect" and b["earned"] for b in honors["badges"])
    assert len(honors["records"]) == 2, "user_honors 应每次交卷写一条"

    for period in ("day", "week", "month"):
        rows = bank.top_ranking(period)
        me = next((x for x in rows if x["user_id"] == user_row["id"]), None)
        assert me, f"{period} 榜缺少当前用户"
        assert me["points"] == 100 and me["quiz_count"] == 2 and me["best_score"] == 100
        assert me["display_name"] == "user"
    assert bank.user_rank("day", user_row["id"])["rank"] == 1
    print("7) 交卷写入 quiz_records + user_honors，日 / 周 / 月榜同步累加 ✅")

    # ---------------------------------------------------------------- 8) 页面展示
    ranking_html = user.get("/ranking").get_data(as_text=True)
    assert "user" in ranking_html and "100" in ranking_html
    for period, label in (("day", "日榜"), ("week", "周榜"), ("month", "月榜")):
        body = user.get(f"/ranking?period={period}").get_data(as_text=True)
        assert label in body and "user" in body, period
    profile_html = user.get("/profile").get_data(as_text=True)
    assert "满分王者" in profile_html and "100" in profile_html
    assert "HCIP" in profile_html, "个人中心应展示认证名称"
    print("8) 排行榜（日 / 周 / 月）与个人中心正确展示成绩与徽章 ✅")

    # ---------------------------------------------------------------- 9) 鉴权隔离
    anon = app.test_client()
    assert anon.post("/api/quiz/start", json={"category": TARGET}).status_code == 401
    assert anon.post("/api/quiz/submit", json={"category_id": cat["id"]}).status_code == 401
    legacy = anon.get("/honors", follow_redirects=False)
    assert legacy.status_code == 301 and legacy.headers["Location"].endswith("/profile"), \
        "旧 /honors 已并入个人中心，应 301"
    assert anon.get("/profile").status_code == 302, "未登录访问个人中心应跳登录"
    assert anon.get("/api/profile/me").status_code == 401
    assert user.get("/api/profile/me").status_code == 200
    assert anon.get(f"/api/cert/{TARGET}/intro").status_code == 200, "认证介绍是公开只读接口"
    assert anon.get("/api/admin/intros").status_code == 401
    assert user.get("/api/admin/intros").status_code == 401
    assert admin.post("/api/quiz/start", json={"category": TARGET}).status_code == 401, \
        "管理端 session 不能越权访问用户端接口"

    dash = admin.get("/admin/dashboard").get_data(as_text=True)
    assert "认证介绍管理" in dash and "题库管理" in dash and "排行榜数据" in dash
    for word in ("每日答题", "个人中心", "错题本", "阿里云认证", "导入题目"):
        assert word not in dash, f"管理端不应出现用户端菜单：{word}"
    meta = admin.get("/api/admin/meta").get_json()["data"]
    assert any(s["key"] == "intros" for s in meta["sections"])
    print("9) 用户端 / 管理端接口双向鉴权隔离，管理端 SPA 无用户端导航 ✅")

    print("\n全部通过：认证介绍 CMS、题库绑定、每日答题与榜单落库验证完成。")
finally:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
