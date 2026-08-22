import uuid
import pytest
from unittest.mock import patch
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.pipeline.orchestrator import run_pipeline
from src.dashboard.metrics import all_metrics

FAKE_DRAFT = {
    "text": "这是测试正文。" * 100,
    "model_version": "deepseek/deepseek-chat",
    "cost_cny": 0.01,
    "degraded": False,
}


@pytest.mark.asyncio
async def test_lifecycle_rebuildable_from_node_events():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"rebuild-{uuid.uuid4().hex[:8]}", query="t", content_type="x")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        await run_pipeline(task_id)
    metrics = await all_metrics()
    assert metrics["throughput_per_hour"] >= 1
