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

# ── Nanobot 全链创作 Agent 路径（2026-08-22 改造）─────────────────────
# 创作段六节点（entity_bind/evidence_build/draft_gen/page_split/asset_gen/
# ocr_read）收敛为一个 agent_production 大节点；确定性节点全部保留。
# ref_collect（2026-08-27）：compare/single 的参考图人工确认关卡——
# 搜图≥10张 → OCR 初筛 → 挂起 awaiting_refs 等人工确认后才继续生图。
NODES_AGENT = [
    "task_import", "ref_collect", "agent_production", "rule_check", "cross_check",
    "risk_classify", "review_queue", "batch_signoff", "publish_snapshot",
]


async def _node_ref_collect(input_data: dict) -> dict:
    from src.pipeline.ref_collect import node_ref_collect
    return await node_ref_collect(input_data)


async def _node_agent_production(input_data: dict) -> dict:
    from src.pipeline.agent_production import node_agent_production
    return await node_agent_production(input_data)


NODE_FN_AGENT = {
    "ref_collect": _node_ref_collect,
    "agent_production": _node_agent_production,
    "rule_check": node_rule_check,
    "cross_check": node_cross_check,
    "risk_classify": node_risk_classify,
    "review_queue": node_review_queue,
    "batch_signoff": node_batch_signoff,
    "publish_snapshot": node_publish_snapshot,
}


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
    # 双路径：AGENT_PIPELINE_ENABLED 决定走 Nanobot 创作大节点还是原 13 节点直连
    if settings.agent_pipeline_enabled:
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
    return results
