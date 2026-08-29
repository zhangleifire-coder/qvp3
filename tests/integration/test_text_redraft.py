# 文字核查起草失败显式标记 + 重新起草 集成测试（2026-08-29 空内容事故）
# 事故根因：LLM 输出被 1024 token 截断 → JSON 解析失败 → 静默存空草稿 + auto_ok=true
import asyncio
import json
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.tasks import Task


async def _create(status="draft"):
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"rd-{uuid.uuid4().hex[:8]}",
                    query="雷鸟ai眼镜对比", content_type="x", mode="general",
                    status=status)
        session.add(task)
        await session.commit()
        return task.id


_GOOD = json.dumps({
    "query_clean": {"issues": [], "suggested": ""},
    "body_draft": "正文" * 250,
    "pages_draft": ["P1", "P2", "P3", "P4", "P5", "P6"],
    "image_prompt_draft": ["d1", "d2", "d3", "d4", "d5", "d6"],
}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_unparseable_output_marked_failed():
    """LLM 输出无法解析：显式 draft_error + auto_ok=False，不再静默空草稿。"""
    from src.pipeline.text_check import run_text_check
    task_id = await _create()
    bad = {"text": '```json\n{"query_clean": {"issues": [], "suggested": ""}, '
                   '"body_draft": "写到一半被截断',  # 无闭合 } —— 解析必败
           "model_version": "anthropic/k3", "degraded": True}
    with patch("src.pipeline.text_check.call_with_failover", return_value=bad):
        r = await run_text_check(task_id)
    assert r["auto_ok"] is False and r["issues"] == 1
    async with SessionLocal() as s:
        task = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert task.status == "awaiting_text"
        rv = task.text_review
        assert rv["draft_error"] is True
        assert rv["auto_ok"] is False
        assert "起草失败" in rv["query_clean"]["issues"][0]
        assert rv["body_draft"] == "" and rv["pages_draft"] == []


@pytest.mark.asyncio
async def test_redraft_recovers_after_failure():
    """重新起草：draft_error 任务重跑后草稿恢复。"""
    from src.pipeline.text_check import run_text_check
    from src.api.tasks import redraft_text
    task_id = await _create()
    bad = {"text": "not json at all", "model_version": "anthropic/k3"}
    with patch("src.pipeline.text_check.call_with_failover", return_value=bad):
        await run_text_check(task_id)

    good = {"text": _GOOD, "model_version": "deepseek/deepseek-v4-pro", "degraded": False}
    with patch("src.pipeline.text_check.call_with_failover", return_value=good):
        out = await redraft_text(str(task_id), actor="张三")
        # 后台 create_task 在 patch 上下文内轮询等完成（退出 with 会打到真 LLM）
        for _ in range(60):
            await asyncio.sleep(0.1)
            async with SessionLocal() as s2:
                t2 = (await s2.execute(select(Task).where(Task.id == task_id))).scalar_one()
                if (t2.text_review or {}).get("body_draft"):
                    break
    assert out["ok"] is True and out.get("queued") is True
    async with SessionLocal() as s:
        task = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert "draft_error" not in task.text_review  # 成功路径不带失败标记
        assert len(task.text_review["pages_draft"]) == 6
        assert task.text_review["body_draft"]


@pytest.mark.asyncio
async def test_redraft_rejects_wrong_status():
    """非 awaiting_text 状态不允许重新起草。"""
    from src.api.tasks import redraft_text
    task_id = await _create(status="review")
    with pytest.raises(Exception) as ei:
        await redraft_text(str(task_id), actor="ops")
    assert "仅待人工核查" in str(ei.value)
