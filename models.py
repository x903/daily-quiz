"""数据模型模块（MySQL / SQLite 双后端）。

负责数据库连接、建表，以及 users / daily_sets / questions / daily_records /
wrong_questions / points_ledger 六张表的 CRUD。

连接统一由 `db.py` 提供：生产 / Docker 使用 MySQL（`schema_mysql.sql` 建表），
未配置 MySQL 时回退 SQLite（兼容既有本地库与回归测试）。业务 SQL 保持 SQLite
风格的 `?` 占位符，由 `db.MySQLConnection` 在 MySQL 后端自动转换为 `%s`。

栏目说明：`daily_sets.category_slug` 保存导入时选中的认证栏目节点 slug，
`category_path` 保存当时的完整路径文案快照（如「阿里云认证 › 阿里云ACP ›
云计算高级工程师」）。存快照是为了以后菜单配置变更、节点被移除时，历史题库
仍能显示可读的归属；slug 则用于栏目页按「自身 + 子孙节点」聚合题目。

权限说明（多用户版本）：
- `users` 保存账号与角色（user / admin）。
- `daily_records`（成绩）与 `wrong_questions`（错题）均带 `user_id`，
  普通用户只能读写自己的记录；管理员可查看全部。
- 题目集与题目是全局共享的「题库」，由管理员维护。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import db

BASE_DIR = Path(__file__).resolve().parent
# SQLite 回退后端使用的数据库文件；测试脚本会把它重定向到独立临时库。
DB_PATH = BASE_DIR / "data" / "quiz.db"
# MySQL 后端的建表脚本（同时挂载给 MySQL 容器做首次初始化）。
MYSQL_SCHEMA_FILE = BASE_DIR / "schema_mysql.sql"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_sets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    total_sets     INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL,
    category_slug  TEXT NOT NULL DEFAULT '',
    category_path  TEXT NOT NULL DEFAULT ''
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

-- 积分流水：每次正式答题提交写一条，保留完整积分历史（成绩表会被重答覆盖，
-- 不能作为积分历史来源）。earned_date 为「答题发生日」，用于日/周/月周期统计；
-- set_date 为题库日期，仅用于追溯（补做历史题也算发生当日）。
CREATE TABLE IF NOT EXISTS points_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    daily_set_id  INTEGER NOT NULL,
    record_id     INTEGER,
    earned_date   TEXT NOT NULL,
    set_date      TEXT NOT NULL,
    points        INTEGER NOT NULL DEFAULT 0,
    correct       INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'quiz',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (daily_set_id) REFERENCES daily_sets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_points_user_date ON points_ledger(user_id, earned_date);
CREATE INDEX IF NOT EXISTS idx_points_date ON points_ledger(earned_date);
"""


def get_conn() -> db.Connection:
    """返回统一接口的连接（后端由环境变量 / `db.backend()` 决定）。

    保留此函数作为全部业务模块的连接入口：`from models import get_conn` 的旧调用
    方式不变，同时在这里动态读取 `DB_PATH`，使测试脚本重定向临时 SQLite 库仍生效。
    """
    return db.get_conn(sqlite_path=DB_PATH)


def init_db() -> None:
    """初始化数据库：按后端建表、字段迁移，并把历史成绩回填为积分流水。

    MySQL 后端执行 `schema_mysql.sql`（幂等 CREATE TABLE IF NOT EXISTS）；
    SQLite 后端执行内嵌 SCHEMA。两者随后共用迁移与回填逻辑。
    """
    if db.backend() == "mysql":
        schema = MYSQL_SCHEMA_FILE.read_text(encoding="utf-8")
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        schema = SCHEMA

    conn = get_conn()
    try:
        conn.executescript(schema)
        _migrate(conn)
        # 幂等回填：升级到排行榜版本前的历史成绩也要能进榜
        backfill_points_ledger(conn)
        conn.commit()
    finally:
        conn.close()


