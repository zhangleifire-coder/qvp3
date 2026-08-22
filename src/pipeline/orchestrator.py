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
NODES_AGENT = [
    "task_import", "agent_production", "rule_check", "cross_check",
    "risk_classify", "review_queue", "batch_signoff", "publish_snapshot",
]


async def _node_agent_production(input_data: dict) -> dict:
    from src.pipeline.agent_production import node_agent_production
    return await node_agent_production(input_data)


NODE_FN_AGENT = {
    "agent_production": _node_agent_production,
    "rule_check": node_rule_check,
    "cross_check": node_cross_check,
    "risk_classify": node_risk_classify,
    "review_queue": node_review_queue,
    "batch_signoff": node_batch_signoff,
    "publish_snapshot": node_publish_snapshot,
}


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
        r = await execute_node(task_id, node_name, inputs, fn)
        results.append({"node": node_name, "result": r})
    return results
