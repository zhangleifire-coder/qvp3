# 视觉主体审核 + 3:4 铁律（2026-09-01）集成测试：
# 尺寸偏离重生成→仍偏裁剪归一留痕；主体不符→重画一次→仍不符标记 subject_mismatch；
# 主体一致/审核不可用→不标记不阻塞。
import base64
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from src.config import settings
from src.db.session import SessionLocal
from src.models.tasks import Task
from sqlalchemy import delete


def _png_bytes(w, h):
    img = Image.new("RGB", (w, h), (200, 180, 160))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _data_uri(w, h):
    return "data:image/png;base64," + base64.b64encode(_png_bytes(w, h)).decode()


def _fake_asset(w, h):
    return {"image_url": _data_uri(w, h), "hash": "x",
            "model_version": "gpt-image-2@fusion",
            "source_type": "ai_generated", "copyright_status": "clear",
            "task_id": None, "page_index": 1}


async def _fake_fetch(url):
    # url 形如 data:image/png;base64,... 或落盘后的 /static/...（裁剪后）——
    # 落盘文件由真实 _persist_image 写入，fetch 对 /static 读文件
    if url.startswith("data:"):
        b64 = url.split(",", 1)[1]
        return base64.b64decode(b64), "image/png"
    from pathlib import Path
    local = Path(__file__).resolve().parent.parent.parent / url.lstrip("/")
    return local.read_bytes(), "image/png"


@pytest.mark.asyncio
async def test_size_iron_rule_crops_to_34(monkeypatch):
    """3:4 铁律：重生成仍偏（返回同尺寸）→ 中心裁剪归一 + |cropped 留痕。"""
    from src.pipeline import nodes as N
    tid = uuid.uuid4()
    monkeypatch.setattr(settings, "mock_image_gen", True)  # 跳过主体审核开关? 主体审核由 page_body 控制
    monkeypatch.setattr("src.gateway.ocr.fetch_image_bytes", _fake_fetch)
    # 重生成返回同样 1000x1000（仍偏）→ 触发裁剪
    monkeypatch.setattr(N, "_generate_single_asset",
                        AsyncMock(return_value=_fake_asset(1000, 1000)))
    asset = _fake_asset(1000, 1000)
    out, extra = await N._dedupe_and_validate(
        asset, "prompt", None, tid, 1, set(), page_body="")   # 空 body 跳过主体审核
    assert extra == 1                       # 尺寸重生成发生
    assert "|cropped:1000x1000" in out["model_version"]
    from PIL import Image as I
    data = await _fake_fetch(out["image_url"])
    w, h = I.open(io.BytesIO(data[0])).size
    assert abs(w / h - 0.75) <= 0.01        # 归一为 3:4


@pytest.mark.asyncio
async def test_size_regen_fixes_ratio(monkeypatch):
    """重生成返回 3:4 → 直接采用，不裁剪。"""
    from src.pipeline import nodes as N
    tid = uuid.uuid4()
    monkeypatch.setattr("src.gateway.ocr.fetch_image_bytes", _fake_fetch)
    monkeypatch.setattr(N, "_generate_single_asset",
                        AsyncMock(return_value=_fake_asset(768, 1024)))
    out, extra = await N._dedupe_and_validate(
        _fake_asset(1000, 1000), "p", None, tid, 1, set(), page_body="")
    assert extra == 1 and "|cropped" not in out["model_version"]


@pytest.mark.asyncio
async def test_subject_mismatch_marks_after_failed_redraw(monkeypatch):
    """主体不符 → 重画一次 → 仍不符 → subject_mismatch=True（不阻塞）。"""
    from src.pipeline import nodes as N
    tid = uuid.uuid4()
    monkeypatch.setattr("src.gateway.ocr.fetch_image_bytes", _fake_fetch)

    async def bad_check(url, text):
        return {"ok": False, "actual": "一只狗"}
    monkeypatch.setattr("src.services.visual_check.check_subject_match", bad_check)
    monkeypatch.setattr(N, "_generate_single_asset",
                        AsyncMock(return_value=_fake_asset(768, 1024)))
    out, extra = await N._dedupe_and_validate(
        _fake_asset(768, 1024), "p", None, tid, 1, set(),
        page_body="猫咪每天要喝多少水")
    assert extra == 1                       # 主体不符重画一次
    assert out.get("subject_mismatch") is True
    assert "|subj!" in out["model_version"]


@pytest.mark.asyncio
async def test_subject_ok_no_marks(monkeypatch):
    from src.pipeline import nodes as N
    tid = uuid.uuid4()
    monkeypatch.setattr("src.gateway.ocr.fetch_image_bytes", _fake_fetch)

    async def ok_check(url, text):
        return {"ok": True, "actual": "一只猫在喝水"}
    monkeypatch.setattr("src.services.visual_check.check_subject_match", ok_check)
    monkeypatch.setattr(N, "_generate_single_asset", AsyncMock())
    out, extra = await N._dedupe_and_validate(
        _fake_asset(768, 1024), "p", None, tid, 1, set(),
        page_body="猫咪每天要喝多少水")
    assert extra == 0 and not out.get("subject_mismatch")


@pytest.mark.asyncio
async def test_subject_check_unavailable_skips(monkeypatch):
    """VL 审核不可用（None）→ 跳过，不重画不标记（人工关卡兜底）。"""
    from src.pipeline import nodes as N
    tid = uuid.uuid4()
    monkeypatch.setattr("src.gateway.ocr.fetch_image_bytes", _fake_fetch)

    async def none_check(url, text):
        return None
    monkeypatch.setattr("src.services.visual_check.check_subject_match", none_check)
    monkeypatch.setattr(N, "_generate_single_asset", AsyncMock())
    out, extra = await N._dedupe_and_validate(
        _fake_asset(768, 1024), "p", None, tid, 1, set(),
        page_body="猫咪每天要喝多少水")
    assert extra == 0 and not out.get("subject_mismatch")


@pytest.mark.asyncio
async def test_visual_check_service_parses(monkeypatch):
    """visual_check 服务：mock dashscope 返回严格 JSON 解析；异常返回 None。"""
    from src.services import visual_check as VC
    # conftest 默认关掉 VL 开关（防真实调用），本用例专测服务解析，显式打开
    monkeypatch.setattr(settings, "visual_subject_check_enabled", True)

    class _Resp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content":
                    '{"ok": false, "actual": "一只狗"}'}}]}

    class _Client:
        async def post(self, *a, **kw): return _Resp()

    async def fake_to_data_url(url):
        return url
    monkeypatch.setattr("src.gateway.ocr._image_to_data_url", fake_to_data_url)
    # 共享 client（P1-7）：patch 模块级 get_client 即控制出站调用
    with patch("src.services.visual_check.get_client", return_value=_Client()):
        r = await VC.check_subject_match("data:image/png;base64,x", "文案说猫")
    assert r == {"ok": False, "actual": "一只狗"}

    with patch("src.services.visual_check.get_client",
               side_effect=RuntimeError("vl down")):
        assert await VC.check_subject_match("u", "文") is None
