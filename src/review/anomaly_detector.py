from sqlalchemy import select
from src.db.session import SessionLocal
from src.models.review import ReviewSession
from src.config import settings


async def flag_anomalies():
    async with SessionLocal() as session:
        result = await session.execute(
            select(ReviewSession).where(
                ReviewSession.started_at.is_not(None),
                ReviewSession.finished_at.is_not(None),
                ReviewSession.anomaly_flag.is_(False)))
        flagged_count = 0
        for rs in result.scalars():
            elapsed = (rs.finished_at - rs.started_at).total_seconds()
            if elapsed < settings.anomaly_min_seconds or elapsed > settings.anomaly_max_seconds:
                rs.anomaly_flag = True
                flagged_count += 1
        await session.commit()
    return {"flagged": flagged_count}
