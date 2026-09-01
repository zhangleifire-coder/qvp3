# 场景化扩写器（2026-09-01）：nanobot 记忆会话优先 → DeepSeek 回退 → None；
# 英文视觉骨架注入与中文回退骨架。
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.services.visual_writer import write_page_visuals, _parse_visual
from src.gateway.prompt_versions import get_image_prompt

_GOOD = json.dumps({
    "style_en": "Cream-beige minimalist magazine card, soft studio light, "
                "photographic texture, muted palette, thin dividers.",
    "pages": [f"EN visual page {i}: subject with props, soft window light, "
              f"45-degree view" for i in range(1, 7)],
}, ensure_ascii=False)


def test_parse_visual_ok_and_rejects():
    r = _parse_visual("```json\n" + _GOOD + "\n```")
    assert r and len(r["pages"]) == 6 and "Cream" in r["style_en"]
    assert _parse_visual('{"style_en":"s","pages":["a","b"]}') is None      # 页数不足
    assert _parse_visual("not json at all") is None
    assert _parse_visual('{"style_en":"","pages":["a"]*6}') is None          # 风格空


@pytest.mark.asyncio
async def test_nanobot_memory_session_first():
    """一级 nanobot 记忆会话：成功即返回，不走文本回退。"""
    captured = {}

    async def fake_call_agent(msg, session_id=None, on_delta=None):
        captured["session_id"] = session_id
        return {"text": _GOOD, "prompt_tokens": 10, "completion_tokens": 10}

    async def no_failover(*a, **kw):  # 若被走到说明链路错
        raise AssertionError("should not reach fallback")

    with patch("src.gateway.nanobot_client.call_agent",
               side_effect=fake_call_agent), \
         patch("src.gateway.failover.call_with_failover", new=no_failover):
        r = await write_page_visuals("自然写实暖调", "奶油米底写实摄影", ["文"] * 6)
    assert r and captured["session_id"] == "qvp-visual-memory-v1"
    assert r["pages"][0].startswith("EN visual page 1")


@pytest.mark.asyncio
async def test_fallback_to_failover_on_timeout():
    """nanobot 超时 → 回退 DeepSeek failover（无跨任务记忆但可用）。"""
    async def slow_agent(msg, session_id=None, on_delta=None):
        await __import__("asyncio").sleep(200)

    with patch("src.gateway.nanobot_client.call_agent", side_effect=slow_agent), \
         patch("src.gateway.failover.call_with_failover",
               new=AsyncMock(return_value={"text": _GOOD})):
        r = await write_page_visuals("风格", "描述", ["文"] * 6)
    assert r and len(r["pages"]) == 6


@pytest.mark.asyncio
async def test_all_failed_returns_none():
    async def bad_agent(msg, session_id=None, on_delta=None):
        return {"text": "garbage"}

    with patch("src.gateway.nanobot_client.call_agent", side_effect=bad_agent), \
         patch("src.gateway.failover.call_with_failover",
               new=AsyncMock(side_effect=RuntimeError("both down"))):
        assert await write_page_visuals("s", "d", ["x"] * 6) is None


@pytest.mark.asyncio
async def test_note_to_memory_swallow_errors():
    with patch("src.gateway.nanobot_client.call_agent",
               new=AsyncMock(side_effect=RuntimeError("nanobot down"))):
        await note_ok()  # 不抛异常即通过


async def note_ok():
    from src.services.visual_writer import note_to_memory
    await note_to_memory("P3 画面与文案不符")


def test_english_visual_prompt_assembly():
    """英文骨架：VISUAL DIRECTION + 统一风格 + 中文文案逐字 + 英文约束 + 布局。"""
    p = get_image_prompt(
        "general", "桂花酸梅汤的三个要点", 2,
        visual="A glass pitcher of plum drink on oak table, morning side light",
        style_en="Cream minimalist magazine card, soft light")
    assert "VISUAL DIRECTION" in p and "oak table" in p
    assert "UNIFIED STYLE" in p and "Cream minimalist" in p
    assert "桂花酸梅汤的三个要点" in p          # 中文文案逐字保留
    assert "render VERBATIM" in p
    assert "HANZI RULE" in p and "pure white" in p and "30-100" in p
    assert "key points" in p                      # 英文布局轮换（第2页）
    assert "（主体锚定）" not in p                # 中文骨架未混入


def test_chinese_fallback_unchanged():
    """回退骨架：无 visual 时中文底座原样（含均衡/边框/锚定条款）。"""
    p = get_image_prompt("general", "正文", 1)
    assert "30-100 字" in p and "不得使用纯白或纯黑" in p
    assert "画面主体必须直接描绘本页文案所讲的事物本身" in p
