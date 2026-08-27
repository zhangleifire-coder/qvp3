-- 011: 人审工作流扩展（2026-08-27）
-- Asset：记录每页实际生图提示词（审图/定点修改/AI审核需要），旧图历史与定点修改意见
ALTER TABLE assets ADD COLUMN IF NOT EXISTS prompt_used TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS is_history BOOLEAN DEFAULT FALSE;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS edit_note TEXT;
