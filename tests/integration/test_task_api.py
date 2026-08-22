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
from src.models.entities import Claim, Evidence
from src.models.events import NodeEvent
from src.models.review import (Approval, ReviewSession, ReviewAction,
                               RiskClassification, Issue)


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


async def _make_task(status="review", mode="compare", query=None) -> Task:
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"k-{_uniq()}", query=query or f"q-{_uniq()}",
                    content_type="generic", mode=mode, status=status)
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


# ---------- 审核回写任务状态 ----------

@pytest.mark.asyncio
async def test_single_approve_marks_approved():
    """单角色审核：任一角色通过即生效；其他角色未完成会话被清理出队列。"""
    task = await _make_task(status="review")
    tid = str(task.id)
    await _make_sessions(task.id)
    async with _client() as ac:
        r = await ac.post("/api/review/action", json={
            "task_id": tid, "role": "B", "reviewer_id": f"tester-B-{_uniq()}",
            "action_type": "approve"})
        assert r.json()["ok"] is True
    async with SessionLocal() as s:
        t = (await s.execute(select(Task).where(Task.id == task.id))).scalar_one()
        assert t.status == "approved"
        # 仅 B 的已完成会话保留（留痕），A/C 未完成会话被删除
        sessions = (await s.execute(
            select(ReviewSession).where(ReviewSession.task_id == task.id))).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].role == "B" and sessions[0].finished_at is not None
        approvals = (await s.execute(
            select(Approval).where(Approval.task_id == task.id))).scalars().all()
        assert len(approvals) == 1
        assert approvals[0].conclusion == "approve"
        assert approvals[0].role == "B"
        assert approvals[0].approver_id is not None


@pytest.mark.asyncio
async def test_reject_marks_rejected_and_writes_approval():
    task = await _make_task(status="review")
    tid = str(task.id)
    await _make_sessions(task.id)
    async with _client() as ac:
        r = await ac.post("/api/review/action", json={
            "task_id": tid, "role": "B", "reviewer_id": f"tester-{_uniq()}",
            "action_type": "reject", "reason": "事实错误"})
    assert r.json()["ok"] is True
    async with SessionLocal() as s:
        t = (await s.execute(select(Task).where(Task.id == task.id))).scalar_one()
        assert t.status == "rejected"
        approvals = (await s.execute(
            select(Approval).where(Approval.task_id == task.id))).scalars().all()
        assert len(approvals) == 1
        assert approvals[0].conclusion == "reject"
        assert approvals[0].role == "B"
        issues = (await s.execute(
            select(Issue).where(Issue.task_id == task.id))).scalars().all()
        assert len(issues) == 1 and issues[0].priority == "P1"


# ---------- 任务列表 ----------

@pytest.mark.asyncio
async def test_list_tasks_filter_and_pagination():
    t1 = await _make_task(status="review", mode="compare")
    await _make_task(status="failed", mode="compare")
    await _make_task(status="review", mode="single")
    async with SessionLocal() as s:
        s.add(RiskClassification(task_id=t1.id, level="red", reasons=["r"]))
        await s.commit()
    async with _client() as ac:
        all_resp = (await ac.get("/api/tasks")).json()
        assert all_resp["total"] == 3
        assert len(all_resp["items"]) == 3
        item = all_resp["items"][0]
        assert {"id", "query", "mode", "status", "risk_level", "current_node", "created_at"} <= set(item)

        by_status = (await ac.get("/api/tasks", params={"status": "review"})).json()
        assert by_status["total"] == 2
        assert all(i["status"] == "review" for i in by_status["items"])

        by_mode = (await ac.get("/api/tasks", params={"mode": "single"})).json()
        assert by_mode["total"] == 1

        by_risk = (await ac.get("/api/tasks", params={"risk_level": "red"})).json()
        assert by_risk["total"] == 1
        assert by_risk["items"][0]["id"] == str(t1.id)
        assert by_risk["items"][0]["risk_level"] == "red"

        page = (await ac.get("/api/tasks", params={"limit": 2, "offset": 2})).json()
        assert page["total"] == 3
        assert len(page["items"]) == 1


# ---------- 任务详情 ----------

