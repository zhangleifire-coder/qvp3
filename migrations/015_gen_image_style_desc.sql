-- 015: 图片风格快照（2026-08-31）
-- 直连路径风格自适应：按 query 题材加权随机选向并锁定到任务。
-- gen_image_style 存风格名（展示/Agent 判定沿用），本迁移补存当时的风
-- 格描述词快照——风格库后续被编辑/删除也不影响该任务重生成保持同风格
-- （6 页统一 + 重生成一致的前提是描述词不变，名字只是索引）。
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS gen_image_style_desc TEXT;
