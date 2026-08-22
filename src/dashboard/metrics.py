from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.events import NodeEvent
from src.models.review import ReviewSession, RiskClassification


async def throughput_last_hour():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    async with SessionLocal() as session:
        result = await session.execute(
            select(func.count(Task.id)).where(Task.created_at >= cutoff))
        return result.scalar() or 0


async def first_pass_rate_last_24h():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    async with SessionLocal() as session:
        total = await session.execute(
            select(func.count(Task.id)).where(Task.created_at >= cutoff))
        green = await session.execute(
            select(func.count(RiskClassification.id))
            .join(Task, RiskClassification.task_id == Task.id)
            .where(Task.created_at >= cutoff, RiskClassification.level == "green"))
        total_n = total.scalar() or 0
        green_n = green.scalar() or 0
        return green_n / total_n if total_n > 0 else 0.0


async def human_touch_rate_last_24h():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    async with SessionLocal() as session:
        total = await session.execute(
            select(func.count(Task.id)).where(Task.created_at >= cutoff))
        reviewed = await session.execute(
            select(func.count(func.distinct(ReviewSession.task_id)))
            .where(ReviewSession.started_at >= cutoff))
        total_n = total.scalar() or 0
        rev_n = reviewed.scalar() or 0
        return rev_n / total_n if total_n > 0 else 0.0


async def p95_node_duration():
    async with SessionLocal() as session:
        result = await session.execute(
            select(func.extract("epoch", NodeEvent.finished_at - NodeEvent.started_at))
            .where(NodeEvent.started_at.is_not(None),
                   NodeEvent.finished_at.is_not(None),
                   NodeEvent.anomaly_flag.is_(False)))
        durations = [r[0] for r in result if r[0] is not None]
        if not durations:
            return 0
        durations.sort()
        idx = int(len(durations) * 0.95)
        return durations[min(idx, len(durations) - 1)]


async def cost_per_task_24h():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    async with SessionLocal() as session:
        total_cost = await session.execute(
            select(func.sum(NodeEvent.cost_estimate_cny))
            .where(NodeEvent.started_at >= cutoff))
        total_n = await session.execute(
            select(func.count(Task.id)).where(Task.created_at >= cutoff))
        cost = total_cost.scalar() or 0
        n = total_n.scalar() or 0
        return float(cost) / n if n > 0 else 0.0


async def queue_depth():
    async with SessionLocal() as session:
        result = await session.execute(
            select(ReviewSession.role, func.count(ReviewSession.id))
            .where(ReviewSession.finished_at.is_(None))
            .group_by(ReviewSession.role))
        return {role: count for role, count in result}


async def error_top_n(n: int = 5):
    async with SessionLocal() as session:
        result = await session.execute(
            select(NodeEvent.error_class, func.count(NodeEvent.id))
            .where(NodeEvent.error_class.is_not(None))
            .group_by(NodeEvent.error_class)
            .order_by(func.count(NodeEvent.id).desc())
            .limit(n))
        return [{"error_class": ec, "count": c} for ec, c in result]


async def all_metrics():
    return {
        "throughput_per_hour": await throughput_last_hour(),
        "first_pass_rate_24h": await first_pass_rate_last_24h(),
        "human_touch_rate_24h": await human_touch_rate_last_24h(),
        "p95_node_seconds": await p95_node_duration(),
        "cost_per_task_24h_cny": await cost_per_task_24h(),
        "queue_depth": await queue_depth(),
        "error_top_5": await error_top_n(5),
    }