@pytest.mark.asyncio
async def test_task_detail_includes_node_progress():
    task = await _make_task(status="processing")
    tid = str(task.id)
    now = datetime.now(timezone.utc)
    async with SessionLocal() as s:
        s.add(NodeEvent(task_id=task.id, node_name="task_import",
                        node_idempotency_key=f"e1-{_uniq()}", enqueued_at=now,
                        started_at=now, finished_at=now))
        s.add(NodeEvent(task_id=task.id, node_name="entity_bind",
                        node_idempotency_key=f"e2-{_uniq()}", enqueued_at=now,
                        started_at=now))
        s.add(Draft(task_id=task.id, version=1, body="正文", model_version="m1",
                    prompt_version="p1"))
        s.add(PageCopy(task_id=task.id, page_index=1, body="第一页"))
        s.add(Asset(task_id=task.id, page_index=1, source_type="licensed",
                    copyright_status="clear", hash="h1", image_url="https://x/1.png"))
        claim = Claim(task_id=task.id, claim_text="成立于1990年", risk_level="P2", position=1)
        s.add(claim)
        await s.flush()
        s.add(Evidence(claim_id=claim.id, source_url="https://src", source_level="P1",
                       excerpt="摘录", supports=True))
        s.add(RiskClassification(task_id=task.id, level="green", reasons=[]))
        s.add(ReviewSession(task_id=task.id, role="A"))
        await s.commit()
    async with _client() as ac:
        resp = await ac.get(f"/api/tasks/{tid}/detail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task"]["id"] == tid
    assert data["task"]["query"] == task.query
    assert data["task"]["status"] == "processing"
    assert data["completed_nodes"] == ["task_import"]
    assert data["current_node"] == "entity_bind"
    assert data["draft"] == {"body": "正文", "model_version": "m1", "prompt_version": "p1"}
    assert data["page_copies"] == [{"page_index": 1, "body": "第一页"}]
    assert data["assets"][0]["image_url"] == "https://x/1.png"
    assert data["claims"][0]["claim_text"] == "成立于1990年"
    assert data["evidences"][0]["source_url"] == "https://src"
    assert data["risk"]["level"] == "green"
    review = {r["role"]: r for r in data["review"]}
    assert set(review) == {"A", "B", "C"}
    assert review["A"]["status"] == "pending"
    assert review["B"]["status"] == "no_session"


@pytest.mark.asyncio
async def test_task_detail_404():
    async with _client() as ac:
        resp = await ac.get(f"/api/tasks/{uuid.uuid4()}/detail")
    assert resp.status_code == 404


# ---------- 重试 ----------

@pytest.mark.asyncio
async def test_retry_rejects_non_failed_status():
    task = await _make_task(status="review")
    async with _client() as ac:
        resp = await ac.post(f"/api/tasks/{task.id}/retry")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_retry_failed_task_reenqueues():
    task = await _make_task(status="failed")
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()) as mock_enqueue:
        async with _client() as ac:
            resp = await ac.post(f"/api/tasks/{task.id}/retry")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_enqueue.assert_awaited_once()
    async with SessionLocal() as s:
        t = (await s.execute(select(Task).where(Task.id == task.id))).scalar_one()
        assert t.status == "draft"


@pytest.mark.asyncio
async def test_retry_rejected_task_allowed():
    task = await _make_task(status="rejected")
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()):
        async with _client() as ac:
            resp = await ac.post(f"/api/tasks/{task.id}/retry")
    assert resp.status_code == 200


# ---------- 审核队列增强 ----------

@pytest.mark.asyncio
async def test_review_queue_returns_task_fields():
    task = await _make_task(status="review", mode="compare", query=f"拉萨八中-{_uniq()}")
    await _make_sessions(task.id, roles=("A",))
    async with SessionLocal() as s:
        s.add(RiskClassification(task_id=task.id, level="yellow", reasons=["x"]))
        await s.commit()
    async with _client() as ac:
        data = (await ac.get("/api/review/queue/A")).json()
    entry = next(e for e in data["sessions"] if e["task_id"] == str(task.id))
    assert entry["query"] == task.query
    assert entry["mode"] == "compare"
    assert entry["risk_level"] == "yellow"
    assert entry["created_at"]


# ---------- 节点元数据 ----------

@pytest.mark.asyncio
async def test_meta_nodes():
    async with _client() as ac:
        data = (await ac.get("/api/meta/nodes")).json()
    nodes = data["nodes"]
    assert len(nodes) == 13
    assert nodes[0] == {"name": "task_import", "label": "任务导入"}
    assert nodes[-1]["name"] == "publish_snapshot"
    assert all(n["label"] for n in nodes)


