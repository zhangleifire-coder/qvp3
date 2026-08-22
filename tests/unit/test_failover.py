import pytest
from unittest.mock import patch
from src.gateway.failover import call_with_failover


@pytest.mark.asyncio
async def test_failover_marks_degraded_on_primary_failure():
    primary_exc = Exception("rate limit")
    fallback_result = {"text": "ok", "model_version": "moonshot/moonshot-v1-auto", "cost_cny": 0.001}
    with patch("src.gateway.failover.call_provider") as mock_call, \
         patch("src.gateway.failover.asyncio.sleep", return_value=None):
        # 主 3 次失败（attempt 0,1,2），第 3 次失败后切备成功
        mock_call.side_effect = [primary_exc, primary_exc, primary_exc, fallback_result]
        result = await call_with_failover("p", "deepseek/deepseek-chat", "moonshot/moonshot-v1-auto")
    assert result["degraded"] is True
    assert "original_error" in result


@pytest.mark.asyncio
async def test_failover_returns_primary_directly():
    ok_result = {"text": "ok", "model_version": "deepseek/deepseek-chat", "cost_cny": 0.001}
    with patch("src.gateway.failover.call_provider") as mock_call:
        mock_call.return_value = ok_result
        result = await call_with_failover("p")
    assert result["degraded"] is False
