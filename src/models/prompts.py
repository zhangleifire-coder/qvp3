from sqlalchemy import Column, Text, Boolean, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone

from src.models.tasks import Base


class PromptTemplate(Base):
    """用户自定义提示词（系统默认提示词在 gateway/prompt_versions.py，不落库）。"""
    __tablename__ = "prompt_templates"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stage = Column(Text, nullable=False)          # draft_gen / page_split / image_gen
    mode = Column(Text)                           # general/single/compare；NULL = 环节通用
    name = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    owner_id = Column(UUID(as_uuid=True))  # NULL = 系统级默认覆盖（仅 admin 可写）
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
