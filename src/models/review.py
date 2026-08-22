from sqlalchemy import Column, Text, Boolean, Integer, Float, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from src.models.tasks import Base
import uuid
from datetime import datetime, timezone


class RiskClassification(Base):
    __tablename__ = "risk_classifications"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    level = Column(Text, nullable=False)
    reasons = Column(JSONB, nullable=False, default=[])
    classified_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ReviewSession(Base):
    __tablename__ = "review_sessions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(Text, nullable=False)
    reviewer_id = Column(UUID(as_uuid=True))
    locked_at = Column(TIMESTAMP(timezone=True))
    last_heartbeat_at = Column(TIMESTAMP(timezone=True))
    auto_suspended_at = Column(TIMESTAMP(timezone=True))
    started_at = Column(TIMESTAMP(timezone=True))
    finished_at = Column(TIMESTAMP(timezone=True))
    anomaly_flag = Column(Boolean, nullable=False, default=False)
    time_inconsistency_flag = Column(Boolean, nullable=False, default=False)


class ReviewAction(Base):
    __tablename__ = "review_actions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_session_id = Column(UUID(as_uuid=True), nullable=False)
    idempotency_key = Column(Text, unique=True, nullable=False)
    action_type = Column(Text, nullable=False)
    client_ts = Column(TIMESTAMP(timezone=True), nullable=False)
    server_ts = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    payload = Column(JSONB, nullable=False, default={})
    duration_ms = Column(Integer)


class Batch(Base):
    __tablename__ = "batches"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True))
    risk_level = Column(Text, nullable=False)
    sampling_rate = Column(Float, nullable=False, default=0.20)
    member_count = Column(Integer, nullable=False)
    signoff_status = Column(Text, nullable=False, default="pending")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    signed_at = Column(TIMESTAMP(timezone=True))


class BatchMember(Base):
    __tablename__ = "batch_members"
    batch_id = Column(UUID(as_uuid=True), primary_key=True)
    task_id = Column(UUID(as_uuid=True), primary_key=True)
    sampled = Column(Boolean, nullable=False, default=False)
    review_result = Column(Text)


class Issue(Base):
    __tablename__ = "issues"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(Text, nullable=False)
    priority = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="open")
    created_by = Column(UUID(as_uuid=True))
    closed_by = Column(UUID(as_uuid=True))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(TIMESTAMP(timezone=True))


class Approval(Base):
    __tablename__ = "approvals"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = Column(UUID(as_uuid=True))
    task_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(Text, nullable=False)
    approver_id = Column(UUID(as_uuid=True))
    conclusion = Column(Text, nullable=False)
    signed_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class RejectMark(Base):
    """定点驳回标记：审核员指出具体哪页文案/哪张图有问题（迁移 006）。

    重试时只重生成 status='open' 的标记项，其余内容保留；重生成完成后置 resolved。
    """
    __tablename__ = "reject_marks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(Text)
    item_type = Column(Text, nullable=False)      # page=分页文案 / image=交付配图
    page_index = Column(Integer, nullable=False)  # 1-6
    reason = Column(Text, nullable=False, default="")
    status = Column(Text, nullable=False, default="open")  # open / resolved
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(TIMESTAMP(timezone=True))
