"""staged 分阶段 Agent 路径单测：各阶段契约校验 + 阶段间 DB 数据流报错语义
+ 开关分发（不依赖外部服务）。"""
import uuid

import pytest
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.tasks import Task
from src.pipeline import agent_stages as st


# ── 各阶段契约校验 ─────────────────────────────────────────────


def test_validate_evidence_ok():
    out, errors = st._validate_evidence({
        "evidence": [{"title": "t", "url": "https://a.com", "summary": "s"}],
        "content_style": "避坑指南", "image_style": "真实摄影", "notes": "n"})
    assert errors == []
    assert out["content_style"] == "避坑指南"
    assert out["evidence"][0]["url"] == "https://a.com"


def test_validate_evidence_requires_styles():
    out, errors = st._validate_evidence({"evidence": []})
    assert out is None
    assert any("content_style" in e for e in errors)
    assert any("image_style" in e for e in errors)


def test_validate_evidence_empty_allowed():
    out, errors = st._validate_evidence(
        {"evidence": [], "content_style": "攻略教程", "image_style": "真实摄影"})
    assert errors == [] and out["evidence"] == []


def test_validate_draft_too_short():
    out, errors = st._validate_draft({"draft": "太短"})
    assert out is None and any("draft" in e for e in errors)


def test_validate_draft_ok():
    out, errors = st._validate_draft({"draft": "正文" * 100, "notes": "x"})
    assert errors == [] and len(out["draft"]) >= 150


def test_validate_pages_exactly_six():
    out, errors = st._validate_pages({"pages": ["a", "b", "c"]})
    assert out is None and any("6" in e for e in errors)
    out, errors = st._validate_pages({"pages": ["p", "p", "p", "p", "p", " "]})
    assert out is None  # 空串页不合格
    out, errors = st._validate_pages({"pages": [f"第{i}页" for i in range(1, 7)]})
    assert errors == [] and len(out["pages"]) == 6


def test_validate_assets_images_contract():
    out, errors = st._validate_assets({"images": [{"page_index": 1}] * 6})
    assert out is None and any("image_url" in e for e in errors)
    out, errors = st._validate_assets({"images": []})
    assert out is None and any("6" in e for e in errors)
    out, errors = st._validate_assets({
        "images": [{"page_index": i, "image_url": f"/static/generated/p{i}.png"}
                   for i in range(1, 7)],
        "references": [{"image_url": "https://r.com/1", "title": "r", "engine": "bing"}],
        "ocr_texts": [{"page_index": 1, "text": "第1页"}]})
    assert errors == []
    assert len(out["images"]) == 6
    assert out["ocr_map"] == {1: "第1页"}
    assert out["references"][0]["engine"] == "bing"


# ── 阶段间 DB 数据流（缺上一阶段产物的报错语义）─────────────────


async def _task_without_draft():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"staged-u-{uuid.uuid4().hex[:8]}",
                    query="单测Query", content_type="x", mode="general")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


async def test_pages_stage_requires_draft():
    task_id = await _task_without_draft()
    with pytest.raises(RuntimeError, match="缺少上一阶段正文"):
        await st.node_agent_pages({"task_id": task_id})


async def test_assets_stage_requires_pages():
    task_id = await _task_without_draft()
    with pytest.raises(RuntimeError, match="缺少上一阶段分页文案"):
        await st.node_agent_assets({"task_id": task_id})


# ── 开关分发 ─────────────────────────────────────────────────


def test_variant_default_is_monolith():
    from src.config import settings
    # 发版安全默认：不显式配置时不得走 staged
    assert settings.agent_pipeline_variant == "monolith"


def test_orchestrator_dispatch(monkeypatch):
    from src.config import settings
    from src.pipeline import orchestrator as oc
    monkeypatch.setattr(settings, "agent_pipeline_enabled", True)
    monkeypatch.setattr(settings, "agent_pipeline_variant", "staged")
    assert len(oc.NODES_AGENT_STAGED) == 14
    # staged 与 monolith 的差异仅在创作段 4 节点替换大节点
    assert [n for n in oc.NODES_AGENT_STAGED if n in oc.NODES_AGENT] == [
        n for n in oc.NODES_AGENT if n != "agent_production"]
    for stage in ("agent_evidence", "agent_draft", "agent_pages", "agent_assets"):
        assert stage in oc.NODE_FN_AGENT_STAGED
    # staged 节点表与函数表对齐（task_import 无函数=事件簿记节点，与 monolith 同）
    assert set(oc.NODES_AGENT_STAGED) - {"task_import"} == set(oc.NODE_FN_AGENT_STAGED)
