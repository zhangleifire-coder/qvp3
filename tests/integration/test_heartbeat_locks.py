import uuid
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import text
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.review.heartbeat import auto_suspend_stale_sessions


async def _create_user(role: str) -> str:
    async with SessionLocal() as session:
        uid = (await session.execute(
            text("INSERT INTO users (name, role) VALUES (:n, :r) RETURNING id"),
            {"n": f"hb-{uuid.uuid4().hex[:6]}", "r": role})).scalar_one()
        await session.commit()
    return str(uid)


@pytest.mark.asyncio
async def test_auto_suspend_after_timeout():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"hb-{uuid.uuid4().hex[:8]}", query="t", content_type="x")
        session.add(task)
        await session.commit()
        tid = task.id
        uid = await _create_user("A")
        old = datetime.now(timezone.utc) - timedelta(seconds=6000)
        await session.execute(text(
            "INSERT INTO review_sessions (task_id, role, reviewer_id, locked_at, last_heartbeat_at, started_at) "
            "VALUES (:tid, 'A', :uid, :old, :old, :old)"),
            {"tid": tid, "uid": uuid.UUID(uid), "old": old})
        await session.commit()
    result = await auto_suspend_stale_sessions(timeout_seconds=5400)
    assert len(result["suspended"]) >= 1
