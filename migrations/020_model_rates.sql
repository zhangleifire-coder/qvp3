-- 020: 动态费率表（2026-09-09）
-- LLM/生图费率从 cost_tracker.py 硬编码提升为 DB 权威：改库或管理页保存即生效
-- （进程内缓存 60s，零发版）。cost_tracker 保留同值代码兜底，DB 不可用时降级。
-- 单价单位：元 / 1M tokens；offpeak_ratio=空闲时段折扣（DeepSeek 官方半价=0.5，
-- 非 DeepSeek 无峰谷价=1.0）；per_call_cny=按次计费通道单价（元/次）。
CREATE TABLE IF NOT EXISTS model_rates (
    model_key       TEXT PRIMARY KEY,          -- deepseek-v4-pro / k3 / qwen-vl-ocr / gpt-image-2 ...
    label           TEXT NOT NULL DEFAULT '',
    input_hit_peak  DOUBLE PRECISION NOT NULL DEFAULT 0,  -- 输入·缓存命中·高峰价
    input_miss_peak DOUBLE PRECISION NOT NULL DEFAULT 0,  -- 输入·缓存未命中·高峰价
    output_peak     DOUBLE PRECISION NOT NULL DEFAULT 0,  -- 输出·高峰价
    offpeak_ratio   DOUBLE PRECISION NOT NULL DEFAULT 1.0, -- 空闲时段折扣（0.5=半价）
    per_call_cny    DOUBLE PRECISION NOT NULL DEFAULT 0,  -- 按次计费通道单价（元/次）
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 种子数据（已存在不覆盖，管理员改动保留）：
-- deepseek-v4-pro 为 DeepSeek 官方 2026-09-09 文档价
-- （api-docs.deepseek.com/zh-cn/quick_start/pricing）；
-- k3/gpt-4o/qwen-vl-ocr 沿用 cost_tracker 旧硬编码值（2026-08 客户确认/官网旧价，
-- 待按各厂商最新价校准）。
INSERT INTO model_rates (model_key, label, input_hit_peak, input_miss_peak, output_peak, offpeak_ratio, per_call_cny) VALUES
    ('deepseek-v4-pro', 'DeepSeek V4-Pro（官方价 2026-09-09）', 0.30, 9.0, 27.0, 0.5, 0),
    ('k3',              'Kimi K3 旗舰（2026-08-14 旧价待校准）', 20.0, 20.0, 100.0, 1.0, 0),
    ('qwen-vl-ocr',     '百炼 qwen-vl-ocr（2026-08-20 官网价）', 0.3, 0.3, 0.5, 1.0, 0),
    ('gpt-4o',          'GPT-4o（按汇率 7.2 估算）', 18.0, 18.0, 72.0, 1.0, 0),
    ('gpt-image-2',     'gpt-image-2 生图（按次计费，通道基准价）', 0, 0, 0, 1.0, 0.2),
    ('default',         '兜底费率（未识别模型）', 3.0, 3.0, 6.0, 1.0, 0)
ON CONFLICT (model_key) DO NOTHING;
