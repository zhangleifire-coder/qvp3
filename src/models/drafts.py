from sqlalchemy import Column, Text, Integer, Boolean, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from src.models.tasks import Base
import uuid
from datetime import datetime, timezone


class Draft(Base):
    __tablename__ = "drafts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    version = Column(Integer, nullable=False)
    body = Column(Text, nullable=False)
    model_version = Column(Text, nullable=False)
    prompt_version = Column(Text, nullable=False)
    token_count = Column(Integer)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class PageCopy(Base):
    __tablename__ = "page_copies"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    page_index = Column(Integer, nullable=False)
    body = Column(Text, nullable=False)
    claim_ids = Column(JSONB, nullable=False, default=[])


class RuleResult(Base):
    __tablename__ = "rule_results"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    rule_name = Column(Text, nullable=False)
    passed = Column(Boolean, nullable=False)
    details = Column(JSONB, nullable=False, default={})
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

