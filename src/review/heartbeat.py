import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update
from src.db.session import SessionLocal
from src.models.review import ReviewSession

CLOCK_DRIFT_THRESHOLD_SECONDS = 120  # >2 分钟视为时钟漂移，报警并暂停该审核员采集
TIME_INCONSISTENCY_THRESHOLD = 0.30  # 前后端偏差 >30% 标记 time_inconsistency_flag


def detect_clock_drift(client_ts: datetime, server_ts: datetime) -> dict:
    drift_seconds = abs((client_ts - server_ts).total_seconds())
    return {"drifted": drift_seconds > CLOCK_DRIFT_THRESHOLD_SECONDS,
            "drift_seconds": drift_seconds}


async def record_heartbeat(task_id: str, role: str, reviewer_name: str,
                           client_ts: datetime = None):
    from src.review.users import get_or_create_user
    server_ts = datetime.now(timezone.utc)
    drift = None
    if client_ts is not None:
        drift = detect_clock_drift(client_ts, server_ts)
    async with SessionLocal() as session:
        reviewer_id = await get_or_create_user(session, reviewer_name, role)
        await session.execute(
            update(ReviewSession)
            .where(ReviewSession.task_id == uuid.UUID(task_id),
                   ReviewSession.role == role,
                   ReviewSession.reviewer_id == reviewer_id,
                   ReviewSession.finished_at.is_(None))
            .values(last_heartbeat_at=server_ts))
        await session.commit()
    return {"ok": True, "clock_drift": drift}


async def auto_suspend_stale_sessions(timeout_seconds: int = 5400):
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
    async with SessionLocal() as session:
        result = await session.execute(
            select(ReviewSession).where(
                ReviewSession.finished_at.is_(None),
                ReviewSession.last_heartbeat_at.is_not(None),
                ReviewSession.last_heartbeat_at < cutoff))
        suspended = []
        for rs in result.scalars():
            rs.auto_suspended_at = datetime.now(timezone.utc)
            suspended.append(str(rs.id))
        await session.commit()
        return {"suspended": suspended}


async def heartbeat_loop(interval_seconds: int = 60):
    while True:
        await auto_suspend_stale_sessions()
        await asyncio.sleep(interval_seconds)


async def effective_duration(review_session: ReviewSession) -> float:
    """扣挂起时长的实际操作时长（秒）。挂起期间不计入。"""
    if review_session.started_at is None or review_session.finished_at is None:
        return 0.0
    total = (review_session.finished_at - review_session.started_at).total_seconds()
    if review_session.auto_suspended_at is not None:
        suspended = (review_session.finished_at - review_session.auto_suspended_at).total_seconds()
        total = max(0.0, total - suspended)
    return total


async def detect_time_inconsistency(task_id: str, view_duration_sum: float):
    """finished-started 与 view_durations 累加偏差 >30% 即标记。"""
    async with SessionLocal() as session:
        result = await session.execute(
            select(ReviewSession).where(
                ReviewSession.task_id == uuid.UUID(task_id),
                ReviewSession.finished_at.is_not(None)))
        for rs in result.scalars():
            wall = (rs.finished_at - rs.started_at).total_seconds() if rs.started_at else 0.0
            if wall > 0 and abs(wall - view_duration_sum) / wall > TIME_INCONSISTENCY_THRESHOLD:
                rs.time_inconsistency_flag = True
        await session.commit()
