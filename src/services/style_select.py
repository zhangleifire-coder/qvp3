"""按 query 题材匹配风格库并随机选一个视觉方向（直连路径风格自适应，2026-08-31）。

用户需求：
- 风格不能千篇一律——按 query 标题题材分析，随机出一个风格方向；
- 同一篇的 6 页必须锁定同一风格（字体/色调/装饰全篇统一）。

两级库+钉选（2026-08-31 移植 qvp-dev 8002 已验证方案，迁移 016）：
0. 创建者钉了个人默认风格（users.default_style）→ 直接使用，跳过随机；
1. 创建者个人库（style_keywords.owner_id=uid 且 enabled）非空 → 从中选；
2. 公共库（owner_id IS NULL 且 enabled）非空 → 从中选；
3. 都空 → 代码内置 IMAGE_STYLE_LIBRARY 兜底。

库内选择：query 与各条 keywords 逐一子串命中计分，加权随机
（强相关更可能、弱相关仍有机会 → 兼顾准确与多样）；全不命中 → 等权随机。
选中后由调用方落库 task.gen_image_style（+描述词快照，幂等：已选沿用）；
重生成/定点修改按快照回查同一描述，保证一篇 6 页及重生成版本风格一致。
Agent 路径（nanobot）已有 LLM 风格判定，不走本模块。
"""
import random

from sqlalchemy import select, text

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


async def _default_style(owner_id) -> str | None:
    """创建者钉的个人默认风格名（users.default_style，迁移 016）；未钉/匿名返回 None。"""
    if owner_id is None:
        return None
    async with SessionLocal() as session:
        return (await session.execute(
            text("SELECT default_style FROM users WHERE id = :u"),
            {"u": str(owner_id)})).scalar()


async def style_desc_for(style_name: str, owner_id=None) -> str | None:
    """按风格名反查描述词：个人库（含停用条目，保持已选定风格稳定）→ 公共库
    → 内置库；查不到返回 None。供钉选直通与「存为我的风格」预填使用。"""
    name = (style_name or "").strip()
    if not name:
        return None
    async with SessionLocal() as session:
        if owner_id is not None:
            row = (await session.execute(
                select(StyleKeyword).where(
                    StyleKeyword.owner_id == owner_id,
                    StyleKeyword.style_name == name))).scalars().first()
            if row and row.description:
                return row.description.strip()
        row = (await session.execute(
            select(StyleKeyword).where(
                StyleKeyword.owner_id.is_(None),
                StyleKeyword.style_name == name))).scalars().first()
        if row and row.description:
            return row.description.strip()
    from src.services.combo import IMAGE_STYLE_LIBRARY
    return dict(IMAGE_STYLE_LIBRARY).get(name)


async def _candidates(owner_id=None):
    """按两级库优先级取启用候选：个人库 → 公共库 → 内置（[(名, 描述, keywords)]）。"""
    async with SessionLocal() as session:
        if owner_id is not None:
            rows = list((await session.execute(
                select(StyleKeyword).where(StyleKeyword.enabled,
                                           StyleKeyword.owner_id == owner_id)
                .order_by(StyleKeyword.created_at))).scalars().all())
            if rows:
                return [(r.style_name, (r.description or "").strip(), r.keywords)
                        for r in rows], "personal"
        rows = list((await session.execute(
            select(StyleKeyword).where(StyleKeyword.enabled,
                                       StyleKeyword.owner_id.is_(None))
            .order_by(StyleKeyword.created_at))).scalars().all())
        if rows:
            return [(r.style_name, (r.description or "").strip(), r.keywords)
                    for r in rows], "public"
    from src.services.combo import IMAGE_STYLE_LIBRARY
    return [(n, d, "") for n, d in IMAGE_STYLE_LIBRARY], "builtin"


async def select_image_style(query: str, owner_id=None) -> tuple[str, str]:
    """query → (风格名, 描述词)。钉选直通；否则在候选库内加权随机。"""
    default = await _default_style(owner_id)
    if default:
        desc = await style_desc_for(default, owner_id)
        if desc:
            return default, desc
        # 钉的风格已被删且查不到描述 → 视为未钉，走正常选择
    entries, _scope = await _candidates(owner_id)
    scored = [(n, d, _score(query, kw)) for n, d, kw in entries]
    weights = [s for _, _, s in scored]
    if sum(weights) <= 0:
        name, desc, _ = random.choice(scored)
    else:
        name, desc, _ = random.choices(scored, weights=weights, k=1)[0]
    return name, desc


async def ensure_task_style(task_id) -> tuple[str, str]:
    """任务级风格确定（幂等）：已选沿用（重生成一致），未选才随机；返回 (风格名, 描述)。

    沿用优先级：015 快照 gen_image_style_desc（风格库后续编辑/删除都不影响本任务）
    > 按名回查（个人库含停用 → 公共库 → 内置）> 重选并落库。
    """
    from src.models.tasks import Task
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        owner_id = task.created_by
        if task.gen_image_style:
            if task.gen_image_style_desc:
                return task.gen_image_style, task.gen_image_style_desc
            desc = await style_desc_for(task.gen_image_style, owner_id)
            if desc:
                task.gen_image_style_desc = desc
                await session.commit()
                return task.gen_image_style, desc
        name, desc = await select_image_style(task.query, owner_id)
        task.gen_image_style = name
        task.gen_image_style_desc = desc
        await session.commit()
    return name, desc


def build_style_block(name: str, desc: str) -> str:
    """组装注入每页生图提示词的风格段落（含 6 页统一条款）。"""
    return (f"（本篇视觉风格：{name}）{desc}。{_UNIFY_CLAUSE}")


def page_refs(refs: list | None, page_index: int, per_page: int = 2) -> list:
    """实景参考图按页轮播分配（2026-09-02 用户要求：6 张配图实景用法必须错开）。

    现状问题：reference_urls 全列表不加区别传给每一页——要么 6 页都用同一张
    主图，要么每页把全部参考图都怼进去，六页内容雷同。
    规则：每页取 per_page 张作为该页的参考子集，按 page_index 轮转起点，
    保证相邻页不同、单页不堆砌。refs 不足 2 张时原样返回（全给）。
    """
    if not refs:
        return []
    refs = list(refs)
    if len(refs) <= 2:
        return refs
    start = (page_index - 1) % len(refs)
    n = min(per_page, len(refs))
    return [refs[(start + j) % len(refs)] for j in range(n)]
