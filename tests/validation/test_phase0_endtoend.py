import uuid
import pytest
from sqlalchemy import text
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.review.locks import acquire_lock


async def _create_user() -> str:
    async with SessionLocal() as session:
        uid = (await session.execute(
            text("INSERT INTO users (name, role) VALUES (:n, 'A') RETURNING id"),
            {"n": f"phase0-{uuid.uuid4().hex[:6]}"})).scalar_one()
        await session.commit()
    return str(uid)


@pytest.mark.asyncio
async def test_phase0_single_person_endtoend():
    """1 人顺序扮演 A → B → C 完成一条任务"""
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"phase0-{uuid.uuid4().hex[:8]}", query="测试", content_type="x")
        session.add(task)
        await session.commit()
        tid = str(task.id)
    single_user = await _create_user()
    for role in ["A", "B", "C"]:
        r = await acquire_lock(tid, role, single_user)
        assert r["acquired"] is True
