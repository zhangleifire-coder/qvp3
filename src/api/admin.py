"""后台管理接口：工作周期状态、手动检修、导出记录、删除内容、重新开启、用户管理。"""
import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text

from src.db.session import SessionLocal
from src.models.tasks import Task
from src.services.activity import log_action
from src.stream.maintenance import cycle
from src.stream.progress import progress
from src.stream.scheduler import scheduler

router = APIRouter()

# 工作内容表（不含 users / organizations，删除时保留账号）
CONTENT_TABLES = [
    "tasks", "entity_snapshots", "claims", "evidence", "drafts", "page_copies",
    "assets", "ocr_results", "rule_results", "cross_checks",
    "risk_classifications", "review_sessions", "review_actions", "issues",
    "batches", "batch_members", "approvals", "publish_snapshots", "node_events",
    "reject_marks",
]


@router.get("/api/admin/status")
async def status():
    async with SessionLocal() as session:
        total = await session.execute(select(func.count(Task.id)))
        by_status = dict((await session.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status))).all())
    return {
        "cycle": cycle.snapshot(),
        "scheduler": scheduler.snapshot(),
        "tasks": {"total": total.scalar() or 0, "by_status": by_status},
    }


@router.post("/api/admin/maintenance/start")
async def start_maintenance(actor: str = "anonymous"):
    await log_action(actor, "maintenance_start", "进入手动检修（生产暂停）")
    return {"ok": True, "cycle": await cycle.enter_maintenance("manual")}


@router.post("/api/admin/maintenance/end")
async def end_maintenance(actor: str = "anonymous"):
    await log_action(actor, "maintenance_end", "结束检修，恢复生产")
    return {"ok": True, "cycle": await cycle.exit_maintenance("manual")}


@router.post("/api/admin/restart")
async def restart_work():
    """结束检修并重新开启工作任务。"""
    return {"ok": True, "cycle": await cycle.exit_maintenance("manual")}


@router.post("/api/admin/clear")
async def clear_work(actor: str = "anonymous"):
    """删除全部工作内容（保留账号），并清空队列内存状态。"""
    import asyncpg
    from src.config import settings
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(dsn)
        try:
            # 锁超时 10s，避免在有任务执行时无限挂起
            await conn.execute("SET lock_timeout = '10s'")
            await conn.execute(f"TRUNCATE TABLE {', '.join(CONTENT_TABLES)} CASCADE")
        finally:
            await conn.close()
        scheduler.clear()
        progress.clear()
        await log_action(actor, "admin_clear",
                         f"清空全部工作内容（{len(CONTENT_TABLES)} 张表）")
        return {"ok": True, "cleared_tables": len(CONTENT_TABLES)}
    except asyncpg.exceptions.LockNotAvailableError:
        return {"ok": False, "error": "仍有任务执行中，请等待任务完成后再删除"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@router.get("/api/admin/costs")
async def cost_details():
    """成本明细（管理员决策支持）：按任务 × 节点拆分的全部计费事件 + 多维汇总。

    数据源是 node_events.cost_estimate_cny（每次模型/搜索/生图调用的估算成本）。
    """
    from datetime import timedelta
    from src.models.events import NodeEvent
    from src.stream.progress import NODE_LABEL
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(NodeEvent.task_id, NodeEvent.node_name,
                   NodeEvent.cost_estimate_cny, NodeEvent.model_version,
                   NodeEvent.finished_at)
            .where(NodeEvent.cost_estimate_cny > 0)
            .order_by(NodeEvent.finished_at))).all()
        task_ids = list({r[0] for r in rows})
        tasks = {}
        if task_ids:
            tasks = {t.id: t for t in (await session.execute(
                select(Task).where(Task.id.in_(task_ids)))).scalars().all()}
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    per_task: dict = {}
    node_totals: dict = {}
    model_totals: dict = {}
    total = 0.0
    total_24h = 0.0
    for tid, node, cost, model, finished in rows:
        c = float(cost or 0)
        total += c
        if finished and finished >= day_ago:
            total_24h += c
        t = per_task.setdefault(tid, {"total": 0.0, "items": []})
        t["total"] += c
        t["items"].append({
            "node": node, "label": NODE_LABEL.get(node, node),
            "cost": round(c, 4), "model": model,
            "finished_at": finished.isoformat() if finished else None})
        nt = node_totals.setdefault(
            node, {"label": NODE_LABEL.get(node, node), "count": 0, "cost": 0.0})
        nt["count"] += 1
        nt["cost"] += c
        if model:
            model_totals[model] = model_totals.get(model, 0.0) + c
    task_list = []
    for tid, t in per_task.items():
        task = tasks.get(tid)
        task_list.append({
            "task_id": str(tid),
            "query": task.query if task else "(已清空任务)",
            "mode": task.mode if task else None,
            "status": task.status if task else None,
            "total": round(t["total"], 4),
            "items": t["items"],
        })
    task_list.sort(key=lambda x: -x["total"])
    return {
        "summary": {
            "total_cny": round(total, 4),
            "total_24h_cny": round(total_24h, 4),
            "task_count": len(task_list),
            "avg_per_task_cny": round(total / len(task_list), 4) if task_list else 0,
        },
        "by_node": sorted(({"node": k, "label": v["label"], "count": v["count"],
                            "cost": round(v["cost"], 4)}
                           for k, v in node_totals.items()),
                          key=lambda x: -x["cost"]),
        "by_model": sorted(({"model": k, "cost": round(v, 4)}
                            for k, v in model_totals.items()),
                           key=lambda x: -x["cost"]),
        "tasks": task_list,
    }


