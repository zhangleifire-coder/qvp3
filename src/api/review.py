import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from src.db.session import SessionLocal
from src.models.review import (ReviewSession, ReviewAction, Issue, Approval,
                               RiskClassification, RejectMark)
from src.models.tasks import Task
from src.review.locks import acquire_lock
from src.review.heartbeat import record_heartbeat
from src.review.users import get_or_create_user

REVIEW_ROLES = ("A", "B", "C")

router = APIRouter()


class ClaimIn(BaseModel):
    task_id: str
    role: str
    reviewer_id: str


@router.post("/api/review/claim")
async def claim(payload: ClaimIn):
    return await acquire_lock(payload.task_id, payload.role, payload.reviewer_id)


class HeartbeatIn(BaseModel):
    task_id: str
    role: str
    reviewer_id: str
    client_ts: datetime | None = None


@router.post("/api/review/heartbeat")
async def heartbeat(payload: HeartbeatIn):
    return await record_heartbeat(payload.task_id, payload.role, payload.reviewer_id, payload.client_ts)


class MarkIn(BaseModel):
    item_type: str  # page=分页文案 / image=交付配图
    page_index: int  # 1-6
    reason: str = ""


class ActionIn(BaseModel):
    task_id: str
    role: str
    reviewer_id: str
    action_type: str  # approve / reject
    reason: str | None = None
    marks: list[MarkIn] = []  # 定点驳回标记：只重生成这些项，其余内容保留


@router.post("/api/review/action")
async def action(payload: ActionIn):
    tid = uuid.UUID(payload.task_id)
    # 定点驳回标记校验：非法项直接 400，避免脏数据进库
    if payload.action_type == "reject":
        for m in payload.marks:
            if m.item_type not in ("page", "image") or not (1 <= m.page_index <= 6):
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid mark: {m.item_type} P{m.page_index}")
            if not m.reason.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"标记项 P{m.page_index} 必须填写问题说明")
    async with SessionLocal() as session:
        rs = (await session.execute(
            select(ReviewSession).where(
                ReviewSession.task_id == tid,
                ReviewSession.role == payload.role,
                ReviewSession.finished_at.is_(None)))).scalars().first()
        if not rs:
            return {"ok": False, "error": "no active review session"}
        now = datetime.now(timezone.utc)
        reviewer_id = await get_or_create_user(session, payload.reviewer_id, payload.role)
        if rs.reviewer_id is None:
            rs.reviewer_id = reviewer_id
        session.add(ReviewAction(
            review_session_id=rs.id,
            idempotency_key=f"{rs.id}-{payload.action_type}-{uuid.uuid4().hex[:8]}",
            action_type=payload.action_type,
            client_ts=now, server_ts=now,
            payload={"reason": payload.reason or ""}))
        rs.finished_at = now
        # 审核结论落 approvals 表（批次会签维度以外，按任务逐条记录）
        session.add(Approval(task_id=tid, role=payload.role,
                             approver_id=reviewer_id, conclusion=payload.action_type))
        task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
        if payload.action_type == "reject":
            reason_text = payload.reason or ""
            if payload.marks:
                reason_text = reason_text or "定点驳回（见标记项）"
            session.add(Issue(task_id=tid, role=payload.role,
                              priority="P1", description=reason_text or "审核驳回"))
            for m in payload.marks:
                session.add(RejectMark(
                    task_id=tid, role=payload.role, item_type=m.item_type,
                    page_index=m.page_index, reason=m.reason.strip()))
            if task:
                task.status = "rejected"
        elif payload.action_type == "approve" and task is not None:
            # 单角色审核：任一角色的审核员通过即生效（2026-08-21 起，替代 A/B/C 三方会签）
            task.status = "approved"
        # 任务已定案：删除其他角色未完成的会话，任务从他们的待审队列消失。
        # 当前会话已 finished（见上），不受影响；审核动作/结论已落库留痕。
        await session.execute(
            delete(ReviewSession).where(
                ReviewSession.task_id == tid,
                ReviewSession.finished_at.is_(None)))
        await session.commit()
    from src.services.activity import log_action
    label = "通过" if payload.action_type == "approve" else "驳回"
    detail = f"审核{label}（角色 {payload.role}）：{(task.query if task else '')[:50]}"
    if payload.reason:
        detail += f"；理由：{payload.reason[:200]}"
    if payload.marks:
        items = "、".join(
            f"{'文案' if m.item_type == 'page' else '配图'}P{m.page_index}"
            for m in payload.marks)
        detail += f"；定点标记 {len(payload.marks)} 项（{items}）"
    await log_action(payload.reviewer_id, f"review_{payload.action_type}",
                     detail, task_id=tid)
    return {"ok": True, "action": payload.action_type}


