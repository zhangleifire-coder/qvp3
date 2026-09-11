"""风格关键词库模型（迁移 010）：风格 → 关键词/描述词 的可训练映射。
迁移 023 起条目带 use_when/pitfalls/source 结构化字段（WS1）。"""
from sqlalchemy import Boolean, Column, Text, TIMESTAMP
from sqlalchemy.orm import declarative_base
from sqlalchemy.dialects.postgresql import UUID as PGUUID
import uuid
from datetime import datetime, timezone

Base = declarative_base()


class StyleKeyword(Base):
    __tablename__ = "style_keywords"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 两级库（迁移016）：NULL=admin 公共库，非空=个人库；
    # 同名唯一按 owner 作用域由 DB 两个部分唯一索引保证，ORM 层不再全局 unique
    owner_id = Column(PGUUID(as_uuid=True))
    style_name = Column(Text, nullable=False)
    keywords = Column(Text, nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    # 迁移 023（WS1）：use_when=适用题材条件（喂选型打分，权重减半）；
    # pitfalls=风格专属避坑（喂提示词，措辞纪律 ≤3 条、单条 ≤30 字、正向优先）；
    # source=条目来源（manual/import_20260909/seed_019/oss_distilled）
    use_when = Column(Text, nullable=False, default="")
    pitfalls = Column(Text, nullable=False, default="")
    source = Column(Text, nullable=False, default="manual")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