@router.get("/api/admin/export")
async def export_records(actor: str = "anonymous"):
    """导出工作记录为 CSV（任务 + 正文长度 + 图/证据数 + 成本 + 风险）。"""
    from src.models.drafts import Draft
    from src.models.entities import Claim, Evidence
    from src.models.assets import Asset
    from src.models.events import NodeEvent
    from src.models.review import RiskClassification

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["task_id", "query", "content_type", "status", "risk_level",
                     "draft_chars", "assets", "evidence", "node_events", "cost_cny",
                     "model_version", "created_at"])

    async with SessionLocal() as session:
        tasks = (await session.execute(select(Task).order_by(Task.created_at))).scalars().all()
        for t in tasks:
            draft = (await session.execute(
                select(Draft).where(Draft.task_id == t.id)
                .order_by(Draft.version.desc()))).scalars().first()
            assets = (await session.execute(
                select(func.count(Asset.id)).where(Asset.task_id == t.id))).scalar() or 0
            evidence = (await session.execute(
                select(func.count(Evidence.id)).where(
                    Evidence.claim_id.in_(select(Claim.id).where(Claim.task_id == t.id))))).scalar() or 0
            node_count = (await session.execute(
                select(func.count(NodeEvent.id)).where(NodeEvent.task_id == t.id))).scalar() or 0
            cost = (await session.execute(
                select(func.coalesce(func.sum(NodeEvent.cost_estimate_cny), 0))
                .where(NodeEvent.task_id == t.id))).scalar() or 0
            risk = (await session.execute(
                select(RiskClassification).where(RiskClassification.task_id == t.id))).scalars().first()
            writer.writerow([
                str(t.id), t.query, t.content_type, t.status,
                risk.level if risk else "",
                len(draft.body) if draft else 0,
                assets, evidence, node_count,
                float(cost) if cost else 0,
                draft.model_version if draft else "",
                t.created_at.isoformat() if t.created_at else "",
            ])

    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    filename = f"工作记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    await log_action(actor, "admin_export", "导出工作记录 CSV")
    from urllib.parse import quote
    content_disposition = (
        "attachment; filename=work_records.csv; "
        f"filename*=UTF-8''{quote(filename)}")
    return StreamingResponse(
        iter([csv_bytes]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition})


@router.get("/api/admin/logs")
async def get_logs():
    """返回运行日志（内存缓冲，最近 3000 条）。"""
    return {"count": len(progress.log), "log": progress.get_log()}


@router.get("/api/admin/logs/download")
async def download_logs():
    """下载运行日志为文本文件。"""
    from urllib.parse import quote
    content = "\n".join(progress.get_log())
    text_bytes = ("\ufeff" + content).encode("utf-8")
    filename = f"运行日志_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    content_disposition = (
        "attachment; filename=run_log.txt; "
        f"filename*=UTF-8''{quote(filename)}")
    return StreamingResponse(
        iter([text_bytes]), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition})


# ============ 用户管理（仅 admin，2026-08-20） ============

_VALID_ROLES = ("A", "B", "C", "admin")


async def _require_admin(actor: str):
    """校验 actor 是在职管理员，返回其用户 id。"""
    if not actor:
        raise HTTPException(status_code=401, detail="缺少 actor 参数")
    async with SessionLocal() as session:
        row = (await session.execute(
            text("SELECT id, role, active FROM users WHERE name = :n"),
            {"n": actor})).first()
    if not row:
        raise HTTPException(status_code=401, detail=f"用户不存在: {actor}")
    if row[1] != "admin" or not row[2]:
        raise HTTPException(status_code=403, detail="仅管理员可访问用户管理")
    return row[0]


