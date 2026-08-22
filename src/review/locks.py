import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from src.db.session import SessionLocal
from src.models.review import ReviewSession
from src.review.users import get_or_create_user


async def acquire_lock(task_id: str, role: str, reviewer_name: str) -> dict:
    async with SessionLocal() as session:
        reviewer_id = await get_or_create_user(session, reviewer_name, role)
        result = await session.execute(
            select(ReviewSession).where(
                ReviewSession.task_id == uuid.UUID(task_id),
                ReviewSession.role == role,
                ReviewSession.finished_at.is_(None)))
        sessions = result.scalars().all()
        now = datetime.now(timezone.utc)
        for s in sessions:
            if s.locked_at and s.last_heartbeat_at:
                if (now - s.last_heartbeat_at).total_seconds() < 30:
                    from sqlalchemy import text
                    name = (await session.execute(
                        text("SELECT name FROM users WHERE id = :uid"),
                        {"uid": s.reviewer_id})).scalar()
                    return {"acquired": False, "locked_by": name or str(s.reviewer_id)}
        if sessions:
            for s in sessions:
                s.reviewer_id = reviewer_id
                s.locked_at = now
                s.last_heartbeat_at = now
                s.started_at = now
        else:
            session.add(ReviewSession(
                task_id=uuid.UUID(task_id), role=role,
                reviewer_id=reviewer_id,
                locked_at=now, last_heartbeat_at=now, started_at=now))
        await session.commit()
    return {"acquired": True}
