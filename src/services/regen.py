"""驳回重生成支持：汇总审核驳回反馈 + 清理上一轮生成产物 + 定点重生成。

设计要点：
- 反馈来源是 review_actions（action_type='reject'）的 payload.reason，
  按时间顺序全部保留，让每一轮重生产都知道历史上被驳回过什么。
- 重试驳回任务时删除上一轮的内容产物（草稿/分页/配图/OCR/校验/风险/证据/快照），
  避免新旧内容混杂（旧分页会被 asset_gen 误用、旧配图会让交付校验报"两图同页"）。
- 保留 node_events（成本与审计轨迹）、issues / review_actions / approvals（审核历史）。
- 若驳回时带定点标记（reject_marks），走 partial_regen：只重做被标记的
  页文案/配图，其余已认可内容原样保留，不重复消耗生图算力。
"""
from datetime import datetime, timezone
from sqlalchemy import delete, select, update


async def get_rejection_feedback(session, task_id) -> tuple[int, list[str]]:
    """返回 (驳回次数, 按时间排序的全部驳回理由)。"""
    from src.models.review import ReviewAction, ReviewSession
    rows = (await session.execute(
        select(ReviewAction)
        .join(ReviewSession, ReviewAction.review_session_id == ReviewSession.id)
        .where(ReviewSession.task_id == task_id,
               ReviewAction.action_type == "reject")
        .order_by(ReviewAction.server_ts))).scalars().all()
    reasons = []
    for a in rows:
        r = ((a.payload or {}).get("reason") or "").strip()
        reasons.append(r or "未填写具体理由，请整体提升内容质量")
    return len(rows), reasons


async def clear_generated_content(session, task_id) -> None:
    """删除任务上一轮生成的内容产物（驳回重试前调用）。

    不删：tasks 行本身、node_events（幂等/成本审计）、issues、
    review_actions / approvals（审核历史，反馈来源）、activity_logs。
    未完成的 review_sessions 一并删除（否则审核队列出现重复条目）；
    已完成的保留（其 review_actions 是驳回反馈的来源）。
    """
    from src.models.assets import Asset, OcrResult, CrossCheck
    from src.models.drafts import Draft, PageCopy, RuleResult
    from src.models.entities import Claim, Evidence
    from src.models.review import RiskClassification, ReviewSession
    from src.models.snapshots import PublishSnapshot

    asset_ids = (await session.execute(
        select(Asset.id).where(Asset.task_id == task_id))).scalars().all()
    if asset_ids:
        await session.execute(delete(OcrResult).where(OcrResult.asset_id.in_(asset_ids)))
    await session.execute(delete(CrossCheck).where(CrossCheck.task_id == task_id))
    await session.execute(delete(Asset).where(Asset.task_id == task_id))
    await session.execute(delete(RuleResult).where(RuleResult.task_id == task_id))
    await session.execute(delete(PageCopy).where(PageCopy.task_id == task_id))
    await session.execute(delete(Draft).where(Draft.task_id == task_id))
    await session.execute(delete(RiskClassification).where(RiskClassification.task_id == task_id))
    claim_ids = (await session.execute(
        select(Claim.id).where(Claim.task_id == task_id))).scalars().all()
    if claim_ids:
        await session.execute(delete(Evidence).where(Evidence.claim_id.in_(claim_ids)))
    await session.execute(delete(Claim).where(Claim.task_id == task_id))
    await session.execute(delete(PublishSnapshot).where(PublishSnapshot.task_id == task_id))
    await session.execute(
        delete(ReviewSession).where(ReviewSession.task_id == task_id,
                                    ReviewSession.finished_at.is_(None)))


async def get_open_marks(session, task_id):
    """该任务未处理的定点驳回标记（按标记时间排序）。"""
    from src.models.review import RejectMark
    return (await session.execute(
        select(RejectMark).where(RejectMark.task_id == task_id,
                                 RejectMark.status == "open")
        .order_by(RejectMark.created_at))).scalars().all()


def _fill_template(template: str, mapping: dict) -> str:
    """替换 {placeholder}；模板里缺的占位符以【标签】段落追加，保证信息不丢。"""
    labels = {"body": "正文", "old_copy": "原页文案", "feedback": "审核意见",
              "page_index": "页码"}
    prompt = template
    for key, val in mapping.items():
        ph = "{" + key + "}"
        if ph in prompt:
            prompt = prompt.replace(ph, str(val))
        else:
            prompt += f"\n\n【{labels.get(key, key)}】\n{val}"
    return prompt


