import csv
import io
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import delete, select, func, text
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.services.activity import log_action
from src.stream.scheduler import scheduler

router = APIRouter()


@router.post("/api/tasks/import")
async def import_tasks(file: UploadFile = File(...), actor: str = Form("anonymous")):
    content = await file.read()
    text_content = content.decode("utf-8-sig")  # 兼容 Excel 导出的带 BOM CSV
    reader = csv.DictReader(io.StringIO(text_content))
    imported = 0
    errors = []
    enqueued = []
    async with SessionLocal() as session:
        for row in reader:
            try:
                query = (row.get("query") or "").strip()
                if not query:
                    raise ValueError("query 为空（CSV 需包含 query 列）")
                content_type = (row.get("content_type") or "generic").strip()
                mode = (row.get("mode") or "general").strip()
                if mode not in ("general", "single", "compare"):
                    raise ValueError(f"mode 取值无效：{mode}（应为 general/single/compare）")
                platform = (row.get("platform") or "").strip()
                key = f"{query}|{content_type}|{platform}|{mode}"
                existing = await session.execute(
                    select(Task).where(Task.idempotency_key == key))
                if existing.first():
                    continue
                task = Task(
                    idempotency_key=key,
                    query=query,
                    content_type=content_type,
                    platform=platform or None,
                    mode=mode,
                    status="draft",
                )
                session.add(task)
                await session.flush()
                enqueued.append((task.id, query))
                imported += 1
            except Exception as e:
                errors.append({"row": dict(row), "error": str(e)})
        await session.commit()
    for tid, q in enqueued:
        await scheduler.enqueue(tid, q)
    if imported:
        await log_action(actor, "import_tasks",
                         f"CSV 导入 {imported} 条任务（文件 {file.filename}）")
    return {"imported": imported, "errors": errors}


class ImportQueriesIn(BaseModel):
    queries: list[str]        # 每行一个 Query
    content_type: str = "generic"   # 内容类型：generic / school / product / compare
    mode: str = "general"     # 生产模式：compare / single / general
    actor: str = "anonymous"


@router.post("/api/tasks/import_queries")
async def import_queries(payload: ImportQueriesIn):
    """批量导入 Query 文本，创建任务并加入队列（按并发限制排队执行）。"""
    imported = []
    async with SessionLocal() as session:
        for q in payload.queries:
            q = q.strip()
            if not q:
                continue
            key = f"{q}|{payload.content_type}|{payload.mode}"
            existing = await session.execute(
                select(Task).where(Task.idempotency_key == key))
            if existing.first():
                continue
            task = Task(idempotency_key=key, query=q,
                        content_type=payload.content_type, mode=payload.mode,
                        status="draft")
            session.add(task)
            await session.flush()
            imported.append((task.id, q))
        await session.commit()
    for tid, q in imported:
        await scheduler.enqueue(tid, q)
    if imported:
        sample = "、".join(q[:20] for _, q in imported[:3])
        await log_action(payload.actor, "import_tasks",
                         f"手工导入 {len(imported)} 条任务（模式 {payload.mode}）：{sample}"
                         + ("…" if len(imported) > 3 else ""))
    return {"imported": len(imported), "task_ids": [str(t) for t, _ in imported],
            "queued": True, "queue_size": scheduler.queue.qsize(),
            "concurrency": scheduler.limiter.capacity}


# ============ 组合生成导入（query × 泛化问题池 × 风格/垂类，2026-08-24） ============

class AnalyzeQueryIn(BaseModel):
    query: str                # 原始提问（长情境）
    count: int = 20           # 生成泛化问题数


@router.post("/api/tasks/analyze_query")
async def analyze_query(payload: AnalyzeQueryIn):
    """智能分析：LLM 从原始 query 生成泛化补充问题池（导入前可人工编辑）。"""
    from src.gateway.failover import call_with_failover
    from src.services.combo import analyze_prompt, parse_analyzed_questions
    query = payload.query.strip()
    if len(query) < 4:
        raise HTTPException(status_code=422, detail="query 太短，无法分析")
    count = max(5, min(payload.count, 30))
    result = await call_with_failover(analyze_prompt(query, count))
    questions = parse_analyzed_questions(result["text"])
    if not questions:
        raise HTTPException(status_code=502,
                            detail="模型未返回有效问题列表，请重试或手工填写")
    return {"query": query, "questions": questions,
            "model": result.get("model_version")}


class ComboRowIn(BaseModel):
    query: str                          # 原始提问情境
    supplement_pool: str | list[str]    # 泛化问题池（分隔符文本或列表）
    gen_style: str = ""                 # 风格条件（解读·经验分享…）
    gen_category: str = ""              # 垂类条件（家居/汽车…）
    rounds: int = 3                     # 生成数据条数


class ImportComboIn(BaseModel):
    rows: list[ComboRowIn]
    content_type: str = "generic"
    mode: str = "general"
    actor: str = "anonymous"


