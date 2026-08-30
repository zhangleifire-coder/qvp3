# 实景审图：搜索量至少 12 张 + 手工上传自定义图 集成测试（2026-08-30）
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.assets import Asset
from src.models.tasks import Task


async def _create(mode="single", status="awaiting_refs"):
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"up-{uuid.uuid4().hex[:8]}",
                    query="石头扫地机器人和云鲸扫地机器人哪个好",
                    content_type="x", mode=mode, status=status)
        session.add(task)
        await session.commit()
        return task.id


def _svg(tag):
    import hashlib
    from pathlib import Path
    h = hashlib.md5(tag.encode()).hexdigest()[:10]
    d = Path("static/generated"); d.mkdir(parents=True, exist_ok=True)
    f = d / f"reftest_{h}.svg"
    f.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' width='576' height='768'>"
                 f"<text>{h}</text></svg>", encoding="utf-8")
    return f"/static/generated/{f.name}"


@pytest.mark.asyncio
async def test_search_collects_at_least_12():
    """搜索量口径：渠道充足时每主体 12、两主体合计 24 张候选。"""
    from src.pipeline.ref_collect import node_ref_collect
    task_id = await _create(mode="compare", status="draft")

    async def fake_search(q, count=12):
        return [{"image_url": _svg(f"{q}-{i}"), "title": "t",
                 "engine": "bing"} for i in range(count)]
    with patch("src.gateway.image_search.search_image", new=fake_search), \
         patch("src.config.settings.mock_image_gen", True):
        r = await node_ref_collect({"task_id": task_id})
    assert r["ref_gate"] is True and r["candidates"] >= 12
    assert r["candidates"] == 24   # 两主体 12+12


@pytest.mark.asyncio
async def test_upload_refs_adds_manual_candidates():
    """手工上传：图片本地化 + 候选落库（manual 标记），与搜索候选并列。"""
    from src.api.tasks import upload_refs
    task_id = await _create()

    class FakeFile:
        filename = "my_photo.png"
        content_type = "image/png"
        async def read(self):
            return b"\x89PNG fake-bytes-for-test"

    with patch("src.api.tasks.log_action", new=AsyncMock()), \
         patch("src.config.settings.mock_image_gen", True):
        r = await upload_refs(str(task_id), files=[FakeFile(), FakeFile()], actor="张三")
    assert r["ok"] is True and r["uploaded"] == 2 and r["candidates"] == 2
    async with SessionLocal() as s:
        rows = list((await s.execute(select(Asset).where(
            Asset.task_id == task_id,
            Asset.selection_status == "candidate"))).scalars().all())
        assert len(rows) == 2
        assert all(a.model_version == "manual" for a in rows)
        assert all(a.image_url.startswith("/static/generated/") for a in rows)
        assert all(a.copyright_status == "unknown" for a in rows)
        assert sorted(a.page_index for a in rows) == [1, 2]


@pytest.mark.asyncio
async def test_upload_refs_validations():
    """上传校验：非图片 / 超大 / 非待确认状态。"""
    from src.api.tasks import upload_refs
    task_id = await _create()

    class NotImage:
        filename = "a.txt"; content_type = "text/plain"
        async def read(self): return b"hello"

    with patch("src.config.settings.mock_image_gen", True):
        with pytest.raises(HTTPException) as ei:
            await upload_refs(str(task_id), files=[NotImage()], actor="x")
        assert "不是图片" in str(ei.value.detail)

    class TooBig:
        filename = "big.png"; content_type = "image/png"
        async def read(self): return b"x" * (11 * 1024 * 1024)

    with patch("src.config.settings.mock_image_gen", True):
        with pytest.raises(HTTPException) as ei2:
            await upload_refs(str(task_id), files=[TooBig()], actor="x")
        assert "超过" in str(ei2.value.detail)

    # 状态不对（review 而非 awaiting_refs）
    other = await _create(status="review")
    with patch("src.config.settings.mock_image_gen", True):
        with pytest.raises(HTTPException) as ei3:
            await upload_refs(str(other), files=[NotImage()], actor="x")
        assert "仅待确认参考图状态" in str(ei3.value.detail)
