-- 014: 任务回收站（2026-08-28）
-- 删除任务先入回收站（全表快照 JSONB，可恢复），默认 72h 后彻底删除；
-- 仅 admin 可见（前端角色控制）。与导出分包回收站同语义。
CREATE TABLE IF NOT EXISTS task_recycle (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID NOT NULL,               -- 原任务 id（恢复时保留原 UUID）
    mode        TEXT,
    query       TEXT NOT NULL,
    payload     JSONB NOT NULL,              -- {表名: [行...]} 全量快照（task + 18 子表）
    deleted_by  TEXT,
    deleted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL DEFAULT now() + interval '72 hours'
);
CREATE INDEX IF NOT EXISTS task_recycle_expires_idx ON task_recycle (expires_at);
