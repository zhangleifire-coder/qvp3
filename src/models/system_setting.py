"""系统参数模型（迁移 018）：key-value，web 系统参数页读写。"""
from sqlalchemy import Column, Text, TIMESTAMP
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)
    updated_by = Column(Text)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
