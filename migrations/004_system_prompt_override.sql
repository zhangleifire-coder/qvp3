-- 004 系统默认提示词可落库覆盖：owner_id 允许 NULL（NULL = 系统级覆盖，仅 admin 可写）
ALTER TABLE prompt_templates ALTER COLUMN owner_id DROP NOT NULL;
