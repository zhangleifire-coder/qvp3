-- 008: 风格自适应——图片整体视觉风格
-- Agent 生产时按 query + 检索信息自适应判定图片整体视觉风格（多适配随机选），
-- 落库展示；gen_style（内容风格）普通导入任务也由 Agent 自动判定回填。
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS gen_image_style TEXT;
