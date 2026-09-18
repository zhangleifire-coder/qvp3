from src.config import settings
from src.pipeline.nodes import (
    execute_node, NODES, node_entity_bind, node_evidence_build, node_draft_gen,
    node_rule_check, node_page_split, node_asset_gen, node_ocr_read, node_cross_check,
    node_risk_classify, node_review_queue, node_batch_signoff, node_publish_snapshot,
)

NODE_FN = {
    "entity_bind": node_entity_bind,
    "evidence_build": node_evidence_build,
    "draft_gen": node_draft_gen,
    "rule_check": node_rule_check,
    "page_split": node_page_split,
    "asset_gen": node_asset_gen,
    "ocr_read": node_ocr_read,
    "cross_check": node_cross_check,
    "risk_classify": node_risk_classify,
    "review_queue": node_review_queue,
    "batch_signoff": node_batch_signoff,
    "publish_snapshot": node_publish_snapshot,
}

# ── 创作 Agent 路径（2026-08-22 改造；网关 2026-09-18 起为 dsh_serve）───
# 创作段六节点（entity_bind/evidence_build/draft_gen/page_split/asset_gen/
# ocr_read）收敛为一个 agent_production 大节点；确定性节点全部保留。
# 两段式实景搜图（2026-09-07，吸收 8002 分叉栈）：
# ref_seed（搜图①，自动无人工关：搜图反哺 text_check 起草）→ text_check
# （人工关 awaiting_text）→ ref_collect（搜图②：text_ref 叠加新搜，
# 人工关 awaiting_refs）；确认后幂等续跑机制不变。
NODES_AGENT = [
    "task_import", "ref_seed", "text_check", "ref_collect", "agent_production",
    "rule_check", "cross_check", "risk_classify", "review_queue",
    "batch_signoff", "publish_snapshot",
]


async def _node_text_check(input_data: dict) -> dict:
    """文字自查+人工核查关卡（2026-08-27）：query 中文自查 + 文案/生图描述起草
    → 挂起 awaiting_text 等人工最终核查放行。确认后重跑此节点幂等跳过。"""
    from src.pipeline.text_check import run_text_check
    task_id = input_data["task_id"]
    from sqlalchemy import select as _sel
    from src.db.session import SessionLocal
    from src.models.tasks import Task
    async with SessionLocal() as session:
        t = (await session.execute(_sel(Task).where(Task.id == task_id))).scalar_one()
        # 幂等跳过：仅当起草已完成（有 body_draft）或人工已核查（text_override）。
        # 手工内容导入预存的 {source, user_body} 不算完成——需走改写模式起草。
        rv = t.text_review or {}
        if t.text_override is not None or rv.get("body_draft"):
            return {"skipped": True, "reason": "已完成文字自查/核查"}
    r = await run_text_check(task_id)
    return {"text_gate": True, **r}


async def _node_ref_collect(input_data: dict) -> dict:
    from src.pipeline.ref_collect import node_ref_collect
    return await node_ref_collect(input_data)


async def _node_ref_seed(input_data: dict) -> dict:
    from src.pipeline.ref_seed import node_ref_seed
    return await node_ref_seed(input_data)


async def _node_agent_production(input_data: dict) -> dict:
    from src.pipeline.agent_production import node_agent_production
    return await node_agent_production(input_data)


# ── staged 分阶段 Agent 路径（2026-09-09，AGENT_PIPELINE_VARIANT=staged）────
# agent_production 大节点拆为 4 个独立 Agent 节点（各自独立会话/契约/幂等/
# 成本帧），阶段间只经 DB 传数据；确定性节点与 monolith 完全一致。
NODES_AGENT_STAGED = [
    "task_import", "ref_seed", "text_check", "ref_collect",
    "agent_evidence", "agent_draft", "agent_pages", "agent_assets",
    "rule_check", "cross_check", "risk_classify", "review_queue",
    "batch_signoff", "publish_snapshot",
]


async def _node_agent_evidence(input_data: dict) -> dict:
    from src.pipeline.agent_stages import node_agent_evidence
    return await node_agent_evidence(input_data)


async def _node_agent_draft(input_data: dict) -> dict:
    from src.pipeline.agent_stages import node_agent_draft
    return await node_agent_draft(input_data)


async def _node_agent_pages(input_data: dict) -> dict:
    from src.pipeline.agent_stages import node_agent_pages
    return await node_agent_pages(input_data)


async def _node_agent_assets(input_data: dict) -> dict:
    from src.pipeline.agent_stages import node_agent_assets
    return await node_agent_assets(input_data)


