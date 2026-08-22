import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.events import NodeEvent
from src.models.tasks import Task
from src.pipeline.idempotency import check_or_record_node_event, compute_node_key


async def _new_task(session) -> uuid.UUID:
    task = Task(idempotency_key=f"idem-{uuid.uuid4().hex[:8]}", query="测试", content_type="x")
    session.add(task)
    await session.flush()
    return task.id


def test_compute_node_key_stable():
    payload = {"x": 1, "y": "abc"}
    k1 = compute_node_key("task-1", "node_a", payload)
    k2 = compute_node_key("task-1", "node_a", payload)
    assert k1 == k2


def test_compute_node_key_changes_with_payload():
    k1 = compute_node_key("task-1", "node_a", {"x": 1})
    k2 = compute_node_key("task-1", "node_a", {"x": 2})
    assert k1 != k2


def test_compute_node_key_format():
    k = compute_node_key("task-1", "node_a", {})
    assert len(k) == 64  # sha256 hex


@pytest.mark.asyncio
async def test_failed_node_is_not_skipped_on_retry():
    """失败节点（error_class 非空）重跑时必须重新执行，不能被幂等跳过。"""
    task_id = None
    async with SessionLocal() as session:
        task_id = await _new_task(session)
        payload = {"task_id": str(task_id)}
        ev = await check_or_record_node_event(session, task_id, "asset_gen", payload)
        ev.started_at = datetime.now(timezone.utc)
        ev.finished_at = datetime.now(timezone.utc)
        ev.error_class = "RuntimeError"   # 模拟失败
        await session.commit()
    async with SessionLocal() as session:
        ev2 = await check_or_record_node_event(session, task_id, "asset_gen", payload)
        assert ev2 is not None, "失败节点应重新执行而不是跳过"
        assert ev2.error_class is None


@pytest.mark.asyncio
async def test_succeeded_node_is_skipped_on_retry():
    """成功节点重跑时幂等跳过。"""
    task_id = None
    async with SessionLocal() as session:
        task_id = await _new_task(session)
        payload = {"task_id": str(task_id)}
        ev = await check_or_record_node_event(session, task_id, "asset_gen", payload)
        ev.started_at = datetime.now(timezone.utc)
        ev.finished_at = datetime.now(timezone.utc)
        await session.commit()
    async with SessionLocal() as session:
        assert await check_or_record_node_event(session, task_id, "asset_gen", payload) is None
        count = (await session.execute(
            select(NodeEvent).where(NodeEvent.task_id == task_id))).scalars().all()
        assert len(count) == 1
