"""Nanobot 全链创作 Agent 路径集成测试。

mock 掉 nanobot_client（health/call_agent），Agent 返回契约 JSON；
验证：8 节点流水线跑通、产物完整落库（claims/evidence/drafts/page_copies/
assets/ocr_results）、node_events 记账、工具成本台账合并。
"""
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.gateway.tool_ledger import tool_ledger
from src.models.assets import Asset, OcrResult
from src.models.drafts import Draft, PageCopy
from src.models.entities import Claim, Evidence
from src.models.events import NodeEvent
from src.models.review import ReviewSession
from src.models.snapshots import PublishSnapshot
from src.models.tasks import Task
from src.pipeline.orchestrator import NODES_AGENT, run_pipeline


def _svg_data_uri(tag: str) -> str:
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='576' height='768'>"
           f"<rect width='100%' height='100%' fill='#333'/><text>{tag}</text></svg>")
    import base64
    return "data:image/svg+xml;utf8," + svg


def _agent_json(mode: str = "general") -> str:
    data = {
        "evidence": [{"title": "来源A", "url": "https://a.com/1",
                      "summary": "关键事实：测试主体成立于1990年"},
                     {"title": "来源B", "url": "https://b.com/2", "summary": "佐证"}],
        "content_style": "避坑指南",
        "image_style": "真实摄影",
        "draft": "这是一篇测试正文，用于验证创作 Agent 全链路。" * 20,
        "pages": [f"第{i}页图上文案：测试要点{i}" for i in range(1, 7)],
        "references": [],
        "images": [{"page_index": i, "image_url": _svg_data_uri(f"p{i}")}
                   for i in range(1, 7)],
        "ocr_texts": [{"page_index": i, "text": f"第{i}页图上文案：测试要点{i}"}
                      for i in range(1, 7)],
        "notes": "测试创作",
    }
    if mode != "general":
        data["references"] = [
            {"image_url": _svg_data_uri(f"ref{j}"), "title": f"参考{j}",
             "engine": "bing"} for j in range(1, 3)]
    return json.dumps(data, ensure_ascii=False)


FAKE_AGENT_CALL = {
    "text": _agent_json(),
    "model_version": "nanobot:deepseek/deepseek-v4-pro",
    "prompt_tokens": 100, "completion_tokens": 200,
    "elapsed_seconds": 1.2,
}


async def _create_task(mode: str = "general"):
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"agent-{uuid.uuid4().hex[:8]}",
                    query="测试Query", content_type="x", mode=mode)
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


@pytest.fixture
def agent_path(monkeypatch):
    monkeypatch.setattr(settings, "agent_pipeline_enabled", True)
    with patch("src.pipeline.agent_production.nanobot_client.health",
               new=AsyncMock(return_value=True)), \
         patch("src.pipeline.agent_production.nanobot_client.call_agent",
               new=AsyncMock(return_value=dict(FAKE_AGENT_CALL))):
        yield


async def test_agent_pipeline_produces_full_artifacts(agent_path):
    task_id = await _create_task("general")
    # 模拟 MCP 工具成本回调台账
    await tool_ledger.record(task_id, {"tool": "web_search", "cost_cny": 0.04})
    await tool_ledger.record(task_id, {"tool": "image_gen", "cost_cny": 1.2})

    results = await run_pipeline(task_id)
    assert [r["node"] for r in results] == NODES_AGENT

    async with SessionLocal() as session:
        # 风格自适应回填：普通导入任务 gen_style 为空 → Agent 判定结果回填
        task_row = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        assert task_row.gen_style == "避坑指南"
        assert task_row.gen_image_style == "真实摄影"

        draft = (await session.execute(
            select(Draft).where(Draft.task_id == task_id))).scalars().one()
        assert len(draft.body) > 150
        assert draft.model_version.startswith("nanobot:")
        assert draft.prompt_version == "agent_general_v1"

        pages = (await session.execute(
            select(PageCopy).where(PageCopy.task_id == task_id)
            .order_by(PageCopy.page_index))).scalars().all()
        assert [p.page_index for p in pages] == [1, 2, 3, 4, 5, 6]
        assert pages[0].body.startswith("第1页")

        assets = (await session.execute(
            select(Asset).where(Asset.task_id == task_id))).scalars().all()
        ai = [a for a in assets if a.source_type == "ai_generated"]
        official = [a for a in assets if a.source_type == "official"]
        assert len(ai) == 6 and len(official) == 0
        assert all(a.image_url.startswith("/static/generated/") for a in ai)
        assert all(a.hash for a in ai)

        ocr = (await session.execute(
            select(OcrResult).join(Asset, OcrResult.asset_id == Asset.id)
            .where(Asset.task_id == task_id))).scalars().all()
        assert len(ocr) == 6  # Agent 自检 OCR 已落库

        claim = (await session.execute(
            select(Claim).where(Claim.task_id == task_id))).scalars().one()
        ev = (await session.execute(
            select(Evidence).where(Evidence.claim_id == claim.id))).scalars().all()
        assert len(ev) == 2

        # 下游确定性节点照常工作
        assert len((await session.execute(
            select(ReviewSession).where(ReviewSession.task_id == task_id))).scalars().all()) == 3
        snap = (await session.execute(
            select(PublishSnapshot).where(PublishSnapshot.task_id == task_id))).scalars().one()
        assert snap is not None

        events = (await session.execute(
            select(NodeEvent).where(NodeEvent.task_id == task_id))).scalars().all()
        assert len(events) == 9   # ref_collect 关卡节点（general 记 skip）
        ap = [e for e in events if e.node_name == "agent_production"][0]
        assert ap.error_class is None
        assert ap.cost_estimate_cny and ap.cost_estimate_cny > 1.2  # 文本 + 工具成本已合并
        assert ap.model_version.startswith("nanobot:")


