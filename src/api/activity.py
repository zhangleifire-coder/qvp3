"""操作审计日志查询接口：用户看自己的，admin 看全部（可按用户/动作筛选）。"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select, text

from src.db.session import SessionLocal
from src.models.activity import ActivityLog

router = APIRouter()


def _row(a: ActivityLog) -> dict:
    return {"id": str(a.id), "actor_name": a.actor_name, "action": a.action,
            "detail": a.detail, "task_id": str(a.task_id) if a.task_id else None,
            "created_at": a.created_at.isoformat() if a.created_at else None}


@router.get("/api/activity")
async def list_activity(actor: str, user: str | None = None, action: str | None = None,
                        limit: int = 50, offset: int = 0):
    """日志列表：普通用户强制只看自己；admin 可用 user 参数看任意用户或全部。"""
    if not actor:
        raise HTTPException(status_code=401, detail="缺少 actor 参数")
    async with SessionLocal() as session:
        row = (await session.execute(
            text("SELECT id, role FROM users WHERE name = :n"), {"n": actor})).first()
        if not row:
            raise HTTPException(status_code=401, detail=f"用户不存在: {actor}")
        is_admin = row[1] == "admin"
        # 权限收敛：非 admin 只能看自己；admin 指定 user 则过滤，否则全部
        target = actor if not is_admin else (user or None)

        stmt = select(ActivityLog).order_by(ActivityLog.created_at.desc())
        cnt = select(func.count(ActivityLog.id))
        if target:
            stmt = stmt.where(ActivityLog.actor_name == target)
            cnt = cnt.where(ActivityLog.actor_name == target)
        if action:
            stmt = stmt.where(ActivityLog.action == action)
            cnt = cnt.where(ActivityLog.action == action)
        total = (await session.execute(cnt)).scalar() or 0
        limit = max(1, min(limit, 200))
        rows = (await session.execute(stmt.limit(limit).offset(max(0, offset)))).scalars().all()
        # 动作类型清单（前端筛选器）
        actions = [r[0] for r in (await session.execute(
            select(ActivityLog.action).distinct().order_by(ActivityLog.action))).all()]
    return {"total": total, "logs": [_row(a) for a in rows], "actions": actions,
            "is_admin": is_admin}
