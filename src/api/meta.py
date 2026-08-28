from fastapi import APIRouter

from src.config import settings
from src.stream.progress import NODE_LABEL, node_order

router = APIRouter()


@router.get("/api/meta/nodes")
async def meta_nodes():
    """流水线节点元数据：按执行顺序返回节点名 + 中文标签（双路径自适应）。"""
    return {"nodes": [{"name": name, "label": NODE_LABEL[name]}
                      for name in node_order()]}


@router.get("/api/meta/access")
async def meta_access():
    """前端权限口径：role_all_access=true 时审核台全员开放 A/B/C 角色切换。"""
    return {"role_all_access": settings.role_all_access}


@router.get("/api/meta/review_counts")
async def review_counts():
    """人审关卡待办计数（菜单徽标）：文字核查 / 审图 / 任务审核。

    review 徽标与审核队列同口径：未完成 ReviewSession 的去重任务数。
    任务定案时会删除未完成会话，徽标随之归零；避免按 Task.status 统计时
    出现「徽标有数、所有角色队列全空」的错位。附带分角色计数供审核页 tab 使用。
    """
    from sqlalchemy import func, select
    from src.db.session import SessionLocal
    from src.models.tasks import Task
    from src.models.review import ReviewSession
    async with SessionLocal() as session:
        text_n = (await session.execute(
            select(func.count(Task.id)).where(Task.status == "awaiting_text"))).scalar() or 0
        refs_n = (await session.execute(
            select(func.count(Task.id)).where(Task.status == "awaiting_refs"))).scalar() or 0
        review_n = (await session.execute(
            select(func.count(func.distinct(ReviewSession.task_id)))
            .where(ReviewSession.finished_at.is_(None)))).scalar() or 0
        by_role = {r: n for r, n in (await session.execute(
            select(ReviewSession.role,
                   func.count(func.distinct(ReviewSession.task_id)))
            .where(ReviewSession.finished_at.is_(None))
            .group_by(ReviewSession.role))).all()}
        return {"text": text_n, "refs": refs_n, "review": review_n,
                "review_by_role": {k: by_role.get(k, 0) for k in ("A", "B", "C")}}
