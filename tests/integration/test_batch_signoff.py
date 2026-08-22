import uuid
import pytest
from sqlalchemy import text
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.review.batch_signoff import create_batch


@pytest.mark.asyncio
async def test_create_batch_samples_20_percent():
    async with SessionLocal() as session:
        task_ids = []
        for i in range(10):
            t = Task(idempotency_key=f"batch-{uuid.uuid4().hex[:8]}", query=f"q{i}", content_type="x")
            session.add(t)
            await session.flush()
            task_ids.append(str(t.id))
        await session.commit()
    batch = await create_batch(task_ids=task_ids, sampling_rate=0.20)
    assert batch["member_count"] == 10
    assert batch["sampled_count"] == 2  # 20% of 10