@router.post("/api/tasks/import_combo")
async def import_combo(payload: ImportComboIn):
    """组合生成导入：每行从泛化问题池随机抽 rounds 个问题，各自与原始 query
    组合成一条生产任务（query=抽中的问题，source_query=原始情境，风格/垂类落库）。"""
    import random as _random
    from src.services.combo import parse_pool, pick_rounds
    if payload.mode not in ("general", "single", "compare"):
        raise HTTPException(status_code=422, detail="mode 取值无效")
    created: list[tuple[object, str, str]] = []
    skipped: list[dict] = []
    async with SessionLocal() as session:
        for row in payload.rows:
            query = row.query.strip()
            pool = parse_pool(row.supplement_pool)
            if not query:
                skipped.append({"query": "", "reason": "原始 query 为空"})
                continue
            if not pool:
                skipped.append({"query": query[:50], "reason": "泛化问题池为空"})
                continue
            picked = pick_rounds(pool, row.rounds, rng=_random.Random())
            for q in picked:
                key = f"combo|{query}|{q}|{payload.mode}|{payload.content_type}"
                exists = await session.execute(
                    select(Task.id).where(Task.idempotency_key == key))
                if exists.first():
                    skipped.append({"query": q[:50],
                                    "reason": "该组合已存在（重复角度自动去重）"})
                    continue
                task = Task(
                    idempotency_key=key, query=q,
                    content_type=payload.content_type, mode=payload.mode,
                    status="draft",
                    source_query=query, supplement_question=q,
                    gen_style=row.gen_style.strip() or None,
                    gen_category=row.gen_category.strip() or None)
                session.add(task)
                await session.flush()
                created.append((task.id, q, query))
        await session.commit()
    for tid, q, _ in created:
        await scheduler.enqueue(tid, q)
    if created:
        sample = "、".join(q[:20] for _, q, _ in created[:3])
        await log_action(payload.actor, "import_tasks",
                         f"组合生成导入 {len(created)} 条任务（模式 {payload.mode}，"
                         f"风格 {payload.rows[0].gen_style or '默认'}）：{sample}"
                         + ("…" if len(created) > 3 else ""))
    return {"imported": len(created),
            "task_ids": [str(t) for t, _, _ in created],
            "items": [{"task_id": str(t), "question": q, "source": src[:80]}
                      for t, q, src in created],
            "skipped": skipped, "queued": True,
            "queue_size": scheduler.queue.qsize()}


@router.get("/api/tasks")
async def list_tasks(status: str | None = None, mode: str | None = None,
                     risk_level: str | None = None, q: str | None = None,
                     limit: int = 20, offset: int = 0):
    """任务列表：状态/模式/风险筛选 + 关键词搜索 + 分页，每项带当前节点与风险等级。"""
    from src.models.events import NodeEvent
    from src.models.review import RiskClassification
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    async with SessionLocal() as session:
        filters = []
        if status:
            filters.append(Task.status == status)
        if mode:
            filters.append(Task.mode == mode)
        if q:
            filters.append(Task.query.ilike(f"%{q.strip()}%"))
        if risk_level:
            filters.append(Task.id.in_(
                select(RiskClassification.task_id).where(RiskClassification.level == risk_level)))
        total = (await session.execute(
            select(func.count(Task.id)).where(*filters))).scalar() or 0
        tasks = (await session.execute(
            select(Task).where(*filters)
            .order_by(Task.created_at.desc()).limit(limit).offset(offset))).scalars().all()
        items = []
        for t in tasks:
            current = (await session.execute(
                select(NodeEvent.node_name).where(NodeEvent.task_id == t.id)
                .order_by(NodeEvent.enqueued_at.desc()).limit(1))).scalar_one_or_none()
            risk = (await session.execute(
                select(RiskClassification).where(
                    RiskClassification.task_id == t.id))).scalars().first()
            items.append({
                "id": str(t.id),
                "query": t.query,
                "mode": t.mode,
                "status": t.status,
                "risk_level": risk.level if risk else None,
                "current_node": current,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            })
        return {"total": total, "items": items}


