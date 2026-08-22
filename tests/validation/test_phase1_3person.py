import asyncio
import uuid
import pytest
from sqlalchemy import text
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.review.locks import acquire_lock


async def _create_user(role: str) -> str:
    async with SessionLocal() as session:
        uid = (await session.execute(
            text("INSERT INTO users (name, role) VALUES (:n, :r) RETURNING id"),
            {"n": f"phase1-{uuid.uuid4().hex[:6]}", "r": role})).scalar_one()
        await session.commit()
    return str(uid)


@pytest.mark.asyncio
async def test_phase1_three_people_parallel():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"phase1-{uuid.uuid4().hex[:8]}", query="测试", content_type="x")
        session.add(task)
        await session.commit()
        tid = str(task.id)
    reviewers = {role: await _create_user(role) for role in ["A", "B", "C"]}
    locks = await asyncio.gather(
        acquire_lock(tid, "A", reviewers["A"]),
        acquire_lock(tid, "B", reviewers["B"]),
        acquire_lock(tid, "C", reviewers["C"]),
    )
    assert all(l["acquired"] for l in locks)
