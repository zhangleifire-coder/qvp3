-- 005 用户操作审计日志：每个用户的操作动作与内容落库，长期保留
-- user_id 可空（登录失败等无账号场景）；actor_name 冗余存用户名，账号删除后日志仍可读
CREATE TABLE IF NOT EXISTS activity_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid,
    actor_name text NOT NULL,
    action text NOT NULL,          -- login/import_tasks/review_approve/prompt_create/...
    detail text,                   -- 操作内容描述（导入了什么、审了哪条、改了什么）
    task_id uuid,                  -- 关联任务（可空）
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS activity_logs_actor_idx ON activity_logs (actor_name, created_at DESC);
CREATE INDEX IF NOT EXISTS activity_logs_created_idx ON activity_logs (created_at DESC);
