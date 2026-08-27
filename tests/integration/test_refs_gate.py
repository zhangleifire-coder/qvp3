"""参考图确认关卡 + 风格关键词库 集成测试。

流程：compare 任务 → ref_collect 搜图(mock)初筛 → awaiting_refs 挂起 →
人工确认（勾选保留）→ 入队续跑 → agent_production 用确认图（refs_section 注入）。
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.models.assets import Asset
from src.models.tasks import Task


def _svg(tag):
    """生成一张本地 SVG 候选图（生产同构：/static/generated/ 路径）。
    不用 data URI——fetch_image_bytes 对 utf8 data URI 走 base64 分支会报错。"""
    import hashlib
    from pathlib import Path
    h = hashlib.md5(tag.encode()).hexdigest()[:10]
    d = Path("static/generated")
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"reftest_{h}.svg"
    f.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' width='576' height='768'>"
                 f"<text>{h}</text></svg>", encoding="utf-8")
    return f"/static/generated/{f.name}"


async def _create(mode="compare"):
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"gate-{uuid.uuid4().hex[:8]}",
                    query="戴森吸尘器和小米吸尘器哪个好", content_type="x",
                    mode=mode)
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


class TestSplitSubjects:
    def test_compare_split(self):
        from src.pipeline.ref_collect import split_subjects
        a, b = split_subjects("戴森吸尘器和小米吸尘器哪个好", "compare")
        assert "戴森" in a and "小米" in b

    def test_single_whole(self):
        from src.pipeline.ref_collect import split_subjects
        assert len(split_subjects("iPhone 17 怎么选", "single")) == 1


async def test_ref_gate_suspend_and_confirm():
    """关卡：搜图 → awaiting_refs 挂起；确认 → 候选转 confirmed 并入队。"""
    from src.pipeline.ref_collect import node_ref_collect
    from src.api.tasks import RefsConfirmIn, confirm_refs
    task_id = await _create()

    async def fake_search(q, count=6):
        # 按 query 独立成批（两主体各自的 6 张，URL 不重叠）
        return [{"image_url": _svg(f"{q}-{i}"), "title": f"{q[:8]}-{i}",
                 "engine": "bing"} for i in range(6)]
    with patch("src.gateway.image_search.search_image", new=fake_search):
        r = await node_ref_collect({"task_id": task_id})
    assert r["ref_gate"] is True and r["candidates"] == 12   # 两主体 6+6

    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        assert task.status == "draft"   # 挂起状态由编排器写（下一步模拟）
        cands = list((await session.execute(
            select(Asset).where(Asset.task_id == task_id,
                                Asset.selection_status == "candidate"))).scalars().all())
        assert len(cands) == 12

    # 编排器挂起（模拟 orchestrator._suspend_for_refs 的状态写入）
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        task.status = "awaiting_refs"
        await session.commit()

    # 人工确认：保留前 5 张
    keep = [str(c.id) for c in cands[:5]]
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()) as enq:
        out = await confirm_refs(str(task_id), RefsConfirmIn(keep_ids=keep, actor="张三"))
    assert out["kept"] == 5 and out["rejected"] == 7 and enq.called
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        assert task.status == "draft"
        kept = list((await session.execute(
            select(Asset).where(Asset.task_id == task_id,
                                Asset.selection_status == "confirmed"))).scalars().all())
        assert len(kept) == 5


async def test_ref_gate_general_skipped():
    from src.pipeline.ref_collect import node_ref_collect
    task_id = await _create(mode="general")
    r = await node_ref_collect({"task_id": task_id})
    assert r.get("skipped") and "general" in r["reason"]


async def test_stage2_refs_section_injected():
    """阶段2：已有确认图时 agent 指令注入【已确认实景参考图】且要求跳过搜图。"""
    from src.pipeline.agent_production import _compose_message
    refs = ("【已确认实景参考图（人工筛选后保留，必须使用）】\n"
            "  1. /static/generated/x.png（OCR命中: 戴森）\n"
            "要求：跳过 image_search，把以上图片路径原样传入\n\n")
    msg = _compose_message("t1", "q", "compare", "D", "P", "I", [], refs)
    assert "已确认实景参考图" in msg and "跳过 image_search" in msg
    assert "/static/generated/x.png" in msg


async def test_style_kb_crud_and_injection():
    """风格库：CRUD + 导入幂等 + 注入（非空替代内置）。"""
    from src.api.styles import (StyleIn, delete_style, import_styles, list_styles,
                                style_library_text, upsert_style)
    await upsert_style(StyleIn(style_name="测试科技蓝", keywords="手机,数码",
                               description="深蓝科技光感"), actor="张三")
    await upsert_style(StyleIn(style_name="测试科技蓝", keywords="手机",
                               description="更新后的描述"), actor="张三")   # 同名覆盖
    items = (await list_styles())["items"]
    row = next(i for i in items if i["style_name"] == "测试科技蓝")
    assert row["description"] == "更新后的描述"

    text = await style_library_text()
    assert text is not None and "测试科技蓝" in text and "更新后的描述" in text

    # CSV 导入
    import io
    csv_file = io.BytesIO("style_name,keywords,description\n测试暖木,家具,装修,暖木色居家\n"
                          .encode("utf-8"))

    class _F:
        filename = "t.csv"
        async def read(self):
            return csv_file.getvalue()
    r = await import_styles(file=_F(), actor="张三")
    assert r["imported"] == 1
    assert "测试暖木" in await style_library_text()

    await delete_style(row["id"], actor="张三")
    await delete_style(next(i["id"] for i in (await list_styles())["items"]
                            if i["style_name"] == "测试暖木"), actor="张三")


async def test_refs_board_and_research():
    """审图板块：awaiting 列表 + 驳回重搜（清空候选重搜集）。"""
    import hashlib
    from src.api.tasks import RefsResearchIn, list_refs_awaiting, research_refs
    from src.pipeline.ref_collect import node_ref_collect

    def _loc(tag):
        from pathlib import Path
        h = hashlib.md5(tag.encode()).hexdigest()[:10]
        d = Path("static/generated"); d.mkdir(parents=True, exist_ok=True)
        f = d / f"reftest2_{h}.svg"
        f.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' width='576' height='768'><text>{h}</text></svg>",
                     encoding="utf-8")
        return f"/static/generated/{f.name}"

    task_id = await _create()
    async def fake_search(q, count=6):
        return [{"image_url": _loc(f"{q}-{i}"), "title": "t", "engine": "bing"} for i in range(6)]
    with patch("src.gateway.image_search.search_image", new=fake_search):
        await node_ref_collect({"task_id": task_id})
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        task.status = "awaiting_refs"
        await session.commit()

    # awaiting 列表
    lst = await list_refs_awaiting()
    assert any(i["id"] == str(task_id) for i in lst["items"])

    # 驳回重搜（带补充关键词）：候选清空重搜集
    with patch("src.gateway.image_search.search_image", new=fake_search):
        r = await research_refs(str(task_id),
                                RefsResearchIn(extra_query="新关键词实拍", actor="张三"))
    assert r["ok"] and r["candidates"] == 6   # 重搜词不含连接词→单一主体 6 张
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert task.status == "awaiting_refs"   # 重搜后仍挂起等确认
        cands = list((await session.execute(
            select(Asset).where(Asset.task_id == task_id,
                                Asset.selection_status == "candidate"))).scalars().all())
        assert len(cands) == 6


async def test_text_gate_suspend_and_confirm():
    """文字核查：text_check 自查 → awaiting_text → 人工确认（override）→ 入队续跑。"""
    from src.pipeline.text_check import run_text_check, effective_texts
    from src.api.tasks import TextConfirmIn, confirm_text, list_text_awaiting
    task_id = await _create(mode="general")

    import json as _j
    _body = "这是正文第一段，包含最好的表述。" + "内容补充说明。" * 60
    fake = {"text": _j.dumps({
                "query_clean": {"issues": ["绝对化表述：最高"],
                                "suggested": "戴森吸尘器和小米吸尘器怎么选性价比高"},
                "body_draft": _body,
                "pages_draft": ["P1封面", "P2要点", "P3要点", "P4要点", "P5要点", "P6结尾"],
                "image_prompt_draft": ["d1", "d2", "d3", "d4", "d5", "d6"]},
                ensure_ascii=False),
            "model_version": "m", "cost_cny": 0, "degraded": False}
    with patch("src.pipeline.text_check.call_with_failover", return_value=fake):
        r = await run_text_check(task_id)
    assert r["issues"] == 1 and r["auto_ok"] is False

    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert task.status == "awaiting_text"
        assert task.text_review["query_clean"]["issues"]
        assert len(task.text_review["pages_draft"]) == 6
        assert task.text_review["body_draft"]          # 正文草稿已存
        assert any("最" in i for i in task.text_review["body_issues"])  # 禁词自动检查

    lst = await list_text_awaiting()
    assert any(i["id"] == str(task_id) for i in lst["items"])

    # 人工确认（修正 query + 草稿）
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()) as enq:
        out = await confirm_text(str(task_id), TextConfirmIn(
            query="戴森吸尘器和小米吸尘器怎么选性价比高",
            body="人工核定正文。",
            pages=["P1", "P2", "P3", "P4", "P5", "P6"],
            image_prompts=["d1", "d2", "d3", "d4", "d5", "d6"], actor="张三"))
    assert out["ok"] and "query" in out["overridden"] and enq.called
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert task.status == "draft"
        eff = effective_texts(task)
        assert eff["query"] == "戴森吸尘器和小米吸尘器怎么选性价比高"
        assert eff["body"] == "人工核定正文。"       # 人工正文优先
        assert eff["pages"][0] == "P1"


async def test_text_gate_effective_fallback():
    """人工未修正时 effective_texts 回退自动草稿。"""
    from src.pipeline.text_check import effective_texts
    from types import SimpleNamespace
    t = SimpleNamespace(query="原query",
                        text_review={"query": "原query", "pages_draft": ["p1"] * 6,
                                     "image_prompt_draft": ["d1"] * 6},
                        text_override=None)
    eff = effective_texts(t)
    assert eff["query"] == "原query" and eff["pages"] == ["p1"] * 6
