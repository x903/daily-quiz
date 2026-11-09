"""回归测试：未登录浏览（游客模式）与「答题才登录」的边界。

全程使用独立临时 SQLite 库，不触碰正在运行的 data/quiz.db。

覆盖：
1. 未登录可浏览首页 / 排行榜 / 认证栏目 / 题目查看 / 每日答题页；
2. 游客导航只保留公共模块，不出现错题本、导入题目、后台管理；
3. 用户登录页不出现管理员登录入口（管理员始终走独立 /admin/login）；
4. 答题、成绩、错题、导出等动作未登录时 302 到 /login 并带 next；
5. 登录成功后回跳到 next 指定页面，且拒绝站外跳转（防开放重定向）；
6. 抽题 / 判分接口仍要求登录（401），认证介绍与导航树接口公开。
"""
from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path

# 必须在 import app 之前重定向数据库：app.py 在模块加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_guest_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

from app import app  # noqa: E402
import test_support  # noqa: E402

DATE = "2026-11-20"
SLUG = "hcip-datacom"
MD_TEXT = """# 游客浏览测试_2026-11-20
**1.** 在 OSPF 中，用于计算最短路径的算法是？
- A. Dijkstra
- B. RIP
- C. BGP
- D. STP

**答案：A**

**解析：**
- **考点**：OSPF 使用 SPF（Dijkstra）算法
"""


def login(client, username: str, password: str):
    return client.post("/login", data={
        "username": username, "password": password, **test_support.captcha_fields(),
    })


def goto(client, url: str):
    """跟随重定向，返回最终响应。"""
    return client.get(url, follow_redirects=True)


try:
    models.init_db()

    # 先用管理员接口导入一套题库，供游客浏览与答题跳转使用
    admin = app.test_client()
    assert admin.post(
        "/admin/login", data={
            "username": "admin", "password": "admin123",
            **test_support.captcha_fields("admin"),
        }
    ).status_code == 302
    r = admin.post(
        "/api/admin/import",
        data={
            "md_file": (io.BytesIO(MD_TEXT.encode("utf-8")), f"guest_{DATE}.md"),
            "date": DATE,
            "title": "游客浏览测试",
            "category_slug": SLUG,
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200 and r.get_json()["ok"], r.get_data(as_text=True)
    print("0) 准备题库：管理员导入 1 套到 hcip-datacom ✅")

    # ---------------------------------------------------------------- 1) 公开浏览
    anon = app.test_client()
    public_pages = [
        "/", "/ranking", "/api/ranking",
        "/cert/aliyun", "/cert/huawei/datacom/hcip", "/cert/aws/cloud-practitioner",
        f"/date/{DATE}", "/daily-quiz", "/api/cert/tree", "/sitemap.xml",
    ]
    for url in public_pages:
        code = anon.get(url).status_code
        assert code == 200, f"{url} 应公开可访问，实际 {code}"
    # 旧地址 301 到新结构
    for old, target in (("/certification", "/cert/aliyun"), ("/practice", "/")):
        resp = anon.get(old)
        assert resp.status_code == 301 and target in resp.headers["Location"], old
    home = anon.get("/").get_data(as_text=True)
    assert "认证题库网" in home and "认证分类" in home and "排行榜" in home
    assert "开始每日答题" in home, "首页应有每日答题入口"
    print("1) 首页 / 排行榜 / 认证频道 / 认证详情 / 每日答题均可未登录浏览 ✅")

    # ---------------------------------------------------------------- 2) 游客导航
    nav_html = anon.get("/leaderboard").get_data(as_text=True)
    top_nav = nav_html.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
    for text in ("首页", "每日答题", "阿里云认证", "华为认证", "AWS 认证", "思科认证"):
        assert text in top_nav, f"游客导航缺少公共入口：{text}"
    # 排行榜 / 错题本不再占用全站导航，入口在个人中心（游客无个人中心）
    for text in ("排行榜", "错题本", "导入题目", "后台管理"):
        assert text not in top_nav, f"游客导航不应出现：{text}"
    assert "登录" in nav_html and "注册" in nav_html
    print("2) 游客导航仅公共模块，排行榜/错题本/个人/管理入口均已隐藏 ✅")

    # ---------------------------------------------------------------- 3) 登录页
    login_html = anon.get("/login").get_data(as_text=True)
    assert "/admin/login" not in login_html and "管理员入口" not in login_html, \
        "用户登录页不应出现管理员登录入口"
    assert "admin123" not in login_html, "登录页不应展示管理员演示账号"
    assert anon.get("/admin/login").status_code == 200, "管理员独立入口应可用"
    admin_login_html = anon.get("/admin/login").get_data(as_text=True)
    for leak in ("admin123", "默认管理员", "首次启动自动创建"):
        assert leak not in admin_login_html, f"管理端登录页不得泄露默认凭据：{leak}"
    print("3) 用户登录页无管理员入口；/admin/login 独立且不回显默认凭据 ✅")

    # ---------------------------------------------------------------- 4) 答题需登录
    protected = [f"/quiz/{DATE}", "/wrong", f"/score/{DATE}", "/export/wrong"]
    for url in protected:
        resp = anon.get(url)
        assert resp.status_code == 302, f"{url} 未登录应重定向"
        loc = resp.headers["Location"]
        assert "/login" in loc and "next=" in loc, f"{url} 应带 next 回跳参数，实际 {loc}"
    print("4) 答题 / 成绩 / 错题 / 导出未登录均跳登录并携带 next ✅")

    # ---------------------------------------------------------------- 5) 登录回跳
    c = app.test_client()
    resp = c.post("/login", data={
        "username": "user", "password": "user123", "next": f"/quiz/{DATE}",
        **test_support.captcha_fields(),
    })
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/quiz/{DATE}"), \
        f"登录后应回跳答题页，实际 {resp.headers.get('Location')}"
    assert c.get(f"/quiz/{DATE}").status_code == 200

    # 站外 next 必须被丢弃，回落到首页
    c2 = app.test_client()
    resp = c2.post("/login?next=//evil.example.com", data={
        "username": "user", "password": "user123", **test_support.captcha_fields(),
    })
    assert resp.status_code == 302
    assert resp.headers["Location"].rstrip("/") in ("", "http://localhost"), \
        f"站外跳转应被拒绝，实际 {resp.headers['Location']}"
    print("5) 登录后回跳原页面，站外 next 被拒绝 ✅")

    # ---------------------------------------------------------------- 6) 接口鉴权
    assert anon.get("/api/practice/tree").status_code == 200
    assert anon.get("/api/cert/tree").status_code == 200
    assert anon.get(f"/api/cert/intro?slug={SLUG}").status_code == 200
    assert anon.get(f"/api/practice/questions?slug={SLUG}").status_code == 401
    assert anon.post("/api/practice/check", json={"answers": {}}).status_code == 401
    assert anon.post("/api/quiz/start", json={"category_id": "huawei/datacom/hcip"}).status_code == 401
    print("6) 导航树 / 认证介绍公开；抽题 / 判分仍需登录（401）✅")

    print("\n全部通过：游客浏览、登录回跳与管理员独立入口验证完成。")
finally:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
