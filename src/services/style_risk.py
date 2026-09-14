"""风格生图风险判定（2026-09-14，双候选选优的门控）。

背景：9-14 P1/P2 后重生率已被预算硬顶 + VL 申诉压到低位，「每页固定双候选」
会把成本从期望 ~7 张锁死到 12 张（不划算）。正确姿势是按风格风险自适应：
first_sim 数据显示某风格不合格率系统性偏高（真渲染问题、申诉救不回）时，
该风格的任务才开双候选（每页生成 2 张、OCR 选优），正常风格维持单候选。

阈值与判定保持纯函数（_decide_dual）以便单测；统计查询结果进程内缓存 30 分钟。
"""
import time

from sqlalchemy import text

from src.db.session import SessionLocal

DUAL_FAIL_PCT = 30.0     # 不合格率 ≥30% 才值得双候选
DUAL_MIN_PAGES = 12      # 近 7 天最少 12 页（≈2 个任务）数据才可信
HISTORY_DAYS = 7
_CACHE_TTL_SECONDS = 1800

_cache: dict[str, tuple[float, int]] = {}   # style -> (ts, candidates)


def _decide_dual(pages: int, fail_pages: int) -> int:
    """纯判定：页数够且不合格率达阈值 → 2 候选，否则 1。"""
    if pages >= DUAL_MIN_PAGES and 100.0 * fail_pages / pages >= DUAL_FAIL_PCT:
        return 2
    return 1


async def image_candidates_for_style(gen_image_style: str | None) -> int:
    """按风格近 7 天首轮 sim 统计决定该任务每页生成几张候选（1 或 2）。"""
    style = (gen_image_style or "").strip()
    if not style:
        return 1
    hit = _cache.get(style)
    if hit and time.time() - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    n = 1
    try:
        async with SessionLocal() as s:
            row = (await s.execute(text("""
                SELECT count(*),
                       count(*) FILTER (WHERE a.first_sim < 1.0)
                FROM assets a JOIN tasks t ON t.id = a.task_id
                WHERE t.gen_image_style = :style
                  AND a.source_type = 'ai_generated'
                  AND a.is_history = false
                  AND a.first_sim IS NOT NULL
                  AND a.created_at > now() - make_interval(days => :days)
            """), {"style": style, "days": HISTORY_DAYS})).first()
        if row:
            n = _decide_dual(int(row[0] or 0), int(row[1] or 0))
    except Exception:  # noqa: BLE001——统计失败不阻断生图（回退单候选）
        import traceback
        traceback.print_exc()
        n = 1
    _cache[style] = (time.time(), n)
    return n


def reset_cache() -> None:
    """测试用。"""
    _cache.clear()
