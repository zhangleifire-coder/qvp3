from sqlalchemy import Column, Integer, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
import uuid
from datetime import datetime, timezone

Base = declarative_base()


class Task(Base):
    __tablename__ = "tasks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key = Column(Text, unique=True, nullable=False)
    query = Column(Text, nullable=False)
    content_type = Column(Text, nullable=False)
    mode = Column(Text, nullable=False, default="general")
    platform = Column(Text)
    sla_hours = Column(Integer, nullable=False, default=24)
    priority = Column(Text, nullable=False, default="normal")
    status = Column(Text, nullable=False, default="draft")
    # 组合生成导入（007）：query=抽中的泛化问题，source_query=原始情境，风格/垂类注入创作提示词
    source_query = Column(Text)
    supplement_question = Column(Text)
    gen_style = Column(Text)
    gen_category = Column(Text)
    template_id = Column(UUID(as_uuid=True))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by = Column(UUID(as_uuid=True))
