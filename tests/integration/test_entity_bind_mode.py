import uuid
import pytest
from unittest.mock import patch
from sqlalchemy import select, func
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.assets import Asset
from src.pipeline.nodes import node_entity_bind


@pytest.mark.asyncio
async def test_entity_bind_skips_search_for_general():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"e-{uuid.uuid4().hex[:8]}", query="通用内容",
                    content_type="generic", mode="general")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        tid = task.id
    with patch("src.gateway.image_search.search_image") as mock_search:
        out = await node_entity_bind({"task_id": tid})
    assert out["searched_images"] == 0
    mock_search.assert_not_called()
    async with SessionLocal() as session:
        cnt = (await session.execute(
            select(func.count()).select_from(Asset).where(Asset.task_id == tid))).scalar_one()
        assert cnt == 0
