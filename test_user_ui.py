"""验证角色路由隔离后的界面差异：
- 普通用户保持 localhost:5000 现有界面与完整导航菜单（含导入题目、后台管理）；
- 普通用户 / 未登录访问管理端入口被拦截；
- 管理员从 /admin/login 独立登录，直达 /admin/dashboard，界面不含任何用户端菜单；
- 「历史日期」栏目已下线；日期自动探测保持可用。
"""
import test_support
from app import app, _guess_date

client = app.test_client()
FULL_NAV = ("首页", "每日答题", "阿里云认证", "华为认证", "AWS 认证", "思科认证")
PROFILE_ONLY = ("排行榜", "错题本")
HIDDEN_IN_ADMIN = ("每日答题", "错题本", "阿里云认证", "华为认证")


def login(username, password, cli=None):
    return test_support.login(cli or client, username, password)


# 1) 普通用户登录成功，管理端入口被拦截
r = login("user", "user123")
assert r.status_code == 302, "普通用户登录应成功"
r_imp = client.get("/import", follow_redirects=False)
print("普通用户 GET /import ->", r_imp.status_code, "(期望 302 拦截)")
assert r_imp.status_code == 302

# 2) 普通用户首页保持完整导航菜单（排行榜 / 错题本已改为个人中心入口）
body = client.get("/").get_data(as_text=True)
top_nav = body.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
for label in FULL_NAV:
    assert label in top_nav, f"普通用户导航缺少：{label}"
for label in PROFILE_ONLY:
    assert label not in top_nav, f"全站导航不应出现：{label}"
print("普通用户导航：", " / ".join(FULL_NAV), "✅")

# 2.1) 个人荣誉已并入个人中心：导航去掉独立入口，用户名即入口，旧地址 301
assert "个人荣誉" not in body, "导航栏不应再出现「个人荣誉」"
assert 'href="/profile"' in body, "用户名应链接到个人中心"
assert client.get("/profile").status_code == 200
legacy = client.get("/honors", follow_redirects=False)
assert legacy.status_code == 301 and legacy.headers["Location"].endswith("/profile"), \
    "旧 /honors 应 301 到 /profile"
profile_body = client.get("/profile").get_data(as_text=True)
for block in ("个人中心", "荣誉徽章", "历史答题记录", "今日排名", "累计积分"):
    assert block in profile_body, f"个人中心缺少：{block}"
print("个人荣誉并入个人中心：导航无独立入口，/honors -> 301 /profile  ✅")

# 3) 历史日期栏目已下线
assert client.get("/history").status_code == 404
for path in ("/", "/cert/aliyun/acp"):
    page = client.get(path).get_data(as_text=True)
    assert "历史日期" not in page, f"{path} 仍出现历史日期入口"
print("/history 404，页面无历史日期入口  ✅")

# 4) 普通用户可正常答题
assert client.get("/quiz/2026-11-04").status_code == 200
print("普通用户答题页 GET /quiz/2026-11-04 -> 200  ✅")

# 5) 管理员独立入口：登录 -> dashboard，界面无用户端菜单
client.get("/logout", follow_redirects=False)
admin_client = app.test_client()
r = test_support.login(admin_client, "admin", "admin123")
assert r.status_code == 302 and r.headers["Location"].endswith("/admin/dashboard")
dash = admin_client.get("/admin/dashboard")
assert dash.status_code == 200
dash_body = dash.get_data(as_text=True)
assert "管理台" in dash_body and "控制台概览" in dash_body
for label in HIDDEN_IN_ADMIN:
    assert label not in dash_body, f"管理后台不应出现用户端菜单：{label}"
assert admin_client.get("/api/admin/overview").get_json()["ok"]
# 管理员会话独立于用户端：公开页可浏览，但顶栏给「进入后台」入口，不会串成普通用户
admin_home = admin_client.get("/")
assert admin_home.status_code == 200
assert "进入管理后台" in admin_home.get_data(as_text=True)
assert admin_client.get("/wrong").status_code == 302, "管理员 session 不能直接看用户错题本"
print("管理员 /admin/login -> /admin/dashboard，界面无用户导航，API 可用  ✅")

# 6) 日期自动探测：文件名 / 标题 / 正文头部
cases = [
    ("ACP模拟题20道_2026-11-04.md", "", "", "2026-11-04"),
    ("", "我的题库 2026-12-01", "", "2026-12-01"),
    ("", "", "> 生成日期：2026-10-25\n题目...", "2026-10-25"),
    ("随便起名.md", "无日期标题", "普通正文没有日期", ""),
]
for fn, title, text, expect in cases:
    got = _guess_date(text, title, fn)
    assert got == expect, f"日期探测失败: {fn}/{title} -> {got}"
print("日期自动探测（文件名 / 标题 / 正文）✅")

print("\n✅ 全部通过：普通用户完整导航，管理员独立后台且无用户菜单。")
