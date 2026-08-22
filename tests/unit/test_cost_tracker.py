from src.gateway.cost_tracker import estimate_cost


def test_estimate_cost_deepseek():
    cost = estimate_cost("deepseek/deepseek-chat", 1_000_000, 500_000)
    assert abs(cost - (3.0 + 0.5 * 6.0)) < 0.001


def test_estimate_cost_kimi():
    cost = estimate_cost("moonshot/moonshot-v1-auto", 1_000_000, 1_000_000)
    assert abs(cost - (20.0 + 100.0)) < 0.001


def test_estimate_cost_unknown_uses_default():
    cost = estimate_cost("unknown-model", 1_000_000, 1_000_000)
    assert cost > 0
