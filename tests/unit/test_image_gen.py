import pytest
from unittest.mock import AsyncMock, patch
import httpx
from src.gateway import image_gen

# `mock_external_calls` autouse fixture 会把模块级 image_gen.generate_image 替换成
# AsyncMock（供 pipeline/集成测试避免真实网络调用）。这里在 import 时保存真实函数，
# 单元测试要测的正是它内部的路由逻辑（_generate vs _edit_with_references）。
_generate_image = image_gen.generate_image


@pytest.mark.asyncio
async def test_generate_text_only_routes_to_generate():
    with patch.object(image_gen, "_generate", new=AsyncMock(return_value={"image_url": "u", "hash": "h", "model_version": "gpt-image-1.5"})) as gen, \
         patch.object(image_gen, "_edit_with_references", new=AsyncMock()) as edit:
        await _generate_image("prompt")
    gen.assert_awaited_once()
    edit.assert_not_called()


@pytest.mark.asyncio
async def test_generate_with_refs_routes_to_edit():
    with patch.object(image_gen, "_edit_with_references", new=AsyncMock(return_value={"image_url": "u", "hash": "h", "model_version": "gpt-image-1.5"})) as edit:
        await _generate_image("prompt", reference_image_urls=["https://x/a.png"])
    edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ref_download_failure_falls_back_to_generate():
    with patch.object(image_gen, "_edit_with_references",
                      new=AsyncMock(side_effect=httpx.HTTPStatusError("err", request=None, response=None))) as edit, \
         patch.object(image_gen, "_generate", new=AsyncMock(return_value={"image_url": "u", "hash": "h", "model_version": "gpt-image-1.5"})) as gen:
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
