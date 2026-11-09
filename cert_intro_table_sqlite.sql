-- ============================================================================
-- 每日答题系统 · cert_intro（认证介绍）表 · SQLite 建表语句
-- ============================================================================
-- 与 cert_intro_table_mysql.sql 等价，供本地 SQLite 开发环境 / 回归测试使用。
-- 应用启动时 models.init_db() 会通过内嵌 SCHEMA 自动执行，无需手工运行。
-- ============================================================================

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
