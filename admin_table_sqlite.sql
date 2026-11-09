-- ============================================================================
-- 每日答题系统 · 独立管理员表 admin（SQLite 本地回退后端）
--
-- 与 admin_table_mysql.sql 字段一一对应；SQLite 不支持列 COMMENT，
-- 字段语义见 MySQL 版脚本。该 DDL 已内嵌在 models.SCHEMA 中，应用启动即幂等执行。
--   sqlite3 data/quiz.db < admin_table_sqlite.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS admin (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE,
    password       TEXT NOT NULL,              -- PBKDF2:sha256 哈希，不存明文
    role           TEXT NOT NULL DEFAULT 'admin',
    display_name   TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    last_login_at  TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL DEFAULT ''
);
