"""参考图反哺 text_check 起草 单测（2026-09-07 两段式实景搜图，吸收 8002）。

节点序 ref_seed（搜图①自动）→ text_check → ref_collect（搜图②人工关）：
ref_seed 搜集的创作参考图（Asset selection_status='text_ref'）在
run_text_check 起草时作为信息段追加到三式提示词之后；无图时提示词与此前
逐字节一致（零差异）。

mock 手法同 test_polish_rounds.py：patch text_check.call_with_failover
捕获提示词；DB 走 conftest 测试库。
"""
import json
import uuid

from sqlalchemy import select
from unittest.mock import patch

from src.db.session import SessionLocal
from src.gateway.prompt_versions import _DRAFT_SHARED
from src.models.assets import Asset
from src.models.tasks import Task
from src.pipeline import text_check as tc
from src.pipeline.text_check import run_text_check

_QUERY = "德龙EC685和柏翠PE3690入门咖啡机对比"


def _draft_json(body: str) -> str:
    return json.dumps({
        "query_clean": {"issues": [], "suggested": ""},
        "body_draft": body,
        "pages_draft": [f"P{i}" for i in range(1, 7)],
        "image_prompt_draft": [f"d{i}" for i in range(1, 7)],
    }, ensure_ascii=False)


async def _new_task(mode="compare", text_review=None) -> uuid.UUID:
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"refs-{uuid.uuid4().hex[:8]}",
                 query=_QUERY, content_type="x", mode=mode,
                 text_review=text_review)
        s.add(t)
        await s.commit()
        return t.id


async def _add_confirmed_ref(task_id, page_index=1, tag="r1") -> None:
    """造一条搜图①（ref_seed）产出的创作参考图（selection_status='text_ref'）。
    2026-09-07 两段式后 text_check 起草读 text_ref（不再是 confirmed）。"""
    async with SessionLocal() as s:
        s.add(Asset(task_id=task_id, page_index=page_index, subject=_QUERY,
                    source_type="official", copyright_status="unknown",
                    hash=f"h-{tag}-{uuid.uuid4().hex[:6]}",
                    image_url=f"/static/generated/ref_{tag}.png",
                    origin_url=f"https://example.com/{tag}.jpg",
                    model_version="bing", is_illustration=False,
                    selection_status="text_ref", ocr_hit=f"EC685,{tag}"))
        await s.commit()


def _capture(calls: list):
    async def fake(prompt, *args, **kw):
        calls.append(prompt)
        if "校稿编辑" in prompt:
            # 校稿轮回显待校正文（不参与本文件断言，保持正文稳定即可）
            return {"text": prompt.split("稿件：\n", 1)[-1],
                    "model_version": "k", "cost_cny": 0.0, "degraded": False}
        return {"text": _draft_json("正文草稿。" * 100),
                "model_version": "m", "cost_cny": 0.0, "degraded": False}
    return fake


async def test_draft_prompt_includes_confirmed_refs():
    """有 text_ref 创作参考图：全新起草提示词含参考图段（URL/来源/OCR 命中 + 贴合要求）。"""
    task_id = await _new_task()
    await _add_confirmed_ref(task_id, 1, "ec685")
    await _add_confirmed_ref(task_id, 2, "pe3690")
    calls: list = []
    with patch.object(tc, "call_with_failover", side_effect=_capture(calls)):
        await run_text_check(task_id)
    prompt = calls[0]
    assert "已确认实景参考图" in prompt
    assert "/static/generated/ref_ec685.png" in prompt
    assert "https://example.com/ec685.jpg" in prompt     # 来源 URL
    assert "EC685" in prompt                             # OCR 命中词
    assert "不得编写参考图里没有的内容" in prompt
    # 参考图段追加在人设化共享段之后
    assert prompt.index("已确认实景参考图") > prompt.index(_DRAFT_SHARED[:20])


async def test_draft_prompt_zero_diff_without_refs():
    """无确认参考图：起草提示词与此前逐字节一致（零差异守卫）。"""
    task_id = await _new_task()
    calls: list = []
    with patch.object(tc, "call_with_failover", side_effect=_capture(calls)):
        await run_text_check(task_id)
    expected = (tc._TEXT_CHECK_PROMPT.format(
        query=_QUERY, mode_desc=tc._MODE_DESC["compare"]) + "\n" + _DRAFT_SHARED)
    assert calls[0] == expected
    assert "已确认实景参考图" not in calls[0]


async def test_rewrite_branch_includes_refs():
    """手工底稿改写分支同样追加参考图段（且保留手写正文注入）。"""
    user_body = "手写正文：两台机器都用了一段时间。" * 20
    task_id = await _new_task(
        text_review={"source": "manual", "user_body": user_body})
    await _add_confirmed_ref(task_id, 1, "ec685")
    calls: list = []
    with patch.object(tc, "call_with_failover", side_effect=_capture(calls)):
        await run_text_check(task_id)
    prompt = calls[0]
    assert user_body[:30] in prompt                      # 改写分支底稿注入
    assert "已确认实景参考图" in prompt
    assert prompt.index("已确认实景参考图") > prompt.index(user_body[:30])


async def test_feedback_branch_includes_refs():
    """驳回定向修改分支同样追加参考图段。"""
    task_id = await _new_task(text_review={
        "body_draft": "旧稿正文", "pages_draft": ["P1"], "image_prompt_draft": ["d1"],
        "feedback": [{"target": "body", "note": "第二段压缩"}]})
    await _add_confirmed_ref(task_id, 1, "pe3690")
    calls: list = []
    with patch.object(tc, "call_with_failover", side_effect=_capture(calls)):
        await run_text_check(task_id)
    prompt = calls[0]
    assert "第二段压缩" in prompt                        # 驳回意见注入
    assert "已确认实景参考图" in prompt
    assert "/static/generated/ref_pe3690.png" in prompt


async def test_candidate_assets_not_injected():
    """仅 candidate/confirmed（非 text_ref）的图不进入起草提示词。"""
    for status in ("candidate", "confirmed"):
        task_id = await _new_task()
        async with SessionLocal() as s:
            s.add(Asset(task_id=task_id, page_index=1, subject=_QUERY,
                        source_type="official", copyright_status="unknown",
                        hash=f"h-{status}-{uuid.uuid4().hex[:6]}",
                        image_url=f"/static/generated/{status}.png",
                        model_version="bing", is_illustration=False,
                        selection_status=status))
            await s.commit()
        calls: list = []
        with patch.object(tc, "call_with_failover", side_effect=_capture(calls)):
            await run_text_check(task_id)
        assert "已确认实景参考图" not in calls[0]
        assert f"{status}.png" not in calls[0]