@router.get("/api/tasks/{task_id}/detail")
async def task_detail(task_id: str):
    """任务详情：全字段 + 节点进度 + 正文/分页/图片/事实点/证据/风险 + 三方审核状态。"""
    from src.models.drafts import Draft, PageCopy
    from src.models.entities import Claim, Evidence
    from src.models.assets import Asset
    from src.models.events import NodeEvent
    from src.models.review import RiskClassification, ReviewSession, ReviewAction
    from src.api.review import REVIEW_ROLES
    from datetime import datetime, timezone
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid task_id")
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        events = (await session.execute(
            select(NodeEvent).where(NodeEvent.task_id == tid)
            .order_by(NodeEvent.enqueued_at))).scalars().all()
        completed_nodes = list(dict.fromkeys(
            e.node_name for e in events
            if e.finished_at is not None and e.error_class is None))
        current_node = events[-1].node_name if events else None
        draft = (await session.execute(
            select(Draft).where(Draft.task_id == tid)
            .order_by(Draft.version.desc()))).scalars().first()
        page_copies = (await session.execute(
            select(PageCopy).where(PageCopy.task_id == tid)
            .order_by(PageCopy.page_index))).scalars().all()
        assets = (await session.execute(
            select(Asset).where(Asset.task_id == tid)
            .order_by(Asset.page_index))).scalars().all()
        claims = (await session.execute(
            select(Claim).where(Claim.task_id == tid)
            .order_by(Claim.position))).scalars().all()
        evidences = []
        for c in claims:
            evs = (await session.execute(
                select(Evidence).where(Evidence.claim_id == c.id))).scalars().all()
            evidences.extend(evs)
        risk = (await session.execute(
            select(RiskClassification).where(
                RiskClassification.task_id == tid))).scalars().first()
        # 三方审核状态：每个角色的 session 状态 + 最新 action 结论 + 审核员
        sessions = (await session.execute(
            select(ReviewSession).where(ReviewSession.task_id == tid))).scalars().all()
        by_role: dict[str, list] = {}
        for rs in sessions:
            by_role.setdefault(rs.role, []).append(rs)
        review_status = []
        for role in REVIEW_ROLES:
            role_sessions = by_role.get(role, [])
            rs = max(role_sessions, key=lambda s: s.finished_at or s.started_at or s.locked_at or datetime.min.replace(tzinfo=timezone.utc), default=None)
            entry = {"role": role, "status": "no_session", "action": None, "reviewer": None}
            if rs is not None:
                if rs.finished_at is not None:
                    entry["status"] = "finished"
                elif rs.auto_suspended_at is not None:
                    entry["status"] = "suspended"
                elif rs.started_at is not None or rs.locked_at is not None:
                    entry["status"] = "active"
                else:
                    entry["status"] = "pending"
                act = (await session.execute(
                    select(ReviewAction).where(ReviewAction.review_session_id == rs.id)
                    .order_by(ReviewAction.server_ts.desc()).limit(1))).scalars().first()
                if act:
                    entry["action"] = act.action_type
                if rs.reviewer_id is not None:
                    entry["reviewer"] = (await session.execute(
                        text("SELECT name FROM users WHERE id = :id"),
                        {"id": rs.reviewer_id})).scalar_one_or_none()
            review_status.append(entry)
        from src.models.review import RejectMark
        marks = (await session.execute(
            select(RejectMark).where(RejectMark.task_id == tid,
                                     RejectMark.status == "open")
            .order_by(RejectMark.page_index))).scalars().all()
        # 节点时间线：每节点最新一轮事件的 状态/耗时/成本/模型/错误（历史细节，
        # 直连/Agent 两路径通用——大节点改造后在这里看得到每个环节的明细）
        latest: dict[str, NodeEvent] = {}
        for e in events:
            latest[e.node_name] = e      # events 按时间升序，后写覆盖即最新
        node_timeline = []
        for name, e in latest.items():
            if e.error_class is not None:
                st = "failed"
            elif e.finished_at is not None:
                st = "done"
            elif e.started_at is not None:
                st = "running"
            else:
                st = "pending"
            dur = (round((e.finished_at - e.started_at).total_seconds(), 1)
                   if e.started_at and e.finished_at else None)
            node_timeline.append({
                "node": name, "status": st,
                "started_at": e.started_at.isoformat() if e.started_at else None,
                "finished_at": e.finished_at.isoformat() if e.finished_at else None,
                "duration_s": dur,
                "cost_cny": float(e.cost_estimate_cny)
                if e.cost_estimate_cny is not None else None,
                "model_version": e.model_version,
                "prompt_version": e.prompt_version,
                "error": e.error_class,
            })
        from src.stream.progress import node_order
        _order = {n: i for i, n in enumerate(node_order())}
        node_timeline.sort(key=lambda x: _order.get(x["node"], 99))
        return {
            "task": {
                "id": str(task.id),
                "idempotency_key": task.idempotency_key,
                "query": task.query,
                "content_type": task.content_type,
                "mode": task.mode,
                "platform": task.platform,
                "sla_hours": task.sla_hours,
                "priority": task.priority,
                "status": task.status,
                "source_query": task.source_query,
                "supplement_question": task.supplement_question,
                "gen_style": task.gen_style,
                "gen_category": task.gen_category,
                "gen_image_style": task.gen_image_style,
                "template_id": str(task.template_id) if task.template_id else None,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "created_by": str(task.created_by) if task.created_by else None,
            },
            "completed_nodes": completed_nodes,
            "current_node": current_node,
            "node_timeline": node_timeline,
            "draft": {"body": draft.body, "model_version": draft.model_version,
                      "prompt_version": draft.prompt_version} if draft else None,
            "page_copies": [{"page_index": p.page_index, "body": p.body} for p in page_copies],
            "assets": [{"page_index": a.page_index, "source_type": a.source_type,
                        "image_url": a.image_url, "id": str(a.id),
                        "selection_status": a.selection_status,
                        "ocr_hit": a.ocr_hit,
                        "display_url": f"/api/assets/{a.id}/image"} for a in assets],
            "claims": [{"claim_text": c.claim_text, "risk_level": c.risk_level,
                        "verification_status": c.verification_status} for c in claims],
            "evidences": [{"source_url": e.source_url, "excerpt": e.excerpt,
                           "source_level": e.source_level, "supports": e.supports}
                          for e in evidences],
            "risk": {"level": risk.level, "reasons": risk.reasons} if risk else None,
            "review": review_status,
            "reject_marks": [{"item_type": m.item_type, "page_index": m.page_index,
                              "reason": m.reason} for m in marks],
        }


_EDITABLE_STATUSES = ("draft", "failed", "rejected", "cancelled")


class TaskPatchIn(BaseModel):
    query: str | None = None
    mode: str | None = None
    content_type: str | None = None
    platform: str | None = None
    priority: str | None = None


