from sqlalchemy import Column, Integer, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import JSONB
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
    gen_image_style = Column(Text)   # Agent/直连路径选定的图片整体视觉风格名
    # 015：选定时的风格描述词快照——风格库后续编辑/删除不影响本任务重生成同风格
    gen_image_style_desc = Column(Text)
    # 016：asset_gen 前 LLM 从 6 页文案提取的每页画面主体（JSON 数组 6 字符串），
    # 注入生图提示词替换通用主体锚定条款；NULL=未提取/提取失败
    page_subjects = Column(JSONB)
    # 027（v0.1.4 P2）：结构化分页文案快照（page_schema.PageSpec 数组：
    # title/subtitle/points/subject/info_task）——compose 直连消费；
    # NULL=旧任务/机械切割回退，compose 走 spec_from_plain 兼容层
    page_specs = Column(JSONB)
    # 文字自查+人工核查（012）：自动自查草稿 / 人工修改后的最终版
    text_review = Column(JSONB)
    text_override = Column(JSONB)
    # 文字自查+人工核查（012）：自动自查草稿 / 人工修改后的最终版
    text_review = Column(JSONB)
    text_override = Column(JSONB)
    template_id = Column(UUID(as_uuid=True))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by = Column(UUID(as_uuid=True))
