import uuid
import pytest
from unittest.mock import patch
from sqlalchemy import select
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.drafts import Draft
from src.pipeline.nodes import node_draft_gen

FAKE_DRAFT = {"text": "这是测试正文。" * 60, "model_version": "deepseek/deepseek-v4-pro",
              "cost_cny": 0.01, "degraded": False}


@pytest.mark.asyncio
async def test_draft_gen_uses_compare_prompt():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"d-{uuid.uuid4().hex[:8]}", query="A vs B",
                    content_type="compare", mode="compare")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        tid = task.id
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        out = await node_draft_gen({"task_id": tid})
    assert out["prompt_version"] == "draft_compare_v1"
    async with SessionLocal() as session:
        d = (await session.execute(
            select(Draft).where(Draft.task_id == tid))).scalar_one()
        assert d.prompt_version == "draft_compare_v1"
