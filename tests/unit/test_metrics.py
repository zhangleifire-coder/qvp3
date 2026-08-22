import pytest
from src.dashboard.metrics import all_metrics


@pytest.mark.asyncio
async def test_all_metrics_returns_dict():
    m = await all_metrics()
    assert "throughput_per_hour" in m
    assert "queue_depth" in m
    assert "error_top_5" in m
