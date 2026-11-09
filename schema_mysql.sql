-- ============================================================================
-- 每日答题系统 · MySQL 8.0 建表脚本（8 张表）
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

-- 普通答题用户（users 表只存 user，管理员已迁出到独立的 admin 表）
CREATE TABLE IF NOT EXISTS users (
    `id`            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `username`      VARCHAR(64)  NOT NULL,
    `password_hash` VARCHAR(255) NOT NULL COMMENT 'werkzeug pbkdf2:sha256 哈希，禁止明文',
    `nickname`      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '昵称（2-20 字，空则回退用户名）',
    `gender`        VARCHAR(8)   NOT NULL DEFAULT '保密' COMMENT '男 / 女 / 保密',
    `bio`           VARCHAR(255) NOT NULL DEFAULT '' COMMENT '个人简介，≤200 字',
    `phone`         VARCHAR(20)  NULL COMMENT '手机号，未绑定为 NULL',
    `phone_verified` TINYINT(1)  NOT NULL DEFAULT 0,
    `email`         VARCHAR(128) NULL COMMENT '邮箱（小写），未绑定为 NULL',
    `email_verified` TINYINT(1)  NOT NULL DEFAULT 0,
    `reset_token`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '重置 Token 的 SHA-256',
    `reset_token_expire_at` VARCHAR(19) NOT NULL DEFAULT '',
    `role`          VARCHAR(16)  NOT NULL DEFAULT 'user',
    `created_at`    VARCHAR(19)  NOT NULL,
    `updated_at`    VARCHAR(19)  NOT NULL DEFAULT '' COMMENT '资料最后更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_users_username` (`username`),
    UNIQUE KEY `uk_users_phone` (`phone`),
    UNIQUE KEY `uk_users_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 短信验证码：只存 SHA-256，10 分钟有效，单码最多 5 次尝试
CREATE TABLE IF NOT EXISTS sms_codes (
    `id`         INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`    INT UNSIGNED NOT NULL,
    `phone`      VARCHAR(20)  NOT NULL,
    `purpose`    VARCHAR(24)  NOT NULL DEFAULT 'bind_phone',
    `code_hash`  VARCHAR(64)  NOT NULL,
    `expires_at` VARCHAR(19)  NOT NULL,
    `attempts`   INT          NOT NULL DEFAULT 0,
    `used`       TINYINT(1)   NOT NULL DEFAULT 0,
    `created_at` VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_sms_user` (`user_id`, `purpose`, `created_at`),
    CONSTRAINT `fk_sms_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短信验证码';

-- 邮箱验证码 / 密码重置 Token：两者共用一张表，用 purpose 区分
CREATE TABLE IF NOT EXISTS email_tokens (
    `id`         INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`    INT UNSIGNED NOT NULL,
    `email`      VARCHAR(128) NOT NULL,
    `purpose`    VARCHAR(24)  NOT NULL COMMENT 'bind_email / reset_password',
    `code_hash`  VARCHAR(64)  NOT NULL DEFAULT '',
    `token_hash` VARCHAR(64)  NOT NULL DEFAULT '',
    `expires_at` VARCHAR(19)  NOT NULL,
    `attempts`   INT          NOT NULL DEFAULT 0,
    `used`       TINYINT(1)   NOT NULL DEFAULT 0,
    `created_at` VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_email_user` (`user_id`, `purpose`, `created_at`),
    KEY `idx_email_target` (`email`, `purpose`, `created_at`),
    CONSTRAINT `fk_email_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='邮箱验证码与重置 Token';

-- 独立管理员表：与 users 完全解耦，登录入口 /admin/login，接口前缀 /api/admin/
-- password 存 PBKDF2:sha256 哈希（字段命名对齐需求 id/username/password，值绝非明文）
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
    `id`               INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `daily_set_id`     INT UNSIGNED NULL COMMENT '按日期题库归属；认证题库为 NULL',
    `cert_category_id` INT UNSIGNED NULL COMMENT '认证分类归属（认证题库必填）',
    `set_index`        INT          NOT NULL DEFAULT 0,
    `type`             VARCHAR(16)  NOT NULL,
    `question`         TEXT         NOT NULL,
    `options`          TEXT         NOT NULL,
    `answer`           TEXT         NOT NULL,
    `explanation`      TEXT         NOT NULL,
    `difficulty`       VARCHAR(16)  NOT NULL DEFAULT '中',
    `status`           VARCHAR(16)  NOT NULL DEFAULT 'active',
    `created_at`       VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_questions_set` (`daily_set_id`, `set_index`),
    KEY `idx_q_category` (`cert_category_id`, `status`),
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

-- 认证百科介绍（CMS）：三级菜单详情页首屏优先展示本表内容，管理员可随时编辑。
-- slug 对应 navigation.py 中三级叶子节点，由 models.seed_cert_intros 幂等播种。
CREATE TABLE IF NOT EXISTS cert_intro (
    `id`           INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `slug`         VARCHAR(64)  NOT NULL COMMENT '三级菜单节点 slug，唯一',
    `cert_name`    VARCHAR(128) NOT NULL COMMENT '认证名称（后台可搜索）',
    `vendor`       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '一级厂商',
    `direction`    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '二级方向',
    `level`        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '三级等级 / 考试代码',
    `intro`        TEXT         NOT NULL COMMENT '百科介绍正文（管理员可编辑）',
    `source`       VARCHAR(255) NOT NULL DEFAULT '' COMMENT '资料来源',
    `is_preset`    TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '1=系统预置',
    `updated_by`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '最后修改人',
    `created_at`   VARCHAR(19)  NOT NULL,
    `updated_at`   VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_cert_intro_slug` (`slug`),
    KEY `idx_cert_intro_vendor` (`vendor`),
    KEY `idx_cert_intro_name` (`cert_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='认证百科介绍（CMS 可编辑）';

-- ============================================================================
-- 认证题库网 v2：认证分类 / 五段式介绍 / 答题记录 / 荣誉 / 排行榜
-- ============================================================================

CREATE TABLE IF NOT EXISTS admins (
    `id`            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `username`      VARCHAR(32)  NOT NULL,
    `password`      VARCHAR(255) NOT NULL,
    `role`          VARCHAR(16)  NOT NULL DEFAULT 'admin',
    `display_name`  VARCHAR(64)  NOT NULL DEFAULT '',
    `status`        VARCHAR(16)  NOT NULL DEFAULT 'active',
    `last_login_at` VARCHAR(19)  NOT NULL DEFAULT '',
    `created_at`    VARCHAR(19)  NOT NULL,
    `updated_at`    VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_admins_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='管理员（独立于 users）';

CREATE TABLE IF NOT EXISTS cert_categories (
    `id`              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `vendor_slug`     VARCHAR(32)  NOT NULL,
    `vendor_name`     VARCHAR(64)  NOT NULL,
    `direction_slug`  VARCHAR(48)  NOT NULL,
    `direction_name`  VARCHAR(64)  NOT NULL,
    `level_slug`      VARCHAR(48)  NOT NULL DEFAULT '',
    `level_name`      VARCHAR(96)  NOT NULL DEFAULT '',
    `path_slug`       VARCHAR(160) NOT NULL COMMENT 'aliyun/acp/cloud',
    `cert_name`       VARCHAR(128) NOT NULL,
    `exam_code`       VARCHAR(48)  NOT NULL DEFAULT '',
    `difficulty`      VARCHAR(16)  NOT NULL DEFAULT '',
    `sort_order`      INT          NOT NULL DEFAULT 0,
    `status`          VARCHAR(16)  NOT NULL DEFAULT 'active',
    `seo_title`       VARCHAR(255) NOT NULL DEFAULT '',
    `seo_keywords`    VARCHAR(255) NOT NULL DEFAULT '',
    `seo_description` VARCHAR(500) NOT NULL DEFAULT '',
    `created_at`      VARCHAR(19)  NOT NULL,
    `updated_at`      VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_cat_path` (`path_slug`),
    KEY `idx_cat_vendor` (`vendor_slug`, `sort_order`),
    KEY `idx_cat_direction` (`vendor_slug`, `direction_slug`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='认证分类（三级）';

CREATE TABLE IF NOT EXISTS cert_intros (
    `id`          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `category_id` INT UNSIGNED NOT NULL,
    `positioning` TEXT         NOT NULL,
    `audience`    TEXT         NOT NULL,
    `exam_topics` TEXT         NOT NULL,
    `difficulty`  VARCHAR(16)  NOT NULL DEFAULT '',
    `advice`      TEXT         NOT NULL,
    `summary`     VARCHAR(500) NOT NULL DEFAULT '',
    `source`      VARCHAR(255) NOT NULL DEFAULT '',
    `updated_by`  VARCHAR(64)  NOT NULL DEFAULT '',
    `created_at`  VARCHAR(19)  NOT NULL,
    `updated_at`  VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_intro_category` (`category_id`),
    CONSTRAINT `fk_intro_category`
        FOREIGN KEY (`category_id`) REFERENCES `cert_categories` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='认证介绍（五段式）';

CREATE TABLE IF NOT EXISTS quiz_records (
    `id`           INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`      INT UNSIGNED NOT NULL,
    `category_id`  INT UNSIGNED NOT NULL,
    `cert_path`    VARCHAR(160) NOT NULL DEFAULT '',
    `cert_name`    VARCHAR(128) NOT NULL DEFAULT '',
    `question_ids` TEXT         NOT NULL,
    `answers`      TEXT         NOT NULL,
    `total`        INT          NOT NULL DEFAULT 0,
    `correct`      INT          NOT NULL DEFAULT 0,
    `score`        INT          NOT NULL DEFAULT 0,
    `accuracy`     DECIMAL(5,4) NOT NULL DEFAULT 0,
    `duration`     INT          NOT NULL DEFAULT 0,
    `started_at`   VARCHAR(19)  NOT NULL DEFAULT '',
    `finished_at`  VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_qr_user` (`user_id`, `finished_at`),
    KEY `idx_qr_cat` (`category_id`, `finished_at`),
    KEY `idx_qr_time` (`finished_at`),
    CONSTRAINT `fk_qr_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_qr_category` FOREIGN KEY (`category_id`) REFERENCES `cert_categories` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='每日答题记录';

CREATE TABLE IF NOT EXISTS user_honors (
    `id`          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`     INT UNSIGNED NOT NULL,
    `category_id` INT UNSIGNED NOT NULL,
    `cert_name`   VARCHAR(128) NOT NULL DEFAULT '',
    `score`       INT          NOT NULL DEFAULT 0,
    `correct`     INT          NOT NULL DEFAULT 0,
    `total`       INT          NOT NULL DEFAULT 0,
    `duration`    INT          NOT NULL DEFAULT 0,
    `quiz_date`   VARCHAR(10)  NOT NULL,
    `created_at`  VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_uh_user` (`user_id`, `created_at`),
    KEY `idx_uh_date` (`quiz_date`),
    CONSTRAINT `fk_uh_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_uh_category` FOREIGN KEY (`category_id`) REFERENCES `cert_categories` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='个人荣誉流水';

CREATE TABLE IF NOT EXISTS rankings (
    `id`          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `period_type` VARCHAR(8)   NOT NULL COMMENT 'day/week/month',
    `period_key`  VARCHAR(16)  NOT NULL COMMENT '2026-11-06 / 2026-W45 / 2026-11',
    `user_id`     INT UNSIGNED NOT NULL,
    `points`      INT          NOT NULL DEFAULT 0,
    `quiz_count`  INT          NOT NULL DEFAULT 0,
    `best_score`  INT          NOT NULL DEFAULT 0,
    `updated_at`  VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_rank` (`period_type`, `period_key`, `user_id`),
    KEY `idx_rank_query` (`period_type`, `period_key`, `points`),
    CONSTRAINT `fk_rank_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='排行榜积分汇总';

-- ===========================================================================
-- 自由练习：考试规则 / 题库 / 题库题目 / 练习场次 / 逐题作答 / 学习进度
-- ===========================================================================

CREATE TABLE IF NOT EXISTS exam_rules (
    `id`                 INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `code`               VARCHAR(64)  NOT NULL,
    `name`               VARCHAR(128) NOT NULL,
    `vendor_slug`        VARCHAR(32)  NOT NULL DEFAULT '',
    `cert_path`          VARCHAR(128) NOT NULL DEFAULT '',
    `total_questions`    INT          NOT NULL DEFAULT 0,
    `single_count`       INT          NOT NULL DEFAULT 0,
    `multiple_count`     INT          NOT NULL DEFAULT 0,
    `score_per_question` DECIMAL(6,2) NOT NULL DEFAULT 1.00,
    `total_score`        DECIMAL(6,2) NOT NULL DEFAULT 100.00,
    `pass_score`         DECIMAL(6,2) NOT NULL DEFAULT 80.00,
    `duration_minutes`   INT          NOT NULL DEFAULT 120,
    `description`        VARCHAR(512) NOT NULL DEFAULT '',
    `is_preset`          TINYINT(1)   NOT NULL DEFAULT 0,
    `status`             VARCHAR(16)  NOT NULL DEFAULT 'active',
    `created_at`         VARCHAR(19)  NOT NULL,
    `updated_at`         VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_er_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='考试规则模板';

CREATE TABLE IF NOT EXISTS question_banks (
    `id`                 INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `slug`               VARCHAR(64)  NOT NULL,
    `name`               VARCHAR(128) NOT NULL,
    `vendor_slug`        VARCHAR(32)  NOT NULL DEFAULT '',
    `vendor_name`        VARCHAR(64)  NOT NULL DEFAULT '',
    `cert_path`          VARCHAR(128) NOT NULL DEFAULT '',
    `cert_name`          VARCHAR(128) NOT NULL DEFAULT '',
    `category_id`        INT UNSIGNED NULL,
    `level_tag`          VARCHAR(16)  NOT NULL DEFAULT '基础',
    `difficulty`         VARCHAR(16)  NOT NULL DEFAULT '中等',
    `description`        VARCHAR(512) NOT NULL DEFAULT '',
    `rule_code`          VARCHAR(64)  NOT NULL DEFAULT 'custom',
    `rule_name`          VARCHAR(128) NOT NULL DEFAULT '',
    `total_questions`    INT          NOT NULL DEFAULT 0,
    `single_count`       INT          NOT NULL DEFAULT 0,
    `multiple_count`     INT          NOT NULL DEFAULT 0,
    `score_per_question` DECIMAL(6,2) NOT NULL DEFAULT 1.00,
    `total_score`        DECIMAL(6,2) NOT NULL DEFAULT 100.00,
    `pass_score`         DECIMAL(6,2) NOT NULL DEFAULT 80.00,
    `duration_minutes`   INT          NOT NULL DEFAULT 120,
    `status`             VARCHAR(16)  NOT NULL DEFAULT 'draft',
    `sort_order`         INT          NOT NULL DEFAULT 0,
    `created_by`         VARCHAR(64)  NOT NULL DEFAULT '',
    `created_at`         VARCHAR(19)  NOT NULL,
    `updated_at`         VARCHAR(19)  NOT NULL DEFAULT '',
    `published_at`       VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_qb_slug` (`slug`),
    KEY `idx_qb_status` (`status`, `sort_order`),
    KEY `idx_qb_cert` (`cert_path`, `status`),
    KEY `idx_qb_vendor` (`vendor_slug`, `status`),
    CONSTRAINT `fk_qb_category` FOREIGN KEY (`category_id`) REFERENCES `cert_categories` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='自由练习题库';

CREATE TABLE IF NOT EXISTS bank_questions (
    `id`          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `bank_id`     INT UNSIGNED NOT NULL,
    `type`        VARCHAR(16)  NOT NULL DEFAULT 'single',
    `question`    TEXT         NOT NULL,
    `options`     TEXT         NOT NULL,
    `answer`      TEXT         NOT NULL,
    `explanation` TEXT         NOT NULL,
    `difficulty`  VARCHAR(16)  NOT NULL DEFAULT '中等',
    `sort_order`  INT          NOT NULL DEFAULT 0,
    `status`      VARCHAR(16)  NOT NULL DEFAULT 'active',
    `created_at`  VARCHAR(19)  NOT NULL,
    `updated_at`  VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    KEY `idx_bq_bank` (`bank_id`, `sort_order`),
    KEY `idx_bq_type` (`bank_id`, `type`),
    CONSTRAINT `fk_bq_bank` FOREIGN KEY (`bank_id`) REFERENCES `question_banks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='题库题目';

CREATE TABLE IF NOT EXISTS bank_practice_sessions (
    `id`            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `token`         VARCHAR(64)  NOT NULL,
    `bank_id`       INT UNSIGNED NOT NULL,
    `user_id`       INT UNSIGNED NOT NULL,
    `question_ids`  TEXT         NOT NULL,
    `answers`       TEXT         NOT NULL,
    `total`         INT          NOT NULL DEFAULT 0,
    `answered`      INT          NOT NULL DEFAULT 0,
    `correct`       INT          NOT NULL DEFAULT 0,
    `score`         DECIMAL(6,2) NOT NULL DEFAULT 0.00,
    `accuracy`      DECIMAL(6,2) NOT NULL DEFAULT 0.00,
    `status`        VARCHAR(16)  NOT NULL DEFAULT 'ongoing',
    `current_index` INT          NOT NULL DEFAULT 0,
    `started_at`    VARCHAR(19)  NOT NULL,
    `deadline_at`   VARCHAR(19)  NOT NULL DEFAULT '',
    `finished_at`   VARCHAR(19)  NOT NULL DEFAULT '',
    `duration`      INT          NOT NULL DEFAULT 0,
    `submit_reason` VARCHAR(16)  NOT NULL DEFAULT '',
    `created_at`    VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_bps_token` (`token`),
    KEY `idx_bps_user_bank` (`user_id`, `bank_id`, `status`),
    KEY `idx_bps_deadline` (`status`, `deadline_at`),
    KEY `idx_bps_bank` (`bank_id`, `status`),
    CONSTRAINT `fk_bps_bank` FOREIGN KEY (`bank_id`) REFERENCES `question_banks` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_bps_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='练习场次';

CREATE TABLE IF NOT EXISTS bank_practice_answers (
    `id`          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `session_id`  INT UNSIGNED NOT NULL,
    `bank_id`     INT UNSIGNED NOT NULL,
    `user_id`     INT UNSIGNED NOT NULL,
    `question_id` INT UNSIGNED NOT NULL,
    `user_answer` VARCHAR(32)  NOT NULL DEFAULT '[]',
    `is_correct`  TINYINT(1)   NOT NULL DEFAULT 0,
    `answered_at` VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_bpa_session_q` (`session_id`, `question_id`),
    KEY `idx_bpa_user_bank` (`user_id`, `bank_id`, `question_id`),
    KEY `idx_bpa_session` (`session_id`),
    CONSTRAINT `fk_bpa_session` FOREIGN KEY (`session_id`) REFERENCES `bank_practice_sessions` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_bpa_bank` FOREIGN KEY (`bank_id`) REFERENCES `question_banks` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_bpa_question` FOREIGN KEY (`question_id`) REFERENCES `bank_questions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='逐题作答流水';

CREATE TABLE IF NOT EXISTS bank_practice_progress (
    `id`                INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`           INT UNSIGNED NOT NULL,
    `bank_id`           INT UNSIGNED NOT NULL,
    `answered_count`    INT          NOT NULL DEFAULT 0,
    `correct_count`     INT          NOT NULL DEFAULT 0,
    `wrong_count`       INT          NOT NULL DEFAULT 0,
    `best_score`        DECIMAL(6,2) NOT NULL DEFAULT 0.00,
    `last_score`        DECIMAL(6,2) NOT NULL DEFAULT 0.00,
    `last_session_id`   INT UNSIGNED NULL,
    `last_practiced_at` VARCHAR(19)  NOT NULL DEFAULT '',
    `updated_at`        VARCHAR(19)  NOT NULL DEFAULT '',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_bpp_user_bank` (`user_id`, `bank_id`),
    KEY `idx_bpp_bank` (`bank_id`, `answered_count`),
    CONSTRAINT `fk_bpp_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_bpp_bank` FOREIGN KEY (`bank_id`) REFERENCES `question_banks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='题库学习进度';

-- 模拟考积分流水：一套题库 × 一个用户 × 一个自然日只计一次（唯一键），
-- 重复练习不叠加。points 由应用层按成绩折算，session_id 指向首次计分的场次。
CREATE TABLE IF NOT EXISTS bank_exam_awards (
    `id`          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`     INT UNSIGNED NOT NULL,
    `bank_id`     INT UNSIGNED NOT NULL,
    `session_id`  INT UNSIGNED NOT NULL,
    `earned_date` VARCHAR(10)  NOT NULL,
    `score`       DECIMAL(6,2) NOT NULL DEFAULT 0.00,
    `points`      INT          NOT NULL DEFAULT 0,
    `created_at`  VARCHAR(19)  NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_bea_user_bank_date` (`user_id`, `bank_id`, `earned_date`),
    KEY `idx_bea_user_date` (`user_id`, `earned_date`),
    KEY `idx_bea_session` (`session_id`),
    CONSTRAINT `fk_bea_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_bea_bank` FOREIGN KEY (`bank_id`) REFERENCES `question_banks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='模拟考积分流水';

