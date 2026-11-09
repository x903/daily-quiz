-- ============================================================================
-- 每日答题系统 · cert_intro（认证介绍）表 · MySQL 8.0 建表语句
-- ============================================================================
-- 用途：存储三级菜单详情页首屏展示的「认证百科介绍」。介绍文本由管理员在
--       后台「认证介绍管理」中随时修改，前端读取本表而非硬编码配置。
-- 关联：slug 与 navigation.py 中三级叶子节点的 slug 一一对应，不做物理外键
--       （菜单是配置而非数据库实体），由 models.seed_cert_intros 保证同步。
-- 说明：本文件是 schema_mysql.sql 中同名表的独立副本，可单独执行；
--       已合入权威 DDL，Docker 首次启动会自动建表。
--
-- 【手工执行】
--   CREATE DATABASE IF NOT EXISTS daily_quiz
--     DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
--   USE daily_quiz;
--   SOURCE cert_intro_table_mysql.sql;
-- ============================================================================

CREATE TABLE IF NOT EXISTS cert_intro (
    `id`           INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `slug`         VARCHAR(64)  NOT NULL COMMENT '三级菜单节点 slug，唯一',
    `cert_name`    VARCHAR(128) NOT NULL COMMENT '认证名称（后台可搜索）',
    `vendor`       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '一级厂商：华为认证 / AWS 认证 / 思科认证',
    `direction`    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '二级方向：数通 / 云计算 / 架构设计 …',
    `level`        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '三级等级 / 考试代码',
    `intro`        TEXT         NOT NULL COMMENT '百科介绍正文（管理员可编辑）',
    `source`       VARCHAR(255) NOT NULL DEFAULT '' COMMENT '资料来源（厂商官方 / 权威百科）',
    `is_preset`    TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '1=系统预置，0=管理员自定义',
    `updated_by`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '最后修改人（admin 表用户名）',
    `created_at`   VARCHAR(19)  NOT NULL COMMENT '创建时间',
    `updated_at`   VARCHAR(19)  NOT NULL DEFAULT '' COMMENT '最后修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_cert_intro_slug` (`slug`),
    KEY `idx_cert_intro_vendor` (`vendor`),
    KEY `idx_cert_intro_name` (`cert_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='认证百科介绍（CMS 可编辑）';
