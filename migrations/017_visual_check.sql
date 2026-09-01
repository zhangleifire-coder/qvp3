-- 017: 视觉主体审核（2026-09-01，图文一致性铁律）
-- assets.subject_mismatch：VL 审核判定该页配图主体与文案不符（重画后仍不符）
-- 时置 true——不阻塞流水线，交人工审核关卡显式把关（前端黄色标签）。
ALTER TABLE assets ADD COLUMN IF NOT EXISTS subject_mismatch BOOLEAN NOT NULL DEFAULT false;
