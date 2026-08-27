-- 012: 文字自查 + 人工核查（2026-08-27）
-- 任务状态链：draft → text_check（query 中文自查+文案/生图描述起草）→
--   awaiting_text（人工最终核查）→ 放行进入生产（ref_collect/审图 → agent 生图）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS text_review JSONB;  -- 自动自查+文案/生图描述草稿
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS text_override JSONB; -- 人工修改后的最终版