@router.patch("/api/tasks/{task_id}")
async def patch_task(task_id: str, payload: TaskPatchIn, actor: str = "anonymous"):
    """编辑任务条目（Query/模式/优先级等）。

    仅未在生产/审核通道中的状态可改（draft/failed/rejected/cancelled）：
    review/approved 的产物与 Query 已绑定，改 Query 会造成内容错位；
    processing 中途改参数会与正在跑的流水线竞态。
    """
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid task_id")
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status not in _EDITABLE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"当前状态 {task.status} 不可编辑（仅 {'/'.join(_EDITABLE_STATUSES)}；"
                       f"生产中的任务请先中断，已审核任务不可改）")
        updates = (payload.model_dump(exclude_unset=True)
                   if hasattr(payload, "model_dump")
                   else {k: v for k, v in dict(payload).items() if v is not None})
        if not updates:
            raise HTTPException(status_code=422, detail="没有要修改的字段")
        if "query" in updates:
            q = (updates["query"] or "").strip()
            if not q:
                raise HTTPException(status_code=422, detail="query 不能为空")
            updates["query"] = q
        if "mode" in updates and updates["mode"] not in ("general", "single", "compare"):
            raise HTTPException(status_code=422, detail="mode 取值无效")
        if "priority" in updates and updates["priority"] not in ("urgent", "normal", "scheduled"):
            raise HTTPException(status_code=422, detail="priority 取值无效")
        for k, v in updates.items():
            setattr(task, k, v)
        # 幂等键跟随内容重算，防与其它任务撞车
        new_key = f"{task.query}|{task.content_type}|{task.platform or ''}|{task.mode}"
        clash = await session.execute(
            select(Task.id).where(Task.idempotency_key == new_key, Task.id != tid))
        if clash.first():
            await session.rollback()
            raise HTTPException(status_code=409, detail="已存在相同 Query+模式+类型的任务")
        task.idempotency_key = new_key
        await session.commit()
    scheduler.update_meta(tid, query=updates.get("query"))
    await log_action(actor, "update", f"编辑任务：{(updates.get('query') or '')[:50]}", task_id=tid)
    return {"ok": True, "task_id": task_id, "updated": sorted(updates.keys())}


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, actor: str = "anonymous"):
    """删除任务条目及其全部产物（17 张子表级联；审计日志保留）。

    生产中（processing）不可删——先在监控页中断；排队中（draft）自动出队。
    """
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid task_id")
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status == "processing":
            raise HTTPException(
                status_code=400, detail="任务正在生产中，请先在实时监控页中断后再删除")
        query = task.query
    # 排队中的任务先出队（worker 取到时直接丢弃）
    await scheduler.cancel(tid)
    from src.models.assets import Asset, CrossCheck, OcrResult
    from src.models.drafts import Draft, PageCopy, RuleResult
    from src.models.entities import Claim, Evidence
    from src.models.events import NodeEvent
    from src.models.review import (Approval, BatchMember, Issue, RejectMark,
                                   ReviewAction, ReviewSession, RiskClassification)
    from src.models.snapshots import PublishSnapshot
    async with SessionLocal() as session:
        rs_ids = select(ReviewSession.id).where(ReviewSession.task_id == tid)
        asset_ids = select(Asset.id).where(Asset.task_id == tid)
        claim_ids = select(Claim.id).where(Claim.task_id == tid)
        for stmt in (
            delete(ReviewAction).where(ReviewAction.review_session_id.in_(rs_ids)),
            delete(ReviewSession).where(ReviewSession.task_id == tid),
            delete(OcrResult).where(OcrResult.asset_id.in_(asset_ids)),
            delete(Asset).where(Asset.task_id == tid),
            delete(CrossCheck).where(CrossCheck.task_id == tid),
            delete(RuleResult).where(RuleResult.task_id == tid),
            delete(RiskClassification).where(RiskClassification.task_id == tid),
            delete(Issue).where(Issue.task_id == tid),
            delete(Approval).where(Approval.task_id == tid),
            delete(PublishSnapshot).where(PublishSnapshot.task_id == tid),
            delete(NodeEvent).where(NodeEvent.task_id == tid),
            delete(RejectMark).where(RejectMark.task_id == tid),
            delete(BatchMember).where(BatchMember.task_id == tid),
            delete(PageCopy).where(PageCopy.task_id == tid),
            delete(Draft).where(Draft.task_id == tid),
            delete(Evidence).where(Evidence.claim_id.in_(claim_ids)),
            delete(Claim).where(Claim.task_id == tid),
            delete(Task).where(Task.id == tid),
        ):
            await session.execute(stmt)
        await session.commit()
    await log_action(actor, "delete", f"删除任务及其全部产物：{query[:50]}", task_id=tid)
    return {"ok": True, "task_id": task_id, "deleted": query[:50]}


class RefsConfirmIn(BaseModel):
    keep_ids: list[str]          # 保留的候选 Asset id（其余候选剔除）
    actor: str = "anonymous"


@router.post("/api/tasks/{task_id}/refs/confirm")
async def confirm_refs(task_id: str, payload: RefsConfirmIn):
    """人工确认参考图筛选结果：勾选保留 → confirmed（其余 candidate 剔除），
    任务从 awaiting_refs 回到队列继续生产（agent 用确认图生图）。"""
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid task_id")
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status != "awaiting_refs":
            raise HTTPException(
                status_code=400,
                detail=f"仅待确认参考图状态可确认，当前: {task.status}")
        from src.models.assets import Asset
        keep = set(payload.keep_ids)
        cands = list((await session.execute(
            select(Asset).where(Asset.task_id == tid,
                                Asset.source_type == "official",
                                Asset.selection_status.in_(["candidate", "confirmed"]))
            .order_by(Asset.page_index))).scalars().all())
        kept = 0
        for a in cands:
            if str(a.id) in keep:
                a.selection_status = "confirmed"
                kept += 1
            else:
                a.selection_status = "rejected"
        if not kept:
            raise HTTPException(
                status_code=422, detail="至少保留一张参考图（全部剔除请用中断任务）")
        # 剔除项落库后清理，保留项重排序 1..N
        for i, a in enumerate([a for a in cands if a.selection_status == "confirmed"], 1):
            a.page_index = i
        for a in [a for a in cands if a.selection_status == "rejected"]:
            await session.delete(a)
        task.status = "draft"
        await session.commit()
        query = task.query
    from src.stream.scheduler import scheduler
    await scheduler.enqueue(tid, query)
    await log_action(payload.actor, "refs_confirm",
                     f"确认参考图 {kept} 张（剔除 {len(cands) - kept} 张），继续生产", task_id=tid)
    return {"ok": True, "kept": kept, "rejected": len(cands) - kept, "queued": True}


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, actor: str = "anonymous"):
    """手工中断任务：排队中→直接出队；生产中→取消执行协程（幂等可重试）。

    注意：中断只停止本侧流水线与流式读取；Nanobot 侧 Agent 若已开始生成，
    其当轮推理会继续到自然结束（MCP 配额仍在兜底）。已产生的产物与
    node_events 保留，重试时已完成节点跳过。
    """
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid task_id")
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status not in ("draft", "processing", "awaiting_refs"):
            raise HTTPException(
                status_code=400,
                detail=f"只有排队中/生产中/待确认参考图的任务可以中断，当前状态: {task.status}")
        query = task.query
    how = await scheduler.cancel(tid)
    if how == "not_found":
        # 不在调度器内（如刚重启未恢复）：直接落库
        await scheduler._mark_status(tid, "cancelled")
    await log_action(actor, "cancel", f"手工中断任务：{query[:50]}", task_id=tid)
    return {"ok": True, "task_id": task_id, "cancelled_via": how}


