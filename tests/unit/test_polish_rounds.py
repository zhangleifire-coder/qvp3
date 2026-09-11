"""text_check 两轮 Kimi 校稿 + polish/audit-sop 提示词库注册 单测（2026-09-07）。

设计变更「DeepSeek 生文，Kimi 两轮校稿修正」：
- 起草 JSON 解析成功后、落库前对 body_draft 做两轮校稿（Kimi 主、DeepSeek 备）；
- 长度护栏（校后 < 校前 60% 弃用该轮）、单轮失败/返回空容错不阻断；
- 留痕 text_review.polish；STAGE_CATALOG/default_prompt 注册三个新 stage。

mock 手法与 tests/unit 现有一致：patch src.pipeline.text_check.call_with_failover
（patch 对 async 目标自动生成 AsyncMock）；DB 走 conftest 测试库。
"""
import json
import uuid

from sqlalchemy import select
from unittest.mock import patch

from src.db.session import SessionLocal
from src.gateway import skill_loader
from src.gateway.failover import DEEPSEEK_MODEL, KIMI_MODEL
from src.gateway.prompt_versions import STAGE_CATALOG, default_prompt
from src.models.tasks import Task
from src.pipeline import text_check as tc
from src.pipeline.text_check import run_text_check


def _draft_json(body: str) -> str:
    return json.dumps({
        "query_clean": {"issues": [], "suggested": ""},
        "body_draft": body,
        "pages_draft": [f"P{i}" for i in range(1, 7)],
        "image_prompt_draft": [f"d{i}" for i in range(1, 7)],
    }, ensure_ascii=False)


def _llm(text: str, model: str = "m", cost: float = 0.01) -> dict:
    return {"text": text, "model_version": model, "cost_cny": cost,
            "degraded": False}


async def _new_task() -> uuid.UUID:
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"polish-{uuid.uuid4().hex[:8]}",
                 query="冰牛奶怎么选", content_type="x", mode="general")
        s.add(t)
        await s.commit()
        return t.id


async def _review(task_id) -> dict:
    async with SessionLocal() as s:
        t = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()
        return t.text_review


# ── 提示词库注册 ──

def test_stage_catalog_registers_three_new_stages():
    stages = {c["stage"]: c for c in STAGE_CATALOG}
    for s in ("polish_round1", "polish_round2", "audit_sop"):
        assert s in stages, f"STAGE_CATALOG 缺少 {s}"
        assert stages[s]["modes"] == [None]
        assert stages[s]["hint"]
    assert "{body}" in stages["polish_round1"]["hint"]
    assert "{body}" in stages["polish_round2"]["hint"]


def test_default_prompt_matches_skill_texts():
    """default_prompt 三分支返回与 skills/ 包文本一致（含 {body} 占位）。"""
    r1 = skill_loader.fragment("polish", "round1")
    r2 = skill_loader.fragment("polish", "round2")
    sop = skill_loader.skill_body("audit-sop")
    assert default_prompt("polish_round1") == r1
    assert default_prompt("polish_round2") == r2
    assert default_prompt("audit_sop") == sop
    assert "{body}" in r1 and "{body}" in r2
    assert "第一轮校稿" in r1 and "终校" in r2
    assert "信源采信优先级" in sop and "输出纪律" in sop


# ── 两轮校稿主流程 ──

