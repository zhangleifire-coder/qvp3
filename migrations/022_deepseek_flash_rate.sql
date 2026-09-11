-- 022: DeepSeek 主模型切换 v4-pro → v4-flash（2026-09-10 用户决策）
-- 官方价（https://api-docs.deepseek.com/zh-cn/quick_start/pricing 2026-09-09）：
-- v4-flash 输入命中 0.10/空闲 0.05、未命中 3.0/1.5、输出 9.0/4.5（元/1M tokens，
-- 高峰=北京时间工作日 9-12/14-18 点，空闲半价）——约为 v4-pro 的 1/3。
-- 模型切换本体在代码默认值（src/config.py deepseek_model / dsh_serve primary_model
-- / nanobot 双 preset），本迁移只补费率表行；v4-pro 行保留（历史台账归属用）。
INSERT INTO model_rates (model_key, label, input_hit_peak, input_miss_peak, output_peak, offpeak_ratio, per_call_cny) VALUES
    ('deepseek-v4-flash', 'DeepSeek V4-Flash（官方价 2026-09-09，现役主模型）', 0.10, 3.0, 9.0, 0.5, 0)
ON CONFLICT (model_key) DO NOTHING;