@router.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str, actor: str = "anonymous"):
    """重试失败/被驳回/被中断的任务。

    失败/中断任务：幂等续跑（已完成节点跳过）。
    驳回任务：先清理上一轮内容产物，再全链重跑——流水线会把历史驳回理由
    注入草稿生成提示词（见 services/regen.py 与 orchestrator.run_pipeline）。
    """
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid task_id")
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status not in ("failed", "rejected", "cancelled"):
            raise HTTPException(
                status_code=400,
                detail=f"only failed/rejected/cancelled tasks can be retried, current status: {task.status}")
        was_rejected = task.status == "rejected"
        mark_count = 0
        if was_rejected:
            from src.services.regen import clear_generated_content, get_open_marks
            mark_count = len(await get_open_marks(session, tid))
            if not mark_count:
                # 无定点标记：整体重生成，清理上一轮全部内容产物
                await clear_generated_content(session, tid)
        task.status = "draft"
        query = task.query
        priority = task.priority or "normal"
        await session.commit()
    kind = "partial_regen" if mark_count else "pipeline"
    await scheduler.enqueue(tid, query, priority=priority, kind=kind)
    detail = f"重试任务：{query[:50]}"
    if was_rejected:
        if mark_count:
            detail += f"（定点重生成：{mark_count} 项标记，其余已认可内容保留）"
        else:
            detail += "（驳回重生成：已清理上一轮产物，驳回理由将注入重新生产）"
    await log_action(actor, "retry_task", detail, task_id=tid)
    return {"ok": True, "task_id": str(tid), "status": "draft", "kind": kind}


@router.get("/api/tasks/stats")
async def task_stats():
    """任务状态统计 + 流水线进度（供进度页轮询）。"""
    async with SessionLocal() as session:
        status_counts = dict((await session.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status))).all())
        total = await session.execute(select(func.count(Task.id)))
        node_counts = dict((await session.execute(
            text("SELECT node_name, count(*) FROM node_events WHERE finished_at IS NOT NULL AND error_class IS NULL GROUP BY node_name"))).all())
        return {
            "total": total.scalar() or 0,
            "by_status": status_counts,
            "nodes_completed": node_counts,
            "queue": scheduler.snapshot(),
        }


@router.get("/api/tasks/random_sample")
async def random_sample():
    """随机抽查一条已完成内容：正文 + 6 图 + 证据 + 风险。"""
    from src.models.drafts import Draft, PageCopy
    from src.models.entities import Claim, Evidence
    from src.models.assets import Asset
    from src.models.review import RiskClassification
    async with SessionLocal() as session:
        done = await session.execute(
            select(Task).where(Task.status.in_(["review", "approved"]))
            .order_by(func.random()).limit(1))
        task = done.scalars().first()
        if not task:
            return {"ok": False, "error": "暂无已完成内容"}
        tid = task.id
        draft = (await session.execute(
            select(Draft).where(Draft.task_id == tid).order_by(Draft.version.desc()))).scalars().first()
        assets = (await session.execute(
            select(Asset).where(Asset.task_id == tid).order_by(Asset.page_index))).scalars().all()
        page_copies = (await session.execute(
            select(PageCopy).where(PageCopy.task_id == tid)
            .order_by(PageCopy.page_index))).scalars().all()
        claims = (await session.execute(select(Claim).where(Claim.task_id == tid))).scalars().all()
        evidences = []
        for c in claims:
            evs = (await session.execute(select(Evidence).where(Evidence.claim_id == c.id))).scalars().all()
            evidences.extend(evs)
        risk = (await session.execute(
            select(RiskClassification).where(RiskClassification.task_id == tid))).scalars().first()
        return {
            "ok": True,
            "task_id": str(tid),
            "query": task.query,
            "content_type": task.content_type,
            "status": task.status,
            "draft": {"body": draft.body, "model_version": draft.model_version} if draft else None,
            "assets": [{"page_index": a.page_index, "source_type": a.source_type,
                        "image_url": a.image_url, "copyright_status": a.copyright_status,
                        "display_url": f"/api/assets/{a.id}/image"} for a in assets],
            "page_copies": [{"page_index": p.page_index, "body": p.body} for p in page_copies],
            "claims": [{"claim_text": c.claim_text, "risk_level": c.risk_level} for c in claims],
            "evidences": [{"source_url": e.source_url, "excerpt": e.excerpt} for e in evidences],
            "risk": {"level": risk.level, "reasons": risk.reasons} if risk else None,
        }


@router.get("/api/assets/{asset_id}/image")
async def asset_image(asset_id: str):
    """配图代理：把生图代理返回的"签名内联 URL"解码为浏览器可加载的图片字节。"""
    from src.models.assets import Asset
    from src.gateway.ocr import fetch_image_bytes
    try:
        aid = uuid.UUID(asset_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid asset_id")
    async with SessionLocal() as session:
        asset = (await session.execute(select(Asset).where(Asset.id == aid))).scalars().first()
    if not asset or not asset.image_url:
        raise HTTPException(status_code=404, detail="asset not found")
    try:
        data, ctype = await fetch_image_bytes(asset.image_url)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"image fetch failed: {e}")
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=86400"})


def _normalize_image(data: bytes, target_size: tuple) -> bytes:
    """把图片统一为目标尺寸（默认 1152x1536），已是目标尺寸则原样返回。"""
    import io as _io
    from PIL import Image
    img = Image.open(_io.BytesIO(data))
    if img.size == target_size:
        return data
    img = img.convert("RGB").resize(target_size, Image.LANCZOS)
    out = _io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ============ 任务式导出（后台打包 + 进度条 + 分包下载，2026-08-22） ============
