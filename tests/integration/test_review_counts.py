# 人审关卡菜单徽标口径回归：review 徽标必须与审核队列同源
# （未完成 ReviewSession 的去重任务数），杜绝「徽标有数、队列全空」错位。
import uuid

import pytest
from httpx import AsyncClient, ASGITransport

from src.api.main import app
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.review import ReviewSession


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


async def _make_task(status="review") -> Task:
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"k-{_uniq()}", query=f"q-{_uniq()}",
                    content_type="generic", mode="general", status=status)
        session.add(task)
        await session.commit()
        return task


async def _make_sessions(task_id, roles=("A", "B", "C")):
    async with SessionLocal() as session:
        for role in roles:
            session.add(ReviewSession(task_id=task_id, role=role))
        await session.commit()


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _counts():
    async with _client() as ac:
        r = await ac.get("/api/meta/review_counts")
        assert r.status_code == 200
        return r.json()


@pytest.mark.asyncio
async def test_badge_counts_pending_sessions_distinct_by_task():
    """同一任务 A/B/C 三个会话只计 1；分角色计数各为 1。"""
    task = await _make_task(status="review")
    await _make_sessions(task.id)
    counts = await _counts()
    assert counts["review"] == 1
    assert counts["review_by_role"] == {"A": 1, "B": 1, "C": 1}


@pytest.mark.asyncio
async def test_badge_ignores_orphan_review_status():
    """孤儿场景（status=review 但无未完成会话）：徽标必须为 0，不得虚报。"""
    await _make_task(status="review")  # 不建 ReviewSession
    counts = await _counts()
    assert counts["review"] == 0
    assert counts["review_by_role"] == {"A": 0, "B": 0, "C": 0}


@pytest.mark.asyncio
async def test_badge_resets_after_action():
    """审核定案（approve 删除未完成会话）后徽标立即归零。"""
    task = await _make_task(status="review")
    await _make_sessions(task.id)
    async with _client() as ac:
        r = await ac.post("/api/review/action", json={
            "task_id": str(task.id), "role": "A",
            "reviewer_id": f"tester-{_uniq()}", "action_type": "approve"})
        assert r.json()["ok"] is True
    counts = await _counts()
    assert counts["review"] == 0
    assert counts["review_by_role"] == {"A": 0, "B": 0, "C": 0}


@pytest.mark.asyncio
async def test_badge_partial_roles():
    """会话只存在于部分角色时：总数按任务去重，分角色各计各的。"""
    t1 = await _make_task(status="review")
    t2 = await _make_task(status="review")
    await _make_sessions(t1.id, roles=("A", "B", "C"))
    await _make_sessions(t2.id, roles=("B",))  # B 已领走 A/C 之外的单角色场景
    counts = await _counts()
    assert counts["review"] == 2
    assert counts["review_by_role"]["A"] == 1
    assert counts["review_by_role"]["B"] == 2
    assert counts["review_by_role"]["C"] == 1
