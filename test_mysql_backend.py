"""MySQL 后端专项回归测试。

运行（需先有可用 MySQL，例如 docker compose up -d db）：

    DB_BACKEND=mysql MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 \
    MYSQL_USER=quiz MYSQL_PASSWORD=quizpass MYSQL_DATABASE=daily_quiz \
    python test_mysql_backend.py

或在容器内执行：docker compose exec web python test_mysql_backend.py

未配置 MySQL 时自动跳过（因此混在 SQLite 回归里执行也不会报错）。
测试使用独立的临时认证分类与独立用户名，结束后级联清理，不影响正式题库。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if os.environ.get("DB_BACKEND", "").strip().lower() != "mysql":
    print("SKIP：未设置 DB_BACKEND=mysql，跳过 MySQL 专项回归。")
    sys.exit(0)

import db  # noqa: E402
import models  # noqa: E402

# 生产环境默认不回显调试验证码；本测试依赖 debug_code / debug_link，显式打开。
# 用赋值而不是 setdefault：容器里 compose 已注入 ACCOUNT_DEV_MODE=0
os.environ["ACCOUNT_DEV_MODE"] = "1"

BASE = Path(__file__).resolve().parent
DATE = "2099-12-31"
TEST_USER = "mysql_probe_user"
PROBE_PATH = "probe/mysql/temp"

MD_TEXT = "\n\n".join(
    f"""**{i}.** MySQL 专项探测第 {i} 题：下列关于 OSPF 与 BGP 的说法，正确的是？
- A. OSPF 使用 Dijkstra 算法计算最短路径
- B. BGP 使用距离矢量算法且不依赖 TCP
- C. OSPF 是自治系统间的路由协议
- D. BGP 不支持路由策略

**答案：A**

