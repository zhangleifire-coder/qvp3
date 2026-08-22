-- 003 提示词库 + 管理员账号
-- prompt_templates：用户自定义提示词，按 (stage, mode, owner) 组织；
-- 每个 (owner, stage, mode) 最多一条 is_active=true，流水线优先于系统默认。

CREATE TABLE IF NOT EXISTS prompt_templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stage text NOT NULL,                 -- draft_gen / page_split / image_gen
    mode text,                           -- general/single/compare；NULL = 环节通用（如 page_split）
    name text NOT NULL,
    content text NOT NULL,
    owner_id uuid NOT NULL REFERENCES users(id),
    is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prompt_templates_owner_idx
    ON prompt_templates (owner_id, stage, mode);

-- 管理员账号（幂等）：admin / root_admin_1234
INSERT INTO users (name, role, password_hash)
SELECT 'admin', 'admin', '00966a77545559807e39f3c1c48cda55c0d4b5bb9cf88dce21c158f48542ea03'
WHERE NOT EXISTS (SELECT 1 FROM users WHERE name = 'admin');
