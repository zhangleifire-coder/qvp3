from sqlalchemy import Column, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone

from src.models.tasks import Base


class ActivityLog(Base):
    """用户操作审计日志（005 迁移）：动作 + 内容，永久保留。"""
    __tablename__ = "activity_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True))          # 可空：登录失败等场景
    actor_name = Column(Text, nullable=False)     # 冗余用户名，账号删除后日志仍可读
    action = Column(Text, nullable=False)
    detail = Column(Text)
    task_id = Column(UUID(as_uuid=True))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
