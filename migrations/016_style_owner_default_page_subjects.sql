-- 016: 两级风格库+偏好闭环+分页画面主体（2026-08-31，自 qvp-dev 8002 移植）
-- 一、风格库用户隔离（参照 qvp-dev 迁移 012）：style_keywords 加 owner_id，
--     NULL=admin 公共库、非空=个人库；style_name 全局 UNIQUE 改为「同一 owner
--     内唯一」（NULL owner 之间也唯一，两个部分唯一索引）。存量条目 owner_id
--     为 NULL，自然归入公共库，无需回填。
-- 二、users.default_style：个人钉选的默认生图风格名，style_select 最优先直用。
-- 三、tasks.page_subjects：asset_gen 生图前 LLM 从 6 页文案提取的每页画面主体
--     （JSON 数组 6 字符串），注入提示词替换通用主体锚定条款；NULL=未提取。

ALTER TABLE style_keywords ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES users(id);
ALTER TABLE style_keywords DROP CONSTRAINT IF EXISTS style_keywords_style_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS style_keywords_owner_name_uidx
    ON style_keywords (owner_id, style_name) WHERE owner_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS style_keywords_public_name_uidx
    ON style_keywords (style_name) WHERE owner_id IS NULL;

ALTER TABLE users ADD COLUMN IF NOT EXISTS default_style TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS page_subjects JSONB;
