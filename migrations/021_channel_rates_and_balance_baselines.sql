-- 021: 生图分通道费率 + 余额基准点（2026-09-09）
-- 1) gpt-image-2 按通道拆行：fusion=0.2 元/张（用户账单流水实证，gpt-image-2 每次
--    调用扣 ¥0.2000 账号折扣后）；linkai/moacode/openox 暂参照 fusion 待校准。
--    cost_tracker.per_call_cost("gpt-image-2@<channel>") 精确匹配，无该通道行时
--    回退 gpt-image-2 基准行 → settings.image_cost_per_image_cny 全局兜底。
-- 2) 修正存量：020 旧种子把基准行写成 0.4（2026-08-31 旧调价），仅在值仍为 0.4
--    时改回 0.2（管理员已在页面改过的保留）。
-- 3) balance_baselines：FusionAI/Kimi 无公开余额 API（2026-09-09 实测 8 路径全
--    404），管理员从控制台抄录余额基准点，此后按台账类别消耗扣减估算当前余额。
INSERT INTO model_rates (model_key, label, input_hit_peak, input_miss_peak, output_peak, offpeak_ratio, per_call_cny) VALUES
    ('gpt-image-2@fusion',  'gpt-image-2 · FusionAI（账单实证 0.2 元/张 2026-09-09）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2@linkai',  'gpt-image-2 · LinkAI（参照 fusion 待校准）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2@moacode', 'gpt-image-2 · Moacode（参照 fusion 待校准）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2@openox',  'gpt-image-2 · Openox（参照 fusion 待校准）', 0, 0, 0, 1.0, 0.2)
ON CONFLICT (model_key) DO NOTHING;

UPDATE model_rates SET per_call_cny = 0.2,
       label = 'gpt-image-2 生图（按次计费，通道基准价）'
WHERE model_key = 'gpt-image-2' AND per_call_cny = 0.4;

CREATE TABLE IF NOT EXISTS balance_baselines (
    provider    TEXT PRIMARY KEY,          -- fusion / kimi（无公开余额 API 的厂商）
    balance_cny DOUBLE PRECISION NOT NULL, -- 管理员从厂商控制台抄录的余额（元）
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 抄录时刻（台账扣减起点）
    updated_by  TEXT
);