@router.get("/api/review/queue/{role}")
async def queue(role: str):
    from sqlalchemy import text
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(ReviewSession, Task, RiskClassification)
            .join(Task, Task.id == ReviewSession.task_id)
            .outerjoin(RiskClassification, RiskClassification.task_id == ReviewSession.task_id)
            .where(ReviewSession.role == role,
                   ReviewSession.finished_at.is_(None)))).all()
        reviewer_ids = [rs.reviewer_id for rs, _, _ in rows if rs.reviewer_id]
        names = {}
        if reviewer_ids:
            names = {str(r[0]): r[1] for r in (await session.execute(
                text("SELECT id, name FROM users WHERE id = ANY(:ids)"),
                {"ids": reviewer_ids})).all()}
        sessions = []
        for rs, t, rc in rows:
            # 30s 心跳窗口内有心跳视为被占用（与领取锁口径一致）
            locked = bool(rs.locked_at and rs.last_heartbeat_at
                          and (now - rs.last_heartbeat_at).total_seconds() < 30)
            sessions.append({
                "task_id": str(rs.task_id),
                "query": t.query,
                "mode": t.mode,
                "risk_level": rc.level if rc else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "locked": locked,
                "locked_by": names.get(str(rs.reviewer_id)) if locked else None,
            })
        return {"sessions": sessions}


@router.get("/api/review/task/{task_id}")
async def task_detail(task_id: str):
    """审核员查看的任务详情：正文、事实点、证据、图片、OCR、风险。"""
    from src.models.tasks import Task
    from src.models.drafts import Draft, PageCopy
    from src.models.entities import Claim, Evidence
    from src.models.assets import Asset, OcrResult
    from src.models.review import RiskClassification
    tid = uuid.UUID(task_id)
    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
        if not task:
            return {"error": "task not found"}
        draft = (await session.execute(
            select(Draft).where(Draft.task_id == tid).order_by(Draft.version.desc()))).scalars().first()
        claims = (await session.execute(select(Claim).where(Claim.task_id == tid))).scalars().all()
        evidences = []
        for c in claims:
            evs = (await session.execute(select(Evidence).where(Evidence.claim_id == c.id))).scalars().all()
            evidences.extend(evs)
        assets = (await session.execute(
            select(Asset).where(Asset.task_id == tid).order_by(Asset.page_index))).scalars().all()
        ocrs = (await session.execute(
            select(OcrResult).where(OcrResult.asset_id.in_([a.id for a in assets])))).scalars().all()
        risk = (await session.execute(
            select(RiskClassification).where(RiskClassification.task_id == tid))).scalars().first()
        return {
            "task": {"query": task.query, "content_type": task.content_type, "status": task.status},
            "draft": {"body": draft.body, "model_version": draft.model_version} if draft else None,
            "claims": [{"claim_text": c.claim_text, "risk_level": c.risk_level} for c in claims],
            "evidences": [{"source_url": e.source_url, "excerpt": e.excerpt} for e in evidences],
            "assets": [{"page_index": a.page_index, "source_type": a.source_type,
                        "image_url": a.image_url, "copyright_status": a.copyright_status,
                        "display_url": f"/api/assets/{a.id}/image"} for a in assets],
            "ocr": [{"asset_id": str(o.asset_id), "raw_text": o.raw_text} for o in ocrs],
            "risk": {"level": risk.level, "reasons": risk.reasons} if risk else None,
        }