@pytest.mark.asyncio
async def test_export_approved_zip():
    """导出已通过内容包：ZIP 内含正文/分页/配图，配图被归一到目标尺寸。"""
    import io
    import zipfile
    from PIL import Image

    task = await _make_task(status="approved", query=f"导出测试-{_uniq()}")
    async with SessionLocal() as session:
        session.add(Draft(task_id=task.id, body="正文内容", version=1,
                          model_version="m", prompt_version="p"))
        session.add(PageCopy(task_id=task.id, page_index=1, body="第一页", claim_ids=[]))
        session.add(Asset(task_id=task.id, page_index=1, source_type="ai_generated",
                          hash="h1", image_url="https://example.com/p1.png",
                          copyright_status="clear",
                          model_version="gpt-image-2", is_illustration=False))
        await session.commit()

    # 造一张非目标尺寸的小图，验证导出时被归一化
    src = io.BytesIO()
    Image.new("RGB", (100, 100), (255, 0, 0)).save(src, format="PNG")
    small_png = src.getvalue()

    with patch("src.gateway.ocr.fetch_image_bytes",
               new=AsyncMock(return_value=(small_png, "image/png"))):
        async with _client() as client:
            resp = await client.get("/api/tasks/export_approved")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any(n.endswith("正文.txt") for n in names)
    assert any(n.endswith("分页文案/P1.txt") for n in names)
    img_name = next(n for n in names if n.endswith("图片/P1.png"))
    with zf.open(img_name) as f:
        img = Image.open(io.BytesIO(f.read()))
    assert img.size == (1152, 1536)


@pytest.mark.asyncio
async def test_export_approved_empty_404():
    async with _client() as client:
        resp = await client.get("/api/tasks/export_approved")
    assert resp.status_code in (200, 404)   # 视是否已有 approved 任务


@pytest.mark.asyncio
async def test_export_job_flow_with_progress():
    """任务式导出：start 启动后台打包 → 轮询进度 → 下载 ZIP。"""
    import asyncio
    import io as _io
    import zipfile

    task = await _make_task(status="approved", query=f"任务式导出-{_uniq()}")
    async with SessionLocal() as session:
        session.add(Draft(task_id=task.id, body="正文内容", version=1,
                          model_version="m", prompt_version="p"))
        session.add(PageCopy(task_id=task.id, page_index=1, body="第一页", claim_ids=[]))
        await session.commit()

    async with _client() as client:
        r = await client.post("/api/export/approved/start?actor=tester")
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        assert r.json()["total"] == 1
        s = {}
        for _ in range(50):
            s = (await client.get(f"/api/export/{job_id}")).json()
            if s["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.1)
        assert s["status"] == "done", s
        assert s["done"] == 1 and s["total"] == 1
        assert len(s["parts"]) == 1 and s["parts"][0]["tasks"] == 1
        d = await client.get(f"/api/export/{job_id}/download/1")
        assert d.status_code == 200
        zf = zipfile.ZipFile(_io.BytesIO(d.content))
        names = zf.namelist()
        assert any(n.endswith("正文.txt") for n in names)
        assert any(n.endswith("分页文案/P1.txt") for n in names)


@pytest.mark.asyncio
async def test_export_job_splits_every_10_tasks():
    """每 10 条任务分一个包：11 条 → 2 包（10 + 1），可逐包下载。"""
    import asyncio
    import io as _io
    import zipfile

    async with SessionLocal() as session:
        for k in range(11):
            task = Task(idempotency_key=f"k-{_uniq()}", query=f"分包测试{k}-{_uniq()}",
                        content_type="generic", mode="general", status="approved")
            session.add(task)
            await session.flush()
            session.add(Draft(task_id=task.id, body=f"正文{k}", version=1,
                              model_version="m", prompt_version="p"))
        await session.commit()

    async with _client() as client:
        r = await client.post("/api/export/approved/start?actor=tester")
        job_id = r.json()["job_id"]
        assert r.json()["total"] == 11
        s = {}
        for _ in range(50):
            s = (await client.get(f"/api/export/{job_id}")).json()
            if s["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.1)
        assert s["status"] == "done", s
        assert [p["tasks"] for p in s["parts"]] == [10, 1]
        # 逐包下载：第 1 包 10 条目录，第 2 包 1 条目录
        for part, expect_dirs in ((1, 10), (2, 1)):
            d = await client.get(f"/api/export/{job_id}/download/{part}")
            assert d.status_code == 200
            zf = zipfile.ZipFile(_io.BytesIO(d.content))
            dirs = {n.split("/")[0] for n in zf.namelist() if "/" in n}
            assert len(dirs) == expect_dirs
        # 越界包号 400
        d = await client.get(f"/api/export/{job_id}/download/3")
        assert d.status_code == 400


@pytest.mark.asyncio
async def test_export_job_start_empty_404():
    async with _client() as client:
        r = await client.post("/api/export/approved/start")
    assert r.status_code == 404
