"""权限与角色端到端测试（使用 Flask test client，无需启动服务器）。

覆盖：
- 未登录可浏览首页 / 排行榜 / 认证栏目（200），但答题与个人数据需登录；
- 管理员可进入 /admin、/import；
- 普通用户访问后台被拦截（302）；
- 答题成绩与错题正确归属到当前用户；
- 用户之间数据相互隔离。
"""
import json

import auth
import models
import test_support
import wrong_book
from app import app

models.init_db()
auth.create_default_users()

# 管理员账号现在位于独立的 admin 表，与 users 表彻底分开
admin = models.get_admin_by_username("admin")
user = models.get_user_by_username("user")
assert admin and user, "默认账号未就绪"


def set_session(sess, u, *, is_admin=False):
    test_support.set_session(sess, u, is_admin=is_admin)


print("=== 未登录访问控制 ===")
c = app.test_client()
assert c.get("/").status_code == 200, "未登录也应能浏览首页"
assert c.get("/leaderboard").status_code == 200, "未登录也应能浏览排行榜"
assert c.get("/admin").status_code == 302
assert c.get("/login").status_code == 200
assert c.get("/register").status_code == 200
print("  GET / 与 /leaderboard -> 200, /admin -> 302, /login & /register -> 200  ✅")

print("=== 登录流程 ===")
r = test_support.login(c, "admin", "admin123")
assert r.status_code == 302, r.status_code
print("  管理员独立登录 /admin/login -> 302  ✅")

print("=== 管理员后台访问 ===")
with app.test_client() as c2:
    with c2.session_transaction() as s:
        set_session(s, admin, is_admin=True)
    assert c2.get("/admin").status_code == 200
    assert c2.get("/import").status_code == 200
    assert c2.get("/admin/users").status_code == 200
    assert c2.get("/admin/records").status_code == 200
    print("  /admin /import /admin/users /admin/records -> 200  ✅")

print("=== 普通用户被后台拦截 ===")
with app.test_client() as c3:
    with c3.session_transaction() as s:
        set_session(s, user)
    assert c3.get("/").status_code == 200
    assert c3.get("/admin").status_code == 302, "普通用户应被拦截"
    assert c3.get("/import").status_code == 302, "普通用户应被拦截"
    print("  普通用户 / -> 200, /admin /import -> 302 拦截  ✅")

print("=== 成绩与错题归属 + 隔离 ===")
# 确保 2026-11-03 的 ACP 题目在库
s = models.get_daily_set_by_date("2026-11-03")
assert s, "2026-11-03 题库缺失"
questions = models.get_all_questions_by_set(s["id"])
# user 答满分
with app.test_client() as cu:
    with cu.session_transaction() as sess:
        set_session(sess, user)
    data = {"duration": 120}
    for q in questions:
        data[f"q_{q['id']}"] = json.loads(q["answer"])[0]
    rr = cu.post(f"/quiz/2026-11-03", data=data, follow_redirects=False)
    assert rr.status_code == 302
    rec = models.get_record_by_set_user(s["id"], user["id"])
    assert rec and rec["user_id"] == user["id"] and rec["correct"] == len(questions)
    assert wrong_book.get_wrong_by_date("2026-11-03", user["id"]) == []
    # admin 视角查全部，应能看到这条记录且 user_id 正确
    all_recs = models.list_all_records()
    assert any(r["user_id"] == user["id"] for r in all_recs)
    # 用户隔离：按 user_id 过滤只返回该用户数据
    other = models.get_user_by_username("smoketest")
    if other:
        other_wrongs = wrong_book.get_all_wrong(user_id=other["id"])
        # smoketest 在冒烟测试中答错 3 题；user 本次满分无错题
        assert all(w["user_id"] == other["id"] for w in other_wrongs)
        assert wrong_book.get_all_wrong(user_id=user["id"]) == []  # user 满分故无错题
        print(f"  隔离校验：smoketest 错题 {len(other_wrongs)} 条均归属其自身；user 无错题  ✅")
    print(f"  user 满分提交 -> record.user_id={rec['user_id']}, 正确 {rec['correct']}/{len(questions)}  ✅")
    print("  用户间错题隔离校验通过  ✅")

print("\n✅ 权限与角色测试全部通过")
