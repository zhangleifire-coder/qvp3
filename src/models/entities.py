from sqlalchemy import Column, Text, TIMESTAMP, Date, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from src.models.tasks import Base
import uuid
from datetime import datetime, timezone


class EntitySnapshot(Base):
    __tablename__ = "entity_snapshots"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(Text, nullable=False)
    canonical_name = Column(Text, nullable=False)
    version = Column(Text, nullable=False)
    valid_from = Column(TIMESTAMP(timezone=True), nullable=False)
    valid_until = Column(TIMESTAMP(timezone=True))
    attributes = Column(JSONB, nullable=False, default={})
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class Claim(Base):
    __tablename__ = "claims"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    claim_text = Column(Text, nullable=False)
    risk_level = Column(Text, nullable=False)
    verification_status = Column(Text, nullable=False, default="pending")
    position = Column(Integer, nullable=False)


class Evidence(Base):
    __tablename__ = "evidence"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id = Column(UUID(as_uuid=True), nullable=False)
    source_url = Column(Text, nullable=False)
    source_level = Column(Text, nullable=False)
    publish_date = Column(Date)
    captured_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    excerpt = Column(Text)
    supports = Column(Boolean, nullable=False)
