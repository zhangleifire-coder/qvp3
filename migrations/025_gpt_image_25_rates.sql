-- 025: 生图模型切换 gpt-image-2 → gpt-image-2.5-flare（2026-09-14）
-- FusionAI 文档确认 model 字段可填 gpt-image-2.5-flare / gpt-image-2.5-sunburst，
-- API 路径、参数与 gpt-image-2 一致（/v1/images/generations 与 /edits）。
-- 本迁移只新增 2.5 费率行；gpt-image-2 各行保留，供历史台账与回退场景查询。
INSERT INTO model_rates (model_key, label, input_hit_peak, input_miss_peak, output_peak, offpeak_ratio, per_call_cny) VALUES
    ('gpt-image-2.5-flare',       'gpt-image-2.5-flare 生图（按次计费，通道基准价；参照 gpt-image-2 0.2 元/张待校准）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-flare@fusion',  'gpt-image-2.5-flare · FusionAI（主通道，参照 gpt-image-2 0.2 元/张待校准）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-flare@linkai',  'gpt-image-2.5-flare · LinkAI（参照 fusion 待校准）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-flare@moacode', 'gpt-image-2.5-flare · Moacode（参照 fusion 待校准）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-flare@openox',  'gpt-image-2.5-flare · Openox（参照 fusion 待校准）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-sunburst',       'gpt-image-2.5-sunburst 生图（按次计费，通道基准价；备选型号）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-sunburst@fusion',  'gpt-image-2.5-sunburst · FusionAI（备选型号）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-sunburst@linkai',  'gpt-image-2.5-sunburst · LinkAI（备选型号）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-sunburst@moacode', 'gpt-image-2.5-sunburst · Moacode（备选型号）', 0, 0, 0, 1.0, 0.2),
    ('gpt-image-2.5-sunburst@openox',  'gpt-image-2.5-sunburst · Openox（备选型号）', 0, 0, 0, 1.0, 0.2)
ON CONFLICT (model_key) DO NOTHING;