NODE_FN_AGENT_STAGED = {
    "ref_seed": _node_ref_seed,
    "text_check": _node_text_check,
    "ref_collect": _node_ref_collect,
    "agent_evidence": _node_agent_evidence,
    "agent_draft": _node_agent_draft,
    "agent_pages": _node_agent_pages,
    "agent_assets": _node_agent_assets,
    "rule_check": node_rule_check,
    "cross_check": node_cross_check,
    "risk_classify": node_risk_classify,
    "review_queue": node_review_queue,
    "batch_signoff": node_batch_signoff,
    "publish_snapshot": node_publish_snapshot,
}


NODE_FN_AGENT = {
    "ref_seed": _node_ref_seed,
    "text_check": _node_text_check,
    "ref_collect": _node_ref_collect,
    "agent_production": _node_agent_production,
    "rule_check": node_rule_check,
    "cross_check": node_cross_check,
    "risk_classify": node_risk_classify,
    "review_queue": node_review_queue,
    "batch_signoff": node_batch_signoff,
    "publish_snapshot": node_publish_snapshot,
}


async def _suspend_for_text(task_id, r: dict) -> None:
    """文字核查关卡：任务挂起 awaiting_text（编排器/调度器识别）。"""
    from sqlalchemy import select as _select
    from src.db.session import SessionLocal
    from src.models.tasks import Task
    from src.stream.bus import bus
    async with SessionLocal() as session:
        t = (await session.execute(
            _select(Task).where(Task.id == task_id))).scalar_one()
        t.status = "awaiting_text"
        await session.commit()
    await bus.publish("node_finished", {
        "node": "text_check", "label": "文字自查",
        "msg": (f"文案 {r.get('candidates_pages', 0)} 页 + 生图描述起草完成"
                + (f"，发现 {r.get('issues', 0)} 个问题待核查"
                   if r.get("issues") else "，自查通过")
                + "，等待人工最终核查放行")}, task_id=str(task_id))


async def _suspend_for_refs(task_id, r: dict) -> None:
    """参考图关卡：任务挂起 awaiting_refs（调度器收尾会尊重该状态不覆盖）。"""
    from sqlalchemy import select as _select
    from src.db.session import SessionLocal
    from src.models.tasks import Task
    from src.stream.bus import bus
    async with SessionLocal() as session:
        t = (await session.execute(
            _select(Task).where(Task.id == task_id))).scalar_one()
        t.status = "awaiting_refs"
        await session.commit()
    await bus.publish("node_finished", {
        "node": "ref_collect", "label": "参考图确认",
        "msg": f"候选 {r.get('candidates', 0)} 张（OCR 命中 {r.get('ocr_hits', 0)}），"
               "等待人工确认后继续生产"}, task_id=str(task_id))


async def run_pipeline(task_id, node_inputs: dict | None = None) -> list:
    from src.db.session import SessionLocal
    from src.services.regen import get_rejection_feedback
    results = []
    inputs = dict(node_inputs) if node_inputs else {"task_id": task_id}
    # 驳回重生成：把审核反馈放进节点输入——幂等键随输入变化，
    # 相关节点自动重跑；驳回次数保证同一理由再次驳回时键仍不同。
    async with SessionLocal() as session:
        rounds, reasons = await get_rejection_feedback(session, task_id)
    if rounds:
        inputs["regen"] = {"round": rounds, "feedback": reasons}
    # 双路径：AGENT_PIPELINE_ENABLED 决定走创作大节点（dsh_serve 网关）还是原 13 节点直连；
    # Agent 路径内再由 AGENT_PIPELINE_VARIANT 分发：staged=4 个分阶段 Agent 节点，
    # monolith（默认）=agent_production 大节点（秒级回退）
    if settings.agent_pipeline_enabled:
        if settings.agent_pipeline_variant == "staged":
            nodes, fns = NODES_AGENT_STAGED, NODE_FN_AGENT_STAGED
        else:
            nodes, fns = NODES_AGENT, NODE_FN_AGENT
    else:
        nodes, fns = NODES, NODE_FN
    for node_name in nodes:
        fn = fns.get(node_name)
        r = await execute_node(task_name_wrap(task_id), node_name, inputs, fn) \
            if False else await execute_node(task_id, node_name, inputs, fn)
        results.append({"node": node_name, "result": r})
        # 参考图确认关卡：候选就绪 → 挂起任务等人工确认（确认后重新入队续跑，
        # ref_collect 已完成会幂等跳过，agent_production 用确认后的参考图）
        if isinstance(r, dict) and r.get("ref_gate"):
            await _suspend_for_refs(task_id, r)
            break
        if isinstance(r, dict) and r.get("text_gate"):
            await _suspend_for_text(task_id, r)
            break
    return results
