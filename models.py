"""数据模型模块（MySQL / SQLite 双后端）。

负责数据库连接、建表，以及 admin / users / daily_sets / questions /
daily_records / wrong_questions / points_ledger / cert_intro 八张表的 CRUD。

角色隔离：`admin` 表与 `users` 表各自独立，管理员不占用 users 的 id，
两套账号分别由 /admin/login 与 /login 登录、分别写入独立 session。

连接统一由 `db.py` 提供：生产 / Docker 使用 MySQL（`schema_mysql.sql` 建表），
未配置 MySQL 时回退 SQLite（兼容既有本地库与回归测试）。业务 SQL 保持 SQLite
风格的 `?` 占位符，由 `db.MySQLConnection` 在 MySQL 后端自动转换为 `%s`。

栏目说明：`daily_sets.category_slug` 保存导入时选中的认证栏目节点 slug，
`category_path` 保存当时的完整路径文案快照（如「阿里云认证 › 阿里云ACP ›
云计算高级工程师」）。存快照是为了以后菜单配置变更、节点被移除时，历史题库
仍能显示可读的归属；slug 则用于栏目页按「自身 + 子孙节点」聚合题目。

权限说明（隔离后的版本）：
- `admin` 保存后台管理员；`users` 只保存普通答题用户（role 恒为 user）。
- `daily_records`（成绩）与 `wrong_questions`（错题）均带 `user_id`，
  指向 users.id；管理员账号不产生答题数据。
- 题目集与题目是全局共享的「题库」，由管理员通过 /api/admin/ 维护。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import db
import navigation

BASE_DIR = Path(__file__).resolve().parent
# SQLite 回退后端使用的数据库文件；测试脚本会把它重定向到独立临时库。
DB_PATH = BASE_DIR / "data" / "quiz.db"
# MySQL 后端的建表脚本（同时挂载给 MySQL 容器做首次初始化）。
MYSQL_SCHEMA_FILE = BASE_DIR / "schema_mysql.sql"

SCHEMA = """
-- 普通答题用户（管理员不在这里）
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    nickname      TEXT NOT NULL DEFAULT '',
    gender        TEXT NOT NULL DEFAULT '保密',
    bio           TEXT NOT NULL DEFAULT '',
    phone         TEXT,
    phone_verified INTEGER NOT NULL DEFAULT 0,
    email         TEXT,
    email_verified INTEGER NOT NULL DEFAULT 0,
    reset_token   TEXT NOT NULL DEFAULT '',
    reset_token_expire_at TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT ''
);

-- 手机号 / 邮箱唯一索引在 _migrate() 中创建：旧库要先补列再建索引

-- 短信验证码：只存 SHA-256，10 分钟有效，单码最多 5 次尝试
CREATE TABLE IF NOT EXISTS sms_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    phone       TEXT NOT NULL,
    purpose     TEXT NOT NULL DEFAULT 'bind_phone',
    code_hash   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sms_user ON sms_codes(user_id, purpose, created_at);

-- 邮箱验证码 / 密码重置 Token：两者共用一张表，用 purpose 区分
CREATE TABLE IF NOT EXISTS email_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    email       TEXT NOT NULL,
    purpose     TEXT NOT NULL,
    code_hash   TEXT NOT NULL DEFAULT '',
    token_hash  TEXT NOT NULL DEFAULT '',
    expires_at  TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_email_user ON email_tokens(user_id, purpose, created_at);
CREATE INDEX IF NOT EXISTS idx_email_target ON email_tokens(email, purpose, created_at);

