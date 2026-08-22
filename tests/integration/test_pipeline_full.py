import uuid
import pytest
from unittest.mock import patch
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.pipeline.orchestrator import run_pipeline

FAKE_DRAFT = {
    "text": "这是测试正文。" * 100,
    "model_version": "deepseek/deepseek-chat",
    "cost_cny": 0.01,
    "degraded": False,
}


@pytest.mark.asyncio
async def test_full_pipeline_all_13_nodes():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"full-{uuid.uuid4().hex[:8]}", query="测试", content_type="x")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        results = await run_pipeline(task_id)
    assert len(results) == 13
