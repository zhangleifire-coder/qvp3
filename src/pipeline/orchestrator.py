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


async def run_pipeline(task_id, node_inputs: dict | None = None) -> list:
    from src.db.session import SessionLocal
    from src.services.regen import get_rejection_feedback
    results = []
    inputs = dict(node_inputs) if node_inputs else {"task_id": task_id}
    # 驳回重生成：把审核反馈放进节点输入——幂等键随输入变化，
    # 全链路节点自动重跑；驳回次数保证同一理由再次驳回时键仍不同。
    async with SessionLocal() as session:
        rounds, reasons = await get_rejection_feedback(session, task_id)
    if rounds:
        inputs["regen"] = {"round": rounds, "feedback": reasons}
    for node_name in NODES:
        fn = NODE_FN.get(node_name)
        r = await execute_node(task_id, node_name, inputs, fn)
        results.append({"node": node_name, "result": r})
    return results
