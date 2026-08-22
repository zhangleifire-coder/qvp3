-- 006: 定点驳回标记（审核员指出具体哪页文案/哪张图有问题）
-- 重试时仅重生成被标记项，其余已认可内容保留，避免重复浪费算力。
CREATE TABLE IF NOT EXISTS reject_marks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role        TEXT,                       -- 标记的审核角色（A/B/C）
    item_type   TEXT NOT NULL CHECK (item_type IN ('page', 'image')),
    page_index  INTEGER NOT NULL CHECK (page_index BETWEEN 1 AND 6),
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',   -- open / resolved
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS reject_marks_task_id_idx ON reject_marks (task_id);
