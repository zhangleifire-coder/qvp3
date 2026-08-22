import pytest
from sqlalchemy import select
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.events import NodeEvent
from src.models.entities import EntitySnapshot, Claim, Evidence
from src.models.drafts import Draft, PageCopy, RuleResult
from src.models.assets import Asset, OcrResult, CrossCheck
from src.models.review import RiskClassification, ReviewSession, Batch, BatchMember, Issue, Approval
from src.models.snapshots import PublishSnapshot


@pytest.mark.asyncio
async def test_all_core_tables_readable():
    async with SessionLocal() as session:
        for model in [Task, NodeEvent, EntitySnapshot, Claim, Evidence,
                      Draft, PageCopy, RuleResult, Asset, OcrResult, CrossCheck,
                      RiskClassification, ReviewSession, Batch, BatchMember,
                      Issue, Approval, PublishSnapshot]:
            await session.execute(select(model).limit(1))
