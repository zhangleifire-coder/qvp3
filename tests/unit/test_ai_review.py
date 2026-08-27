"""AI 双重审核：视觉判定 + 自动重生成 单测。"""
from unittest.mock import AsyncMock, patch
import pytest

from src.pipeline.ai_review import _vl_review, _gen_one_with_review


class TestVlReview:
    async def test_parse_structured_result(self):
        payload = {"choices": [{"message": {"content":
            '{"text_ok": true, "text_amount_ok": false, "ref_ok": true, '
            '"issues": ["文字过多"], "suggest": "精简图上文字"}'}}]}
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (10, 10)).save(buf, format="PNG")
        fake_img = (buf.getvalue(), "image/png")

        class _R:
            status_code = 200
            def json(self):
                return payload
        class _C:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **kw): return _R()
        with patch("src.pipeline.ai_review.fetch_image_bytes",
                   new=AsyncMock(return_value=fake_img)), \
             patch("src.pipeline.ai_review.httpx.AsyncClient", _C):
            r = await _vl_review("/x.png", "测试文案", 1, True)
        assert r["pass"] is False and "文字过多" in r["issues"]
        assert r["suggest"] == "精简图上文字"

    async def test_failure_defaults_pass(self):
        with patch("src.pipeline.ai_review.fetch_image_bytes",
                   new=AsyncMock(side_effect=RuntimeError("down"))):
            r = await _vl_review("/x.png", "文案", 1, False)
        assert r["pass"] is True   # 通道故障默认通过，不误杀


class TestGenWithReview:
    async def test_pass_first_round(self):
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (10, 10)).save(buf, format="PNG")
        png = buf.getvalue()

        async def fake_gen(prompt, reference_image_urls=None):
            return {"image_url": "data:image/png;base64,xxx",
                    "model_version": "gpt-image-2"}
        async def fake_fetch(url):
            return (png, "image/png")
        async def fake_ocr(url):
            return {"raw_text": "测试文案", "cost_cny": 0, "model": "qwen"}
        with patch("src.gateway.image_gen.generate_image", new=fake_gen), \
             patch("src.pipeline.ai_review.fetch_image_bytes", new=fake_fetch), \
             patch("src.gateway.ocr.ocr_image", new=fake_ocr), \
             patch("src.pipeline.ai_review._vl_review",
                   new=AsyncMock(return_value={"pass": True, "issues": [], "suggest": ""})), \
             patch("src.pipeline.nodes._persist_image", return_value="/static/generated/x.png"):
            url, model, review = await _gen_one_with_review(
                "tid", 1, "测试文案", "基础提示词", [], False)
        assert review["pass"] and review["rounds"] == 1

    async def test_regen_on_fail_and_flag_after_max(self):
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (10, 10)).save(buf, format="PNG")
        png = buf.getvalue()

        async def fake_gen(prompt, reference_image_urls=None):
            return {"image_url": "data:image/png;base64,xxx", "model_version": "m"}
        async def fake_fetch(url):
            return (png, "image/png")
        async def fake_ocr(url):
            return {"raw_text": "净氺滤蕊", "cost_cny": 0, "model": "qwen"}  # 扭曲
        with patch("src.gateway.image_gen.generate_image", new=fake_gen), \
             patch("src.pipeline.ai_review.fetch_image_bytes", new=fake_fetch), \
             patch("src.gateway.ocr.ocr_image", new=fake_ocr), \
             patch("src.pipeline.ai_review._vl_review",
                   new=AsyncMock(return_value={"pass": False, "issues": ["文字扭曲"],
                                               "suggest": "文字保持正确"})), \
             patch("src.pipeline.nodes._persist_image", return_value="/static/generated/x.png"):
            url, model, review = await _gen_one_with_review(
                "tid", 1, "净水器滤芯", "基础提示词", [], True)
        assert review["rounds"] == 2 and not review["pass"]
        assert review["flagged"]        # 两轮仍败打标记进人工审核
