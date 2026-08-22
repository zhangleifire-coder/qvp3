"""驳回重生成：驳回理由注入提示词 + 旧产物清理 + 全链重跑。"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func

from src.api.main import app
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.assets import Asset
from src.models.drafts import Draft, PageCopy
from src.models.events import NodeEvent
from src.models.review import ReviewSession, ReviewAction
from src.pipeline.orchestrator import run_pipeline

FAKE_DRAFT = {
    "text": "这是测试正文。" * 100,
    "model_version": "deepseek/deepseek-chat",
    "cost_cny": 0.01,
    "degraded": False,
}
REJECT_REASON = "第3页文案与配图不符，数据无来源"


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_task(status="draft") -> Task:
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"k-{_uniq()}", query=f"q-{_uniq()}",
                    content_type="generic", mode="general", status=status)
        session.add(task)
        await session.commit()
        return task


async def _reject(task_id, reason=REJECT_REASON):
    """模拟审核员驳回：完成的会话 + reject 动作 + 任务状态 rejected。"""
    async with SessionLocal() as session:
        rs = ReviewSession(task_id=task_id, role="A",
                           finished_at=datetime.now(timezone.utc))
        session.add(rs)
        await session.flush()
        session.add(ReviewAction(
            review_session_id=rs.id,
            idempotency_key=f"ra-{_uniq()}",
            action_type="reject",
            client_ts=datetime.now(timezone.utc),
            server_ts=datetime.now(timezone.utc),
            payload={"reason": reason}))
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        task.status = "rejected"
        await session.commit()


@pytest.mark.asyncio
async def test_regen_injects_feedback_and_reruns_all_nodes():
    """驳回后重跑：13 个节点全部重新执行，草稿提示词含驳回理由，产物不叠加。"""
    task = await _make_task()
    captured = []

    async def fake_llm(prompt, *a, **kw):
        captured.append(prompt)
        return FAKE_DRAFT

    with patch("src.pipeline.nodes.call_with_failover", side_effect=fake_llm):
        await run_pipeline(task.id)
    assert not any(REJECT_REASON in p for p in captured)

    await _reject(task.id)
    # 重试入口：清理上一轮产物（此处直接调清理，等同 retry 接口行为）
    from src.services.regen import clear_generated_content
    async with SessionLocal() as session:
        await clear_generated_content(session, task.id)
        await session.commit()
    async with SessionLocal() as session:
        for model in (Draft, PageCopy, Asset):
            n = (await session.execute(
                select(func.count()).select_from(model).where(
                    model.task_id == task.id))).scalar()
            assert n == 0, f"{model.__tablename__} 未清理"

    captured.clear()
    with patch("src.pipeline.nodes.call_with_failover", side_effect=fake_llm):
        results = await run_pipeline(task.id)
    # 全链重跑：没有任何节点被幂等跳过
    assert len(results) == 13
    assert not any(r["result"].get("skipped") for r in results)
    # 草稿提示词包含驳回理由
    assert any(REJECT_REASON in p for p in captured)
    # 产物重新生成且不叠加：6 页分页 + 6 张配图，草稿版本号递增
    async with SessionLocal() as session:
        pages = (await session.execute(
            select(func.count()).select_from(PageCopy).where(
                PageCopy.task_id == task.id))).scalar()
        assert pages == 6
        assets = (await session.execute(
            select(func.count()).select_from(Asset).where(
                Asset.task_id == task.id, Asset.source_type == "ai_generated"))).scalar()
        assert assets == 6
        draft = (await session.execute(
            select(Draft).where(Draft.task_id == task.id)
            .order_by(Draft.version.desc()))).scalars().first()
        # 旧产物已清理，草稿从 version 1 重新计数；prompt_version 标记 regen 轮次
        assert draft.version == 1
        assert "regen1" in draft.prompt_version


@pytest.mark.asyncio
async def test_retry_endpoint_clears_content_for_rejected_task():
    """retry 接口：驳回任务清理产物并重新入队；node_events 与驳回动作保留。"""
    task = await _make_task(status="review")
    async with SessionLocal() as session:
        session.add(Draft(task_id=task.id, version=1, body="旧正文",
                          model_version="m", prompt_version="p"))
        session.add(PageCopy(task_id=task.id, page_index=1, body="旧分页", claim_ids=[]))
        session.add(Asset(task_id=task.id, page_index=1, source_type="ai_generated",
                          copyright_status="clear", hash="h", image_url="x"))
        session.add(NodeEvent(task_id=task.id, node_name="draft_gen",
                              node_idempotency_key=f"ne-{_uniq()}",
                              enqueued_at=datetime.now(timezone.utc),
                              finished_at=datetime.now(timezone.utc)))
        # 一个未完成的会话（其他角色还没审）——应被清理，避免队列重复
        session.add(ReviewSession(task_id=task.id, role="B"))
        await session.commit()
    await _reject(task.id)

    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()):
        async with _client() as ac:
            resp = await ac.post(f"/api/tasks/{task.id}/retry?actor=tester")
    assert resp.status_code == 200

    async with SessionLocal() as session:
        assert (await session.execute(
            select(func.count()).select_from(Draft).where(Draft.task_id == task.id))).scalar() == 0
        assert (await session.execute(
            select(func.count()).select_from(PageCopy).where(PageCopy.task_id == task.id))).scalar() == 0
        assert (await session.execute(
            select(func.count()).select_from(Asset).where(Asset.task_id == task.id))).scalar() == 0
        # node_events（成本/审计）与驳回动作（反馈来源）保留
        assert (await session.execute(
            select(func.count()).select_from(NodeEvent).where(
                NodeEvent.task_id == task.id))).scalar() == 1
        actions = (await session.execute(
            select(ReviewAction).join(
                ReviewSession, ReviewAction.review_session_id == ReviewSession.id)
            .where(ReviewSession.task_id == task.id))).scalars().all()
        assert len(actions) == 1 and actions[0].action_type == "reject"
        # 未完成的会话已删，已完成的（驳回方）保留
        sessions = (await session.execute(
            select(ReviewSession).where(ReviewSession.task_id == task.id))).scalars().all()
        assert len(sessions) == 1 and sessions[0].finished_at is not None
        t = (await session.execute(select(Task).where(Task.id == task.id))).scalar_one()
        assert t.status == "draft"


@pytest.mark.asyncio
async def test_second_reject_same_reason_still_reruns():
    """同一理由二次驳回：驳回次数计入幂等键，仍会全链重跑。"""
    task = await _make_task()
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        await run_pipeline(task.id)
    await _reject(task.id, "理由不变")
    from src.services.regen import clear_generated_content
    async with SessionLocal() as session:
        await clear_generated_content(session, task.id)
        await session.commit()
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        r1 = await run_pipeline(task.id)
    assert not any(r["result"].get("skipped") for r in r1)
    # 第二次驳回（相同理由）→ 仍重跑
    await _reject(task.id, "理由不变")
    async with SessionLocal() as session:
        await clear_generated_content(session, task.id)
        await session.commit()
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_DRAFT):
        r2 = await run_pipeline(task.id)
    assert not any(r["result"].get("skipped") for r in r2)


# ---------- 定点驳回标记 + 定点重生成 ----------


async def _reject_with_marks(task_id, marks, reason=""):
    """通过审核接口驳回并携带定点标记（需先有该角色的未完成会话）。"""
    async with _client() as ac:
        r = await ac.post("/api/review/action", json={
            "task_id": str(task_id), "role": "A",
            "reviewer_id": f"tester-A-{_uniq()}",
            "action_type": "reject", "reason": reason, "marks": marks})
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_partial_regen_only_touches_marked_items():
    """定点重生成：只重做被标记的页文案/配图，其余产物原样保留。"""
    from src.models.review import RejectMark, RiskClassification
    from src.services.regen import partial_regen

    task = await _make_task()
    captured = []

    async def fake_llm(prompt, *a, **kw):
        captured.append(prompt)
        return FAKE_DRAFT

    with patch("src.pipeline.nodes.call_with_failover", side_effect=fake_llm):
        await run_pipeline(task.id)

    async with SessionLocal() as session:
        old_pages = {p.page_index: p.body for p in (await session.execute(
            select(PageCopy).where(PageCopy.task_id == task.id))).scalars().all()}
        old_assets = {a.page_index: a.id for a in (await session.execute(
            select(Asset).where(Asset.task_id == task.id,
                                Asset.source_type == "ai_generated"))).scalars().all()}
        draft_count = (await session.execute(
            select(func.count()).select_from(Draft).where(
                Draft.task_id == task.id))).scalar()
    assert len(old_pages) == 6 and len(old_assets) == 6

    # 标记：文案 P2「数据无来源」+ 配图 P3「图上有乱码」
    await _reject_with_marks(task.id, [
        {"item_type": "page", "page_index": 2, "reason": "数据无来源"},
        {"item_type": "image", "page_index": 3, "reason": "图上有乱码"},
    ])

    captured.clear()
    with patch("src.pipeline.nodes.call_with_failover", side_effect=fake_llm):
        result = await partial_regen(task.id)

    assert result["pages_rewritten"] == [2]
    assert result["images_regenerated"] == [2, 3]  # 文案重写的页连带重生图
    # 重写提示词携带驳回理由
    assert any("数据无来源" in p for p in captured)

    async with SessionLocal() as session:
        new_pages = {p.page_index: p.body for p in (await session.execute(
            select(PageCopy).where(PageCopy.task_id == task.id))).scalars().all()}
        new_assets = {a.page_index: a.id for a in (await session.execute(
            select(Asset).where(Asset.task_id == task.id,
                                Asset.source_type == "ai_generated"))).scalars().all()}
        # P2 文案被重写，其他页原样
        assert new_pages[2] != old_pages[2]
        for p in (1, 3, 4, 5, 6):
            assert new_pages[p] == old_pages[p]
        # P2/P3 图被替换，其他图原样（不重复消耗生图算力）
        assert new_assets[2] != old_assets[2]
        assert new_assets[3] != old_assets[3]
        for p in (1, 4, 5, 6):
            assert new_assets[p] == old_assets[p]
        # 草稿不动、页数/图数不叠加
        assert (await session.execute(
            select(func.count()).select_from(Draft).where(
                Draft.task_id == task.id))).scalar() == draft_count
        assert len(new_pages) == 6 and len(new_assets) == 6
        # 标记闭环
        marks = (await session.execute(
            select(RejectMark).where(RejectMark.task_id == task.id))).scalars().all()
        assert len(marks) == 2 and all(m.status == "resolved" for m in marks)
        # 风险分级重建为 1 条；审核会话重排为 3 个未完成
        assert (await session.execute(
            select(func.count()).select_from(RiskClassification).where(
                RiskClassification.task_id == task.id))).scalar() == 1
        sessions = (await session.execute(
            select(ReviewSession).where(ReviewSession.task_id == task.id,
                                        ReviewSession.finished_at.is_(None)))
        ).scalars().all()
        assert len(sessions) == 3


@pytest.mark.asyncio
async def test_retry_with_marks_enqueues_partial_without_clearing():
    """带标记的驳回任务重试：走 partial_regen 工种，不清理已有产物。"""
    from src.models.review import RejectMark

    task = await _make_task(status="review")
    async with SessionLocal() as session:
        session.add(Draft(task_id=task.id, version=1, body="正文",
                          model_version="m", prompt_version="p"))
        session.add(ReviewSession(task_id=task.id, role="A"))
        await session.commit()
    await _reject_with_marks(task.id, [
        {"item_type": "image", "page_index": 5, "reason": "风格不统一"}])

    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()) as mock_enqueue:
        async with _client() as ac:
            resp = await ac.post(f"/api/tasks/{task.id}/retry?actor=tester")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "partial_regen"
    _, kwargs = mock_enqueue.call_args
    assert kwargs.get("kind") == "partial_regen"
    async with SessionLocal() as session:
        # 产物未清理；标记仍 open（等 partial_regen 处理后闭环）
        assert (await session.execute(
            select(func.count()).select_from(Draft).where(
                Draft.task_id == task.id))).scalar() == 1
        assert (await session.execute(
            select(func.count()).select_from(RejectMark).where(
                RejectMark.task_id == task.id,
                RejectMark.status == "open"))).scalar() == 1


@pytest.mark.asyncio
async def test_reject_mark_validation():
    """非法标记（类型/页码越界、缺理由）被 400 拒绝。"""
    task = await _make_task(status="review")
    async with SessionLocal() as session:
        session.add(ReviewSession(task_id=task.id, role="A"))
        await session.commit()
    async with _client() as ac:
        r = await ac.post("/api/review/action", json={
            "task_id": str(task.id), "role": "A",
            "reviewer_id": f"tester-A-{_uniq()}",
            "action_type": "reject",
            "marks": [{"item_type": "image", "page_index": 9, "reason": "x"}]})
        assert r.status_code == 400
        r = await ac.post("/api/review/action", json={
            "task_id": str(task.id), "role": "A",
            "reviewer_id": f"tester-A-{_uniq()}",
            "action_type": "reject",
            "marks": [{"item_type": "page", "page_index": 2, "reason": "  "}]})
        assert r.status_code == 400
