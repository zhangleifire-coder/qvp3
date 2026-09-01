# 2026-09-01 吸收 8002 优化的回归：防负优化是核心——
# ① 润色护栏（过短弃用/开关关闭=旧行为）② 信源分级 ③ 素材库复用（命中挂载/
# 磁盘死链不挂/开关关闭=纯搜索）④ general 实景开关默认关=行为不变
# ⑤ 分页文案手动编辑端点 ⑥ 排版铁律进底座（英中双版）
import uuid

import pytest
from sqlalchemy import delete, select

from src.config import settings
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.assets import Asset
from src.models.drafts import PageCopy


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def test_source_level_grading():
    from src.pipeline.nodes import source_level_for
    assert source_level_for("https://www.gov.cn/zhengce/x") == "P0"
    assert source_level_for("https://www.pku.edu.cn/info") == "P0"
    assert source_level_for("https://baike.baidu.com/item/x") == "P2"
    assert source_level_for("https://www.wikipedia.org/wiki/X") == "P2"
    assert source_level_for("https://some.news.site/article") == "P3"
    assert source_level_for("") == "P3"


@pytest.mark.asyncio
async def test_draft_polish_guardrails(monkeypatch):
    """润色护栏：过短（<60%）弃用沿用原稿；开关关闭时完全不调润色。"""
    from src.pipeline import nodes as N
    calls = {"n": 0}

    async def fake_failover(prompt, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:      # 创作段
            return {"text": "秋天的早晨很美。" * 60, "model_version": "m",
                    "cost_cny": 0.01, "degraded": False}
        return {"text": "截断了", "model_version": "m2",      # 润色段过短
                "cost_cny": 0.005, "degraded": False}

    monkeypatch.setattr(N, "call_with_failover", fake_failover)
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"pl-{_uniq()}", query="q", content_type="x",
                 mode="general", status="processing")
        s.add(t); await s.commit(); tid = t.id
    try:
        r = await N.node_draft_gen({"task_id": tid})
        assert calls["n"] == 2                       # 创作后确实尝试润色
        assert "秋天的早晨" in r["text"]              # 过短润色被护栏弃用
        assert "_polished" not in r["prompt_version"]
        assert r["cost_cny"] == 0.015                 # 两段成本都入账
        # 开关关闭 → 只调一次（旧行为）
        calls["n"] = 0
        monkeypatch.setattr(settings, "draft_polish_enabled", False)
        r2 = await N.node_draft_gen({"task_id": tid})
        assert calls["n"] == 1
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Task).where(Task.id == tid))
            await s.commit()


@pytest.mark.asyncio
async def test_draft_persona_shared_in_default_template(monkeypatch):
    """系统默认模板追加人设段；自定义模板不追加（防覆盖用户意图）。"""
    from src.pipeline import nodes as N

    captured = {}
    async def fake_failover(prompt, *a, **kw):
        captured["prompt"] = prompt
        return {"text": "正文" * 200, "model_version": "m",
                "cost_cny": 0.01, "degraded": False}
    monkeypatch.setattr(N, "call_with_failover", fake_failover)
    monkeypatch.setattr(settings, "draft_polish_enabled", False)
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"pp-{_uniq()}", query="q", content_type="x",
                 mode="general", status="processing")
        s.add(t); await s.commit(); tid = t.id
    try:
        await N.node_draft_gen({"task_id": tid})
        assert "人设与真人感" in captured["prompt"]
        assert "标题" in captured["prompt"] and "25字" in captured["prompt"]
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Task).where(Task.id == tid))
            await s.commit()


@pytest.mark.asyncio
async def test_library_reuse_match_and_mount(monkeypatch):
    """素材库复用：subject 双向包含命中即挂载（免搜索）；死链（文件不在磁盘）不挂；
    开关关闭=零复用（纯搜索旧行为）。"""
    from src.pipeline import ref_collect as RC
    from unittest.mock import AsyncMock

    # 造一张库中素材（本地文件真实存在：用 nodes._persist_image 落一张）
    from src.pipeline.nodes import _persist_image
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
           '<rect width="10" height="10"/></svg>').encode()
    async with SessionLocal() as s:
        donor = Task(idempotency_key=f"don-{_uniq()}", query="戴森吸尘器怎么选",
                     content_type="x", mode="compare", status="review")
        s.add(donor); await s.commit(); donor_id = donor.id
    local_url = _persist_image(donor_id, 1, "ref", svg, "image/svg+xml")
    async with SessionLocal() as s:
        import hashlib as _h
        s.add(Asset(task_id=donor_id, page_index=1, subject="戴森吸尘器",
                    source_type="official", copyright_status="unknown",
                    hash=_h.md5(svg).hexdigest(), image_url=local_url,
                    model_version="bing", is_illustration=False,
                    selection_status="confirmed"))
        await s.commit()

    # 新任务 query 含「戴森吸尘器」→ 库命中
    hits = await RC._library_matches("戴森吸尘器和小米怎么选", ["戴森吸尘器", "小米"])
    assert any(h["engine"] == "library" for h in hits)

    # 完全无关 query → 不命中
    misses = await RC._library_matches("桂花酸梅汤做法", ["桂花酸梅汤"])
    assert not any(h["engine"] == "library" for h in misses)

    # 开关关闭时 node_ref_collect 不做库匹配（直接走搜索路径）
    async with SessionLocal() as s:
        t2 = Task(idempotency_key=f"lib-{_uniq()}", query="戴森吸尘器对比",
                  content_type="x", mode="compare", status="processing")
        s.add(t2); await s.commit(); t2_id = t2.id
    searched = {"n": 0}
    async def fake_search(q, count=None):
        searched["n"] += 1
        return [{"image_url": "https://example.com/x.png", "title": "t",
                 "engine": "bing"}]
    monkeypatch.setattr(RC.settings, "asset_library_reuse", False)
    monkeypatch.setattr("src.gateway.image_search.search_image", fake_search)

    async def fake_fetch(url):
        return (b"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" "
                b"height=\"10\"><rect width=\"10\" height=\"10\"/></svg>",
                "image/svg+xml")
    monkeypatch.setattr("src.gateway.ocr.fetch_image_bytes", fake_fetch)
    monkeypatch.setattr(RC.settings, "mock_image_gen", True)  # 跳过 OCR
    try:
        r = await RC.node_ref_collect({"task_id": t2_id})
        assert searched["n"] >= 1            # 关闭复用 → 正常搜索（旧行为）
        assert r.get("candidates", 0) >= 1
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Asset).where(Asset.task_id.in_([donor_id, t2_id])))
            await s.execute(delete(Task).where(Task.id.in_([donor_id, t2_id])))
            await s.commit()


