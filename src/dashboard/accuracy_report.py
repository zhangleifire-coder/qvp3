from sqlalchemy import select, func
from src.db.session import SessionLocal
from src.models.review import ReviewSession


async def accuracy_report():
    async with SessionLocal() as session:
        total = await session.execute(
            select(func.count(ReviewSession.id))
            .where(ReviewSession.finished_at.is_not(None)))
        incon = await session.execute(
            select(func.count(ReviewSession.id))
            .where(ReviewSession.finished_at.is_not(None),
                   ReviewSession.time_inconsistency_flag.is_(True)))
        anomaly = await session.execute(
            select(func.count(ReviewSession.id))
            .where(ReviewSession.finished_at.is_not(None),
                   ReviewSession.anomaly_flag.is_(True)))
        total_n = total.scalar() or 0
        incon_n = incon.scalar() or 0
        anomaly_n = anomaly.scalar() or 0
        return {
            "time_inconsistency_rate": (incon_n / total_n) if total_n > 0 else 0.0,
            "anomaly_rate": (anomaly_n / total_n) if total_n > 0 else 0.0,
            "total_reviewed": total_n,
            "inconsistency_flagged": incon_n,
            "anomaly_flagged": anomaly_n,
        }
