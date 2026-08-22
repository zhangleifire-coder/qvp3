import uuid
from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel
from src.db.session import SessionLocal
from src.models.entities import EntitySnapshot, Claim, Evidence

router = APIRouter()


class EntityIn(BaseModel):
    entity_type: str
    canonical_name: str
    version: str
    valid_from: datetime
    valid_until: datetime | None = None
    attributes: dict = {}


@router.post("/api/entities", status_code=201)
async def create_entity(payload: EntityIn):
    async with SessionLocal() as session:
        e = EntitySnapshot(**payload.model_dump())
        session.add(e)
        await session.commit()
        await session.refresh(e)
        return {"id": str(e.id), "canonical_name": e.canonical_name}


class ClaimIn(BaseModel):
    task_id: str
    claim_text: str
    risk_level: str
    position: int


class EvidenceIn(BaseModel):
    claim_id: str
    source_url: str
    source_level: str
    publish_date: str | None = None
    excerpt: str | None = None
    supports: bool


@router.post("/api/claims", status_code=201)
async def create_claim(payload: ClaimIn):
    async with SessionLocal() as session:
        c = Claim(
            task_id=uuid.UUID(payload.task_id),
            claim_text=payload.claim_text,
            risk_level=payload.risk_level,
            position=payload.position,
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return {"id": str(c.id)}


@router.post("/api/evidence", status_code=201)
async def create_evidence(payload: EvidenceIn):
    async with SessionLocal() as session:
        e = Evidence(
            claim_id=uuid.UUID(payload.claim_id),
            source_url=payload.source_url,
            source_level=payload.source_level,
            excerpt=payload.excerpt,
            supports=payload.supports,
        )
        session.add(e)
        await session.commit()
        return {"id": str(e.id)}
