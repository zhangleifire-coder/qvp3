# 手工内容导入（query + 用户手写正文 → AI 改写优化）集成测试
import asyncio
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
    async def fake_failover(prompt, **kw):
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


@pytest.mark.asyncio
async def test_text_reject_marks_drive_targeted_rewrite():
    """驳回重写：标记存 review.feedback → 定向修改 prompt（注入意见+底稿）→ 意见留痕。"""
    from src.api.tasks import import_manual, ManualImportIn, reject_text, TextRejectIn
    from src.pipeline.text_check import run_text_check
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()):
        out = await import_manual(ManualImportIn(query="驳回标记测试标题", body=_BODY))
    tid = out["task_id"]
    # 第一轮：起草（改写模式）
    first = {"text": json.dumps({
        "query_clean": {"issues": [], "suggested": ""},
        "body_draft": "第一版正文。" * 100,
        "pages_draft": ["P1旧", "P2", "P3", "P4", "P5", "P6"],
        "image_prompt_draft": ["d1", "d2", "d3", "d4", "d5", "d6"]},
        ensure_ascii=False), "model_version": "m"}
    with patch("src.pipeline.text_check.call_with_failover", return_value=first):
        await run_text_check(tid)

    # 驳回：标记第1页文案 + 正文
    captured = {}
    async def fake_failover(prompt, **kw):
        captured["prompt"] = prompt
        return {"text": json.dumps({
            "query_clean": {"issues": [], "suggested": ""},
            "body_draft": "第二版正文。" * 100,
            "pages_draft": ["P1新", "P2", "P3", "P4", "P5", "P6"],
            "image_prompt_draft": ["d1", "d2", "d3", "d4", "d5", "d6"]},
            ensure_ascii=False), "model_version": "m"}
    with patch("src.pipeline.text_check.call_with_failover", side_effect=fake_failover):
        r = await reject_text(tid, TextRejectIn(actor="张三", marks=[
            {"target": "page:1", "note": "封面文案不够吸引人，改成疑问句式"},
            {"target": "body", "note": "第二段太啰嗦，压缩到两句话"},
        ]))
        # 后台 create_task 在 patch 上下文内轮询等完成（退出 with 会打到真 LLM）
        for _ in range(60):
            await asyncio.sleep(0.1)
            async with SessionLocal() as s2:
                t2 = (await s2.execute(select(Task).where(Task.id == uuid.UUID(tid)))).scalar_one()
                if (t2.text_review or {}).get("body_draft", "").startswith("第二版正文"):
                    break
    assert r["ok"] is True and r["marks"] == 2 and r.get("queued") is True
    p = captured["prompt"]
    assert "驳回标记与修改意见" in p
    assert "第1页图上文案" in p and "疑问句式" in p        # 标记+意见注入
    assert "P1旧" in p and "第一版正文" in p               # 当前草稿作为底稿
    async with SessionLocal() as s:
        task = (await s.execute(select(Task).where(Task.id == uuid.UUID(tid)))).scalar_one()
        assert task.status == "awaiting_text"               # 改完回到核查
        rv = task.text_review
        assert "feedback" not in rv or not rv.get("feedback")   # 已处理清除
        lf = rv.get("last_feedback") or []
        assert len(lf) == 2 and lf[0]["target"] == "page:1"    # 意见留痕
        assert rv["pages_draft"][0] == "P1新"


@pytest.mark.asyncio
async def test_text_reject_validation():
    """驳回参数校验：空标记 / 无效 target / 缺意见 / 非核查状态。"""
    from src.api.tasks import reject_text, TextRejectIn
    with pytest.raises(HTTPException) as ei:
        await reject_text("00000000-0000-0000-0000-000000000000",
                          TextRejectIn(marks=[], actor="x"))
    assert ei.value.status_code == 422
    with pytest.raises(HTTPException) as ei2:
        await reject_text("00000000-0000-0000-0000-000000000000",
                          TextRejectIn(marks=[{"target": "page:9", "note": "x"}], actor="x"))
    assert "无效标记对象" in str(ei2.value.detail)
    with pytest.raises(HTTPException) as ei3:
        await reject_text("00000000-0000-0000-0000-000000000000",
                          TextRejectIn(marks=[{"target": "body", "note": " "}], actor="x"))
    assert "缺少修改意见" in str(ei3.value.detail)