@pytest.mark.asyncio
async def test_general_ref_gate_default_off():
    """防负优化关键回归：ref_for_general_enabled 默认关 → general 仍跳过实景关卡。"""
    from src.pipeline.ref_collect import node_ref_collect
    assert settings.ref_for_general_enabled is False
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"ge-{_uniq()}", query="量子纠缠科普",
                 content_type="x", mode="general", status="processing")
        s.add(t); await s.commit(); tid = t.id
    try:
        r = await node_ref_collect({"task_id": tid})
        assert r.get("skipped") is True
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Task).where(Task.id == tid))
            await s.commit()


@pytest.mark.asyncio
async def test_page_text_edit_endpoint():
    from src.api.tasks import edit_page_text, PageTextEditIn
    from fastapi import HTTPException
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"pe-{_uniq()}", query="q", content_type="x",
                 mode="general", status="review")
        s.add(t); await s.commit(); tid = t.id
        s.add(PageCopy(task_id=tid, page_index=2, body="旧文案", claim_ids=[]))
        await s.commit()
    try:
        r = await edit_page_text(str(tid), 2,
                                 PageTextEditIn(body="新文案：桂花酸梅汤要点"))
        assert r["ok"] and r["chars"] == 11
        async with SessionLocal() as s:
            row = (await s.execute(select(PageCopy).where(
                PageCopy.task_id == tid, PageCopy.page_index == 2))).scalar_one()
        assert row.body.startswith("新文案")
        # 越界/超长/空 校验
        with pytest.raises(HTTPException) as e:
            await edit_page_text(str(tid), 9, PageTextEditIn(body="x"))
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await edit_page_text(str(tid), 2, PageTextEditIn(body="字" * 201))
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await edit_page_text(str(tid), 2, PageTextEditIn(body="  "))
        assert e.value.status_code == 400
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(PageCopy).where(PageCopy.task_id == tid))
            await s.execute(delete(Task).where(Task.id == tid))
            await s.commit()


def test_typography_rules_in_both_bases():
    """排版铁律进英中双底座：双色标题/胶囊豁免/深框≤1/简体字形禁日文新字体。"""
    from src.gateway.prompt_versions import (get_image_prompt,
                                              _IMAGE_CONSTRAINTS_EN,
                                              _SHARED_IMAGE_STYLE)
    assert "Two-tone headline" in _IMAGE_CONSTRAINTS_EN
    assert "CAPSULE" in _IMAGE_CONSTRAINTS_EN and "at most" in _IMAGE_CONSTRAINTS_EN
    assert "shinjitai" in _IMAGE_CONSTRAINTS_EN and "TYPESET COPY" in _IMAGE_CONSTRAINTS_EN
    assert "双色排版" in _SHARED_IMAGE_STYLE and "胶囊" in _SHARED_IMAGE_STYLE
    assert "最多出现1次" in _SHARED_IMAGE_STYLE
    assert "日文新字体" in _SHARED_IMAGE_STYLE and "一字不差" in _SHARED_IMAGE_STYLE
    # 2026-09-01 颜色决策放开：强调色自选协调（不再固定暖橘）
    assert "harmonizes" in _IMAGE_CONSTRAINTS_EN and "teal" in _IMAGE_CONSTRAINTS_EN
    assert "协调的" in _SHARED_IMAGE_STYLE and "不固定某一种" in _SHARED_IMAGE_STYLE
    from src.services.visual_writer import _VISUAL_PROMPT
    assert "COLOUR DIRECTION" in _VISUAL_PROMPT and "sage green" in _VISUAL_PROMPT
    assert "EXTRA attention" in _VISUAL_PROMPT   # 配色反馈优先学习
    # 双模式最终提示词均含铁律
    p_en = get_image_prompt("general", "文案", 1, visual="V", style_en="S")
    assert "Two-tone headline" in p_en and "shinjitai" in p_en
    p_cn = get_image_prompt("general", "文案", 1)
    assert "双色排版" in p_cn and "日文新字体" in p_cn


def test_pages_prompt_fidelity_clause():
    from src.gateway.prompt_versions import PAGES_PROMPT
    assert "不自行改写" in PAGES_PROMPT
