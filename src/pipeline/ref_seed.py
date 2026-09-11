"""ref_seed：搜图①——自动实景搜图反哺文字创作（无人工关，2026-09-07 两段式）。

节点序（Agent 路径）：task_import → ref_seed（本节点，自动）→ text_check
（人工关，起草提示词吃 text_ref 信息段）→ ref_collect（搜图②：text_ref
叠加新搜 → 人工确认关 awaiting_refs）→ agent_production → …

素材库复用 + 按主体搜图 + 下载本地化 + OCR 初筛全部复用
ref_collect._collect_candidates（同一实现）；结果落 assets
（selection_status='text_ref'，独立于 candidate/confirmed/rejected 语义），
不挂起，完成直接进 text_check。general 模式仍受 ref_for_general_enabled
门控（默认关=跳过，与 ref_collect 同语义，flag off 时整条链路零差异）。
"""
from sqlalchemy import delete, select

from src.config import settings
from src.db.session import SessionLocal
from src.models.assets import Asset
from src.models.tasks import Task
from src.pipeline.ref_collect import _collect_candidates


async def node_ref_seed(input_data: dict) -> dict:
    task_id = input_data["task_id"]
    from src.pipeline.nodes import _emit_progress

    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        mode, query = (task.mode or "general"), task.query

    # 与 ref_collect 同一门控：general 默认不走实景搜集（产品方向开关）
    if mode not in ("compare", "single") and not settings.ref_for_general_enabled:
        _emit_progress(task_id, "ref_seed", msg="general 无需创作参考图，跳过")
        return {"skipped": True, "reason": "general 无需创作参考图"}

    # 幂等：搜图①已产出（text_ref）、搜图②已汇集（candidate）或人工已确认
    # （confirmed）任一存在即跳过——重跑/续跑不重复搜图
    async with SessionLocal() as session:
        existing = (await session.execute(
            select(Asset.id).where(
                Asset.task_id == task_id, Asset.source_type == "official",
                Asset.selection_status.in_(["text_ref", "candidate", "confirmed"]))
            .limit(1))).first()
    if existing:
        _emit_progress(task_id, "ref_seed", msg="已有创作参考图，跳过")
        return {"skipped": True, "reason": "已有创作参考图（text_ref/candidate/confirmed）"}

    candidates, hits = await _collect_candidates(task_id, query, mode, "ref_seed")
    if not candidates:
        # 一张没搜到不挂起：文字创作照常（无参考图信息段，零差异路径）
        _emit_progress(task_id, "ref_seed", msg="未搜到实景图，文字创作照常")
        return {"text_refs": 0, "ocr_hits": 0,
                "reason": "未搜到实景图，文字创作照常"}
    async with SessionLocal() as session:
        # 重跑刷新：清掉旧 text_ref（candidate/confirmed 的人工结论不动）
        await session.execute(delete(Asset).where(
            Asset.task_id == task_id, Asset.source_type == "official",
            Asset.selection_status == "text_ref"))
        for rank, c in enumerate(candidates, start=1):
            session.add(Asset(
                task_id=task_id, page_index=rank, subject=query,
                source_type="official", copyright_status="unknown",
                hash=c["hash"], image_url=c["local_url"],
                origin_url=c["origin"], model_version=c["engine"],
                is_illustration=False,
                selection_status="text_ref", ocr_hit=c["ocr_hit"] or None))
        await session.commit()
    _emit_progress(task_id, "ref_seed",
                   msg=f"创作参考图 {len(candidates)} 张已就绪（OCR 命中 {hits} 张），"
                       "进入文字起草")
    return {"text_refs": len(candidates), "ocr_hits": hits}
