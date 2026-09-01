# 驳回即自动重生成（2026-09-02）：审核驳回落库后直接入队重做，不再二次确认。
# 分支：带定点标记→partial_regen；无标记→清产物全链；auto_regen 失败不影响
# 审核结论；非 rejected 状态跳过（防误触）。
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.review import ReviewSession, RejectMark
from src.models.drafts import Draft, PageCopy


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


async def _mk_rejected_task(with_marks: bool):
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"ar-{_uniq()}", query="测试驳回重做",
                 content_type="x", mode="general", status="review")
        s.add(t); await s.commit(); tid = t.id
        s.add(ReviewSession(task_id=tid, role="A"))
        s.add(Draft(task_id=tid, version=1, body="旧正文", model_version="m",
                    prompt_version="p"))
        for i in (1, 2):
            s.add(PageCopy(task_id=tid, page_index=i, body=f"旧P{i}", claim_ids=[]))
        if with_marks:
            s.add(RejectMark(task_id=tid, role="A", item_type="image",
                             page_index=2, reason="构图重复"))
        await s.commit()
    return tid


@pytest.mark.asyncio
async def test_reject_with_marks_auto_partial_regen():
    """带标记驳回 → 自动入队 partial_regen，产物保留（只重做标记项）。"""
    from src.api.review import action, ActionIn
    tid = await _mk_rejected_task(with_marks=True)
    try:
        with patch("src.stream.scheduler.scheduler.enqueue",
                   new=AsyncMock()) as enq:
            r = await action(ActionIn(task_id=str(tid), role="A",
                                      reviewer_id="审甲", action_type="reject",
                                      reason="", marks=[
                                          {"item_type": "image", "page_index": 2,
                                           "reason": "构图重复"}]))
        assert r["ok"] and r["auto_regen"]["ok"]
        assert r["auto_regen"]["kind"] == "partial_regen"
        assert r["auto_regen"]["marks"] == 2   # 预置1条+本次标记1条
        assert enq.await_count == 1
        kwargs = enq.await_args.kwargs
        assert kwargs.get("kind") == "partial_regen"
        # 产物保留（定点模式不清产物）
        async with SessionLocal() as s:
            n = len((await s.execute(select(PageCopy).where(
                PageCopy.task_id == tid))).scalars().all())
        assert n == 2
        async with SessionLocal() as s:
            st = (await s.execute(select(Task.status).where(
                Task.id == tid))).scalar()
        assert st == "draft"   # 已入重生成流程
    finally:
        async with SessionLocal() as s:
            from sqlalchemy import delete
            for M in (RejectMark, PageCopy, Draft, ReviewSession):
                await s.execute(delete(M).where(M.task_id == tid))
            await s.execute(delete(Task).where(Task.id == tid))
            await s.commit()


@pytest.mark.asyncio
async def test_reject_without_marks_full_regen():
    """无标记驳回 → 清空旧产物，自动入队 pipeline（全链重跑）。"""
    from src.api.review import action, ActionIn
    tid = await _mk_rejected_task(with_marks=False)
    try:
        with patch("src.stream.scheduler.scheduler.enqueue",
                   new=AsyncMock()) as enq:
            r = await action(ActionIn(task_id=str(tid), role="A",
                                      reviewer_id="审甲", action_type="reject",
                                      reason="整体质量差", marks=[]))
        assert r["auto_regen"]["kind"] == "pipeline"
        assert enq.await_args.kwargs.get("kind") == "pipeline"
        async with SessionLocal() as s:
            n = len((await s.execute(select(PageCopy).where(
                PageCopy.task_id == tid))).scalars().all())
        assert n == 0   # 旧产物已清理
    finally:
        async with SessionLocal() as s:
            from sqlalchemy import delete
            for M in (RejectMark, PageCopy, Draft, ReviewSession):
                await s.execute(delete(M).where(M.task_id == tid))
            await s.execute(delete(Task).where(Task.id == tid))
            await s.commit()


@pytest.mark.asyncio
async def test_enqueue_failure_keeps_review_result():
    """自动入队失败 → 审核结论不受影响（任务 rejected，可手动重试）。"""
    from src.api.review import action, ActionIn
    tid = await _mk_rejected_task(with_marks=False)
    try:
        with patch("src.stream.scheduler.scheduler.enqueue",
                   new=AsyncMock(side_effect=RuntimeError("queue down"))):
            r = await action(ActionIn(task_id=str(tid), role="A",
                                      reviewer_id="审甲", action_type="reject",
                                      reason="x", marks=[]))
        assert r["ok"] is True            # 审核动作本身成功
        assert r["auto_regen"] is None    # 自动重生成失败被吞并留痕
        async with SessionLocal() as s:
            st = (await s.execute(select(Task.status).where(
                Task.id == tid))).scalar()
        assert st == "rejected"           # 保持可手动重试
    finally:
        async with SessionLocal() as s:
            from sqlalchemy import delete
            for M in (RejectMark, PageCopy, Draft, ReviewSession):
                await s.execute(delete(M).where(M.task_id == tid))
            await s.execute(delete(Task).where(Task.id == tid))
            await s.commit()


@pytest.mark.asyncio
async def test_auto_regen_skips_non_rejected():
    from src.services.regen import auto_regen_after_reject
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"nr-{_uniq()}", query="q", content_type="x",
                 mode="general", status="review")
        s.add(t); await s.commit(); tid = t.id
    try:
        r = await auto_regen_after_reject(tid)
        assert r["ok"] is False and "rejected" in r["reason"]
    finally:
        async with SessionLocal() as s:
            await s.execute(Task.__table__.delete().where(Task.id == tid))
            await s.commit()
