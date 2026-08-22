from sqlalchemy import Column, Integer, Numeric, Text, Boolean, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from src.models.tasks import Base
import uuid
from datetime import datetime


class NodeEvent(Base):
    __tablename__ = "node_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    node_name = Column(Text, nullable=False)
    node_idempotency_key = Column(Text, nullable=False)
    enqueued_at = Column(TIMESTAMP(timezone=True), nullable=False)
    started_at = Column(TIMESTAMP(timezone=True))
    finished_at = Column(TIMESTAMP(timezone=True))
    model_version = Column(Text)
    prompt_version = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)
    cost_estimate_cny = Column(Numeric(10, 4))
    error_class = Column(Text)
    anomaly_flag = Column(Boolean, nullable=False, default=False)
