-- 018: 系统参数 web 化（2026-09-01）
-- 行为开关（如 通用模式实景图/校稿润色/素材库复用/视觉主体审核）从 .env
-- 提升为可在 web「系统参数」页即时切换：DB 为权威持久层，启动时加载覆盖
-- settings 内存值（.env 仍为未配置时的初始默认），改动即时生效无需重启。
CREATE TABLE IF NOT EXISTS system_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
