-- 010: 参考图人工确认关卡 + 风格关键词库（2026-08-27）
-- compare/single 任务：搜图≥10张 → OCR/关键词自动初筛 → awaiting_refs 人工确认 → 生图。
-- 风格关键词库：用户"知识训练"落地——风格→关键词集/描述词映射，生成时自动匹配。

-- 参考图筛选状态：candidate=待确认（默认）/ confirmed=人工保留 / rejected=人工剔除
ALTER TABLE assets ADD COLUMN IF NOT EXISTS selection_status TEXT DEFAULT NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS ocr_hit TEXT DEFAULT NULL;  -- OCR 初筛命中的关键词（逗号分隔）

-- 风格关键词库（图片视觉风格的可训练映射）
CREATE TABLE IF NOT EXISTS style_keywords (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    style_name  TEXT NOT NULL,                       -- 风格名（如：科技蓝调）
    keywords    TEXT NOT NULL DEFAULT '',             -- 匹配关键词（逗号分隔，query/检索信息命中即加分）
    description TEXT NOT NULL DEFAULT '',             -- 描述词（注入生图提示词的视觉描述）
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (style_name)
);
CREATE INDEX IF NOT EXISTS style_keywords_enabled_idx ON style_keywords (enabled);
