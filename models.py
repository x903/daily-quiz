"""数据模型模块（SQLite）。

负责数据库连接、建表，以及 users / daily_sets / questions / daily_records /
wrong_questions 五张表的 CRUD。

权限说明（多用户版本）：
- `users` 保存账号与角色（user / admin）。
- `daily_records`（成绩）与 `wrong_questions`（错题）均带 `user_id`，
  普通用户只能读写自己的记录；管理员可查看全部。
- 题目集与题目是全局共享的「题库」，由管理员维护。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "quiz.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_sets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    total_sets  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    daily_set_id INTEGER NOT NULL,
    set_index    INTEGER NOT NULL,
    type         TEXT NOT NULL,
    question     TEXT NOT NULL,
    options      TEXT NOT NULL,
    answer       TEXT NOT NULL,
    explanation  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    FOREIGN KEY (daily_set_id) REFERENCES daily_sets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daily_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    daily_set_id INTEGER NOT NULL,
    user_id      INTEGER NOT NULL DEFAULT 0,
    total        INTEGER NOT NULL,
    correct      INTEGER NOT NULL,
    wrong        INTEGER NOT NULL,
    accuracy     REAL NOT NULL,
    duration     INTEGER NOT NULL,
    finished_at  TEXT NOT NULL,
    FOREIGN KEY (daily_set_id) REFERENCES daily_sets(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wrong_questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    daily_set_id   INTEGER NOT NULL,
    question_id    INTEGER NOT NULL,
    user_id        INTEGER NOT NULL DEFAULT 0,
    user_answer    TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (daily_set_id) REFERENCES daily_sets(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


def get_conn() -> sqlite3.Connection:
    """返回一个开启外键约束、使用 Row 工厂的数据库连接。

    Returns:
        sqlite3.Connection: 已配置好的连接对象。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """初始化数据库：确保目录存在、创建全部表结构并做字段迁移。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """对旧库做向后兼容迁移。

    - 多用户版：为 daily_records / wrong_questions 增加 user_id。
    - 解析版：为 questions 增加 explanation（旧题目默认为空，不显示解析块）。
    """
    rec_cols = [r["name"] for r in conn.execute("PRAGMA table_info(daily_records)")]
    if "user_id" not in rec_cols:
        conn.execute(
            "ALTER TABLE daily_records ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"
        )
    wq_cols = [r["name"] for r in conn.execute("PRAGMA table_info(wrong_questions)")]
    if "user_id" not in wq_cols:
        conn.execute(
            "ALTER TABLE wrong_questions ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"
        )
    q_cols = [r["name"] for r in conn.execute("PRAGMA table_info(questions)")]
    if "explanation" not in q_cols:
        conn.execute(
            "ALTER TABLE questions ADD COLUMN explanation TEXT NOT NULL DEFAULT ''"
        )


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

def create_user(username: str, password_hash: str, role: str = "user") -> int:
    """创建用户，返回新用户 id。"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (username, password_hash, role, created_at),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def count_users() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    conn.close()
    return row["c"]


def list_users_with_stats() -> list[dict]:
    """列出全部用户，并关联其答题统计（答题次数、平均正确率、错题数）。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT u.id, u.username, u.role, u.created_at,
               (SELECT COUNT(*) FROM daily_records dr WHERE dr.user_id = u.id) AS record_count,
               (SELECT AVG(dr.accuracy) FROM daily_records dr WHERE dr.user_id = u.id) AS avg_accuracy,
               (SELECT COUNT(*) FROM wrong_questions wq WHERE wq.user_id = u.id) AS wrong_count
        FROM users u
        ORDER BY u.id
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# daily_sets & questions
# ---------------------------------------------------------------------------

def get_daily_set_by_date(date: str) -> Optional[sqlite3.Row]:
    """按日期获取每日题目集。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_sets WHERE date = ?", (date,)).fetchone()
    conn.close()
    return row