@router.get("/api/admin/users")
async def list_users(actor: str):
    await _require_admin(actor)
    async with SessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT id, name, role, active, created_at FROM users ORDER BY created_at"))).all()
    return {"users": [
        {"id": str(r[0]), "name": r[1], "role": r[2], "active": r[3],
         "created_at": r[4].isoformat() if r[4] else None}
        for r in rows]}


class UserIn(BaseModel):
    actor: str
    name: str
    password: str
    role: str = "A"


@router.post("/api/admin/users")
async def create_user(payload: UserIn):
    await _require_admin(payload.actor)
    name = payload.name.strip()
    if not name or not payload.password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    if payload.role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"角色必须是 {'/'.join(_VALID_ROLES)}")
    from src.api.auth import hash_password
    async with SessionLocal() as session:
        exists = await session.execute(
            text("SELECT 1 FROM users WHERE name = :n"), {"n": name})
        if exists.first():
            raise HTTPException(status_code=400, detail="用户名已存在")
        r = await session.execute(text(
            "INSERT INTO users (name, role, password_hash) VALUES (:n, :r, :p) RETURNING id"),
            {"n": name, "r": payload.role, "p": hash_password(payload.password)})
        await session.commit()
        await log_action(payload.actor, "user_create",
                         f"新建用户 {name}（角色 {payload.role}）")
        return {"ok": True, "id": str(r.scalar()), "name": name, "role": payload.role}


class UserUpdate(BaseModel):
    actor: str
    name: str | None = None
    password: str | None = None
    role: str | None = None
    active: bool | None = None


async def _count_other_active_admins(session, user_id) -> int:
    return (await session.execute(text(
        "SELECT count(*) FROM users WHERE role = 'admin' AND active AND id <> :id"),
        {"id": user_id})).scalar() or 0


@router.put("/api/admin/users/{user_id}")
async def update_user(user_id: str, payload: UserUpdate):
    await _require_admin(payload.actor)
    from src.api.auth import hash_password
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT id, name, role, active FROM users WHERE id = :id"),
            {"id": user_id})).first()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        if payload.role is not None and payload.role not in _VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"角色必须是 {'/'.join(_VALID_ROLES)}")
        # 护栏：不能把最后一个在职管理员降级/停用
        demoting = (payload.role is not None and payload.role != "admin" and row[2] == "admin")
        disabling = (payload.active is False and row[2] == "admin" and row[3])
        if (demoting or disabling) and await _count_other_active_admins(session, user_id) == 0:
            raise HTTPException(status_code=400, detail="至少保留一个在职管理员")
        if payload.name is not None and payload.name.strip() and payload.name.strip() != row[1]:
            dup = await session.execute(
                text("SELECT 1 FROM users WHERE name = :n AND id <> :id"),
                {"n": payload.name.strip(), "id": user_id})
            if dup.first():
                raise HTTPException(status_code=400, detail="用户名已存在")
            await session.execute(text("UPDATE users SET name = :v WHERE id = :id"),
                                  {"v": payload.name.strip(), "id": user_id})
        if payload.password:
            await session.execute(text("UPDATE users SET password_hash = :v WHERE id = :id"),
                                  {"v": hash_password(payload.password), "id": user_id})
        if payload.role is not None:
            await session.execute(text("UPDATE users SET role = :v WHERE id = :id"),
                                  {"v": payload.role, "id": user_id})
        if payload.active is not None:
            await session.execute(text("UPDATE users SET active = :v WHERE id = :id"),
                                  {"v": payload.active, "id": user_id})
        await session.commit()
        changes = []
        if payload.name is not None and payload.name.strip() and payload.name.strip() != row[1]:
            changes.append(f"改名 {row[1]}→{payload.name.strip()}")
        if payload.password:
            changes.append("重置密码")
        if payload.role is not None and payload.role != row[2]:
            changes.append(f"角色 {row[2]}→{payload.role}")
        if payload.active is not None and payload.active != row[3]:
            changes.append("启用" if payload.active else "停用")
        await log_action(payload.actor, "user_update",
                         f"修改用户 {row[1]}：{'，'.join(changes) or '无变化'}")
        return {"ok": True}


@router.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: str, actor: str):
    admin_id = await _require_admin(actor)
    if str(admin_id) == user_id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账号")
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT name, role, active FROM users WHERE id = :id"), {"id": user_id})).first()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        if row[1] == "admin" and row[2] and \
                await _count_other_active_admins(session, user_id) == 0:
            raise HTTPException(status_code=400, detail="至少保留一个在职管理员")
        try:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            await session.commit()
            await log_action(actor, "user_delete", f"删除用户 {row[0]}（角色 {row[1]}）")
        except Exception:
            await session.rollback()
            raise HTTPException(
                status_code=400,
                detail="该用户名下有任务/审核数据，无法删除，建议改为停用")
        return {"ok": True}
