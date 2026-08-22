import uuid
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import text
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.review.anomaly_detector import flag_anomalies


async def _insert_session(started_delta: timedelta, finished_delta: timedelta):
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"anom-{uuid.uuid4().hex[:8]}", query="t", content_type="x")
        session.add(task)
        await session.commit()
        now = datetime.now(timezone.utc)
        await session.execute(text(
            "INSERT INTO review_sessions (task_id, role, started_at, finished_at, last_heartbeat_at) "
            "VALUES (:tid, 'A', :st, :ft, :st)"),
            {"tid": task.id, "st": now - started_delta, "ft": now - finished_delta})
        await session.commit()


@pytest.mark.asyncio
async def test_too_fast_flagged():
    await _insert_session(timedelta(seconds=60), timedelta(seconds=58))  # 2 秒完成
    result = await flag_anomalies()
    assert result["flagged"] >= 1


@pytest.mark.asyncio
async def test_too_slow_flagged():
    await _insert_session(timedelta(seconds=8000), timedelta(seconds=0))  # 8000 秒
    result = await flag_anomalies()
    assert result["flagged"] >= 1
