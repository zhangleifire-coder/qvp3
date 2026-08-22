import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from src.api.main import app
from src.db.session import SessionLocal
from src.models.tasks import Task


async def _create_user(role: str) -> str:
    async with SessionLocal() as session:
        uid = (await session.execute(
            text("INSERT INTO users (name, role) VALUES (:n, :r) RETURNING id"),
            {"n": f"tester-{uuid.uuid4().hex[:6]}", "r": role})).scalar_one()
        await session.commit()
    return str(uid)


@pytest.mark.asyncio
async def test_double_claim_second_fails():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"lock-{uuid.uuid4().hex[:8]}", query="t", content_type="x")
        session.add(task)
        await session.commit()
        tid = str(task.id)
    u1 = await _create_user("A")
    u2 = await _create_user("A")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/api/review/claim", json={"task_id": tid, "role": "A", "reviewer_id": u1})
        r2 = await ac.post("/api/review/claim", json={"task_id": tid, "role": "A", "reviewer_id": u2})
    assert r1.json()["acquired"] is True
    assert r2.json()["acquired"] is False
