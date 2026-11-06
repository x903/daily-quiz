"""MySQL 后端专项回归测试。

运行（需先有可用 MySQL，例如 docker compose up -d db）：

    DB_BACKEND=mysql MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 \
    MYSQL_USER=quiz MYSQL_PASSWORD=quizpass MYSQL_DATABASE=daily_quiz \
    python test_mysql_backend.py

未配置 MySQL 时自动跳过（因此混在 SQLite 回归里执行也不会报错）。
测试数据使用独立日期与独立用户名，结束后自行清理，不影响已有题库。
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
import navigation  # noqa: E402
import parser as md_parser  # noqa: E402
import points  # noqa: E402
import quiz_logic  # noqa: E402

BASE = Path(__file__).resolve().parent
DATE = "2099-12-31"
TEST_USER = "mysql_probe_user"
MD_FILE = BASE / "data" / "ACP模拟题20道_2026-11-04.md"


def cleanup() -> None:
    """删除本次测试创建的题库与用户（级联清理成绩 / 错题 / 积分）。"""
    conn = models.get_conn()
    try:
        with conn:
            conn.execute("DELETE FROM users WHERE username = ?", (TEST_USER,))
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
        tables = [r["name"] for r in c.execute(
            "SELECT TABLE_NAME AS name FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
        ).fetchall()]
        expected = {
            "users", "daily_sets", "questions",
            "daily_records", "wrong_questions", "points_ledger",
        }
        assert expected.issubset(set(tables)), f"缺少表：{expected - set(tables)}"
        meta = c.execute(
            "SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'questions'"
        ).fetchone()
        assert meta["ENGINE"] == "InnoDB", f"引擎应为 InnoDB，实际 {meta['ENGINE']}"
        assert "utf8mb4" in meta["TABLE_COLLATION"], f"字符集应为 utf8mb4：{meta}"
        fk_count = c.execute(
            "SELECT COUNT(*) AS n FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL"
        ).fetchone()["n"]
        assert fk_count >= 8, f"外键数量不足：{fk_count}"
    finally:
        c.close()
    print("2) schema_mysql.sql 幂等建表：6 表 / InnoDB / utf8mb4 / 外键完整 ✅")

    # 3) ? 占位符转换：字符串字面量内的问号不能被误替换
    c = models.get_conn()
    try:
        row = c.execute("SELECT '?' AS literal, ? AS val", ("OK",)).fetchone()
        assert row["literal"] == "?" and row["val"] == "OK", f"占位符转换错误：{row}"
        rows = c.execute("SELECT ? AS a, ? AS b", (1, 2)).fetchall()
        assert rows == [{"a": 1, "b": 2}], f"多占位符错误：{rows}"
        assert c.execute("PRAGMA foreign_keys = ON").fetchone() is None
        cols = [r["name"] for r in c.execute("PRAGMA table_info(daily_sets)").fetchall()]
        assert {"date", "title", "category_slug"} <= set(cols), f"列信息读取失败：{cols}"
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

    # 6) 端到端：导入 → 答题判分 → 积分 → 榜单 → 编辑 → 级联删除
    cleanup()
    from app import app  # noqa: E402 - 需在建表后导入（模块加载时会 init_db）
    import auth  # noqa: E402

    parsed = md_parser.parse_markdown(MD_FILE.read_text(encoding="utf-8"))
    assert parsed.total_questions == 20 and not parsed.failed
    slug = "acp-cloud-computing"
    imported = quiz_logic.import_day(
        DATE, "MySQL 端到端探测 🚀", parsed,
        category_slug=slug, category_path=navigation.category_path_label(slug),
    )
    assert imported["ok"] and imported["total_questions"] == 20
    s = models.get_daily_set_by_date(DATE)
    assert s["title"] == "MySQL 端到端探测 🚀", "utf8mb4 应能存储 emoji"
    assert s["category_slug"] == slug

    ok, _ = auth.register(TEST_USER, "probe123", role="user")
    assert ok, "测试用户创建失败"
    user = models.get_user_by_username(TEST_USER)
    assert user is not None
    uid = user["id"]

    # 全部作答正确（从题库读取正确答案），验证判分 / 成绩 / 积分链路
    rows = models.get_all_questions_by_set(s["id"])
    form = {"duration": "9"}
    for r in rows:
        for letter in json.loads(r["answer"]):
            form.setdefault(f"q_{r['id']}", []).append(letter)
    from werkzeug.datastructures import MultiDict  # noqa: E402
    multi = MultiDict()
    for key, value in form.items():
        if isinstance(value, list):
            for v in value:
                multi.add(key, v)
        else:
            multi[key] = value
    result = quiz_logic.grade_and_save(DATE, uid, multi, 9)
    assert result["correct"] == 20 and result["points"] == 20 * points.POINTS_PER_CORRECT

    import leaderboard  # noqa: E402
    # 积分按「答题发生日」统计，因此查当天的日榜
    today_board = leaderboard.get_leaderboard("day")
    assert today_board["items"], "当日日榜应有数据"
    assert any(i["user_id"] == uid and i["points"] == 20 for i in today_board["items"])

    client = app.test_client()
    assert client.post("/login", data={"username": "admin", "password": "admin123"}).status_code == 302
    assert client.get("/leaderboard").status_code == 200
    assert client.get(f"/certification/{'acp'}").status_code == 200
    assert "MySQL 端到端探测" in client.get("/admin").get_data(as_text=True)

    # 删除题库应级联清理成绩 / 错题 / 积分 / 题目
    models.delete_set_cascade(DATE)
    assert models.get_daily_set_by_date(DATE) is None
    c = models.get_conn()
    try:
        n = c.execute(
            "SELECT COUNT(*) AS n FROM points_ledger p "
            "JOIN daily_sets ds ON ds.id = p.daily_set_id WHERE ds.date = ?", (DATE,)
        ).fetchone()["n"]
        assert n == 0, "删除题库后积分流水应被清理"
    finally:
        c.close()
    cleanup()
    print("6) 端到端：导入 / 判分 / 积分 / 榜单 / 后台 / 级联删除全部通过 ✅")

    print("\n全部通过：MySQL 后端专项回归完成。")


if __name__ == "__main__":
    main()
