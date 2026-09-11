"""超级管理控制台（2026-09-10）：字段工具函数 + failover 动态链/备用开关。"""
import pytest
from unittest.mock import AsyncMock, patch

from src.api.superadmin import mask_secret, reorder_channels_first, validate_field
from src.config import settings
from src.gateway import failover


# ── mask_secret ──
def test_mask_secret():
    assert mask_secret("") == ""
    assert mask_secret("short") == "****"
    assert mask_secret("sk-abcdefghij12345") == "sk-a****2345"


# ── reorder_channels_first ──
def test_reorder_channels_first():
    assert reorder_channels_first("fusion,linkai,moacode,openox", "openox") == \
        "openox,fusion,linkai,moacode"
    assert reorder_channels_first("fusion,openox", "openox") == "openox,fusion"
    with pytest.raises(ValueError):
        reorder_channels_first("fusion,linkai", "moacode")   # 不在顺序里


# ── validate_field ──
def test_validate_channels():
    assert validate_field("image_gen_channels", "openox, fusion") == "openox,fusion"
    with pytest.raises(ValueError):
        validate_field("image_gen_channels", "fusion,badchan")
    with pytest.raises(ValueError):
        validate_field("image_gen_channels", "fusion,fusion")
    with pytest.raises(ValueError):
        validate_field("image_gen_channels", "")


def test_validate_base_url_and_empty():
    assert validate_field("nanobot_base_url", " http://127.0.0.1:8901/v1 ") == \
        "http://127.0.0.1:8901/v1"
    with pytest.raises(ValueError):
        validate_field("nanobot_base_url", "127.0.0.1:8901")
    with pytest.raises(ValueError):
        validate_field("deepseek_model", "")


# ── failover：默认链每次调用解析（在线切主模型即时生效）──
@pytest.mark.asyncio
async def test_failover_primary_resolved_per_call():
    """改 settings.deepseek_model 后，缺省调用应使用新模型（原实现冻结在 import）。"""
    mock_cp = AsyncMock(side_effect=[{"text": "ok"}])
    with patch.object(failover, "call_provider", mock_cp), \
         patch.object(settings, "deepseek_model", "deepseek-v4-pro"):
        r = await failover.call_with_failover("hi")
    assert r["text"] == "ok"
    assert mock_cp.call_args.args[0] == "deepseek/deepseek-v4-pro"


@pytest.mark.asyncio
async def test_failover_disabled_fallback_skipped():
    """备1 停用时应直接跳到备2（备2 有 key），不发起对备1 的调用。"""
    import asyncio
    mock_cp = AsyncMock(side_effect=[
        asyncio.TimeoutError(),                # 主模型超时（超时直接降级，不重试）
        {"text": "by-code"},                   # 备2 成功
    ])
    with patch.object(failover, "call_provider", mock_cp), \
         patch.object(settings, "text_fallback1_enabled", False), \
         patch.object(settings, "text_fallback2_enabled", True), \
         patch.object(settings, "kimi_code_api_key", "sk-test-code"):
        r = await failover.call_with_failover("hi")
    assert r["text"] == "by-code"
    used = [c.args[0] for c in mock_cp.call_args_list]
    assert "openai/kimi-k3" not in used        # 备1 被停用，从未调用
    assert used[0].startswith("deepseek/")
    assert used[1] == "anthropic/k3"


@pytest.mark.asyncio
async def test_failover_all_disabled_raises():
    """主+备用全失败/全停用 → 报错（不静默吞）。"""
    mock_cp = AsyncMock(side_effect=RuntimeError("down"))
    with patch.object(failover, "call_provider", mock_cp), \
         patch.object(settings, "text_fallback1_enabled", False), \
         patch.object(settings, "text_fallback2_enabled", False), \
         patch.object(failover, "KIMI_MODEL", "openai/kimi-k3"):
        import asyncio
        with pytest.raises(RuntimeError):
            # 主模型超时型异常 → 直接进降级段；两段都被停用 → All providers failed
            with patch.object(failover.asyncio, "sleep", AsyncMock()):
                await failover.call_with_failover("hi")
