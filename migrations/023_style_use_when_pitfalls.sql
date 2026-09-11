-- 023: 风格条目结构升级（2026-09-11，WS1；原规划编号 021，因 021/022 已被占用顺延）
-- use_when=适用题材条件（喂选型打分，权重减半），pitfalls=风格专属避坑
-- （喂提示词负面约束，措辞纪律：≤3 条、单条 ≤30 字、正向优先）
-- source=条目来源（manual/import_20260909/seed_019/oss_distilled），便于审计与回滚
-- 回滚设计：三列均有默认空值——置空即退化为升级前行为，无需回滚迁移。
ALTER TABLE style_keywords ADD COLUMN IF NOT EXISTS use_when  TEXT NOT NULL DEFAULT '';
ALTER TABLE style_keywords ADD COLUMN IF NOT EXISTS pitfalls  TEXT NOT NULL DEFAULT '';
ALTER TABLE style_keywords ADD COLUMN IF NOT EXISTS source    TEXT NOT NULL DEFAULT 'manual';
