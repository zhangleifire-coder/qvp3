from sqlalchemy import Column, Text, Integer, Boolean, Float, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from src.models.tasks import Base
import uuid
from datetime import datetime, timezone


class Asset(Base):
    __tablename__ = "assets"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    page_index = Column(Integer, nullable=False)
    subject = Column(Text)
    source_type = Column(Text, nullable=False)
    copyright_status = Column(Text, nullable=False)
    license_scope = Column(Text)
    hash = Column(Text, nullable=False)
    image_url = Column(Text)
    origin_url = Column(Text)  # 上游原始地址（本地持久化前的来源，用于版权追溯）
    model_version = Column(Text)
    is_illustration = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class OcrResult(Base):
    __tablename__ = "ocr_results"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(UUID(as_uuid=True), nullable=False)
    raw_text = Column(Text, nullable=False)
    key_fields = Column(JSONB, nullable=False, default={})
    confidence = Column(Float, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class CrossCheck(Base):
    __tablename__ = "cross_checks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    field_name = Column(Text, nullable=False)
    expected = Column(Text, nullable=False)
    actual = Column(Text, nullable=False)
    matched = Column(Boolean, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
