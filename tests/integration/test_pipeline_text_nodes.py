import uuid
import pytest
from unittest.mock import patch
from sqlalchemy import select
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.entities import Claim
from src.pipeline.orchestrator import run_pipeline

FAKE_DRAFT = {
    "text": "这是测试正文。" * 100,  # 600 字
    "model_version": "deepseek/deepseek-chat",
    "cost_cny": 0.01,
    "degraded": False,
}


@pytest.mark.asyncio
async def test_pipeline_runs_through_page_split():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"text-{uuid.uuid4().hex[:8]}", query="测试查询", content_type="x")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
        session.add(Claim(task_id=task_id, claim_text="某事实", risk_level="P1", position=1))
        await session.commit()
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        results = await run_pipeline(task_id)
    nodes = [r["node"] for r in results]
    assert "draft_gen" in nodes
    assert "rule_check" in nodes
    assert "page_split" in nodes
