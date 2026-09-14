-- 024: 首过率治理（2026-09-14 P2）——assets 记录首轮 OCR 相似度
-- 供 scripts/garble_stats.py 按风格统计首轮 sim 分布 / 重生率 / 申诉率，
-- 定位系统性触发重生的风格与版式做提示词加固。纯观测列，无行为变更。
ALTER TABLE assets ADD COLUMN IF NOT EXISTS first_sim double precision;