_EXPORT_JOBS: dict[str, dict] = {}
_EXPORT_DIR = Path(__file__).resolve().parent.parent.parent / "exports"
_EXPORT_TTL = 3600      # 打包结果保留 1 小时
_EXPORT_PART_SIZE = 10  # 每包最多任务数（逐包下载，避免单包过大）

# ============ 导出文件回收站（2026-08-27）============
# 分包手动删除/一键清理 → 移入 exports/recycle/（sidecar JSON 记元数据），
# 仅 admin 可见（前端角色控制，与平台惯例一致）；超 72h 惰性自动清理。
_RECYCLE_DIR = _EXPORT_DIR / "recycle"
_RECYCLE_TTL_HOURS = 72


def _purge_recycle_expired() -> int:
    """清理回收站中超过 72h 的项（惰性触发：访问回收站/删除/清理时执行）。"""
    import json as _json
    import time as _time
    if not _RECYCLE_DIR.exists():
        return 0
    n = 0
    for meta in _RECYCLE_DIR.glob("*.json"):
        try:
            m = _json.loads(meta.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if _time.time() - m.get("deleted_ts", 0) > _RECYCLE_TTL_HOURS * 3600:
            meta.unlink(missing_ok=True)
            meta.with_suffix("").unlink(missing_ok=True)   # sidecar X.json → 数据 X
            n += 1
    return n


def _find_part_file(job: dict, part_no: int) -> str | None:
    """按包号定位分包文件路径（zip 名内嵌 _p{N}.zip；删除后位置索引会错位）。"""
    suffix = f"_p{part_no}.zip"
    for f in job.get("files") or []:
        if f.endswith(suffix):
            return f
    return None


def _part_meta(job: dict, part_no: int) -> dict:
    """取某包的元信息（条数等），parts_done（运行中）/parts（完成）合并找。"""
    for p in (job.get("parts_done") or []) + (job.get("parts") or []):
        if p.get("part") == part_no:
            return p
    return {}


def _recycle_part(job: dict, part_no: int, actor: str) -> dict:
    """把某分包移入回收站（文件改名保留 + sidecar 元数据），并从 job 下载列表移除。"""
    import json as _json
    import time as _time
    target = _find_part_file(job, part_no)
    if not target:
        raise HTTPException(status_code=400, detail="该分包不存在或已删除")
    src = Path(target)
    tasks_in_part = _part_meta(job, part_no).get("tasks")
    _RECYCLE_DIR.mkdir(parents=True, exist_ok=True)
    dst = _RECYCLE_DIR / src.name
    src.rename(dst)
    now = _time.time()
    meta = {"job_id": job.get("job_id"), "part": part_no, "filename": src.name,
            "tasks": tasks_in_part, "size": dst.stat().st_size,
            "deleted_by": actor or "anonymous", "deleted_ts": now,
            "expires_ts": now + _RECYCLE_TTL_HOURS * 3600}
    (dst.parent / (dst.name + ".json")).write_text(
        _json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    # 按包号（非位置）从三个列表移除，防删除后错位
    job["files"] = [f for f in (job.get("files") or []) if f != str(src)]
    job["parts_done"] = [p for p in (job.get("parts_done") or [])
                         if p.get("part") != part_no]
    job["parts"] = [p for p in (job.get("parts") or []) if p.get("part") != part_no]
    return meta


async def _build_approved_zips(job: dict | None = None,
                               part_size: int | None = None) -> tuple[list[dict], int]:
    """构建已通过内容包（正文 + 分页文案 + 配图统一 1152x1536）。

    part_size 非空时每 part_size 条任务分一个包；任务式导出（job 非空）分包落盘
    exports/，同步导出（job 为空）在内存出单包字节。
    job 非空时往里写进度（total/done/detail），并向 SSE 总线流式广播
    export_progress 事件（前端导出弹窗实时滚动展示）；每包落盘即广播 part
    事件，前端可不等整单完成先行下载已就绪分包。
    返回 (parts, 任务数)；part 含 part/tasks/size + file（落盘）或 bytes（内存）。
    """
    import asyncio
    import re
    import zipfile
    from src.models.drafts import Draft, PageCopy
    from src.models.assets import Asset
    from src.gateway.ocr import fetch_image_bytes
    from src.config import settings
    from src.stream.bus import bus

    # 捕获主事件循环：_flush 在 to_thread 工作线程执行，SSE 发布须回投主循环
    main_loop = asyncio.get_running_loop()

    def _emit(phase: str, **fields) -> None:
        """打包进度流式广播（SSE）；总线异常不影响打包本身。线程安全。"""
        if job is None:
            return
        payload = {"job_id": job.get("job_id"), "phase": phase, **fields}

        def _spawn() -> None:
            main_loop.create_task(bus.publish("export_progress", payload))

        try:
            asyncio.get_running_loop().create_task(
                bus.publish("export_progress", payload))
        except RuntimeError:  # 工作线程：回投主循环
            try:
                main_loop.call_soon_threadsafe(_spawn)
            except RuntimeError:
                pass

    target_size = tuple(int(x) for x in settings.image_size.split("x"))
    async with SessionLocal() as session:
        tasks = (await session.execute(
            select(Task).where(Task.status == "approved")
            .order_by(Task.created_at))).scalars().all()
        if not tasks:
            raise HTTPException(
                status_code=404,
                detail="暂无可导出内容：任务经审核角色（A/B/C 任一）通过后，"
                       "即进入导出通道")
        task_ids = [t.id for t in tasks]
        drafts = {d.task_id: d for d in (await session.execute(
            select(Draft).where(Draft.task_id.in_(task_ids)))).scalars().all()}
        pages = (await session.execute(
            select(PageCopy).where(PageCopy.task_id.in_(task_ids))
            .order_by(PageCopy.page_index))).scalars().all()
        assets = (await session.execute(
            select(Asset).where(Asset.task_id.in_(task_ids),
                                Asset.source_type == "ai_generated")
            .order_by(Asset.page_index))).scalars().all()
    pages_by_task: dict = {}
    for p in pages:
        pages_by_task.setdefault(p.task_id, []).append(p)
    assets_by_task: dict = {}
    for a in assets:
        assets_by_task.setdefault(a.task_id, []).append(a)

    if job is not None:
        job.update(total=len(tasks), done=0, detail="准备打包…",
                   parts_done=[], images_done=0)
        _emit("start", total=len(tasks),
              parts_expected=-(-len(tasks) // part_size) if part_size else 1)
    to_disk = job is not None and bool(part_size)
    parts: list[dict] = []
    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    manifest = ["task_id,query,mode,created_at"]
    part_tasks = 0

    def _flush() -> None:
        nonlocal buf, zf, manifest, part_tasks
        if part_tasks == 0:
            return
        zf.writestr("manifest.csv", "\n".join(manifest))
        zf.close()
        data = buf.getvalue()
        idx = len(parts) + 1
        part = {"part": idx, "tasks": part_tasks, "size": len(data)}
        if to_disk:
            _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            path = _EXPORT_DIR / f"approved_{job['job_id']}_p{idx}.zip"
            path.write_bytes(data)
            part["file"] = str(path)
            # 落盘即登记：打包进行中前端就能下载该分包
            job.setdefault("files", []).append(str(path))
        else:
            part["bytes"] = data
        parts.append(part)
        if job is not None and to_disk:
            job.setdefault("parts_done", []).append(
                {"part": idx, "tasks": part_tasks, "size": len(data)})
            _emit("part", part=idx, tasks=part_tasks, size=len(data))
        buf = io.BytesIO()
        zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
        manifest = ["task_id,query,mode,created_at"]
        part_tasks = 0

    async def _prep_image(a):
        """抓取 + 归一化单张图（并发执行，信号量限流）；返回 (asset, data, err)。"""
        async with img_sem:
            try:
                data, _ = await fetch_image_bytes(a.image_url)
                # 图片归一化是 CPU 密集同步操作，丢到线程池，
                # 避免打包期间阻塞事件循环（进度查询/其他请求卡死）
                data = await asyncio.to_thread(_normalize_image, data, target_size)
                return a, data, None
            except Exception as e:  # noqa: BLE001
                return a, None, e

    img_sem = asyncio.Semaphore(8)  # 同一任务内 6 张图并发抓取/归一化，整包提速
    for i, task in enumerate(tasks, 1):
        part_no = len(parts) + 1
        if job is not None:
            job["detail"] = f"打包第 {i}/{len(tasks)} 条（第 {part_no} 包）：{task.query[:20]}"
            _emit("task", done=i - 1, total=len(tasks), part=part_no,
                  query=task.query[:40], images_done=job.get("images_done", 0))
        dirname = f"{i:03d}_" + re.sub(r'[\\/:*?"<>|\s]+', "_", task.query)[:20]
        manifest.append(f"{task.id},{task.query},{task.mode},{task.created_at}")
        draft = drafts.get(task.id)
        if draft:
            zf.writestr(f"{dirname}/正文.txt", draft.body)
        for p in pages_by_task.get(task.id, []):
            zf.writestr(f"{dirname}/分页文案/P{p.page_index}.txt", p.body or "")
        task_assets = assets_by_task.get(task.id, [])
        prepped = await asyncio.gather(*[_prep_image(a) for a in task_assets])
        for j, (a, data, err) in enumerate(prepped, 1):
            if job is not None:
                job["detail"] = (f"打包第 {i}/{len(tasks)} 条（第 {part_no} 包）："
                                 f"{task.query[:20]}（图片 {j}/{len(task_assets)}）")
                job["images_done"] = job.get("images_done", 0) + (0 if err else 1)
                _emit("image", done=i, total=len(tasks), part=part_no,
                      img=j, img_total=len(task_assets),
                      query=task.query[:40], ok=err is None,
                      images_done=job["images_done"])
            if err is None:
                await asyncio.to_thread(
                    zf.writestr, f"{dirname}/图片/P{a.page_index}.png", data)
            else:
                zf.writestr(f"{dirname}/图片/P{a.page_index}_下载失败.txt",
                            f"原图地址: {a.image_url[:200]}\n错误: {err}")
        part_tasks += 1
        if job is not None:
            job["done"] = i
            _emit("task_done", done=i, total=len(tasks), part=part_no,
                  query=task.query[:40])
        if part_size and part_tasks >= part_size:
            await asyncio.to_thread(_flush)
    await asyncio.to_thread(_flush)
    return parts, len(tasks)


@router.get("/api/tasks/export_approved")
async def export_approved(actor: str = "anonymous"):
    """导出已通过（approved）任务的内容包 ZIP（同步单包版，小批量直接可用）。

    前端默认走任务式导出（/api/export/approved/start，带进度条 + 分包下载）。
    """
    from fastapi.responses import StreamingResponse
    parts, n = await _build_approved_zips()
    await log_action(actor, "export_approved",
                     f"导出已通过内容包 ZIP（{n} 条任务）")
    return StreamingResponse(
        io.BytesIO(parts[0]["bytes"]), media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=approved_content.zip"})


def _sweep_export_jobs() -> None:
    """惰性清理：过期任务记录与分包 ZIP 文件（回收站过期项一并清）。"""
    import time
    _purge_recycle_expired()
    now = time.time()
    for jid, job in list(_EXPORT_JOBS.items()):
        if now - job.get("created", 0) > _EXPORT_TTL:
            for f in job.get("files", []):
                Path(f).unlink(missing_ok=True)
            del _EXPORT_JOBS[jid]


async def _run_export_job(job_id: str, actor: str) -> None:
    job = _EXPORT_JOBS[job_id]
    from src.stream.bus import bus
    try:
        parts, n = await _build_approved_zips(job, part_size=_EXPORT_PART_SIZE)
        job["parts"] = [{"part": p["part"], "tasks": p["tasks"], "size": p["size"]}
                        for p in parts]
        job["files"] = [p["file"] for p in parts]
        job["status"] = "done"
        job["detail"] = f"打包完成：{n} 条 / {len(parts)} 包，可逐包下载"
        await bus.publish("export_progress", {
            "job_id": job_id, "phase": "done", "total": n,
            "detail": job["detail"],
            "parts": job["parts"], "images_done": job.get("images_done", 0)})
        await log_action(actor, "export_approved",
                         f"导出已通过内容包 ZIP（{n} 条任务 / {len(parts)} 包，任务式导出）")
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        job["detail"] = "打包失败"
        await bus.publish("export_progress", {
            "job_id": job_id, "phase": "error", "error": str(e)})


@router.post("/api/export/approved/start")
async def start_approved_export(actor: str = "anonymous"):
    """启动后台打包（每 10 条一包），返回 job_id；前端轮询进度后逐包下载。"""
    import asyncio
    import time
    _sweep_export_jobs()
    async with SessionLocal() as session:
        n = (await session.execute(
            select(func.count(Task.id)).where(Task.status == "approved"))).scalar() or 0
    if not n:
        raise HTTPException(
            status_code=404,
            detail="暂无可导出内容：任务经审核角色（A/B/C 任一）通过后，即进入导出通道")
    job_id = uuid.uuid4().hex[:12]
    _EXPORT_JOBS[job_id] = {"status": "running", "total": n, "done": 0,
                            "detail": "启动打包…", "error": None,
                            "job_id": job_id, "files": [], "parts": [],
                            "parts_done": [], "images_done": 0,
                            "created": time.time()}
    asyncio.create_task(_run_export_job(job_id, actor))
    return {"job_id": job_id, "total": n,
            "parts_expected": -(-n // _EXPORT_PART_SIZE)}


@router.get("/api/export/recycle")
async def list_export_recycle():
    """回收站列表（前端仅 admin 角色展示；访问时惰性清理过期项）。"""
    import json as _json
    import time as _time
    purged = _purge_recycle_expired()
    items = []
    if _RECYCLE_DIR.exists():
        now = _time.time()
        for meta in sorted(_RECYCLE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime,
                           reverse=True):
            try:
                m = _json.loads(meta.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            m["expires_in_hours"] = round(max(0, (m.get("expires_ts", 0) - now) / 3600), 1)
            items.append(m)
    return {"items": items, "ttl_hours": _RECYCLE_TTL_HOURS, "purged": purged}


@router.delete("/api/export/recycle/{filename}")
async def purge_recycle_item(filename: str):
    """永久删除回收站单项（前端仅 admin 展示入口）。"""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename or ""):
        raise HTTPException(status_code=400, detail="非法文件名")
    data = _RECYCLE_DIR / filename
    sidecar = _RECYCLE_DIR / (filename + ".json")
    if not data.exists() and not sidecar.exists():
        raise HTTPException(status_code=404, detail="回收站项不存在")
    data.unlink(missing_ok=True)
    sidecar.unlink(missing_ok=True)
    return {"ok": True, "purged": filename}


@router.get("/api/export/{job_id}")
async def export_job_status(job_id: str):
    job = _EXPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="export job not found")
    return {k: job.get(k) for k in ("status", "total", "done", "detail", "error",
                                    "parts", "parts_done", "images_done")}


@router.get("/api/export/{job_id}/download/{part}")
async def download_export_part(job_id: str, part: int):
    from fastapi.responses import FileResponse
    job = _EXPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="export job not found")
    # 按包号定位（非位置索引：删除某包后位置会错位）；打包进行中即可下载已落盘分包
    target = _find_part_file(job, part)
    if not target:
        raise HTTPException(status_code=400, detail="该分包尚未就绪、已删除或已过期")
    tasks_in_part = _part_meta(job, part).get("tasks", "")
    return FileResponse(
        target, media_type="application/zip",
        filename=f"已通过内容包_第{part}包_{tasks_in_part}条.zip")


@router.get("/api/export/{job_id}/download")
async def download_export(job_id: str):
    """兼容入口：下载第 1 包。"""
    return await download_export_part(job_id, 1)


@router.delete("/api/export/{job_id}/part/{part}")
async def delete_export_part(job_id: str, part: int, actor: str = "anonymous"):
    """手动删除单个分包：文件移入回收站（admin 可见，72h 自动清理）。"""
    job = _EXPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="export job not found")
    _purge_recycle_expired()
    meta = _recycle_part(job, part, actor)
    return {"ok": True, "recycled": meta["filename"],
            "expires_hours": _RECYCLE_TTL_HOURS}


@router.post("/api/export/{job_id}/clear")
async def clear_export_parts(job_id: str, actor: str = "anonymous"):
    """一键清理：该导出单的全部已就绪分包移入回收站。"""
    import re as _re
    job = _EXPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="export job not found")
    _purge_recycle_expired()
    # 从文件名解析包号（位置索引在部分删除后会错位）
    part_nos = []
    for f in list(job.get("files") or []):
        m = _re.search(r"_p(\d+)\.zip$", f)
        if m:
            part_nos.append(int(m.group(1)))
    n = 0
    for pn in part_nos:
        _recycle_part(job, pn, actor)
        n += 1
    await log_action(actor, "export_clear", f"一键清理导出分包 {n} 个（已入回收站）")
    return {"ok": True, "recycled": n, "expires_hours": _RECYCLE_TTL_HOURS}


