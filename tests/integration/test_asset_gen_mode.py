import uuid
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.drafts import PageCopy
from src.models.assets import Asset
from src.pipeline.nodes import node_asset_gen

FAKE_IMAGE = {"hash": "abc", "image_url": "https://example.com/i.png", "model_version": "gpt-image-1.5"}


async def _make_task(mode):
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"a-{uuid.uuid4().hex[:8]}", query="q",
                    content_type="x", mode=mode)
        session.add(task)
        await session.commit()
        await session.refresh(task)
        tid = task.id
        for i in range(1, 7):
            session.add(PageCopy(task_id=tid, page_index=i, body=f"第{i}页文案", claim_ids=[]))
        if mode in ("compare", "single"):
            session.add(Asset(task_id=tid, page_index=0, subject="q", source_type="official",
                              copyright_status="unknown", hash="h1",
                              image_url="https://example.com/real.png",
                              model_version="search", is_illustration=False))
        await session.commit()
    return tid


@pytest.mark.asyncio
async def test_asset_gen_general_no_reference():
    tid = await _make_task("general")
    with patch("src.gateway.image_gen.generate_image", new=AsyncMock(return_value=FAKE_IMAGE)) as gen:
        await node_asset_gen({"task_id": tid})
    for call in gen.await_args_list:
        assert call.kwargs.get("reference_image_urls") is None


@pytest.mark.asyncio
async def test_asset_gen_compare_passes_reference():
    tid = await _make_task("compare")
    with patch("src.gateway.image_gen.generate_image", new=AsyncMock(return_value=FAKE_IMAGE)) as gen:
        await node_asset_gen({"task_id": tid})
    refs = [call.kwargs.get("reference_image_urls") for call in gen.await_args_list]
    assert all(r == ["https://example.com/real.png"] for r in refs)
