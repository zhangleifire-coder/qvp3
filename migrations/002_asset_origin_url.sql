-- 配图本地持久化：image_url 存本地 /static/generated/ 路径，原始上游 URL 保留在 origin_url
ALTER TABLE assets ADD COLUMN IF NOT EXISTS origin_url TEXT;
