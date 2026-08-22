import csv
import io
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select, func, text
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


@router.get("/api/tasks")
async def list_tasks(status: str | None = None, mode: str | None = None,
                     risk_level: str | None = None, limit: int = 20, offset: int = 0):
    """任务列表：状态/模式/风险筛选 + 分页，每项带当前节点与风险等级。"""
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
                "template_id": str(task.template_id) if task.template_id else None,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "created_by": str(task.created_by) if task.created_by else None,
            },
            "completed_nodes": completed_nodes,
            "current_node": current_node,
            "draft": {"body": draft.body, "model_version": draft.model_version,
                      "prompt_version": draft.prompt_version} if draft else None,
            "page_copies": [{"page_index": p.page_index, "body": p.body} for p in page_copies],
            "assets": [{"page_index": a.page_index, "source_type": a.source_type,
                        "image_url": a.image_url,
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


@router.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str, actor: str = "anonymous"):
    """重试失败/被驳回的任务。

    失败任务：幂等续跑（已完成节点跳过）。
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
        if task.status not in ("failed", "rejected"):
            raise HTTPException(
                status_code=400,
                detail=f"only failed/rejected tasks can be retried, current status: {task.status}")
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


async def _build_approved_zips(job: dict | None = None,
                               part_size: int | None = None) -> tuple[list[dict], int]:
    """构建已通过内容包（正文 + 分页文案 + 配图统一 1152x1536）。

    part_size 非空时每 part_size 条任务分一个包；任务式导出（job 非空）分包落盘
    exports/，同步导出（job 为空）在内存出单包字节。
    job 非空时往里写进度（total/done/detail），供进度条轮询。
    返回 (parts, 任务数)；part 含 part/tasks/size + file（落盘）或 bytes（内存）。
    """
    import asyncio
    import re
    import zipfile
    from src.models.drafts import Draft, PageCopy
    from src.models.assets import Asset
    from src.gateway.ocr import fetch_image_bytes
    from src.config import settings

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
        job.update(total=len(tasks), done=0, detail="准备打包…")
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
        else:
            part["bytes"] = data
        parts.append(part)
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
            if err is None:
                await asyncio.to_thread(
                    zf.writestr, f"{dirname}/图片/P{a.page_index}.png", data)
            else:
                zf.writestr(f"{dirname}/图片/P{a.page_index}_下载失败.txt",
                            f"原图地址: {a.image_url[:200]}\n错误: {err}")
        part_tasks += 1
        if job is not None:
            job["done"] = i
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
    """惰性清理：过期任务记录与分包 ZIP 文件。"""
    import time
    now = time.time()
    for jid, job in list(_EXPORT_JOBS.items()):
        if now - job.get("created", 0) > _EXPORT_TTL:
            for f in job.get("files", []):
                Path(f).unlink(missing_ok=True)
            del _EXPORT_JOBS[jid]


async def _run_export_job(job_id: str, actor: str) -> None:
    job = _EXPORT_JOBS[job_id]
    try:
        parts, n = await _build_approved_zips(job, part_size=_EXPORT_PART_SIZE)
        job["parts"] = [{"part": p["part"], "tasks": p["tasks"], "size": p["size"]}
                        for p in parts]
        job["files"] = [p["file"] for p in parts]
        job["status"] = "done"
        job["detail"] = f"打包完成：{n} 条 / {len(parts)} 包，可逐包下载"
        await log_action(actor, "export_approved",
                         f"导出已通过内容包 ZIP（{n} 条任务 / {len(parts)} 包，任务式导出）")
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        job["detail"] = "打包失败"


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
                            "created": time.time()}
    asyncio.create_task(_run_export_job(job_id, actor))
    return {"job_id": job_id, "total": n}


@router.get("/api/export/{job_id}")
async def export_job_status(job_id: str):
    job = _EXPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="export job not found")
    return {k: job.get(k) for k in ("status", "total", "done", "detail", "error", "parts")}


@router.get("/api/export/{job_id}/download/{part}")
async def download_export_part(job_id: str, part: int):
    from fastapi.responses import FileResponse
    job = _EXPORT_JOBS.get(job_id)
    files = (job or {}).get("files") or []
    if not job or job.get("status") != "done" or not (1 <= part <= len(files)):
        raise HTTPException(status_code=400, detail="导出尚未完成或已过期")
    tasks_in_part = job["parts"][part - 1]["tasks"]
    return FileResponse(
        files[part - 1], media_type="application/zip",
        filename=f"已通过内容包_第{part}包_{tasks_in_part}条.zip")


@router.get("/api/export/{job_id}/download")
async def download_export(job_id: str):
    """兼容入口：下载第 1 包。"""
    return await download_export_part(job_id, 1)
