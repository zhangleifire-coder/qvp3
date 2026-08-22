import random
import uuid
from src.db.session import SessionLocal
from src.models.review import Batch, BatchMember


async def create_batch(task_ids: list, sampling_rate: float = 0.20):
    sampled_count = max(1, round(len(task_ids) * sampling_rate))
    sampled = set(random.sample(task_ids, sampled_count))
    async with SessionLocal() as session:
        batch = Batch(risk_level="green", sampling_rate=sampling_rate,
                      member_count=len(task_ids))
        session.add(batch)
        await session.commit()
        for tid in task_ids:
            session.add(BatchMember(
                batch_id=batch.id, task_id=uuid.UUID(str(tid)),
                sampled=(str(tid) in {str(s) for s in sampled}),
                review_result="pending"))
        await session.commit()
    return {"batch_id": str(batch.id), "member_count": len(task_ids),
            "sampled_count": len(sampled)}
