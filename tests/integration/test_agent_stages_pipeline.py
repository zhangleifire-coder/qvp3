"""staged 分阶段 Agent 路径集成测试（AGENT_PIPELINE_VARIANT=staged）。

mock 掉 dsh_client（health/call_agent），call_agent 按 session_id 中的
阶段名返回对应阶段契约 JSON；验证：14 节点流水线跑通、各阶段产物落库
（claims/evidence → drafts → page_copies → assets/ocr_results）、阶段间只经
DB 传数据、node_events 按阶段拆分记账、工具成本台账按阶段归属、
text_override 直通、单阶段失败不影响已完成阶段幂等。
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
from src.pipeline.orchestrator import NODES_AGENT_STAGED, run_pipeline


def _svg_data_uri(tag: str) -> str:
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='576' height='768'>"
           f"<rect width='100%' height='100%' fill='#333'/><text>{tag}</text></svg>")
    return "data:image/svg+xml;utf8," + svg


def _stage_json(stage: str, mode: str = "general") -> str:
    if stage == "evidence":
        data = {
            "evidence": [{"title": "来源A", "url": "https://a.com/1",
                          "summary": "关键事实：测试主体成立于1990年"},
                         {"title": "来源B", "url": "https://b.com/2", "summary": "佐证"}],
            "content_style": "避坑指南",
            "image_style": "真实摄影",
            "notes": "取证完成",
        }
    elif stage == "draft":
        data = {"draft": "这是一篇测试正文，用于验证分阶段创作链路。" * 20,
                "notes": "正文完成"}
    elif stage == "pages":
        data = {"pages": [f"第{i}页图上文案：测试要点{i}" for i in range(1, 7)],
                "notes": "分页完成"}
    else:  # assets
        data = {
            "references": [],
            "images": [{"page_index": i, "image_url": _svg_data_uri(f"p{i}")}
                       for i in range(1, 7)],
            "ocr_texts": [{"page_index": i, "text": f"第{i}页图上文案：测试要点{i}"}
                          for i in range(1, 7)],
            "notes": "配图完成",
        }
        if mode != "general":
            data["references"] = [
                {"image_url": _svg_data_uri(f"ref{j}"), "title": f"参考{j}",
                 "engine": "bing"} for j in range(1, 3)]
    return json.dumps(data, ensure_ascii=False)


def _fake_call_agent(mode: str = "general", tool_costs: bool = True):
    """按 session_id 中的阶段名返回对应契约 JSON；同时模拟 MCP 工具成本回调
    （evidence 阶段记 web_search、assets 阶段记 image_gen），验证成本按阶段归属。"""
    async def _call(user_message, *, session_id, on_delta=None):
        stage = session_id.rsplit("-", 2)[-2]
        if tool_costs:
            tid = session_id[len("qvp-task-"):].rsplit("-", 2)[0]
            if stage == "evidence":
                await tool_ledger.record(tid, {"tool": "web_search", "cost_cny": 0.04})
            elif stage == "assets":
                await tool_ledger.record(tid, {"tool": "image_gen", "cost_cny": 1.2})
        return {"text": _stage_json(stage, mode),
                "model_version": "dsh:deepseek/deepseek-v4-flash",
                "prompt_tokens": 100, "completion_tokens": 200,
                "elapsed_seconds": 1.2}
    return _call


async def _create_task(mode: str = "general", with_body: bool = False):
    async with SessionLocal() as session:
        override = {"query": "测试Query"}
        if with_body:
            override["body"] = "人工核定正文：这条内容已经人工核查放行。" * 15
        task = Task(idempotency_key=f"staged-{uuid.uuid4().hex[:8]}",
                    query="测试Query", content_type="x", mode=mode,
                    text_override=override)   # 预置：跳过文字核查关卡
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


@pytest.fixture
def staged_path(monkeypatch):
    monkeypatch.setattr(settings, "agent_pipeline_enabled", True)
    monkeypatch.setattr(settings, "agent_pipeline_variant", "staged")
    with patch("src.pipeline.agent_stages.dsh_client.health",
               new=AsyncMock(return_value=True)):
        yield


async def test_staged_pipeline_general_full_artifacts(staged_path):
    task_id = await _create_task("general")
    with patch("src.pipeline.agent_stages.dsh_client.call_agent",
               new=AsyncMock(side_effect=_fake_call_agent("general"))):
        results = await run_pipeline(task_id)
    assert [r["node"] for r in results] == NODES_AGENT_STAGED

    async with SessionLocal() as session:
        # 风格自适应回填（agent_evidence 阶段）
        task_row = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        assert task_row.gen_style == "避坑指南"
        assert task_row.gen_image_style == "真实摄影"

        claim = (await session.execute(
            select(Claim).where(Claim.task_id == task_id))).scalars().one()
        ev = (await session.execute(
            select(Evidence).where(Evidence.claim_id == claim.id))).scalars().all()
        assert len(ev) == 2

        draft = (await session.execute(
            select(Draft).where(Draft.task_id == task_id))).scalars().one()
        assert len(draft.body) > 150
        assert draft.model_version.startswith("dsh:")
        assert draft.prompt_version == "agent_draft_general_v1"

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

        ocr = (await session.execute(
            select(OcrResult).join(Asset, OcrResult.asset_id == Asset.id)
            .where(Asset.task_id == task_id))).scalars().all()
        assert len(ocr) == 6  # Agent 自检 OCR 已落库

        # 下游确定性节点照常工作
        assert len((await session.execute(
            select(ReviewSession).where(ReviewSession.task_id == task_id))).scalars().all()) == 3
        snap = (await session.execute(
            select(PublishSnapshot).where(PublishSnapshot.task_id == task_id))).scalars().one()
        assert snap is not None

        events = (await session.execute(
            select(NodeEvent).where(NodeEvent.task_id == task_id))).scalars().all()
        assert len(events) == 14  # staged 全链 14 节点
        by_name = {e.node_name: e for e in events}
        # 阶段成本拆分可见：web_search 归 evidence、image_gen + OCR 归 assets
        assert by_name["agent_evidence"].cost_estimate_cny >= 0.04
        assert by_name["agent_assets"].cost_estimate_cny > 1.2
        assert by_name["agent_draft"].cost_estimate_cny > 0
        assert by_name["agent_pages"].cost_estimate_cny > 0
        for stage in ("agent_evidence", "agent_draft", "agent_pages", "agent_assets"):
            assert by_name[stage].error_class is None
            assert by_name[stage].model_version.startswith("dsh:")


async def test_staged_pipeline_compare_mode_persists_references(staged_path):
    task_id = await _create_task("compare")
    with patch("src.pipeline.agent_stages.dsh_client.call_agent",
               new=AsyncMock(side_effect=_fake_call_agent("compare"))):
        await run_pipeline(task_id)
    async with SessionLocal() as session:
        assets = (await session.execute(
            select(Asset).where(Asset.task_id == task_id))).scalars().all()
        official = [a for a in assets if a.source_type == "official"]
        assert len(official) == 2  # 参考图落 official 素材
        ai = [a for a in assets if a.source_type == "ai_generated"]
        assert len(ai) == 6


async def test_staged_pipeline_single_mode(staged_path):
    task_id = await _create_task("single")
    with patch("src.pipeline.agent_stages.dsh_client.call_agent",
               new=AsyncMock(side_effect=_fake_call_agent("single"))):
        results = await run_pipeline(task_id)
    assert [r["node"] for r in results] == NODES_AGENT_STAGED
    async with SessionLocal() as session:
        assets = (await session.execute(
            select(Asset).where(Asset.task_id == task_id))).scalars().all()
        assert len([a for a in assets if a.source_type == "official"]) == 2
        assert len([a for a in assets if a.source_type == "ai_generated"]) == 6
        draft = (await session.execute(
            select(Draft).where(Draft.task_id == task_id))).scalars().one()
        assert draft.prompt_version == "agent_draft_single_v1"


async def test_staged_correction_round_same_session(staged_path):
    """某阶段首轮输出不合格 → 同 stage session 纠错重问 → 第二轮通过。"""
    task_id = await _create_task("general")
    bad = {"text": "抱歉我无法完成", "model_version": "dsh:m",
           "prompt_tokens": 5, "completion_tokens": 5, "elapsed_seconds": 0.1}
    good = {"text": _stage_json("pages"),
            "model_version": "dsh:deepseek/deepseek-v4-flash",
            "prompt_tokens": 100, "completion_tokens": 200, "elapsed_seconds": 1.0}
    real = _fake_call_agent("general")

    async def _call(user_message, *, session_id, on_delta=None):
        if session_id.rsplit("-", 2)[-2] == "pages":
            _call.pages_calls.append(session_id)
            if len(_call.pages_calls) == 1:
                return dict(bad)
            return dict(good)
        return await real(user_message, session_id=session_id, on_delta=on_delta)
    _call.pages_calls = []

    with patch("src.pipeline.agent_stages.dsh_client.call_agent",
               new=AsyncMock(side_effect=_call)):
        results = await run_pipeline(task_id)
    assert [r["node"] for r in results] == NODES_AGENT_STAGED
    assert len(_call.pages_calls) == 2
    assert _call.pages_calls[0] == _call.pages_calls[1]  # 纠错复用同 stage session
    assert "-pages-" in _call.pages_calls[0]


async def test_staged_stage_failure_isolated(staged_path):
    """单阶段两次不合格 → 该阶段节点失败，已完成阶段（evidence/draft）事件不受影响。"""
    task_id = await _create_task("general")
    bad = {"text": "还是不行", "model_version": "dsh:m",
           "prompt_tokens": 5, "completion_tokens": 5, "elapsed_seconds": 0.1}
    real = _fake_call_agent("general")

    async def _call(user_message, *, session_id, on_delta=None):
        if session_id.rsplit("-", 2)[-2] == "pages":
            return dict(bad)
        return await real(user_message, session_id=session_id, on_delta=on_delta)

    with patch("src.pipeline.agent_stages.dsh_client.call_agent",
               new=AsyncMock(side_effect=_call)):
        with pytest.raises(RuntimeError, match="两次未通过校验"):
            await run_pipeline(task_id)
    async with SessionLocal() as session:
        events = (await session.execute(
            select(NodeEvent).where(NodeEvent.task_id == task_id))).scalars().all()
        by_name = {e.node_name: e for e in events}
        assert by_name["agent_pages"].error_class == "RuntimeError"
        assert by_name["agent_evidence"].error_class is None
        assert by_name["agent_draft"].error_class is None
        assert "agent_assets" not in by_name  # 未走到
        # 已完成阶段产物在库（断点续跑可只用它们）
        assert (await session.execute(
            select(Draft).where(Draft.task_id == task_id))).scalars().one()


async def test_staged_text_override_passthrough(staged_path):
    """人工核定正文直通：agent_draft 不调 Agent 直接落库，成本为 0。"""
    task_id = await _create_task("general", with_body=True)
    sessions: list[str] = []

    async def _spy(user_message, *, session_id, on_delta=None):
        sessions.append(session_id)
        return await _fake_call_agent("general")(user_message,
                                                 session_id=session_id,
                                                 on_delta=on_delta)

    with patch("src.pipeline.agent_stages.dsh_client.call_agent",
               new=AsyncMock(side_effect=_spy)):
        results = await run_pipeline(task_id)
    assert [r["node"] for r in results] == NODES_AGENT_STAGED
    assert not any("-draft-" in s for s in sessions)  # draft 阶段未调 Agent
    async with SessionLocal() as session:
        draft = (await session.execute(
            select(Draft).where(Draft.task_id == task_id))).scalars().one()
        assert draft.body.startswith("人工核定正文")
        assert draft.model_version == "human_confirmed"
        events = (await session.execute(
            select(NodeEvent).where(NodeEvent.task_id == task_id,
                                    NodeEvent.node_name == "agent_draft"))).scalars().one()
        assert events.cost_estimate_cny == 0


async def test_staged_monolith_variant_unaffected(monkeypatch):
    """开关语义：variant=monolith（默认）时 Agent 路径仍走 agent_production 大节点。"""
    monkeypatch.setattr(settings, "agent_pipeline_enabled", True)
    monkeypatch.setattr(settings, "agent_pipeline_variant", "monolith")
    from src.pipeline.orchestrator import NODES_AGENT
    from src.stream.progress import node_order
    assert node_order() == list(NODES_AGENT)
    monkeypatch.setattr(settings, "agent_pipeline_variant", "staged")
    assert node_order() == list(NODES_AGENT_STAGED)
    monkeypatch.setattr(settings, "agent_pipeline_enabled", False)
    from src.stream.progress import NODE_ORDER
    assert node_order() == list(NODE_ORDER)
