import uuid
import pytest
from unittest.mock import patch
from sqlalchemy import select
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.events import NodeEvent
from src.pipeline.orchestrator import run_pipeline

FAKE_DRAFT = {
    "text": "这是测试正文。" * 100,
    "model_version": "deepseek/deepseek-chat",
    "cost_cny": 0.01,
    "degraded": False,
}


@pytest.mark.asyncio
async def test_pipeline_records_node_events():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"test-{uuid.uuid4().hex[:8]}", query="test", content_type="x")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        results = await run_pipeline(task_id)
    assert len(results) == 13
    assert results[0]["node"] == "task_import"
    async with SessionLocal() as session:
        events = await session.execute(
            select(NodeEvent).where(NodeEvent.task_id == task_id))
        assert len(events.scalars().all()) == 13