def get_daily_set_by_id(set_id: int) -> Optional[sqlite3.Row]:
    """按 id 获取每日题目集。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_sets WHERE id = ?", (set_id,)).fetchone()
    conn.close()
    return row


def create_daily_set(date: str, title: str, total_sets: int, status: str = "pending") -> int:
    """创建一条每日题目集记录，返回新 id。"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO daily_sets (date, title, total_sets, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (date, title, total_sets, status, created_at),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def set_set_status(
    set_id: int, status: str, conn: Optional[sqlite3.Connection] = None
) -> None:
    """更新某日题目集状态。

    Args:
        set_id: 题目集 id。
        status: 目标状态（pending / completed）。
        conn: 外部连接。传入时由调用方统一管理事务，这里不提交也不关闭，
              以便与判分、错题写入合并为一个原子事务。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        conn.execute("UPDATE daily_sets SET status = ? WHERE id = ?", (status, set_id))
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def insert_question(
    set_id: int,
    set_index: int,
    q_type: str,
    question: str,
    options_json: str,
    answer_json: str,
    explanation: str = "",
) -> int:
    """插入一道题目（含解析文本），返回新题目 id。"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO questions "
        "(daily_set_id, set_index, type, question, options, answer, explanation, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            set_id,
            set_index,
            q_type,
            question,
            options_json,
            answer_json,
            explanation or "",
            created_at,
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def get_all_questions_by_set(set_id: int) -> list[sqlite3.Row]:
    """获取某日题目集的全部题目（含选项与答案，按套与 id 排序）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM questions WHERE daily_set_id = ? ORDER BY set_index, id",
        (set_id,),
    ).fetchall()
    conn.close()
    return rows


def get_quiz_questions_by_set(set_id: int) -> list[sqlite3.Row]:
    """获取答题用题目（不含答案，防止客户端作弊）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, daily_set_id, set_index, type, question, options "
        "FROM questions WHERE daily_set_id = ? ORDER BY set_index, id",
        (set_id,),
    ).fetchall()
    conn.close()
    return rows


def delete_set_cascade(date: str) -> None:
    """级联删除某日期的题目集及其题目、成绩、错题（单事务）。

    用 `with conn:` 包住全部删除，任一语句失败时整体回滚，避免出现
    「错题已删、题目集还在」的半删状态；`finally` 保证连接释放。
    """
    conn = get_conn()
    try:
        with conn:
            conn.execute(
                "DELETE FROM wrong_questions WHERE daily_set_id IN "
                "(SELECT id FROM daily_sets WHERE date = ?)",
                (date,),
            )
            conn.execute(
                "DELETE FROM daily_records WHERE daily_set_id IN "
                "(SELECT id FROM daily_sets WHERE date = ?)",
                (date,),
            )
            conn.execute(
                "DELETE FROM questions WHERE daily_set_id IN "
                "(SELECT id FROM daily_sets WHERE date = ?)",
                (date,),
            )
            conn.execute("DELETE FROM daily_sets WHERE date = ?", (date,))
    finally:
        conn.close()


def update_daily_set(
    set_id: int,
    new_date: str,
    title: str,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """更新题目集的日期与标题。

    Args:
        set_id: 题目集 id。
        new_date: 新日期（YYYY-MM-DD）；唯一性由调用方校验。
        title: 新标题。
        conn: 外部连接，传入时由调用方统一管理事务。

    Raises:
        sqlite3.IntegrityError: 新日期与已有题目集冲突（date 有 UNIQUE 约束）。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        conn.execute(
            "UPDATE daily_sets SET date = ?, title = ? WHERE id = ?",
            (new_date, title, set_id),
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def update_question(
    question_id: int,
    q_type: str,
    question: str,
    options_json: str,
    answer_json: str,
    explanation: str,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """更新一道题的题型、题干、选项、答案与解析。

    Args:
        question_id: 题目 id。
        q_type / question / options_json / answer_json / explanation: 新内容。
        conn: 外部连接，传入时由调用方统一管理事务。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        conn.execute(
            "UPDATE questions SET type = ?, question = ?, options = ?, answer = ?, "
            "explanation = ? WHERE id = ?",
            (q_type, question, options_json, answer_json, explanation, question_id),
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def delete_question(
    question_id: int, conn: Optional[sqlite3.Connection] = None
) -> None:
    """删除单道题；其错题记录由外键 ON DELETE CASCADE 自动清理。

    Args:
        question_id: 题目 id。
        conn: 外部连接，传入时由调用方统一管理事务。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def recount_total_sets(
    set_id: int, conn: Optional[sqlite3.Connection] = None
) -> int:
    """按剩余题目的 set_index 重算并写回 total_sets，返回新题数。

    编辑页允许删除单题，删除后必须同步 total_sets，否则后台列表与答题页的
    题数展示会与真实题目数量不一致。

    Args:
        set_id: 题目集 id。
        conn: 外部连接，传入时由调用方统一管理事务。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT set_index) AS c FROM questions WHERE daily_set_id = ?",
            (set_id,),
        ).fetchone()
        total = int(row["c"])
        conn.execute(
            "UPDATE daily_sets SET total_sets = ? WHERE id = ?", (total, set_id)
        )
        if owns_conn:
            conn.commit()
        return total
    finally:
        if owns_conn:
            conn.close()


