"""VL 申诉通道（2026-09-14 P2）单测：OCR 误判放行 / 真不合格重生 / VL 不可用降级。

mock：ocr_image（首轮 sim 由返回值控制）/ check_text_match（申诉结果）/
generate_image + fetch_image_bytes + _persist_image（重生链路）。
"""
from unittest.mock import AsyncMock, patch

from src.pipeline.agent_shared import _garble_check_and_regen

_PAGES = ["第1页正文内容测试" * 6, "第二页文案样本" * 8]

_IMG_OK = {"page_index": 1, "image_url": "/static/generated/x_p1.png",
           "hash": "h1", "origin_url": "https://ex.com/1.png", "size_ok": True}
_IMG_BAD = {"page_index": 2, "image_url": "/static/generated/x_p2.png",
            "hash": "h2", "origin_url": "https://ex.com/2.png", "size_ok": True}


def _patches(ocr_sim_low: bool, appeal: dict | None):
    """ocr_sim_low=True 模拟 OCR 判不合格；appeal=VL 复核结果。"""
    return (
        patch("src.gateway.ocr.ocr_image",
              new=AsyncMock(return_value={"raw_text":
                              ("错字一大堆" if ocr_sim_low else _PAGES[0]),
                              "cost_cny": 0.001, "model": "qwen-vl-ocr"})),
        patch("src.services.visual_check.check_text_match",
              new=AsyncMock(return_value=appeal)),
        patch("src.gateway.image_gen.generate_image",
              new=AsyncMock(return_value={"hash": "h9",
                              "image_url": "https://ex.com/new.png",
                              "model_version": "gpt-image-2"})),
        patch("src.gateway.ocr.fetch_image_bytes",
              new=AsyncMock(return_value=(b"pngdata", "image/png"))),
        patch("src.pipeline.nodes._persist_image",
              new=AsyncMock(return_value="/static/generated/x_p2_r.png")),
    )


class TestVLAppeal:
    async def test_ocr_pass_no_appeal(self):
        ps = _patches(ocr_sim_low=False, appeal=None)
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            localized, garbled = await _garble_check_and_regen(
                "t-appeal", _PAGES, [dict(_IMG_OK)], "tpl", "general")
        assert garbled == {}
        assert localized[0].get("first_sim") == 1.0

    async def test_ocr_fail_vl_appeal_pass(self):
        """OCR 判不合格 + VL 说一致 → 放行、零重生（申诉成功路径）。"""
        ps = _patches(ocr_sim_low=True, appeal={"ok": True, "actual": "与文案一致"})
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            localized, garbled = await _garble_check_and_regen(
                "t-appeal", _PAGES, [dict(_IMG_BAD)], "tpl", "general")
        assert garbled == {}                       # 未进重生/人工标记
        assert "vlappeal" in localized[0]["prompt_used"]
        assert localized[0]["image_url"] == _IMG_BAD["image_url"]  # 原图保留

    async def test_ocr_fail_vl_confirms_fail(self):
        """OCR 判不合格 + VL 也说不一致 → 走重生（generate_image 被调用）。"""
        ps = _patches(ocr_sim_low=True, appeal={"ok": False, "actual": "文字缺漏"})
        gen_mock = ps[2].new
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            localized, garbled = await _garble_check_and_regen(
                "t-appeal", _PAGES, [dict(_IMG_BAD)], "tpl", "general")
        assert gen_mock.called                     # 重生确实发生
        # 重生后 OCR 仍不合格（mock 固定返回错字）→ 保留原图 + 打标记进人工
        assert localized[0]["image_url"] == _IMG_BAD["image_url"]
        assert garbled == {2: "text_garble"}

    async def test_vl_unavailable_falls_back_to_regen(self):
        """VL 不可用（None）→ 维持 OCR 判定走重生，不阻塞、不误放。"""
        ps = _patches(ocr_sim_low=True, appeal=None)
        gen_mock = ps[2].new
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            localized, garbled = await _garble_check_and_regen(
                "t-appeal", _PAGES, [dict(_IMG_BAD)], "tpl", "general")
        assert gen_mock.called
        assert localized[0].get("first_sim", 1) < 1.0
