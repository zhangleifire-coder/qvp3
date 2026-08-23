"""任务条目 删/改/查（关键词搜索）集成测试。"""
import uuid

import pytest
from sqlalchemy import func, select

from src.db.session import SessionLocal
from src.models.tasks import Task
from src.pipeline.orchestrator import run_pipeline


async def _create(mode="general", query=None):
    from src.stream.scheduler import scheduler
    q = query or f"crud-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"{q}|article||{mode}", query=q,
                    content_type="article", mode=mode, status="draft")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        await scheduler.enqueue(task.id, q)
        return task.id, q


async def test_list_keyword_search():
    from src.api.tasks import list_tasks
    tid, q = await _create(query=f"关键词搜索测试{uuid.uuid4().hex[:6]}怎么选")
    r = await list_tasks(q="关键词搜索测试")
    ids = [i["id"] for i in r["items"]]
    assert str(tid) in ids
    r2 = await list_tasks(q="绝对不存在的关键词xyz")
    assert str(tid) not in [i["id"] for i in r2["items"]]


async def test_patch_draft_task():
    from src.api.tasks import patch_task
    tid, q = await _create()
    r = await patch_task(str(tid), {"query": q + "改", "mode": "compare",
                                    "priority": "urgent"}, actor="张三")
    assert r["ok"]
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
        assert task.query.endswith("改") and task.mode == "compare"
        assert task.priority == "urgent"
        assert task.idempotency_key.endswith("|compare")


async def test_patch_blocked_for_processing_and_review():
    from src.api.tasks import patch_task
    tid, q = await _create()
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
        task.status = "processing"
        await session.commit()
    with pytest.raises(Exception):
        await patch_task(str(tid), {"query": q + "x"})
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
        task.status = "review"
        await session.commit()
    with pytest.raises(Exception):
        await patch_task(str(tid), {"query": q + "x"})


async def test_patch_idempotency_collision():
    from src.api.tasks import patch_task
    tid1, q1 = await _create(query=f"撞车甲{uuid.uuid4().hex[:6]}")
    tid2, q2 = await _create(query=f"撞车乙{uuid.uuid4().hex[:6]}")
    with pytest.raises(Exception):  # 把 tid2 改成与 tid1 相同 query → 409
        await patch_task(str(tid2), {"query": q1, "mode": "general",
                                     "content_type": "article"})


async def test_delete_task_cascades_all_children():
    from src.api.tasks import delete_task
    from src.models.assets import Asset, OcrResult
    from src.models.drafts import Draft, PageCopy
    from src.models.events import NodeEvent
    from src.models.review import ReviewSession
    tid, q = await _create()
    # 跑一遍 mock 流水线制造子表数据
    from unittest.mock import patch
    fake = {"text": "正文内容。" * 60, "model_version": "m", "cost_cny": 0,
            "degraded": False}
    with patch("src.pipeline.nodes.call_with_failover", return_value=fake):
        await run_pipeline(tid)
    async with SessionLocal() as session:
        for model in (Draft, PageCopy, Asset, NodeEvent, ReviewSession):
            n = (await session.execute(
                select(func.count()).select_from(model).where(
                    model.task_id == tid))).scalar()
            assert n > 0, model.__tablename__
        n_ocr = (await session.execute(
            select(func.count()).select_from(OcrResult).join(
                Asset, OcrResult.asset_id == Asset.id).where(
                Asset.task_id == tid))).scalar()
        assert n_ocr > 0
    r = await delete_task(str(tid), actor="张三")
    assert r["ok"]
    async with SessionLocal() as session:
        assert (await session.execute(
            select(func.count()).select_from(Task).where(Task.id == tid))).scalar() == 0
        for model in (Draft, PageCopy, Asset, NodeEvent, ReviewSession):
            n = (await session.execute(
                select(func.count()).select_from(model).where(
                    model.task_id == tid))).scalar()
            assert n == 0, model.__tablename__
        n_ocr = (await session.execute(
            select(func.count()).select_from(OcrResult).join(
                Asset, OcrResult.asset_id == Asset.id).where(
                Asset.task_id == tid))).scalar()
        assert n_ocr == 0


async def test_delete_processing_blocked():
    from src.api.tasks import delete_task
    tid, q = await _create()
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
        task.status = "processing"
        await session.commit()
    with pytest.raises(Exception):
        await delete_task(str(tid))
    async with SessionLocal() as session:
        assert (await session.execute(
            select(func.count()).select_from(Task).where(Task.id == tid))).scalar() == 1
