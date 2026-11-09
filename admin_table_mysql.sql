-- ============================================================================
-- 每日答题系统 · 独立管理员表 admin（MySQL 8.0）
--
-- 目标：管理员账号与 users 表彻底解耦。users 只保存普通答题用户，
--       admin 只保存后台管理员，两套账号使用不同的登录入口与 session。
--
-- 【手工执行】
--   CREATE DATABASE IF NOT EXISTS daily_quiz
--     DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
--   USE daily_quiz;
--   SOURCE admin_table_mysql.sql;
--
-- 【Docker Compose】本文件内容已合入 schema_mysql.sql，MySQL 容器首次启动
--   时会自动执行；应用每次启动还会执行一遍（幂等），无需手工干预。
--
-- 说明：
--   * password 列保存 PBKDF2:sha256 加盐哈希（werkzeug），绝不保存明文；
--     字段名叫 password 是为了与需求「id / username / password」保持一致。
--   * 时间字段统一用 VARCHAR(19)，与现有业务代码保持一致，避免 MySQL
--     返回 datetime 对象导致 Python 侧类型不一致。
--   * status 预留启用/停用，role 预留超级管理员等扩展。
-- ============================================================================

CREATE TABLE IF NOT EXISTS `admin` (
    `id`             INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `username`       VARCHAR(64)  NOT NULL,
    `password`       VARCHAR(255) NOT NULL COMMENT 'PBKDF2:sha256 哈希，不存明文',
    `role`           VARCHAR(16)  NOT NULL DEFAULT 'admin',
    `display_name`   VARCHAR(64)  NOT NULL DEFAULT '',
    `status`         VARCHAR(16)  NOT NULL DEFAULT 'active',
    `last_login_at`  VARCHAR(19)  NOT NULL DEFAULT '',
    `created_at`     VARCHAR(19)  NOT NULL,
    `updated_at`     VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_admin_username` (`username`),
    KEY `idx_admin_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 如需手工创建管理员，密码列必须写入哈希而不是明文，例如由 Python 生成：
--   from werkzeug.security import generate_password_hash
--   generate_password_hash("<你的密码>", method="pbkdf2:sha256")
-- 或让应用启动时按 ADMIN_USERNAME / ADMIN_PASSWORD 自动播种（见 auth.create_default_users）。
