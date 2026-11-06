-- ============================================================================
-- 每日答题系统 · MySQL 8.0 建表脚本（6 张表）
-- ============================================================================
-- 特性：
--   * 全部语句幂等（CREATE TABLE IF NOT EXISTS），既可由 MySQL 容器首次启动时
--     执行（docker-entrypoint-initdb.d），也会在应用 init_db() 时再执行一次。
--   * InnoDB + utf8mb4，支持中文题面、解析与 emoji；全部外键 ON DELETE CASCADE。
--   * 索引内联在 CREATE TABLE 中（MySQL 不支持 CREATE INDEX IF NOT EXISTS），
--     因此重复执行不会因索引已存在而报错。
--
-- 【手工执行】先建库再执行本脚本：
--   CREATE DATABASE IF NOT EXISTS daily_quiz
--     DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
--   USE daily_quiz;
--   SOURCE schema_mysql.sql;
--
-- 【Docker Compose】由 MYSQL_DATABASE 自动建库，本文件挂载到
--   /docker-entrypoint-initdb.d/01-schema.sql，MySQL 会默认选中该库执行。
--
-- 字段语义见 README「数据库设计」章节。注意 `date` / `set_date` / `earned_date`
-- / `created_at` / `finished_at` 统一使用字符串（VARCHAR），与原有业务逻辑一致，
-- 不用 DATE/DATETIME，避免 Python 侧收到 datetime.date 对象导致类型不一致。
-- ============================================================================

-- 用户与角色（user / admin）
CREATE TABLE IF NOT EXISTS users (
    `id`            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `username`      VARCHAR(64)  NOT NULL,
    `password_hash` VARCHAR(255) NOT NULL,
    `role`          VARCHAR(16)  NOT NULL DEFAULT 'user',
    `created_at`    VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_users_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 每日题库（同一日期唯一）；category_slug 用于栏目聚合，category_path 是路径文案快照
CREATE TABLE IF NOT EXISTS daily_sets (
    `id`             INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `date`           VARCHAR(10)  NOT NULL,
    `title`          VARCHAR(255) NOT NULL,
    `total_sets`     INT          NOT NULL DEFAULT 0,
    `status`         VARCHAR(16)  NOT NULL DEFAULT 'pending',
    `created_at`     VARCHAR(19)  NOT NULL,
    `category_slug`  VARCHAR(64)  NOT NULL DEFAULT '',
    `category_path`  VARCHAR(512) NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_daily_sets_date` (`date`),
    KEY `idx_daily_sets_category` (`category_slug`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 题目：options / answer 为 JSON 文本，explanation 为原始 Markdown 解析文本
CREATE TABLE IF NOT EXISTS questions (
    `id`           INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `daily_set_id` INT UNSIGNED NOT NULL,
    `set_index`    INT          NOT NULL,
    `type`         VARCHAR(16)  NOT NULL,
    `question`     TEXT         NOT NULL,
    `options`      TEXT         NOT NULL,
    `answer`       TEXT         NOT NULL,
    `explanation`  TEXT         NOT NULL,
    `created_at`   VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_questions_set` (`daily_set_id`, `set_index`),
    CONSTRAINT `fk_questions_set`
        FOREIGN KEY (`daily_set_id`) REFERENCES `daily_sets` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 成绩：每用户每题库一条（重答覆盖）；与用户、题库均为级联外键
CREATE TABLE IF NOT EXISTS daily_records (
    `id`           INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `daily_set_id` INT UNSIGNED NOT NULL,
    `user_id`      INT UNSIGNED NOT NULL DEFAULT 0,
    `total`        INT          NOT NULL,
    `correct`      INT          NOT NULL,
    `wrong`        INT          NOT NULL,
    `accuracy`     DOUBLE       NOT NULL,
    `duration`     INT          NOT NULL,
    `finished_at`  VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_records_set_user` (`daily_set_id`, `user_id`),
    KEY `idx_records_user` (`user_id`),
    CONSTRAINT `fk_records_set`
        FOREIGN KEY (`daily_set_id`) REFERENCES `daily_sets` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_records_user`
        FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 错题：以 (question_id, user_id) 表示某用户当前最新一次答错状态
CREATE TABLE IF NOT EXISTS wrong_questions (
    `id`             INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `daily_set_id`   INT UNSIGNED NOT NULL,
    `question_id`    INT UNSIGNED NOT NULL,
    `user_id`        INT UNSIGNED NOT NULL DEFAULT 0,
    `user_answer`    TEXT         NOT NULL,
    `correct_answer` TEXT         NOT NULL,
    `created_at`     VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_wrong_question_user` (`question_id`, `user_id`),
    KEY `idx_wrong_set` (`daily_set_id`),
    KEY `idx_wrong_user` (`user_id`),
    CONSTRAINT `fk_wrong_set`
        FOREIGN KEY (`daily_set_id`) REFERENCES `daily_sets` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_wrong_question`
        FOREIGN KEY (`question_id`) REFERENCES `questions` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_wrong_user`
        FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 积分流水：每次正式答题写一条，保留完整历史；排行榜按 earned_date 聚合
CREATE TABLE IF NOT EXISTS points_ledger (
    `id`           INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`      INT UNSIGNED NOT NULL,
    `daily_set_id` INT UNSIGNED NOT NULL,
    `record_id`    INT UNSIGNED NULL,
    `earned_date`  VARCHAR(10)  NOT NULL,
    `set_date`     VARCHAR(10)  NOT NULL,
    `points`       INT          NOT NULL DEFAULT 0,
    `correct`      INT          NOT NULL DEFAULT 0,
    `total`        INT          NOT NULL DEFAULT 0,
    `source`       VARCHAR(16)  NOT NULL DEFAULT 'quiz',
    `created_at`   VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_points_user_date` (`user_id`, `earned_date`),
    KEY `idx_points_date` (`earned_date`),
    KEY `idx_points_record` (`record_id`),
    CONSTRAINT `fk_points_user`
        FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_points_set`
        FOREIGN KEY (`daily_set_id`) REFERENCES `daily_sets` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
