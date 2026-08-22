"""操作审计日志写入助手：业务埋点用，绝不阻塞主流程。"""
import traceback

from sqlalchemy import text

from src.db.session import SessionLocal
from src.models.activity import ActivityLog


async def log_action(actor: str, action: str, detail: str = "", task_id=None) -> None:
    """记录一条用户操作日志。actor 为用户名；user_id 自动关联（查不到为 NULL）。

    审计日志失败不应影响业务操作本身，但必须留痕（print），
    不允许静默丢失（2026-08-20 静默 except 的教训）。
    """
    if not actor:
        actor = "anonymous"
    try:
        async with SessionLocal() as session:
            uid = (await session.execute(
                text("SELECT id FROM users WHERE name = :n"), {"n": actor})).scalar()
            session.add(ActivityLog(
                user_id=uid, actor_name=actor, action=action,
                detail=(detail or "")[:2000],
                task_id=str(task_id) if task_id else None))
            await session.commit()
    except Exception:  # noqa: BLE001
        print(f"[activity] 日志写入失败 {actor}/{action}:", flush=True)
        traceback.print_exc()
