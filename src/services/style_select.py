"""按 query 题材匹配风格库并随机选一个视觉方向（直连路径风格自适应，2026-08-31）。

用户需求：
- 风格不能千篇一律——按 query 标题题材分析，随机出一个风格方向；
- 同一篇的 6 页必须锁定同一风格（字体/色调/装饰全篇统一）。

选择规则：
1. 取启用的风格库条目（DB style_keywords；库空退回代码内置 IMAGE_STYLE_LIBRARY）；
2. query 与每条 keywords 逐一子串命中计分；
3. 有命中时按分值加权随机（强相关更可能、弱相关仍有机会 → 兼顾准确与多样）；
4. 全不命中 → 全库等权随机（题材冷门也保持风格多样性，绝不固定默认风格）。

选中后由调用方落库 task.gen_image_style（幂等：已选沿用）；重生成/定点修改
按风格名回查同一描述，保证一篇 6 页及重生成版本风格一致。
Agent 路径（nanobot）已有 LLM 风格判定，不走本模块。
"""
import random

from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.styles import StyleKeyword

# 6 页统一条款：风格选择按「篇」做一次，全篇共用（字体/色调/装饰锁死，仅布局轮换）
_UNIFY_CLAUSE = (
    "本篇全部页面必须严格统一使用这一风格：同一主色调、同一标题字体与正文字体、"
    "同一装饰语言，只有每页布局可以不同。"
)


def _score(query: str, keywords: str) -> int:
    """query 对一条风格 keywords 的命中数（小写子串匹配，逗号分隔词表）。"""
    q = (query or "").lower()
    if not q:
        return 0
    return sum(1 for kw in (keywords or "").split(",")
               if kw.strip() and kw.strip().lower() in q)


async def select_image_style(query: str) -> tuple[str, str]:
    """query → (风格名, 描述词)。加权随机；全零分等权随机。"""
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(StyleKeyword).where(StyleKeyword.enabled)
            .order_by(StyleKeyword.created_at))).scalars().all())
    if rows:
        entries = [(r.style_name, (r.description or "").strip(), _score(query, r.keywords))
                   for r in rows]
    else:
        # 库空兜底：内置风格库（无关键词 → 等权随机）
        from src.services.combo import IMAGE_STYLE_LIBRARY
        entries = [(n, d, 0) for n, d in IMAGE_STYLE_LIBRARY]
    weights = [s for _, _, s in entries]
    if sum(weights) <= 0:
        name, desc, _ = random.choice(entries)
    else:
        name, desc, _ = random.choices(entries, weights=weights, k=1)[0]
    return name, desc


async def _desc_by_name(name: str) -> str | None:
    async with SessionLocal() as session:
        row = (await session.execute(
            select(StyleKeyword).where(StyleKeyword.style_name == name))).scalars().first()
    if row:
        return (row.description or "").strip() or None
    from src.services.combo import IMAGE_STYLE_LIBRARY
    return dict(IMAGE_STYLE_LIBRARY).get(name)


async def ensure_task_style(task_id) -> tuple[str, str]:
    """任务级风格确定（幂等）：已选沿用（重生成一致），未选才随机；返回 (风格名, 描述)。

    沿用优先级：015 快照 gen_image_style_desc（风格库后续编辑/删除都不影响本任务）
    > 按名回查库中现行描述（同步库内措辞优化）> 重选并落库。
    """
    from src.models.tasks import Task
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        if task.gen_image_style:
            if task.gen_image_style_desc:
                return task.gen_image_style, task.gen_image_style_desc
            desc = await _desc_by_name(task.gen_image_style)
            if desc:
                task.gen_image_style_desc = desc
                await session.commit()
                return task.gen_image_style, desc
        name, desc = await select_image_style(task.query)
        task.gen_image_style = name
        task.gen_image_style_desc = desc
        await session.commit()
    return name, desc


def build_style_block(name: str, desc: str) -> str:
    """组装注入每页生图提示词的风格段落（含 6 页统一条款）。"""
    return (f"（本篇视觉风格：{name}）{desc}。{_UNIFY_CLAUSE}")