**解析：**
- **考点**：OSPF 使用 SPF（Dijkstra），BGP 基于 TCP 179 并支持丰富的路由策略"""
    for i in range(1, 11)
)


def cleanup() -> None:
    """删除本次测试创建的临时分类与用户（外键级联清理题目 / 成绩 / 荣誉）。"""
    conn = models.get_conn()
    try:
        with conn:
            conn.execute(
                "DELETE FROM rankings WHERE user_id IN "
                "(SELECT id FROM users WHERE username = ?)", (TEST_USER,)
            )
            conn.execute("DELETE FROM users WHERE username = ?", (TEST_USER,))
            conn.execute("DELETE FROM cert_categories WHERE path_slug = ?", (PROBE_PATH,))
            conn.execute("DELETE FROM daily_sets WHERE date = ?", (DATE,))
    finally:
        conn.close()


def main() -> None:
    assert db.backend() == "mysql", "DB_BACKEND 未生效"
    conn = models.get_conn()
    assert isinstance(conn, db.MySQLConnection), "应返回 MySQL 适配连接"
    print("1) 后端选择为 MySQL，连接走 PyMySQL 适配层 ✅")

    # 2) 建表脚本可执行且幂等（连续初始化两次不报错）
    models.init_db()
    models.init_db()
    c = models.get_conn()
    try:
        tables = {r["name"] for r in c.execute(
            "SELECT TABLE_NAME AS name FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
        ).fetchall()}
        expected = {
            "admins", "users", "cert_categories", "cert_intros", "cert_intro",
            "questions", "quiz_records", "user_honors", "rankings",
            "daily_sets", "daily_records", "wrong_questions", "points_ledger",
            "sms_codes", "email_tokens", "bank_exam_awards",
        }
        assert expected.issubset(tables), f"缺少表：{expected - tables}"

        cols = {r["name"] for r in c.execute("PRAGMA table_info(cert_categories)").fetchall()}
        for col in ("vendor_slug", "direction_slug", "level_slug", "path_slug",
                    "cert_name", "seo_title", "status"):
            assert col in cols, f"cert_categories 缺少字段 {col}"
        cols = {r["name"] for r in c.execute("PRAGMA table_info(cert_intros)").fetchall()}
        for col in ("category_id", "positioning", "audience", "exam_topics", "advice", "source"):
            assert col in cols, f"cert_intros 缺少字段 {col}"
        cols = {r["name"] for r in c.execute("PRAGMA table_info(quiz_records)").fetchall()}
        for col in ("user_id", "category_id", "cert_path", "score", "accuracy", "duration"):
            assert col in cols, f"quiz_records 缺少字段 {col}"

        # 认证题库的题目不挂 daily_set，旧的 NOT NULL 必须已放开
        nullable = c.execute(
            "SELECT IS_NULLABLE AS n FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'questions' "
            "AND COLUMN_NAME = 'daily_set_id'"
        ).fetchone()["n"]
        assert nullable == "YES", f"questions.daily_set_id 应允许为空，实际 {nullable}"
        nick = c.execute(
            "SELECT IS_NULLABLE AS n FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
            "AND COLUMN_NAME = 'nickname'"
        ).fetchone()
        assert nick is not None, "users.nickname 迁移未执行"

        # 个人中心字段 + 唯一索引（手机号 / 邮箱未绑定时为 NULL）
        user_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        for col in ("gender", "bio", "phone", "phone_verified", "email",
                    "email_verified", "reset_token", "reset_token_expire_at", "updated_at"):
            assert col in user_cols, f"users 缺少个人中心字段 {col}"
        indexes = {r["INDEX_NAME"] for r in c.execute(
            "SELECT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'"
        ).fetchall()}
        assert {"uk_users_phone", "uk_users_email"} <= indexes, f"唯一索引缺失：{indexes}"

        meta = c.execute(
            "SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'cert_categories'"
        ).fetchone()
        assert meta["ENGINE"] == "InnoDB", f"引擎应为 InnoDB，实际 {meta['ENGINE']}"
        assert "utf8mb4" in meta["TABLE_COLLATION"], f"字符集应为 utf8mb4：{meta}"

        cats = c.execute("SELECT COUNT(*) AS n FROM cert_categories").fetchone()["n"]
        intros = c.execute("SELECT COUNT(*) AS n FROM cert_intros").fetchone()["n"]
        assert cats >= 34 and intros >= 34, f"目录未播种：{cats} 分类 / {intros} 介绍"
        fk_count = c.execute(
            "SELECT COUNT(*) AS n FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL"
        ).fetchone()["n"]
        assert fk_count >= 10, f"外键数量不足：{fk_count}"
    finally:
        c.close()
    print(f"2) 幂等建表：{len(tables)} 表 / InnoDB / utf8mb4 / daily_set_id 可空 / 外键完整 ✅")

    # 3) ? 占位符转换：字符串字面量内的问号不能被误替换
    c = models.get_conn()
    try:
        row = c.execute("SELECT '?' AS literal, ? AS val", ("OK",)).fetchone()
        assert row["literal"] == "?" and row["val"] == "OK", f"占位符转换错误：{row}"
        rows = c.execute("SELECT ? AS a, ? AS b", (1, 2)).fetchall()
        assert rows == [{"a": 1, "b": 2}], f"多占位符错误：{rows}"
        assert c.execute("PRAGMA foreign_keys = ON").fetchone() is None
        cols = [r["name"] for r in c.execute("PRAGMA table_info(cert_categories)").fetchall()]
        assert "path_slug" in cols, f"列信息读取失败：{cols}"
    finally:
        c.close()
    print("3) ?→%s 转换 / PRAGMA 映射 / 字典行对象均正常 ✅")

    # 4) 驱动异常归一化为 db.IntegrityError / db.DatabaseError
    c = models.get_conn()
    try:
        try:
            c.execute(
                "INSERT INTO daily_sets (date, title, created_at) VALUES (?, ?, ?)",
                (DATE, "唯一键探测", "2026-01-01 00:00:00"),
            )
            c.execute(
                "INSERT INTO daily_sets (date, title, created_at) VALUES (?, ?, ?)",
                (DATE, "重复日期", "2026-01-01 00:00:00"),
            )
            c.commit()
        except db.IntegrityError:
            pass
        else:
            raise AssertionError("重复日期应触发 db.IntegrityError")
        c.rollback()
        try:
            c.execute("SELECT * FROM 不存在的表")
        except db.DatabaseError:
            pass
        else:
            raise AssertionError("非法 SQL 应触发 db.DatabaseError")
    finally:
        c.close()
    print("4) MySQL 驱动异常统一映射，路由层友好提示链路可用 ✅")

    # 5) 事务回滚：with conn 中抛异常时，已执行的写入整体撤销
    c = models.get_conn()
    try:
        try:
            with c:
                c.execute(
                    "INSERT INTO daily_sets (date, title, created_at) VALUES (?, ?, ?)",
                    ("2099-12-30", "应被回滚", "2026-01-01 00:00:00"),
                )
                raise RuntimeError("模拟中途失败")
        except RuntimeError:
            pass
        left = c.execute(
            "SELECT COUNT(*) AS n FROM daily_sets WHERE date = ?", ("2099-12-30",)
        ).fetchone()["n"]
        assert left == 0, "事务回滚失败，留下了半写数据"
    finally:
        c.close()
    print("5) 单事务回滚有效（与 SQLite 行为一致）✅")

    # 6) 端到端：临时分类 → 导入 → 答题判分 → 榜单 → 后台接口 → 级联删除
    cleanup()
    import auth  # noqa: E402
    import bank  # noqa: E402
    import test_support  # noqa: E402
    from app import app  # noqa: E402 - 需在建表后导入（模块加载时会 init_db）

    auth.create_default_users()
    conn = models.get_conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO cert_categories (vendor_slug, vendor_name, direction_slug, "
                "direction_name, level_slug, level_name, path_slug, cert_name, exam_code, "
                "difficulty, sort_order, status, seo_title, seo_keywords, seo_description, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                ("probe", "探测厂商 🚀", "mysql", "MySQL 方向", "temp", "临时认证",
                 PROBE_PATH, "MySQL 临时认证题库 🚀", "PROBE-1", "进阶", 999,
                 "MySQL 临时认证题库 - 认证题库网", "MySQL,临时认证",
                 "仅用于 MySQL 回归测试的临时分类", "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
            )
    finally:
        conn.close()

    cat = bank.get_category_by_path(PROBE_PATH)
    assert cat and cat["cert_name"] == "MySQL 临时认证题库 🚀", "utf8mb4 应能存储 emoji"
    assert bank.save_intro(cat["id"], {
        "positioning": "MySQL 回归专用临时认证。", "audience": "测试脚本",
        "exam_topics": "连接、事务、字符集", "difficulty": "进阶",
        "advice": "无需备考", "source": "test_mysql_backend.py",
    }, updated_by="probe")
    assert bank.get_intro(cat["id"])["positioning"].startswith("MySQL 回归专用")

    imported = bank.import_questions(MD_TEXT, cat["id"], fmt="markdown")
    assert imported["imported"] == 10 and not imported["failed"], imported
    assert bank.count_questions(cat["id"]) == 10

    ok, message = auth.register(TEST_USER, "probe123", role="user")
    assert ok, f"测试用户创建失败：{message}"
    client = app.test_client()
    assert client.post("/login", data={"username": TEST_USER, "password": "probe123",
                                   **test_support.captcha_fields()}).status_code == 302

    r = client.post("/api/quiz/start", json={"category": PROBE_PATH})
    assert r.status_code == 200, r.get_json()
    start = r.get_json()["data"]
    assert len(start["questions"]) == 10
    ids = [q["id"] for q in start["questions"]]
    truth = bank.questions_by_ids(ids)
    answers = {str(qid): truth[qid]["answer_list"] for qid in ids}
    r = client.post("/api/quiz/submit", json={
        "category_id": cat["id"], "question_ids": ids, "answers": answers,
        "duration": 30, "started_at": start["started_at"],
    })
    assert r.status_code == 200, r.get_json()
    result = r.get_json()["data"]
    assert result["score"] == 100 and result["correct"] == 10 and result["accuracy"] == 1.0

    uid = models.get_user_by_username(TEST_USER)["id"]
    for period in ("day", "week", "month"):
        rows = bank.top_ranking(period, 100)
        me = next((x for x in rows if x["user_id"] == uid), None)
        assert me and me["points"] == 100 and me["quiz_count"] == 1, period
    assert client.get("/ranking").status_code == 200
    assert client.get("/profile").status_code == 200
    assert client.get("/honors", follow_redirects=False).status_code == 301

    # 个人中心：MySQL 下的资料更新 / 绑定 / 找回密码（双后端一致性）
    r = client.put("/api/user/profile", json={
        "nickname": "MySQL 回归", "gender": "保密", "bio": "验证 MySQL 下个人中心链路"})
    assert r.status_code == 200 and r.get_json()["data"]["nickname"] == "MySQL 回归", r.get_json()

    email = f"{TEST_USER}@example.com"
    r = client.post("/api/user/bind-phone", json={"phone": "13700003333"})
    assert r.status_code == 200, r.get_json()
    r = client.post("/api/user/verify-phone", json={
        "phone": "13700003333", "code": r.get_json()["data"]["debug_code"]})
    assert r.status_code == 200 and r.get_json()["data"]["phone"] == "137****3333", r.get_json()

    r = client.post("/api/user/bind-email", json={"email": email.upper()})
    assert r.status_code == 200, r.get_json()
    r = client.post("/api/user/verify-email", json={
        "email": email, "code": r.get_json()["data"]["debug_code"]})
    assert r.status_code == 200 and r.get_json()["data"]["email_verified"] is True
    stored = dict(models.get_user(uid))
    assert stored["email"] == email and int(stored["email_verified"]) == 1, stored

    r = client.post("/api/auth/forgot-password", json={"email": email})
    assert r.status_code == 200, r.get_json()
    token = r.get_json()["data"]["debug_link"].split("token=")[1]
    r = client.post("/api/auth/reset-password", json={
        "token": token, "password": "mysql1234", "confirm_password": "mysql1234"})
    assert r.status_code == 200, r.get_json()
    fresh = app.test_client()
    assert fresh.post("/login", data={"username": TEST_USER, "password": "mysql1234",
                                 **test_support.captcha_fields()}).status_code == 302
    assert client.get("/profile").get_data(as_text=True).count("137****3333") >= 1

    admin = app.test_client()
    # 生产环境启动时会把 admin123 换成 .env 里的 ADMIN_PASSWORD（或随机值），
    # 因此这里按容器环境变量登录，未设置时回退到本地开发默认值。
    admin_pw = os.environ.get("ADMIN_PASSWORD") or "admin123"
    assert test_support.login(admin, "admin", admin_pw).status_code == 302
    payload = admin.get("/api/admin/cert-categories?q=MySQL").get_json()["data"]
    assert any(item["path_slug"] == PROBE_PATH for item in payload["items"])
    qs = admin.get(f"/api/admin/questions?category_id={cat['id']}").get_json()["data"]
    assert qs["total"] == 10
    rankings = admin.get("/api/admin/rankings?period=day").get_json()["data"]
    assert any(row["user_id"] == uid for row in rankings["rows"])
    assert admin.get("/cert/aliyun/acp/cloud").status_code == 200, "目录页应与 MySQL 数据共存"

    # 删除临时分类：题目 / 介绍 / 答题记录应级联清理
    conn = models.get_conn()
    try:
        with conn:
            conn.execute("DELETE FROM cert_categories WHERE path_slug = ?", (PROBE_PATH,))
    finally:
        conn.close()
    assert bank.get_category_by_path(PROBE_PATH) is None
    assert bank.count_questions(cat["id"]) == 0
    c = models.get_conn()
    try:
        left = c.execute(
            "SELECT COUNT(*) AS n FROM quiz_records WHERE category_id = ?", (cat["id"],)
        ).fetchone()["n"]
        assert left == 0, "删除分类后答题记录应级联清理"
    finally:
        c.close()

    cleanup()
    print("6) 端到端：临时分类 / 导入 / 判分 / 日周月榜 / 后台接口 / 级联删除全部通过 ✅")

    print("\n全部通过：MySQL 后端专项回归完成。")


if __name__ == "__main__":
    main()