async def test_agent_pipeline_compare_mode_persists_references(agent_path):
    task_id = await _create_task("compare")
    with patch("src.pipeline.agent_production.nanobot_client.call_agent",
               new=AsyncMock(return_value={
                   **FAKE_AGENT_CALL, "text": _agent_json("compare")})):
        await run_pipeline(task_id)
    async with SessionLocal() as session:
        assets = (await session.execute(
            select(Asset).where(Asset.task_id == task_id))).scalars().all()
        official = [a for a in assets if a.source_type == "official"]
        assert len(official) == 2  # 参考图落 official 素材
        ai = [a for a in assets if a.source_type == "ai_generated"]
        assert len(ai) == 6
        assert len(ai) + len(official) == 8


async def test_agent_output_correction_round(agent_path):
    """首轮输出不合格 → 同 session 纠错重问 → 第二轮通过。"""
    task_id = await _create_task("general")
    bad = {"text": "抱歉我无法完成", "model_version": "nanobot:m",
           "prompt_tokens": 5, "completion_tokens": 5, "elapsed_seconds": 0.1}
    with patch("src.pipeline.agent_production.nanobot_client.call_agent",
               new=AsyncMock(side_effect=[bad, dict(FAKE_AGENT_CALL)])) as calls:
        results = await run_pipeline(task_id)
        assert calls.call_count == 2
        # 纠错追问复用同一 session
        _, kwargs1 = calls.call_args_list[0]
        _, kwargs2 = calls.call_args_list[1]
        assert kwargs1["session_id"] == kwargs2["session_id"]
    assert [r["node"] for r in results] == NODES_AGENT


async def test_agent_two_bad_rounds_fails_node(agent_path):
    task_id = await _create_task("general")
    bad = {"text": "还是不行", "model_version": "nanobot:m",
           "prompt_tokens": 5, "completion_tokens": 5, "elapsed_seconds": 0.1}
    with patch("src.pipeline.agent_production.nanobot_client.call_agent",
               new=AsyncMock(side_effect=[bad, bad])):
        with pytest.raises(RuntimeError, match="两次未通过校验"):
            await run_pipeline(task_id)
    async with SessionLocal() as session:
        events = (await session.execute(
            select(NodeEvent).where(NodeEvent.task_id == task_id))).scalars().all()
        ap = [e for e in events if e.node_name == "agent_production"][0]
        assert ap.error_class == "RuntimeError"


async def test_agent_presets_style_not_overwritten(agent_path):
    """组合导入任务已显式指定 gen_style → Agent 回填不覆盖；图片风格仍记录。"""
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"agent-fix-{uuid.uuid4().hex[:8]}",
                    query="组合风格任务", content_type="x", mode="general",
                    gen_style="解读·经验分享", gen_category="汽车")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    await run_pipeline(task_id)
    async with SessionLocal() as session:
        task_row = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        assert task_row.gen_style == "解读·经验分享"   # 显式风格未被 Agent 判定覆盖
        assert task_row.gen_image_style == "真实摄影"  # 图片风格始终记录本轮判定


async def test_detail_node_timeline(agent_path):
    """任务详情节点时间线：每节点最新一轮 状态/耗时/成本/模型（两路径通用细节）。"""
    from src.api.tasks import task_detail
    task_id = await _create_task("general")
    await run_pipeline(task_id)
    d = await task_detail(str(task_id))
    tl = d["node_timeline"]
    assert len(tl) == len(NODES_AGENT)
    assert [e["node"] for e in tl] == list(NODES_AGENT)   # 按节点顺序排列
    assert all(e["status"] == "done" for e in tl)
    assert all(e["duration_s"] is not None and e["duration_s"] >= 0 for e in tl)
    ap = next(e for e in tl if e["node"] == "agent_production")
    assert ap["model_version"].startswith("nanobot:")
    assert ap["cost_cny"] and ap["cost_cny"] > 0


async def test_garble_regen_loop(agent_path):
    """P0-2：图上文字扭曲时换构图重生（mock 生图路径直接放行，验证不阻断）。"""
    from unittest.mock import patch as _patch
    from src.config import settings as _s
    with _patch.object(_s, "mock_image_gen", True):
        task_id = await _create_task("general")
        await run_pipeline(task_id)
    async with SessionLocal() as session:
        ai = (await session.execute(
            select(Asset).where(Asset.task_id == task_id,
                                Asset.source_type == "ai_generated"))).scalars().all()
        assert len(ai) == 6   # mock 路径跳过扭曲质检，产物照常落库
