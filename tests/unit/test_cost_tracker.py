from datetime import datetime, timezone

from src.gateway.cost_tracker import estimate_cost

# 固定高峰时刻：2026-09-07（周一）北京时间 10:00 = UTC 02:00
PEAK = datetime(2026, 9, 7, 2, 0, tzinfo=timezone.utc)


def test_estimate_cost_deepseek():
    # 官方 2026-09-09 价：未命中输入 9.0 + 输出 27.0（高峰），缓存未拆分按全 miss 计
    cost = estimate_cost("deepseek/deepseek-chat", 1_000_000, 500_000, at_time=PEAK)
    assert abs(cost - (9.0 + 0.5 * 27.0)) < 0.001


def test_estimate_cost_kimi():
    # k3 无峰谷价（offpeak_ratio=1.0），任意时刻同价
    cost = estimate_cost("moonshot/moonshot-v1-auto", 1_000_000, 1_000_000)
    assert abs(cost - (20.0 + 100.0)) < 0.001


def test_estimate_cost_unknown_uses_default():
    cost = estimate_cost("unknown-model", 1_000_000, 1_000_000)
    assert cost > 0


def test_estimate_cost_legacy_three_arg_signature():
    # 旧三参调用兼容（不炸、全 miss、按调用时刻峰谷）
    cost = estimate_cost("dsh:deepseek-v4-pro", 1000, 500)
    assert cost > 0
