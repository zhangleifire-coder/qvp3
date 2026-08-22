"""成本明细接口 /api/admin/costs：按任务×环节拆分 + 多维汇总。"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from src.api.main import app
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.events import NodeEvent


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_task_with_costs():
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"k-{_uniq()}", query=f"q-{_uniq()}",
                    content_type="generic", mode="compare", status="review")
        session.add(task)
        await session.flush()
        now = datetime.now(timezone.utc)
        session.add_all([
            NodeEvent(task_id=task.id, node_name="draft_gen",
                      node_idempotency_key=f"ne-{_uniq()}",
                      enqueued_at=now, finished_at=now,
                      cost_estimate_cny=0.02, model_version="kimi/k3"),
            NodeEvent(task_id=task.id, node_name="asset_gen",
                      node_idempotency_key=f"ne-{_uniq()}",
                      enqueued_at=now, finished_at=now,
                      cost_estimate_cny=1.2, model_version="gpt-image-1.5"),
            # 无费用事件不计入
            NodeEvent(task_id=task.id, node_name="rule_check",
                      node_idempotency_key=f"ne-{_uniq()}",
                      enqueued_at=now, finished_at=now,
                      cost_estimate_cny=0),
        ])
        await session.commit()
        return task


@pytest.mark.asyncio
async def test_costs_breakdown_and_summary():
    task = await _make_task_with_costs()
    async with _client() as ac:
        r = await ac.get("/api/admin/costs")
    assert r.status_code == 200
    data = r.json()
    # 汇总
    s = data["summary"]
    assert abs(s["total_cny"] - 1.22) < 1e-6
    assert s["total_24h_cny"] == s["total_cny"]  # 刚发生的事件在 24h 内
    assert s["task_count"] == 1
    assert abs(s["avg_per_task_cny"] - 1.22) < 1e-6
    # 按环节：rule_check 无费用不出现
    nodes = {n["node"]: n for n in data["by_node"]}
    assert set(nodes) == {"draft_gen", "asset_gen"}
    assert nodes["asset_gen"]["cost"] == 1.2
    assert nodes["asset_gen"]["label"] == "配图生成"
    # 按模型
    models = {m["model"]: m["cost"] for m in data["by_model"]}
    assert models["gpt-image-1.5"] == 1.2
    # 按任务明细：逐项费用齐全
    t = data["tasks"][0]
    assert t["task_id"] == str(task.id)
    assert t["query"] == task.query
    assert abs(t["total"] - 1.22) < 1e-6
    assert len(t["items"]) == 2
    item_nodes = {i["node"] for i in t["items"]}
    assert item_nodes == {"draft_gen", "asset_gen"}


@pytest.mark.asyncio
async def test_costs_empty():
    async with _client() as ac:
        r = await ac.get("/api/admin/costs")
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["total_cny"] == 0
    assert data["summary"]["task_count"] == 0
    assert data["tasks"] == []
