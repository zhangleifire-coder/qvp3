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


# ── 三级链路（2026-09-10：flash 主 → 开放平台 kimi-k3 备1 → Kimi Code k3 备2）──

@pytest.mark.asyncio
async def test_third_level_used_when_two_levels_fail():
    """主 + 备1 都失败 → 备2（Kimi Code）成功，degraded=True，错误聚合含三级中的前两级。"""
    err = Exception("boom")
    third_ok = {"text": "ok", "model_version": "anthropic/k3", "cost_cny": 0.001}
    with patch("src.gateway.failover.call_provider") as mock_call, \
         patch("src.gateway.failover.asyncio.sleep", return_value=None), \
         patch("src.gateway.failover.settings") as ms:
        ms.deepseek_api_key = "dk"
        ms.kimi_api_key = "k1"
        ms.kimi_code_api_key = "k2"
        mock_call.side_effect = [err, err, err, err, third_ok]  # 主×3 + 备1×1 + 备2
        result = await call_with_failover("p")
    assert result["degraded"] is True
    assert "primary(" in result["original_error"]
    assert "fallback(" in result["original_error"]
    # 第三级的 key/base 路由正确
    last = mock_call.call_args_list[-1]
    assert last.kwargs["api_key"] == "k2"
    assert last.kwargs["api_base"] == "https://api.kimi.com/coding"
    # 备1 的 key/base 路由
    mid = mock_call.call_args_list[-2]
    assert mid.kwargs["api_key"] == "k1"
    assert mid.kwargs["api_base"] == "https://api.moonshot.cn/v1"


@pytest.mark.asyncio
async def test_third_level_skipped_without_key():
    """kimi_code_api_key 为空 → 跳过备2，直接报两级聚合错误。"""
    err = Exception("boom")
    with patch("src.gateway.failover.call_provider") as mock_call, \
         patch("src.gateway.failover.asyncio.sleep", return_value=None), \
         patch("src.gateway.failover.settings") as ms:
        ms.deepseek_api_key = "dk"
        ms.kimi_api_key = "k1"
        ms.kimi_code_api_key = ""
        mock_call.side_effect = [err, err, err, err]  # 主×3 + 备1×1
        with pytest.raises(RuntimeError, match="All providers failed"):
            await call_with_failover("p")
    assert mock_call.call_count == 4  # 备2 未调用


@pytest.mark.asyncio
async def test_all_three_levels_fail_aggregate_errors():
    """三级全挂：错误信息带三级各自的错。"""
    with patch("src.gateway.failover.call_provider") as mock_call, \
         patch("src.gateway.failover.asyncio.sleep", return_value=None), \
         patch("src.gateway.failover.settings") as ms:
        ms.deepseek_api_key = "dk"
        ms.kimi_api_key = "k1"
        ms.kimi_code_api_key = "k2"
        mock_call.side_effect = [Exception("e-p")] * 3 + \
            [Exception("e-f1"), Exception("e-f2")]
        with pytest.raises(RuntimeError) as ei:
            await call_with_failover("p")
    msg = str(ei.value)
    assert "e-p" in msg and "e-f1" in msg and "e-f2" in msg
    assert mock_call.call_count == 5


@pytest.mark.asyncio
async def test_default_model_constants_three_tiers():
    from src.gateway import failover as fo
    assert fo.DEEPSEEK_MODEL.startswith("deepseek/deepseek-v4-")
    assert fo.KIMI_MODEL == "openai/kimi-k3"
    assert fo.KIMI_API_BASE == "https://api.moonshot.cn/v1"
    assert fo.KIMI_CODE_MODEL == "anthropic/k3"
    assert fo.KIMI_CODE_API_BASE == "https://api.kimi.com/coding"
    # 三个模型标识的 key/base 路由
    assert fo._api_base_for(fo.DEEPSEEK_MODEL) is None
    assert fo._api_base_for(fo.KIMI_MODEL) == fo.KIMI_API_BASE
    assert fo._api_base_for(fo.KIMI_CODE_MODEL) == fo.KIMI_CODE_API_BASE
