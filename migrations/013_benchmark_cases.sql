-- 013: 标杆案例库（2026-08-28）
-- 81 条成功交付案例（newrank 预览页抓取）：B 共性分析 + C AI 对标审核的数据源。
CREATE TABLE IF NOT EXISTS benchmark_cases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mode        TEXT NOT NULL,              -- general / single / compare
    source_url  TEXT NOT NULL,
    title       TEXT,                       -- 笔记标题
    body_text   TEXT,                       -- 全文（右侧文案区 + 标题）
    page_count  INTEGER,                    -- 轮播页数
    image_urls  JSONB,                      -- 轮播各页图 URL
    shot_path   TEXT,                       -- 首页截图本地路径（对标审核用，防盗链）
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS benchmark_mode_idx ON benchmark_cases (mode);