async def test_two_rounds_kimi_primary_and_trace():
    """两轮均 Kimi 主、DeepSeek 备；检查单文本来自 skill 默认；polish 留痕。"""
    task_id = await _new_task()
    draft_body = "原稿正文。" * 100          # 500 字
    round1_text = "第一轮校后。" * 90        # 540 字 ≥ 60%
    round2_text = "终校正文。" * 90          # 450 字 ≥ 60% of 540
    seen = []

    async def fake(prompt, *args, **kw):
        seen.append((prompt, args))
        return [_llm(_draft_json(draft_body), model="deepseek/m", cost=0.02),
                _llm(round1_text, model="kimi/r1", cost=0.01),
                _llm(round2_text, model="kimi/r2", cost=0.03)][len(seen) - 1]

    with patch.object(tc, "call_with_failover", side_effect=fake):
        r = await run_text_check(task_id)

    assert len(seen) == 3                              # 起草 + 两轮校稿
    draft_call, r1_call, r2_call = seen
    assert draft_call[1] == ()                         # 起草不带显式模型（默认 DeepSeek 主）
    for call, marker in ((r1_call, "第一轮校稿"), (r2_call, "第二轮校稿")):
        prompt, args = call
        assert args[0] == KIMI_MODEL and args[1] == DEEPSEEK_MODEL   # Kimi 主
        assert marker in prompt                       # 检查单文本来自 skill 默认
        assert "{body}" not in prompt                 # 占位已替换
    assert draft_body in r1_call[0]                   # 第1轮校原稿
    assert round1_text in r2_call[0]                  # 第2轮校第1轮结果

    rv = await _review(task_id)
    assert rv["body_draft"] == round2_text            # 终稿为第2轮校后文本
    assert rv["pages_draft"] == [f"P{i}" for i in range(1, 7)]   # 分页不动
    assert rv["image_prompt_draft"] == [f"d{i}" for i in range(1, 7)]
    p = rv["polish"]
    assert p["round1"] == "ok" and p["round2"] == "ok"
    assert p["model"] == {"round1": "kimi/r1", "round2": "kimi/r2"}
    assert abs(p["cost_cny"] - 0.04) < 1e-9           # 两轮合计
    # 节点返回带出成本（execute_node 提取入 node_events）：起草 + 两轮校稿
    assert abs(r["cost_cny"] - 0.06) < 1e-9


async def test_guardrail_discards_round_and_keeps_previous():
    """长度护栏：校后不足校前 60% → 弃用该轮结果保留前文（两轮独立判定）。"""
    task_id = await _new_task()
    draft_body = "原稿正文。" * 100          # 500 字
    round1_text = "第一轮校后。" * 90        # 540 字，护栏通过
    seen = []

    async def fake(prompt, *args, **kw):
        seen.append(prompt)
        return [_llm(_draft_json(draft_body)),
                _llm(round1_text),
                _llm("过短")][len(seen) - 1]  # 第2轮 2 字 < 540*0.6 → 护栏触发

    with patch.object(tc, "call_with_failover", side_effect=fake):
        await run_text_check(task_id)

    rv = await _review(task_id)
    assert rv["body_draft"] == round1_text            # 弃用第2轮，保留第1轮
    assert rv["polish"]["round1"] == "ok"
    assert rv["polish"]["round2"] == "skipped"


async def test_round_failure_tolerated():
    """单轮调用异常 → failed 留痕、跳过该轮不阻断，下一轮校未校文本。"""
    task_id = await _new_task()
    draft_body = "原稿正文。" * 100
    round2_text = "终校正文。" * 90
    seen = []

    async def fake(prompt, *args, **kw):
        seen.append(prompt)
        if len(seen) == 2:
            raise RuntimeError("kimi down")           # 第1轮校稿失败
        return [_llm(_draft_json(draft_body)), None,
                _llm(round2_text)][len(seen) - 1]

    with patch.object(tc, "call_with_failover", side_effect=fake):
        r = await run_text_check(task_id)

    assert len(seen) == 3                              # 失败不阻断后续轮
    assert draft_body in seen[2]                       # 第2轮校的是未校原文
    rv = await _review(task_id)
    assert rv["body_draft"] == round2_text
    p = rv["polish"]
    assert p["round1"] == "failed" and p["round2"] == "ok"
    assert p["errors"] and "round1" in p["errors"][0]
    assert abs(r["cost_cny"] - 0.02) < 1e-9            # 起草 + 第2轮（第1轮无产出）


async def test_empty_body_skips_polish():
    """body 为空：两轮均 skipped，不发起校稿调用。"""
    task_id = await _new_task()
    seen = []

    async def fake(prompt, *args, **kw):
        seen.append(prompt)
        return _llm(_draft_json(""))

    with patch.object(tc, "call_with_failover", side_effect=fake):
        r = await run_text_check(task_id)

    assert len(seen) == 1                              # 只有起草调用
    rv = await _review(task_id)
    p = rv["polish"]
    assert p["round1"] == "skipped" and p["round2"] == "skipped"
    assert p["cost_cny"] == 0.0 and p["model"] == {}
    assert r["auto_ok"] is True
