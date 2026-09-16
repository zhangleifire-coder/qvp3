-- 026: 新增 Kimi K2.6 费率行（2026-09-16）
-- 单价单位：元 / 1M tokens；无峰谷价 offpeak_ratio=1.0。
-- 参考价按公开资料换算（$0.95/$4.00 per 1M tokens，汇率 7.2 ≈ ¥6.84/¥28.8），
-- 取整为 input_miss=7.0 / output=29.0，缓存命中按 1.0；待账单实证后校准。
INSERT INTO model_rates (model_key, label, input_hit_peak, input_miss_peak, output_peak, offpeak_ratio, per_call_cny) VALUES
    ('kimi-k2.6', 'Kimi K2.6（2026-09-16 参考价 ¥7/29 待账单校准）', 1.0, 7.0, 29.0, 1.0, 0)
ON CONFLICT (model_key) DO NOTHING;
