# 手工内容导入（query + 用户手写正文 → AI 改写优化）集成测试
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.tasks import Task

_BODY = ("共享单车是城市短途出行的好选择。" + "先找车再扫码开锁，骑行结束在规范停放点还车，注意避让人流。" * 10)  # >500 字


@pytest.mark.asyncio
async def test_manual_import_creates_task_with_user_body():
    """端点：创建 draft 任务 + text_review 预存 user_body/source + 入队。"""
    from src.api.tasks import import_manual, ManualImportIn
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()) as enq:
        out = await import_manual(ManualImportIn(query="城市共享单车使用指南",
                                                 body=_BODY, actor="张三"))
    assert out["imported"] == 1 and out["queued"] is True and out["body_chars"] >= 200
    assert enq.called
    async with SessionLocal() as s:
        task = (await s.execute(select(Task).where(
            Task.idempotency_key.like(f"manual|城市共享单车使用指南|general|%")))).scalar_one()
        assert task.status == "draft"
        assert task.text_review["source"] == "manual"
        assert task.text_review["user_body"] == _BODY


@pytest.mark.asyncio
async def test_manual_import_idempotent():
    """相同 query+正文 重复导入：幂等跳过。"""
    from src.api.tasks import import_manual, ManualImportIn
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()):
        a = await import_manual(ManualImportIn(query="幂等测试标题", body=_BODY))
        b = await import_manual(ManualImportIn(query="幂等测试标题", body=_BODY))
    assert a["imported"] == 1 and b["imported"] == 0


@pytest.mark.asyncio
async def test_manual_import_validation():
    """字数不足 / query 过短 → 422。"""
    from src.api.tasks import import_manual, ManualImportIn
    with pytest.raises(HTTPException) as ei:
        await import_manual(ManualImportIn(query="短", body=_BODY))
    assert ei.value.status_code == 422
    with pytest.raises(HTTPException) as ei2:
        await import_manual(ManualImportIn(query="够长的标题", body="太短"))
    assert ei2.value.status_code == 422


@pytest.mark.asyncio
async def test_manual_task_not_skipped_by_gate():
    """防回归：手工导入预存 {source, user_body} 不得触发 text_check 幂等跳过
    （2026-08-30 事故：跳过条件 text_review is not None 导致关卡失效直通生产）。"""
    from src.api.tasks import import_manual, ManualImportIn
    from src.pipeline.orchestrator import _node_text_check
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()):
        out = await import_manual(ManualImportIn(query="关卡跳过回归测试", body=_BODY))
    tid = out["task_id"]
    with patch("src.pipeline.text_check.call_with_failover",
               return_value={"text": json.dumps({
                   "query_clean": {"issues": [], "suggested": ""},
                   "body_draft": "正文" * 250,
                   "pages_draft": ["1"] * 6, "image_prompt_draft": ["d"] * 6},
                   ensure_ascii=False), "model_version": "m"}):
        r = await _node_text_check({"task_id": tid})
    assert r.get("text_gate") is True          # 走了起草（改写模式），未跳过
    # 起草完成后再跑 pipeline：幂等跳过（body_draft 已存在）
    r2 = await _node_text_check({"task_id": tid})
    assert r2.get("skipped") is True


@pytest.mark.asyncio
async def test_text_check_rewrite_mode_keeps_user_body():
    """text_check 感知 user_body：走改写 prompt（prompt 含手写正文），review 保留原稿。"""
    from src.api.tasks import import_manual, ManualImportIn
    from src.pipeline.text_check import run_text_check
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()):
        out = await import_manual(ManualImportIn(query="改写模式测试标题", body=_BODY))
    tid = out["task_id"]

    captured = {}
    async def fake_failover(prompt):
        captured["prompt"] = prompt
        return {"text": json.dumps({
            "query_clean": {"issues": [], "suggested": ""},
            "body_draft": "改写后的正文。" * 100,
            "pages_draft": ["P1", "P2", "P3", "P4", "P5", "P6"],
            "image_prompt_draft": ["d1", "d2", "d3", "d4", "d5", "d6"],
        }, ensure_ascii=False), "model_version": "m", "degraded": False}
    with patch("src.pipeline.text_check.call_with_failover", side_effect=fake_failover):
        r = await run_text_check(tid)
    assert r["auto_ok"] is True
    assert "用户手写正文" in captured["prompt"]      # 走了改写 prompt
    assert _BODY[:30] in captured["prompt"]          # 手写内容注入
    async with SessionLocal() as s:
        task = (await s.execute(select(Task).where(Task.id == uuid.UUID(tid)))).scalar_one()
        assert task.status == "awaiting_text"
        rv = task.text_review
        assert rv["source"] == "manual" and rv["user_body"] == _BODY   # 原稿保留
        assert rv["body_draft"].startswith("改写后的正文")
