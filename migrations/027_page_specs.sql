-- 027: 结构化分页文案快照（v0.1.4 P2，2026-09-22）
-- task.page_specs = page_schema.PageSpec 数组
-- （title/subtitle/points/subject/info_task）——分页阶段产出，
-- compose 混合生图直连消费（subject 做插图主体锚定）。
-- NULL=旧任务/机械切割回退，compose 走 spec_from_plain 兼容层。
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS page_specs JSONB;
