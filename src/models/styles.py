"""风格关键词库模型（迁移 010）：风格 → 关键词/描述词 的可训练映射。"""
from sqlalchemy import Boolean, Column, Text, TIMESTAMP
from sqlalchemy.orm import declarative_base
from sqlalchemy.dialects.postgresql import UUID as PGUUID
import uuid
from datetime import datetime, timezone

Base = declarative_base()


class StyleKeyword(Base):
    __tablename__ = "style_keywords"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    style_name = Column(Text, nullable=False, unique=True)
    keywords = Column(Text, nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
