-- 028: 页型模板库（2026-09-24 P2）
-- spec=jsonb（封闭词汇表，src/services/compose_templates.validate_spec 校验）
-- source=seed_11（仓库 JSON SSOT，sync 幂等覆盖）/ vl_extracted（参考图提取，个人库）
--      / manual（手工）。enabled 默认 false（提取/手工模板需人工预览确认后启用）。
CREATE TABLE IF NOT EXISTS compose_templates (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  template_id TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  spec        JSONB NOT NULL,
  page_role   TEXT NOT NULL DEFAULT 'content',
  tags        TEXT[] NOT NULL DEFAULT '{通用}',
  source      TEXT NOT NULL DEFAULT 'manual',
  enabled     BOOLEAN NOT NULL DEFAULT false,
  version     INT NOT NULL DEFAULT 1,
  notes       TEXT NOT NULL DEFAULT '',
  preview_asset_id UUID,
  created_by  UUID,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_compose_templates_role_enabled
  ON compose_templates (page_role, enabled);
