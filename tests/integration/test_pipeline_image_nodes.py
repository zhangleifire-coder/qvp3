import uuid
import pytest
from unittest.mock import patch
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.pipeline.orchestrator import run_pipeline

FAKE_DRAFT = {
    "text": "这是测试正文。" * 100,
    "model_version": "deepseek/deepseek-chat",
    "cost_cny": 0.01,
    "degraded": False,
}


@pytest.mark.asyncio
async def test_pipeline_runs_through_cross_check():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"img-{uuid.uuid4().hex[:8]}", query="测试", content_type="x")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        results = await run_pipeline(task_id)
    nodes = [r["node"] for r in results]
    assert "asset_gen" in nodes
    assert "ocr_read" in nodes
    assert "cross_check" in nodes


@pytest.mark.asyncio
async def test_ocr_read_writes_real_result_and_cost():
    """mock 网关下，ocr_read 应写入网关返回的文字与成本（非桩数据）。"""
    from src.models.assets import Asset, OcrResult
    from sqlalchemy import select
    from tests.conftest import FAKE_OCR
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"ocr-{uuid.uuid4().hex[:8]}", query="测试", content_type="x")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        await run_pipeline(task_id)
    async with SessionLocal() as session:
        ocr_texts = (await session.execute(
            select(OcrResult.raw_text)
            .join(Asset, OcrResult.asset_id == Asset.id)
            .where(Asset.task_id == task_id))).scalars().all()
    assert ocr_texts, "应至少有一条 OCR 结果"
    assert all(t == FAKE_OCR["raw_text"] for t in ocr_texts)
    assert not any(t.startswith("page ") for t in ocr_texts), "不应再是桩数据"