async def partial_regen(task_id) -> dict:
    """定点重生成：只重做被驳回标记的页文案/配图，其余产物原样保留。

    流程：重写被标记页文案（page_regen 提示词 + 审核意见）→ 重生成被标记页配图
    （被重写文案的页自动连带重生成图，保证图文一致）→ OCR 新图 →
    重建交叉校验/风险分级 → 重排审核会话 → 标记置 resolved。
    通过 execute_node 记录节点事件与成本，监控页可见进度。
    """
    import asyncio
    from src.config import settings
    from src.db.session import SessionLocal
    from src.models.tasks import Task
    from src.models.drafts import PageCopy
    from src.models.assets import Asset, OcrResult, CrossCheck
    from src.models.review import RejectMark, ReviewSession, RiskClassification
    from src.pipeline.nodes import (
        execute_node, _latest_draft_body, _generate_single_asset,
        _dedupe_and_validate, node_cross_check, node_risk_classify,
        node_review_queue, call_with_failover)
    from src.gateway.failover import DEEPSEEK_MODEL, KIMI_MODEL
    from src.gateway.prompt_versions import get_effective_prompt, get_image_prompt

    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        mode = task.mode or "general"
        owner_id = task.created_by
        marks = await get_open_marks(session, task_id)
        rounds, _ = await get_rejection_feedback(session, task_id)
    if not marks:
        return {"regenerated": 0}

    page_reasons: dict[int, list[str]] = {}
    image_reasons: dict[int, list[str]] = {}
    for m in marks:
        bucket = page_reasons if m.item_type == "page" else image_reasons
        bucket.setdefault(m.page_index, []).append(m.reason or "存在问题，请重做")
    pages_to_rewrite = sorted(page_reasons)
    # 文案被重写的页必须连带重生成配图（图文一致），加上直接被标记的图
    images_to_regen = sorted(set(image_reasons) | set(pages_to_rewrite))
    base_input = {"task_id": task_id, "regen_round": rounds}

    async def _rewrite_pages(input_data: dict) -> dict:
        template = await get_effective_prompt("page_regen", None, owner_id)
        async with SessionLocal() as session:
            draft_body = await _latest_draft_body(session, task_id)
            rows = (await session.execute(
                select(PageCopy).where(PageCopy.task_id == task_id,
                                       PageCopy.page_index.in_(pages_to_rewrite))
            )).scalars().all()
            old_map = {r.page_index: r.body or "" for r in rows}
        total_cost = 0.0
        models = set()
        for p in pages_to_rewrite:
            fb = "\n".join(f"{i}. {r}" for i, r in enumerate(page_reasons[p], 1))
            prompt = _fill_template(template, {
                "page_index": p, "body": draft_body,
                "old_copy": old_map.get(p, ""), "feedback": fb})
            result = await call_with_failover(prompt, DEEPSEEK_MODEL, KIMI_MODEL)
            total_cost += result["cost_cny"]
            models.add(result["model_version"])
            new_body = result["text"].strip().strip('"`')
            async with SessionLocal() as session:
                row = (await session.execute(
                    select(PageCopy).where(PageCopy.task_id == task_id,
                                           PageCopy.page_index == p))).scalars().first()
                if row:
                    row.body = new_body
                else:
                    session.add(PageCopy(task_id=task_id, page_index=p,
                                         body=new_body, claim_ids=[]))
                await session.commit()
        return {"pages": pages_to_rewrite, "cost_cny": total_cost,
                "model_version": "/".join(sorted(models)) or None,
                "prompt_version": f"page_regen_r{rounds}"}

    async def _regen_images(input_data: dict) -> dict:
        from src.gateway.ocr import ocr_image
        image_template = await get_effective_prompt("image_gen", mode, owner_id)
        async with SessionLocal() as session:
            # 保留图的 hash 作为去重基准：重生成图不得与已认可的图重复
            kept = (await session.execute(
                select(Asset.hash).where(Asset.task_id == task_id,
                                         Asset.source_type == "ai_generated",
                                         ~Asset.page_index.in_(images_to_regen)))
            ).scalars().all()
            seen_hashes = {h for h in kept if h}
            reference_urls = None
            if mode in ("compare", "single"):
                refs = (await session.execute(
                    select(Asset.image_url).where(
                        Asset.task_id == task_id, Asset.source_type == "official",
                        Asset.is_illustration == False))).scalars().all()
                reference_urls = [u for u in refs if u]
            page_rows = (await session.execute(
                select(PageCopy).where(PageCopy.task_id == task_id,
                                       PageCopy.page_index.in_(images_to_regen))
            )).scalars().all()
            body_map = {r.page_index: r.body or "" for r in page_rows}
        extra_gen = 0
        ocr_cost = 0.0
        done_pages = []
        for p in images_to_regen:
            prompt = get_image_prompt(mode, body_map.get(p, ""), p,
                                      template=image_template)
            fb = image_reasons.get(p, []) + page_reasons.get(p, [])
            if fb:
                prompt += ("\n\n【审核意见】该页上一版本被人工审核驳回："
                           + "；".join(fb) + "。请重新绘制，必须避免上述问题。")
            r = await _generate_single_asset(task_id, p, prompt, reference_urls)
            if not settings.mock_image_gen:
                r, extra = await _dedupe_and_validate(
                    r, prompt, reference_urls, task_id, p, seen_hashes)
                extra_gen += extra
            async with SessionLocal() as session:
                olds = (await session.execute(
                    select(Asset).where(Asset.task_id == task_id,
                                        Asset.source_type == "ai_generated",
                                        Asset.page_index == p))).scalars().all()
                for o in olds:
                    await session.execute(
                        delete(OcrResult).where(OcrResult.asset_id == o.id))
                    await session.delete(o)
                await session.flush()
                asset = Asset(**r)
                session.add(asset)
                await session.flush()
                # 新图 OCR（沿用 node_ocr_read 的容错口径：失败记 confidence 0）
                if settings.mock_image_gen:
                    session.add(OcrResult(
                        asset_id=asset.id, raw_text=f"page {p}",
                        key_fields={"page": str(p)}, confidence=0.95))
                else:
                    try:
                        oc = await ocr_image(r["image_url"])
                        session.add(OcrResult(
                            asset_id=asset.id, raw_text=oc["raw_text"],
                            key_fields={"page": str(p),
                                        "ocr_model": oc["model"]},
                            confidence=0.9))
                        ocr_cost += oc["cost_cny"]
                    except Exception:
                        session.add(OcrResult(
                            asset_id=asset.id, raw_text="",
                            key_fields={"page": str(p)}, confidence=0.0))
                await session.commit()
            done_pages.append(p)
            await asyncio.sleep(settings.image_gen_delay_seconds)
        cost = ocr_cost if settings.mock_image_gen else (
            (len(done_pages) + extra_gen) * settings.image_cost_per_image_cny
            + ocr_cost)
        return {"pages": done_pages, "extra_gen": extra_gen, "cost_cny": cost,
                "prompt_version": f"asset_regen_r{rounds}"}

    if pages_to_rewrite:
        await execute_node(task_id, "page_regen",
                           {**base_input, "pages": pages_to_rewrite},
                           _rewrite_pages)
    if images_to_regen:
        await execute_node(task_id, "asset_regen",
                           {**base_input, "pages": images_to_regen},
                           _regen_images)
    # 校验链重建（本地计算 + 已有 OCR，成本低）：基于最新内容重跑
    async with SessionLocal() as session:
        await session.execute(delete(CrossCheck).where(CrossCheck.task_id == task_id))
        await session.execute(
            delete(RiskClassification).where(RiskClassification.task_id == task_id))
        # 上一轮换未完成审核的会话作废，避免审核队列重复
        await session.execute(
            delete(ReviewSession).where(ReviewSession.task_id == task_id,
                                        ReviewSession.finished_at.is_(None)))
        await session.commit()
    await execute_node(task_id, "cross_check", base_input, node_cross_check)
    await execute_node(task_id, "risk_classify", base_input, node_risk_classify)
    await execute_node(task_id, "review_queue", base_input, node_review_queue)
    # 标记闭环
    async with SessionLocal() as session:
        await session.execute(
            update(RejectMark).where(RejectMark.task_id == task_id,
                                     RejectMark.status == "open")
            .values(status="resolved",
                    resolved_at=datetime.now(timezone.utc)))
        await session.commit()
    return {"regenerated": len(marks),
            "pages_rewritten": pages_to_rewrite,
            "images_regenerated": images_to_regen}
