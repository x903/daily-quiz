"""验证：普通用户界面无导入入口；管理员界面保留；/import 对普通用户拦截；日期自动探测。"""
import re
import app as flask_app
from app import app, _guess_date

client = app.test_client()


def login(username, password):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def get_rendered(path, username, password):
    login(username, password)
    resp = client.get(path, follow_redirects=False)
    return resp


# 1) 普通用户不可访问 /import（应 302 跳登录或首页）
r = client.post("/login", data={"username": "user", "password": "user123"}, follow_redirects=False)
assert r.status_code == 302, "登录应成功"
r_imp = client.get("/import", follow_redirects=False)
print("普通用户 GET /import ->", r_imp.status_code, "(期望 302 重定向拦截)")
assert r_imp.status_code == 302

# 2) 普通用户首页不能包含任何「导入」字样
r_home = client.get("/", follow_redirects=False)
body = r_home.get_data(as_text=True)
has_import = ("导入" in body) or ("import_page" in body) or ("去导入" in body)
print("普通用户首页含『导入』字样:", has_import, "(期望 False)")
assert not has_import, "普通用户首页不应出现导入入口"

# 3) 普通用户历史页空态也不含导入链接（用真实渲染，非 admin）
r_hist = client.get("/history", follow_redirects=False)
body_h = r_hist.get_data(as_text=True)
print("普通用户 /history 含『导入题目』链接:", ("导入题目" in body_h), "(期望 False)")
assert "导入题目" not in body_h

# 4) 普通用户可正常看到今日答题（2026-11-04 已导入）
print("普通用户今日答题页 GET /quiz/2026-11-04 ->",
      client.get("/quiz/2026-11-04", follow_redirects=False).status_code, "(期望 200)")
assert client.get("/quiz/2026-11-04", follow_redirects=False).status_code == 200

# 5) 管理员首页应保留导入入口
client.get("/logout", follow_redirects=False)
login("admin", "admin123")
r_admin = client.get("/", follow_redirects=False).get_data(as_text=True)
print("管理员首页含『导入题目』:", ("导入题目" in r_admin), "(期望 True)")
assert "导入题目" in r_admin

# 6) 日期自动探测：文件名 / 标题 / 正文头部
cases = [
    ("ACP模拟题20道_2026-11-04.md", "", "", "2026-11-04"),
    ("", "我的题库 2026-12-01", "", "2026-12-01"),
    ("", "", "> 生成日期：2026-10-25\n题目...", "2026-10-25"),
    ("随便起名.md", "无日期标题", "普通正文没有日期", ""),
]
for fn, title, text, expect in cases:
    got = _guess_date(text, title, fn)
    ok = got == expect
    print(f"  _guess_date(fn={fn!r}, title={title!r}) -> {got!r} 期望 {expect!r} {'OK' if ok else 'FAIL'}")
    assert ok, f"日期探测失败: {fn}/{title}"

print("\n✅ 全部校验通过：普通用户界面无任何导入入口，管理员保留，日期可自动探测。")
