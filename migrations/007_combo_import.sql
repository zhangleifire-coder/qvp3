-- 007: 组合生成导入（泛化问题池随机组合）
-- 每条组合任务 = 原始 query（长情境）× 随机抽取的一个泛化补充问题 × 风格/垂类条件。
-- query 存抽中的补充问题（列表可读），source_query 存原始长情境供 Agent 组合创作。
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_query TEXT;         -- 原始提问情境（导入时的长 query）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS supplement_question TEXT; -- 本条随机抽中的泛化补充问题（=query）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS gen_style TEXT;           -- 内容风格/体裁（解读·经验分享 等）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS gen_category TEXT;        -- 垂类领域（家居/汽车/数码…）