def get_set_stats(
    set_id: int, conn: Optional[sqlite3.Connection] = None
) -> dict:
    """统计某题库的题目数、成绩数、错题数（供编辑页与删除确认页展示）。

    Args:
        set_id: 题目集 id。
        conn: 可选外部连接；传入时不关闭。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM questions q WHERE q.daily_set_id = ?) AS question_count,
              (SELECT COUNT(*) FROM daily_records dr WHERE dr.daily_set_id = ?) AS record_count,
              (SELECT COUNT(*) FROM wrong_questions wq WHERE wq.daily_set_id = ?) AS wrong_count
            """,
            (set_id, set_id, set_id),
        ).fetchone()
        return dict(row)
    finally:
        if owns_conn:
            conn.close()


def list_sets_with_stats() -> list[dict]:
    """管理员题库列表：题目集信息 + 题目/成绩/错题数量（按日期倒序）。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT ds.*,
               (SELECT COUNT(*) FROM questions q WHERE q.daily_set_id = ds.id) AS question_count,
               (SELECT COUNT(*) FROM daily_records dr WHERE dr.daily_set_id = ds.id) AS record_count,
               (SELECT COUNT(*) FROM wrong_questions wq WHERE wq.daily_set_id = ds.id) AS wrong_count
        FROM daily_sets ds
        ORDER BY ds.date DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_daily_sets(user_id: Optional[int] = None) -> list[dict]:
    """列出全部每日题目集，并标注该用户是否已完成。

    Args:
        user_id: 当传入具体用户 id 时，completed 表示该用户是否在该日有成绩；
                 传 None（管理员视角）时表示是否有任意成绩记录。

    Returns:
        按日期倒序的字典列表，含字段 completed（是否已完成）。
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT ds.*,
               (SELECT COUNT(*) FROM daily_records dr
                  WHERE dr.daily_set_id = ds.id
                    AND (? IS NULL OR dr.user_id = ?)) AS rec_count
        FROM daily_sets ds
        ORDER BY ds.date DESC
        """,
        (user_id, user_id),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["completed"] = bool(d.pop("rec_count"))
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# daily_records（按用户隔离）
# ---------------------------------------------------------------------------

def save_record(
    set_id: int,
    user_id: int,
    total: int,
    correct: int,
    wrong: int,
    accuracy: float,
    duration: int,
    finished_at: str,
) -> int:
    """保存（覆盖）某用户某日的成绩记录，返回记录 id。"""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM daily_records WHERE daily_set_id = ? AND user_id = ?",
        (set_id, user_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE daily_records SET total=?, correct=?, wrong=?, accuracy=?, duration=?, finished_at=? "
            "WHERE daily_set_id = ? AND user_id = ?",
            (total, correct, wrong, accuracy, duration, finished_at, set_id, user_id),
        )
        record_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO daily_records "
            "(daily_set_id, user_id, total, correct, wrong, accuracy, duration, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (set_id, user_id, total, correct, wrong, accuracy, duration, finished_at),
        )
        record_id = cur.lastrowid
    conn.commit()
    conn.close()
    return record_id


def get_record_by_set_user(set_id: int, user_id: int) -> Optional[dict]:
    """按（题目集 id, 用户 id）获取成绩记录。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM daily_records WHERE daily_set_id = ? AND user_id = ?",
        (set_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_record(record_id: int) -> Optional[dict]:
    """按记录 id 获取成绩记录（含 user_id 供权限校验）。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM daily_records WHERE id = ?", (record_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_records_by_user(user_id: int) -> list[dict]:
    """获取某用户的全部成绩记录（按完成时间倒序），关联日期与标题。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT dr.*, ds.date, ds.title, ds.total_sets "
        "FROM daily_records dr "
        "JOIN daily_sets ds ON ds.id = dr.daily_set_id "
        "WHERE dr.user_id = ? ORDER BY dr.finished_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_all_records() -> list[dict]:
    """获取全部用户的成绩记录（管理员视角），并关联用户名。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT dr.*, ds.date, ds.title, ds.total_sets, u.username "
        "FROM daily_records dr "
        "JOIN daily_sets ds ON ds.id = dr.daily_set_id "
        "JOIN users u ON u.id = dr.user_id "
        "ORDER BY dr.finished_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
