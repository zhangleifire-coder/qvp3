import pytest
from unittest.mock import AsyncMock, patch
import httpx
from src.gateway import image_gen

# `mock_external_calls` autouse fixture 会把模块级 image_gen.generate_image 替换成
# AsyncMock（供 pipeline/集成测试避免真实网络调用）。这里在 import 时保存真实函数，
# 单元测试要测的正是它内部的路由逻辑（_generate vs _edit_fusion/_edit_moacode）。
_generate_image = image_gen.generate_image


@pytest.mark.asyncio
async def test_generate_text_only_routes_to_generate():
    # 路由测试固定 openox 通道：_next_channel 全局轮询，若轮到 fusion/moacode
    # 会调用未 mock 的真实生图函数（网络重试 60s+ 后假失败）——历史偶发红根因
    with patch.object(image_gen, "_next_channel", return_value="openox"), \
         patch.object(image_gen, "_generate", new=AsyncMock(return_value={"image_url": "u", "hash": "h", "model_version": "gpt-image-1.5"})) as gen, \
         patch.object(image_gen, "_edit_fusion", new=AsyncMock()) as edit:
        await _generate_image("prompt")
    gen.assert_awaited_once()
    edit.assert_not_called()


@pytest.mark.asyncio
async def test_generate_with_refs_routes_to_edit():
    with patch.object(image_gen, "_next_channel", return_value="fusion"), \
         patch.object(image_gen, "_edit_fusion", new=AsyncMock(return_value={"image_url": "u", "hash": "h", "model_version": "gpt-image-1.5"})) as edit:
        await _generate_image("prompt", reference_image_urls=["https://x/a.png"])
    edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ref_download_failure_falls_back_to_generate():
    with patch.object(image_gen, "_next_channel", return_value="fusion"), \
         patch.object(image_gen, "_edit_fusion",
                      new=AsyncMock(side_effect=httpx.HTTPStatusError("err", request=None, response=None))), \
         patch.object(image_gen, "_edit_moacode",
                      new=AsyncMock(side_effect=httpx.HTTPStatusError("err", request=None, response=None))), \
         patch.object(image_gen, "_generate_fusion", new=AsyncMock(return_value={"image_url": "u", "hash": "h", "model_version": "gpt-image-1.5"})) as gen:
        await _generate_image("prompt", reference_image_urls=["https://x/a.png"])
    gen.assert_awaited_once()


@pytest.mark.asyncio
async def test_mock_image_gen_returns_placeholder(monkeypatch):
    monkeypatch.setattr(image_gen.settings, "mock_image_gen", True)
    with patch.object(image_gen, "_generate", new=AsyncMock()) as gen:
        r = await _generate_image("测试 prompt")
    assert r["model_version"] == "mock"
    assert r["image_url"].startswith("data:image/svg+xml")
    assert r["hash"]
    gen.assert_not_called()


@pytest.mark.asyncio
async def test_openox_text_gen_uses_own_credentials():
    """openox 文生图走 OpenAI 兼容 Images API 路径（linkai 删除后为其唯一使用方）。"""
    with patch.object(image_gen, "_next_channel", return_value="openox"), \
         patch.object(image_gen.settings, "openox_api_key", "sk-openox-x"), \
         patch.object(image_gen.settings, "openox_base_url", "https://api.openox.net/v1"), \
         patch.object(image_gen, "_generate",
                      new=AsyncMock(return_value={"image_url": "u", "hash": "h",
                                                  "model_version": "gpt-image-2"})) as gen:
        await _generate_image("prompt")
    gen.assert_awaited_once()
    kwargs = gen.await_args.kwargs
    assert kwargs["api_key"] == "sk-openox-x"
    assert kwargs["base_url"] == "https://api.openox.net/v1"


@pytest.mark.asyncio
async def test_openox_never_handles_image_to_image():
    """图生图轮到 openox 时直接转给支持 edits 的通道，openox 不参与图生图。"""
    with patch.object(image_gen, "_next_channel", return_value="openox"), \
         patch.object(image_gen, "_channels", return_value=["fusion", "openox"]), \
         patch.object(image_gen, "_edit_fusion",
                      new=AsyncMock(return_value={"image_url": "u", "hash": "h",
                                                  "model_version": "gpt-image-2@fusion"})) as edit, \
         patch.object(image_gen, "_generate", new=AsyncMock()) as gen:
        await _generate_image("prompt", reference_image_urls=["https://x/a.png"])
    edit.assert_awaited_once()  # 转给 fusion 的 edits，而非 openox 文生图
    gen.assert_not_called()