-- 独立管理员表；password 存 PBKDF2:sha256 哈希，绝不存明文
CREATE TABLE IF NOT EXISTS admin (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password      TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin',
    display_name  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    last_login_at TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT ''
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
    daily_set_id INTEGER,
    set_index    INTEGER NOT NULL,
    type         TEXT NOT NULL,
    question     TEXT NOT NULL,
    options      TEXT NOT NULL,
    answer       TEXT NOT NULL,
    explanation  TEXT NOT NULL DEFAULT '',
    cert_category_id INTEGER,
    difficulty   TEXT NOT NULL DEFAULT '中',
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL,
    FOREIGN KEY (daily_set_id) REFERENCES daily_sets(id) ON DELETE CASCADE,
    FOREIGN KEY (cert_category_id) REFERENCES cert_categories(id) ON DELETE CASCADE
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

-- 认证百科介绍（CMS）：slug 对应 navigation.py 的三级叶子节点，预置内容由
-- seed_cert_intros 幂等写入；管理员修改的是这张表里的副本，不覆盖配置兜底。
CREATE TABLE IF NOT EXISTS cert_intro (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT NOT NULL UNIQUE,
    cert_name     TEXT NOT NULL,
    vendor        TEXT NOT NULL DEFAULT '',
    direction     TEXT NOT NULL DEFAULT '',
    level         TEXT NOT NULL DEFAULT '',
    intro         TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT '',
    is_preset     INTEGER NOT NULL DEFAULT 1,
    updated_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_cert_intro_vendor ON cert_intro(vendor);
CREATE INDEX IF NOT EXISTS idx_cert_intro_name ON cert_intro(cert_name);

-- ===========================================================================
-- 认证题库网（v2）：认证分类 / 五段式介绍 / 认证题库 / 答题记录 / 荣誉 / 榜单
-- ===========================================================================

-- 独立管理员表（复数命名，与需求一致）；旧 admin 表仅作迁移来源保留
CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password      TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin',
    display_name  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    last_login_at TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT ''
);

-- 认证分类：一行 = 一个可刷题的三级认证（单级方向则二级即叶子）
CREATE TABLE IF NOT EXISTS cert_categories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_slug     TEXT NOT NULL,
    vendor_name     TEXT NOT NULL,
    direction_slug  TEXT NOT NULL,
    direction_name  TEXT NOT NULL,
    level_slug      TEXT NOT NULL DEFAULT '',
    level_name      TEXT NOT NULL DEFAULT '',
    path_slug       TEXT NOT NULL UNIQUE,
    cert_name       TEXT NOT NULL,
    exam_code       TEXT NOT NULL DEFAULT '',
    difficulty      TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active',
    seo_title       TEXT NOT NULL DEFAULT '',
    seo_keywords    TEXT NOT NULL DEFAULT '',
    seo_description TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_cat_vendor ON cert_categories(vendor_slug, sort_order);
CREATE INDEX IF NOT EXISTS idx_cat_direction ON cert_categories(vendor_slug, direction_slug);

-- 认证介绍（五段式），与分类 1:1
CREATE TABLE IF NOT EXISTS cert_intros (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER NOT NULL UNIQUE,
    positioning  TEXT NOT NULL DEFAULT '',
    audience     TEXT NOT NULL DEFAULT '',
    exam_topics  TEXT NOT NULL DEFAULT '',
    difficulty   TEXT NOT NULL DEFAULT '',
    advice       TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    updated_by   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (category_id) REFERENCES cert_categories(id) ON DELETE CASCADE
);

-- 每日答题记录（一次交卷一行，不覆盖）
CREATE TABLE IF NOT EXISTS quiz_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    category_id  INTEGER NOT NULL,
    cert_path    TEXT NOT NULL DEFAULT '',
    cert_name    TEXT NOT NULL DEFAULT '',
    question_ids TEXT NOT NULL DEFAULT '[]',
    answers      TEXT NOT NULL DEFAULT '{}',
    total        INTEGER NOT NULL DEFAULT 0,
    correct      INTEGER NOT NULL DEFAULT 0,
    score        INTEGER NOT NULL DEFAULT 0,
    accuracy     REAL NOT NULL DEFAULT 0,
    duration     INTEGER NOT NULL DEFAULT 0,
    started_at   TEXT NOT NULL DEFAULT '',
    finished_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES cert_categories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_qr_user ON quiz_records(user_id, finished_at);
CREATE INDEX IF NOT EXISTS idx_qr_cat ON quiz_records(category_id, finished_at);
CREATE INDEX IF NOT EXISTS idx_qr_time ON quiz_records(finished_at);

-- 个人荣誉：每次答题写一条荣誉流水
CREATE TABLE IF NOT EXISTS user_honors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    cert_name   TEXT NOT NULL DEFAULT '',
    score       INTEGER NOT NULL DEFAULT 0,
    correct     INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    duration    INTEGER NOT NULL DEFAULT 0,
    quiz_date   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES cert_categories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_uh_user ON user_honors(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_uh_date ON user_honors(quiz_date);

-- 排行榜聚合：日 / 周 / 月三种周期，交卷时同事务 UPSERT
CREATE TABLE IF NOT EXISTS rankings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period_type TEXT NOT NULL,
    period_key  TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    points      INTEGER NOT NULL DEFAULT 0,
    quiz_count  INTEGER NOT NULL DEFAULT 0,
    best_score  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_rank ON rankings(period_type, period_key, user_id);
CREATE INDEX IF NOT EXISTS idx_rank_query ON rankings(period_type, period_key, points);

-- ===========================================================================
-- 自由练习：考试规则 / 题库 / 题库题目 / 练习场次 / 逐题作答 / 学习进度
-- ===========================================================================

-- 考试规则模板：后台可自由配置，题库创建时复制一份快照到 question_banks，
-- 这样后续改规则不会改写已发布题库的历史约束。
CREATE TABLE IF NOT EXISTS exam_rules (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    code               TEXT NOT NULL UNIQUE,
    name               TEXT NOT NULL,
    vendor_slug        TEXT NOT NULL DEFAULT '',
    cert_path          TEXT NOT NULL DEFAULT '',
    total_questions    INTEGER NOT NULL DEFAULT 0,
    single_count       INTEGER NOT NULL DEFAULT 0,
    multiple_count     INTEGER NOT NULL DEFAULT 0,
    score_per_question REAL NOT NULL DEFAULT 1,
    total_score        REAL NOT NULL DEFAULT 100,
    pass_score         REAL NOT NULL DEFAULT 80,
    duration_minutes   INTEGER NOT NULL DEFAULT 120,
    description        TEXT NOT NULL DEFAULT '',
    is_preset          INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'active',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL DEFAULT ''
);

-- 题库（模拟卷 / 专项训练）：前端只展示 status='published' 的记录。
-- total_questions / single_count / multiple_count 是「声明的题量与题型比例」，
-- 发布时必须与题库内实际题目数、单选/多选题数完全一致。
CREATE TABLE IF NOT EXISTS question_banks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    slug               TEXT NOT NULL UNIQUE,
    name               TEXT NOT NULL,
    vendor_slug        TEXT NOT NULL DEFAULT '',
    vendor_name        TEXT NOT NULL DEFAULT '',
    cert_path          TEXT NOT NULL DEFAULT '',
    cert_name          TEXT NOT NULL DEFAULT '',
    category_id        INTEGER,
    level_tag          TEXT NOT NULL DEFAULT '基础',
    difficulty         TEXT NOT NULL DEFAULT '中等',
    description        TEXT NOT NULL DEFAULT '',
    rule_code          TEXT NOT NULL DEFAULT 'custom',
    rule_name          TEXT NOT NULL DEFAULT '',
    total_questions    INTEGER NOT NULL DEFAULT 0,
    single_count       INTEGER NOT NULL DEFAULT 0,
    multiple_count     INTEGER NOT NULL DEFAULT 0,
    score_per_question REAL NOT NULL DEFAULT 1,
    total_score        REAL NOT NULL DEFAULT 100,
    pass_score         REAL NOT NULL DEFAULT 80,
    duration_minutes   INTEGER NOT NULL DEFAULT 120,
    status             TEXT NOT NULL DEFAULT 'draft',
    sort_order         INTEGER NOT NULL DEFAULT 0,
    created_by         TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL DEFAULT '',
    published_at       TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (category_id) REFERENCES cert_categories(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_qb_status ON question_banks(status, sort_order);
CREATE INDEX IF NOT EXISTS idx_qb_cert ON question_banks(cert_path, status);
CREATE INDEX IF NOT EXISTS idx_qb_vendor ON question_banks(vendor_slug, status);

CREATE TABLE IF NOT EXISTS bank_questions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_id      INTEGER NOT NULL,
    type         TEXT NOT NULL DEFAULT 'single',
    question     TEXT NOT NULL,
    options      TEXT NOT NULL DEFAULT '{}',
    answer       TEXT NOT NULL DEFAULT '[]',
    explanation  TEXT NOT NULL DEFAULT '',
    difficulty   TEXT NOT NULL DEFAULT '中等',
    sort_order   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (bank_id) REFERENCES question_banks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bq_bank ON bank_questions(bank_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_bq_type ON bank_questions(bank_id, type);

-- 一次练习 = 一场模拟考试。question_ids 是开考瞬间的题目快照（JSON 数组），
-- 之后管理员增删题目不会改变正在进行 / 已完成的这场练习。
CREATE TABLE IF NOT EXISTS bank_practice_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token         TEXT NOT NULL UNIQUE,
    bank_id       INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    question_ids  TEXT NOT NULL DEFAULT '[]',
    answers       TEXT NOT NULL DEFAULT '{}',
    total         INTEGER NOT NULL DEFAULT 0,
    answered      INTEGER NOT NULL DEFAULT 0,
    correct       INTEGER NOT NULL DEFAULT 0,
    score         REAL NOT NULL DEFAULT 0,
    accuracy      REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'ongoing',
    current_index INTEGER NOT NULL DEFAULT 0,
    started_at    TEXT NOT NULL,
    deadline_at   TEXT NOT NULL DEFAULT '',
    finished_at   TEXT NOT NULL DEFAULT '',
    duration      INTEGER NOT NULL DEFAULT 0,
    submit_reason TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (bank_id) REFERENCES question_banks(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bps_user_bank ON bank_practice_sessions(user_id, bank_id, status);
CREATE INDEX IF NOT EXISTS idx_bps_deadline ON bank_practice_sessions(status, deadline_at);
CREATE INDEX IF NOT EXISTS idx_bps_bank ON bank_practice_sessions(bank_id, status);

-- 逐题作答流水：同一场练习内每题只保留最后一次作答（UNIQUE），
-- 重新练习会产生新的 session，历史场次永久保留。
CREATE TABLE IF NOT EXISTS bank_practice_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    bank_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    user_answer TEXT NOT NULL DEFAULT '[]',
    is_correct  INTEGER NOT NULL DEFAULT 0,
    answered_at TEXT NOT NULL,
    UNIQUE (session_id, question_id),
    FOREIGN KEY (session_id) REFERENCES bank_practice_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (bank_id) REFERENCES question_banks(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES bank_questions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bpa_user_bank ON bank_practice_answers(user_id, bank_id, question_id);
CREATE INDEX IF NOT EXISTS idx_bpa_session ON bank_practice_answers(session_id);

-- 学习进度：按「用户 × 题库」聚合，answered_count 是去重后的已练题数，
-- wrong_count 取每题最后一次作答结果，答对后自动从错题数中移除。
CREATE TABLE IF NOT EXISTS bank_practice_progress (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    bank_id           INTEGER NOT NULL,
    answered_count    INTEGER NOT NULL DEFAULT 0,
    correct_count     INTEGER NOT NULL DEFAULT 0,
    wrong_count       INTEGER NOT NULL DEFAULT 0,
    best_score        REAL NOT NULL DEFAULT 0,
    last_score        REAL NOT NULL DEFAULT 0,
    last_session_id   INTEGER,
    last_practiced_at TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL DEFAULT '',
    UNIQUE (user_id, bank_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (bank_id) REFERENCES question_banks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bpp_bank ON bank_practice_progress(bank_id, answered_count);

-- 模拟考积分流水：一套题库 × 一个用户 × 一个自然日只计一次（UNIQUE），
-- 重复练习不叠加；换题库、换天可再计。points 由 practice_bank 按正确率折算，
-- session_id 指向首次计分的那场练习，用于「本场获得 N 积分」的回显与追溯。
CREATE TABLE IF NOT EXISTS bank_exam_awards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    bank_id     INTEGER NOT NULL,
    session_id  INTEGER NOT NULL,
    earned_date TEXT NOT NULL,
    score       REAL NOT NULL DEFAULT 0,
    points      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    UNIQUE (user_id, bank_id, earned_date),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (bank_id) REFERENCES question_banks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bea_user_date ON bank_exam_awards(user_id, earned_date);
CREATE INDEX IF NOT EXISTS idx_bea_session ON bank_exam_awards(session_id);
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
        # 升级为「独立 admin 表」时，把 users 里遗留的 admin 账号平滑搬过去
        migrate_admin_accounts(conn)
        # 幂等回填：升级到排行榜版本前的历史成绩也要能进榜
        backfill_points_ledger(conn)
        # 幂等播种预置百科介绍：只新增缺失项，绝不覆盖管理员已编辑的正文
        seed_cert_intros(conn)
        # 旧 admin 表 → 新 admins 表（幂等）
        migrate_legacy_admins(conn)
        # 认证分类与五段式介绍：按 catalog.py 幂等播种（不覆盖管理员编辑）
        import bank
        bank.sync_catalog(conn)
        # 自由练习的预置考试规则（ACP 云计算 / ACP 大模型），只补缺失项
        import practice_bank
        practice_bank.seed_rules(conn)
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

    # --- 认证题库网 v2 迁移 -------------------------------------------------
    short_label = "VARCHAR(16)" if is_mysql else "TEXT"
    if "nickname" not in _column_names(conn, "users"):
        nick_type = "VARCHAR(64)" if is_mysql else "TEXT"
        conn.execute(f"ALTER TABLE users ADD COLUMN nickname {nick_type} NOT NULL DEFAULT ''")

    # --- 个人中心：资料字段 + 绑定信息 + 重置 Token ---------------------------
    user_cols = _column_names(conn, "users")
    # (列名, MySQL 类型, SQLite 类型) —— 只为存量库补列，新库由建表语句直接创建
    profile_columns = [
        ("gender", "VARCHAR(8) NOT NULL DEFAULT '保密'", "TEXT NOT NULL DEFAULT '保密'"),
        ("bio", "VARCHAR(255) NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''"),
        ("phone", "VARCHAR(20) NULL", "TEXT"),
        ("phone_verified", "TINYINT(1) NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
        ("email", "VARCHAR(128) NULL", "TEXT"),
        ("email_verified", "TINYINT(1) NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
        ("reset_token", "VARCHAR(64) NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''"),
        ("reset_token_expire_at", "VARCHAR(19) NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''"),
        ("updated_at", "VARCHAR(19) NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''"),
    ]
    for column, mysql_type, sqlite_type in profile_columns:
        if column not in user_cols:
            conn.execute(
                f"ALTER TABLE users ADD COLUMN {column} "
                f"{mysql_type if is_mysql else sqlite_type}"
            )

    # 唯一索引前先清空串：历史账号的 phone/email 都是 ''，会互相冲突
    conn.execute("UPDATE users SET phone = NULL WHERE phone = ''")
    conn.execute("UPDATE users SET email = NULL WHERE email = ''")

    # --- 用户名唯一性：先归一化存量数据，再补大小写不敏感的唯一索引 -----------
    try:
        stats = conn.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT LOWER(TRIM(username))) AS uniq FROM users"
        ).fetchone()
        if int(stats["total"]) == int(stats["uniq"]):
            # 归一化不会撞车时才改写，避免把历史大写用户名改坏
            conn.execute(
                "UPDATE users SET username = LOWER(TRIM(username)) "
                "WHERE username <> LOWER(TRIM(username))"
            )
        else:
            print("[models] 存在仅大小写不同的用户名，跳过自动归一化，请手工清理后重启")
    except db.DatabaseError:
        pass

    if is_mysql:
        has_username_index = conn.execute(
            "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
            "AND COLUMN_NAME = 'username' AND NON_UNIQUE = 0"
        ).fetchone()
        if not int(has_username_index["n"]):
            try:
                conn.execute("ALTER TABLE users ADD UNIQUE KEY uk_users_username (username)")
            except db.DatabaseError:
                print("[models] users.username 唯一索引创建失败，请先清理重复用户名")
    else:
        # SQLite 的 UNIQUE 约束区分大小写，这里补一个 NOCASE 索引兜底
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uk_users_username "
                "ON users(username COLLATE NOCASE)"
            )
        except db.DatabaseError:
            print("[models] users.username NOCASE 唯一索引创建失败，请先清理重复用户名")
    if is_mysql:
        for index_name, column in (("uk_users_phone", "phone"), ("uk_users_email", "email")):
            has_index = conn.execute(
                "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
                "AND INDEX_NAME = ?",
                (index_name,),
            ).fetchone()
            if not int(has_index["n"]):
                try:
                    conn.execute(f"ALTER TABLE users ADD UNIQUE KEY {index_name} ({column})")
                except db.DatabaseError:
                    pass  # 存量库若仍有重复绑定，先跳过，清理后下次启动自动补
    else:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uk_users_phone ON users(phone)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uk_users_email ON users(email)")

    q_cols = _column_names(conn, "questions")
    if "cert_category_id" not in q_cols:
        # MySQL 的外键要求两侧列类型完全一致，cert_categories.id 是 INT UNSIGNED
        conn.execute(
            "ALTER TABLE questions ADD COLUMN cert_category_id INT UNSIGNED"
            if is_mysql else
            "ALTER TABLE questions ADD COLUMN cert_category_id INTEGER"
        )
    if "difficulty" not in q_cols:
        conn.execute(
            f"ALTER TABLE questions ADD COLUMN difficulty {short_label} NOT NULL DEFAULT '中'"
        )
    if "status" not in q_cols:
        conn.execute(
            f"ALTER TABLE questions ADD COLUMN status {short_label} NOT NULL DEFAULT 'active'"
        )

    if not is_mysql:
        # SQLite 无法直接去掉 NOT NULL：认证题库的题目不挂 daily_set，需要重建表。
        info = conn.execute("PRAGMA table_info(questions)").fetchall()
        col = next((c for c in info if c["name"] == "daily_set_id"), None)
        if col is not None and int(col["notnull"]) == 1:
            raw = getattr(conn, "_raw", None)
            if raw is not None:
                raw.execute("PRAGMA foreign_keys = OFF")
                try:
                    conn.executescript(
                        """
                        CREATE TABLE questions_new (
                            id               INTEGER PRIMARY KEY AUTOINCREMENT,
                            daily_set_id     INTEGER,
                            set_index        INTEGER NOT NULL DEFAULT 0,
                            type             TEXT NOT NULL,
                            question         TEXT NOT NULL,
                            options          TEXT NOT NULL,
                            answer           TEXT NOT NULL,
                            explanation      TEXT NOT NULL DEFAULT '',
                            cert_category_id INTEGER,
                            difficulty       TEXT NOT NULL DEFAULT '中',
                            status           TEXT NOT NULL DEFAULT 'active',
                            created_at       TEXT NOT NULL,
                            FOREIGN KEY (daily_set_id) REFERENCES daily_sets(id) ON DELETE CASCADE,
                            FOREIGN KEY (cert_category_id) REFERENCES cert_categories(id) ON DELETE CASCADE
                        );
                        INSERT INTO questions_new (id, daily_set_id, set_index, type, question,
                            options, answer, explanation, cert_category_id, difficulty, status, created_at)
                        SELECT id, daily_set_id, set_index, type, question, options, answer,
                               explanation, cert_category_id, difficulty, status, created_at
                        FROM questions;
                        DROP TABLE questions;
                        ALTER TABLE questions_new RENAME TO questions;
                        CREATE INDEX IF NOT EXISTS idx_questions_set ON questions(daily_set_id, set_index);
                        CREATE INDEX IF NOT EXISTS idx_q_category ON questions(cert_category_id, status);
                        """
                    )
                    raw.commit()
                finally:
                    raw.execute("PRAGMA foreign_keys = ON")

    # 题目分类索引：旧库要先补列再建索引，故放在迁移尾部
    try:
        if is_mysql:
            conn.execute("CREATE INDEX idx_q_category ON questions(cert_category_id, status)")
        else:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_q_category ON questions(cert_category_id, status)"
            )
    except Exception:
        pass  # 索引已存在时忽略

    if is_mysql:
        # 认证题库的题目不挂在「按日期题库」下，需要允许 daily_set_id 为 NULL。
        # 外键在 NULL 上不生效，因此保留约束即可。
        row = conn.execute(
            "SELECT IS_NULLABLE AS n FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'questions' "
            "AND COLUMN_NAME = 'daily_set_id'"
        ).fetchone()
        if row and str(row["n"]).upper() == "NO":
            conn.execute("ALTER TABLE questions MODIFY daily_set_id INT UNSIGNED NULL")

    # --- 题库完整性 ---------------------------------------------------------
    # 分类被删除（或配置下线）后遗留的题目会污染「全站题库」兜底池，启动时清掉。
    conn.execute(
        "DELETE FROM questions WHERE cert_category_id IS NOT NULL "
        "AND cert_category_id NOT IN (SELECT id FROM cert_categories)"
    )
    if is_mysql:
        # 新库由建表脚本自带外键；旧库在这里补一条，删除分类时级联清理题目。
        has_fk = conn.execute(
            "SELECT COUNT(*) AS n FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = 'fk_questions_category'"
        ).fetchone()["n"]
        if not int(has_fk):
            try:
                # 旧库的 cert_category_id 是 signed INT，先对齐成 INT UNSIGNED
                conn.execute(
                    "ALTER TABLE questions MODIFY cert_category_id INT UNSIGNED NULL"
                )
                conn.execute(
                    "ALTER TABLE questions ADD CONSTRAINT fk_questions_category "
                    "FOREIGN KEY (cert_category_id) REFERENCES cert_categories(id) "
                    "ON DELETE CASCADE"
                )
            except db.DatabaseError:
                pass  # 极端脏数据下先跳过，下次启动清完孤儿题会重试


def migrate_legacy_admins(conn: db.Connection) -> int:
    """把旧 `admin` 表的账号复制到新 `admins` 表（幂等，只补缺口）。"""
    try:
        rows = conn.execute("SELECT * FROM admin").fetchall()
    except Exception:  # 全新库或旧表不存在时静默跳过
        return 0
    copied = 0
    for row in rows:
        exists = conn.execute(
            "SELECT id FROM admins WHERE username = ?", (row["username"],)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO admins (username, password, role, display_name, status, "
            "last_login_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["username"], row["password"],
                row["role"] if "role" in row else "admin",
                row["display_name"] if "display_name" in row else "",
                row["status"] if "status" in row else "active",
                row["last_login_at"] if "last_login_at" in row else "",
                row["created_at"], row["updated_at"] if "updated_at" in row else "",
            ),
        )
        copied += 1
    return copied


def migrate_admin_accounts(conn: db.Connection) -> dict:
    """把旧版 users 表中 role='admin' 的账号搬到独立 admin 表（幂等）。

    旧库里管理员与普通用户混在 users 一张表。升级后：
    - 管理员账号按用户名复制进 admin 表（沿用原密码哈希，无需改密）；
    - 若该管理员已经有答题 / 错题 / 积分数据（外键引用 users.id），
      为保证历史数据不丢，保留 users 行但把 role 降为 'user'；
    - 没有关联数据的管理员行直接从 users 删除，做到 users 只存普通用户。

    Returns:
        {"moved": 已删除的 users 行数, "kept": 因历史数据保留并降权的行数,
         "created": 新写入 admins 表的账号数}
    """
    stat = {"moved": 0, "kept": 0, "created": 0}
    rows = conn.execute(
        "SELECT id, username, password_hash, created_at FROM users WHERE role = 'admin'"
    ).fetchall()
    for row in rows:
        uid, username = row["id"], row["username"]
        exists = conn.execute(
            "SELECT id FROM admins WHERE username = ?", (username,)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO admins (username, password, role, created_at) "
                "VALUES (?, ?, 'admin', ?)",
                (username, row["password_hash"], row["created_at"]),
            )
            stat["created"] += 1

        linked = False
        for table in ("daily_records", "wrong_questions", "points_ledger"):
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE user_id = ? LIMIT 1", (uid,)
            ).fetchone():
                linked = True
                break
        if linked:
            conn.execute("UPDATE users SET role = 'user' WHERE id = ?", (uid,))
            stat["kept"] += 1
        else:
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
            stat["moved"] += 1
    return stat


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
# cert_intro：认证百科介绍（CMS 可编辑）
# ---------------------------------------------------------------------------

def seed_cert_intros(conn: db.Connection) -> int:
    """把 navigation.INTRO_PRESETS 的预置介绍幂等写入 cert_intro 表。

    只 INSERT 缺失的 slug，**绝不覆盖**已有行：管理员在后台改过的正文、
    来源与更新时间都会原样保留（应用重启 / 容器重建也不会被重置）。

    Args:
        conn: 数据库连接，事务由调用方（init_db）统一提交。

    Returns:
        本次新增的条数。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created = 0
    for slug, preset in navigation.INTRO_PRESETS.items():
        exists = conn.execute(
            "SELECT id FROM cert_intro WHERE slug = ?", (slug,)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO cert_intro "
            "(slug, cert_name, vendor, direction, level, intro, source, "
            " is_preset, updated_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, '', ?, '')",
            (
                slug,
                preset["cert_name"],
                preset["vendor"],
                preset["direction"],
                preset["level"],
                preset["intro"],
                preset["source"],
                now,
            ),
        )
        created += 1
    return created


def get_cert_intro(slug: str) -> Optional[dict]:
    """按菜单 slug 读取介绍；无记录返回 None（页面回退到导航配置中的文案）。"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM cert_intro WHERE slug = ?", (slug,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_cert_intros(keyword: str = "") -> list[dict]:
    """管理端「认证介绍管理」列表，支持按名称 / 厂商 / 方向 / 等级模糊搜索。"""
    kw = (keyword or "").strip()
    conn = get_conn()
    try:
        if kw:
            like = f"%{kw}%"
            rows = conn.execute(
                "SELECT * FROM cert_intro "
                "WHERE cert_name LIKE ? OR vendor LIKE ? OR direction LIKE ? "
                "     OR level LIKE ? OR slug LIKE ? "
                "ORDER BY vendor, id",
                (like, like, like, like, like),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cert_intro ORDER BY vendor, id"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_cert_intro(
    slug: str,
    intro: str,
    updated_by: str = "",
    cert_name: Optional[str] = None,
    source: Optional[str] = None,
) -> bool:
    """保存管理员编辑的介绍正文（只允许更新已存在的预置条目）。

    Args:
        slug: 菜单节点 slug，必须在 cert_intro 中存在。
        intro: 新正文。
        updated_by: 修改人用户名。
        cert_name / source: 可选，同时更新显示名与资料来源；None 表示不改。

    Returns:
        True 表示已更新；slug 不存在返回 False（不允许通过接口新增未知认证）。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT cert_name, source FROM cert_intro WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return False
        # None 表示保持原值，避免只改正文时误清空名称 / 来源
        new_name = cert_name if cert_name is not None else row["cert_name"]
        new_source = source if source is not None else row["source"]
        cur = conn.execute(
            "UPDATE cert_intro SET intro = ?, cert_name = ?, source = ?, "
            "updated_by = ?, updated_at = ? WHERE slug = ?",
            (intro, new_name, new_source, updated_by, now, slug),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

def create_user(username: str, password_hash: str, role: str = "user",
                *,
                email: Optional[str] = None,
                phone: Optional[str] = None) -> int:
    """创建用户，返回新用户 id。email / phone 可选，写入后为「未验证」状态。"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at, email, phone, "
            "email_verified, phone_verified) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
            (username, password_hash, role, created_at, email or None, phone or None),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def username_taken(username: str) -> bool:
    """用户名是否已被占用：users 与 admins 都算，且不区分大小写。

    管理员表同名也视为占用，避免用户注册出 `admin` 这类容易冒充后台的账号。
    """
    name = (username or "").strip().lower()
    if not name:
        return False
    conn = get_conn()
    try:
        hit = conn.execute(
            "SELECT 1 AS hit FROM users WHERE LOWER(username) = ? LIMIT 1", (name,)
        ).fetchone()
        if hit:
            return True
        hit = conn.execute(
            "SELECT 1 AS hit FROM admins WHERE LOWER(username) = ? LIMIT 1", (name,)
        ).fetchone()
        return bool(hit)
    finally:
        conn.close()


def email_taken(email: str, exclude_user_id: Optional[int] = None) -> bool:
    """邮箱是否已被其他账号占用（大小写不敏感）。"""
    value = (email or "").strip().lower()
    if not value:
        return False
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 AS hit FROM users WHERE LOWER(email) = ? AND id <> ? LIMIT 1",
            (value, int(exclude_user_id or 0)),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def phone_taken(phone: str, exclude_user_id: Optional[int] = None) -> bool:
    """手机号是否已被其他账号占用。"""
    value = (phone or "").strip()
    if not value:
        return False
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 AS hit FROM users WHERE phone = ? AND id <> ? LIMIT 1",
            (value, int(exclude_user_id or 0)),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


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
# admins：独立管理员账号（与 users 完全隔离）
# ---------------------------------------------------------------------------

def create_admin(username: str, password_hash: str, role: str = "admin") -> int:
    """创建管理员，返回新 id。password_hash 为 PBKDF2 哈希，不接收明文。"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO admins (username, password, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, created_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_admin_by_username(username: str) -> Optional[db.Row]:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM admins WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()


def get_admin(admin_id: int) -> Optional[db.Row]:
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()
    finally:
        conn.close()


def count_admins() -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM admins").fetchone()["c"]
    finally:
        conn.close()


def list_admins() -> list[dict]:
    """管理端「管理员账号」列表（不返回密码哈希）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, username, role, display_name, status, "
            "last_login_at, created_at FROM admins ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def touch_admin_login(admin_id: int) -> None:
    """记录最近登录时间（失败不影响登录流程）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE admins SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now, now, admin_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_admin_password(admin_id: int, password_hash: str) -> bool:
    """更新管理员密码哈希，返回是否命中该账号。

    只接收哈希，明文永不入库；同时刷新 updated_at，便于审计改密时间。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE admins SET password = ?, updated_at = ? WHERE id = ?",
            (password_hash, now, int(admin_id)),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0
    finally:
        conn.close()


def update_user_password(user_id: int, password_hash: str) -> bool:
    """更新普通用户密码哈希（仅哈希），并清空可能残留的重置 token。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, reset_token = '', "
            "reset_token_expire_at = '', updated_at = ? WHERE id = ?",
            (password_hash, now, int(user_id)),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0
    finally:
        conn.close()


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


def random_questions_by_category(
    slugs: list[str] | tuple[str, ...], limit: int = 10
) -> list[dict]:
    """从某栏目（含子孙栏目）的题库中**随机**抽取指定数量的试题。

    每日答题练习用：随机函数按后端选择（SQLite ``RANDOM()`` / MySQL ``RAND()``），
    返回值含 answer 与 explanation，供服务端判分；HTTP 接口只下发题面，
    不会把答案提前返回给浏览器。
    """
    if not slugs or limit <= 0:
        return []
    placeholders = ", ".join("?" for _ in slugs)
    rand_fn = "RAND()" if db.backend() == "mysql" else "RANDOM()"
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT q.*, ds.date AS set_date, ds.title AS set_title,
               ds.category_path
        FROM questions q
        JOIN daily_sets ds ON ds.id = q.daily_set_id
        WHERE ds.category_slug IN ({placeholders})
        ORDER BY {rand_fn}
        LIMIT ?
        """,
        (*slugs, int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_questions_by_ids(ids: list[int] | tuple[int, ...]) -> list[dict]:
    """按题目 id 批量读取完整题目（含正确答案与解析），供练习判分回查。

    服务端判分必须回查数据库，绝不信任浏览器提交的正确答案。
    """
    clean = [int(i) for i in ids if str(i).isdigit()]
    if not clean:
        return []
    placeholders = ", ".join("?" for _ in clean)
    conn = get_conn()
    rows = conn.execute(
        f"SELECT * FROM questions WHERE id IN ({placeholders})",
        tuple(clean),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_questions_by_category(slugs: list[str] | tuple[str, ...]) -> int:
    """统计某栏目（含子孙栏目）下的试题总数。"""
    if not slugs:
        return 0
    placeholders = ", ".join("?" for _ in slugs)
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM questions q
        JOIN daily_sets ds ON ds.id = q.daily_set_id
        WHERE ds.category_slug IN ({placeholders})
        """,
        tuple(slugs),
    ).fetchone()
    conn.close()
    return int(row["c"])


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