def _column_names(conn: db.Connection, table: str) -> list[str]:
    """取表的列名；`PRAGMA table_info` 会被 MySQL 适配层自动改写为 information_schema。"""
    return [
        r["name"]
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def _migrate(conn: db.Connection) -> None:
    """对旧库做向后兼容迁移（新建库全部字段已存在，直接跳过）。

    - 多用户版：为 daily_records / wrong_questions 增加 user_id。
    - 解析版：为 questions 增加 explanation（旧题目默认为空，不显示解析块）。
    - 栏目版：为 daily_sets 增加 category_slug / category_path（旧题库为空 = 未分类）。

    MySQL 的 TEXT 不允许字面量默认值，故短文本列在 MySQL 下使用 VARCHAR。
    """
    is_mysql = db.backend() == "mysql"
    short_text = "VARCHAR(512)" if is_mysql else "TEXT"

    ds_cols = _column_names(conn, "daily_sets")
    for column in ("category_slug", "category_path"):
        if column not in ds_cols:
            # 列名来自本模块常量，非用户输入，不存在注入风险
            conn.execute(
                f"ALTER TABLE daily_sets ADD COLUMN {column} {short_text} NOT NULL DEFAULT ''"
            )
    if "user_id" not in _column_names(conn, "daily_records"):
        conn.execute(
            "ALTER TABLE daily_records ADD COLUMN user_id INT NOT NULL DEFAULT 0"
        )
    if "user_id" not in _column_names(conn, "wrong_questions"):
        conn.execute(
            "ALTER TABLE wrong_questions ADD COLUMN user_id INT NOT NULL DEFAULT 0"
        )
    if "explanation" not in _column_names(conn, "questions"):
        # MySQL 添加 NOT NULL TEXT 列时会自动把已有行填为 ''，无需显式 DEFAULT
        default = "" if is_mysql else " DEFAULT ''"
        conn.execute(f"ALTER TABLE questions ADD COLUMN explanation TEXT NOT NULL{default}")


def backfill_points_ledger(conn: db.Connection) -> int:
    """把尚无积分流水的历史成绩回填为积分流水（幂等，返回新增条数）。

    升级到排行榜版本前，`daily_records` 已存在成绩但没有积分流水。这里按
    `(record_id)` 判重：已有正式答题流水的成绩记录不会重复回填；重答覆盖成绩时
    也不会产生第二条回填记录。积分按当时答对题数 1:1 计算，统计日期取
    `finished_at` 的日期部分（答题发生日）。

    Args:
        conn: 数据库连接，由调用方管理事务（init_db 中统一 commit）。

    Returns:
        本次回填的流水条数。
    """
    cur = conn.execute(
        """
        INSERT INTO points_ledger
            (user_id, daily_set_id, record_id, earned_date, set_date,
             points, correct, total, source, created_at)
        SELECT dr.user_id, dr.daily_set_id, dr.id,
               substr(dr.finished_at, 1, 10), ds.date,
               dr.correct, dr.correct, dr.total, 'backfill', dr.finished_at
        FROM daily_records dr
        JOIN daily_sets ds ON ds.id = dr.daily_set_id
        JOIN users u ON u.id = dr.user_id
        WHERE NOT EXISTS (
            SELECT 1 FROM points_ledger p WHERE p.record_id = dr.id
        )
        """
    )
    return cur.rowcount


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


def get_user_by_username(username: str) -> Optional[db.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row


def get_user(user_id: int) -> Optional[db.Row]:
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

def get_daily_set_by_date(date: str) -> Optional[db.Row]:
    """按日期获取每日题目集。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_sets WHERE date = ?", (date,)).fetchone()
    conn.close()
    return row


def get_daily_set_by_id(set_id: int) -> Optional[db.Row]:
    """按 id 获取每日题目集。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_sets WHERE id = ?", (set_id,)).fetchone()
    conn.close()
    return row


def create_daily_set(
    date: str,
    title: str,
    total_sets: int,
    status: str = "pending",
    category_slug: str = "",
    category_path: str = "",
) -> int:
    """创建一条每日题目集记录，返回新 id。

    Args:
        date / title / total_sets / status: 题库基本信息。
        category_slug: 导入时选中的栏目节点 slug；空串表示未分类（兼容脚本导入）。
        category_path: 栏目完整路径文案快照，供列表展示。

    Returns:
        新建题目集的 id。
    """
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO daily_sets "
        "(date, title, total_sets, status, created_at, category_slug, category_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date, title, total_sets, status, created_at, category_slug, category_path),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def set_set_status(
    set_id: int, status: str, conn: Optional[db.Connection] = None
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


def get_all_questions_by_set(set_id: int) -> list[db.Row]:
    """获取某日题目集的全部题目（含选项与答案，按套与 id 排序）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM questions WHERE daily_set_id = ? ORDER BY set_index, id",
        (set_id,),
    ).fetchall()
    conn.close()
    return rows


def get_quiz_questions_by_set(set_id: int) -> list[db.Row]:
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
    """级联删除某日期的题目集及其题目、成绩、错题、积分流水（单事务）。

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
            # 积分流水显式清理（同时也有 FK CASCADE 兜底），避免删除题库后
            # 榜单仍计入已被删除题库产生的积分
            conn.execute(
                "DELETE FROM points_ledger WHERE daily_set_id IN "
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
    conn: Optional[db.Connection] = None,
    category_slug: Optional[str] = None,
    category_path: Optional[str] = None,
) -> None:
    """更新题目集的日期、标题与所属栏目。

    Args:
        set_id: 题目集 id。
        new_date: 新日期（YYYY-MM-DD）；唯一性由调用方校验。
        title: 新标题。
        conn: 外部连接，传入时由调用方统一管理事务。
        category_slug / category_path: 传 None 表示保持原值不变（兼容旧调用）。

    Raises:
        db.IntegrityError: 新日期与已有题目集冲突（date 有 UNIQUE 约束）。
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        if category_slug is None:
            conn.execute(
                "UPDATE daily_sets SET date = ?, title = ? WHERE id = ?",
                (new_date, title, set_id),
            )
        else:
            conn.execute(
                "UPDATE daily_sets SET date = ?, title = ?, category_slug = ?, "
                "category_path = ? WHERE id = ?",
                (new_date, title, category_slug, category_path or "", set_id),
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
    conn: Optional[db.Connection] = None,
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
    question_id: int, conn: Optional[db.Connection] = None
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
    set_id: int, conn: Optional[db.Connection] = None
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
    set_id: int, conn: Optional[db.Connection] = None
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


def list_sets_by_category(
    slugs: list[str] | tuple[str, ...],
    user_id: Optional[int] = None,
) -> list[dict]:
    """列出归属于给定栏目（含其子孙节点）的题库，按日期倒序。

    栏目页需要「父栏目看到全部子栏目的题目」：调用方用
    ``navigation.descendant_slugs()`` 展开 slug 集合后传入。只匹配 slug，
    不使用 ``category_path`` 文本，避免菜单改名后查询失效。

    Args:
        slugs: 栏目 slug 集合；空集合直接返回空列表。
        user_id: 传入时 completed 表示该用户是否已完成；None 表示任意用户。

    Returns:
        与 ``list_daily_sets`` 同结构的字典列表（含 completed）。
    """
    if not slugs:
        return []
    placeholders = ", ".join("?" for _ in slugs)
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT ds.*,
               (SELECT COUNT(*) FROM daily_records dr
                  WHERE dr.daily_set_id = ds.id
                    AND (? IS NULL OR dr.user_id = ?)) AS rec_count
        FROM daily_sets ds
        WHERE ds.category_slug IN ({placeholders})
        ORDER BY ds.date DESC
        """,
        (user_id, user_id, *slugs),
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
