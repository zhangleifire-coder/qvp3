"""ref_collect：参考图搜集 + OCR 初筛 + 人工确认关卡（compare/single，2026-08-27）。

需求：证据来源图片至少搜集 10 张以上 → OCR 比对关键词自动初筛 → 人工确认
筛选结果 → 才进入生图。general 模式（纯文生图）不设关卡，不损吞吐。

实现：确定性后端节点（不经 Agent）——
1. compare 从 query 拆主体 A/B（single 用整条 query），各自搜实景图 6 张合并；
   不足 10 张补搜一次整条 query；
2. 逐张下载本地化（失败/尺寸过小剔除），OCR 识别图上文字与主体关键词比对，
   命中记 ocr_hit（人工确认时的排序依据）；
3. 候选全部落库（selection_status=candidate），返回 ref_gate 标记；
   编排器据标记把任务挂起为 awaiting_refs，等人工确认后继续。
"""
import hashlib
import re
import traceback

from sqlalchemy import delete, select

from src.config import settings
from src.db.session import SessionLocal
from src.models.assets import Asset
from src.models.tasks import Task

_TARGET_MIN = 10          # 至少搜集 10 张（需求口径）
_PER_SUBJECT = 6          # 每主体搜 6 张
_MAX_CANDIDATES = 16      # 候选上限（防页面过长）

# compare 拆主体的连接词（两边各为一个产品/主体）
_SPLIT_RE = re.compile(r"[和与跟]|还是|对比|vs|VS|跟比|和比")


def split_subjects(query: str, mode: str) -> list[str]:
    """compare：把 query 拆成主体 A/B；single：整条 query（去掉「怎么选/对比」尾巴）。"""
    q = (query or "").strip()
    if mode == "compare":
        parts = [p.strip() for p in _SPLIT_RE.split(q) if len(p.strip()) >= 2]
        if len(parts) >= 2:
            return parts[:2]
    q = re.sub(r"(怎么选|哪个好|对比|区别|还是|Vs?|$)", "", q).strip() or q
    return [q]


async def node_ref_collect(input_data: dict) -> dict:
    task_id = input_data["task_id"]
    from src.stream.bus import bus

    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        mode, query = (task.mode or "general"), task.query

    # 关卡仅对参考图模式生效；已有确认参考图（确认后重跑）直接跳过
    if mode not in ("compare", "single"):
        return {"skipped": True, "reason": "general 无需参考图确认"}
    confirmed = await _confirmed_refs(task_id)
    if confirmed:
        return {"skipped": True, "reason": f"已有 {len(confirmed)} 张确认参考图"}

    subjects = split_subjects(query, mode)
    await bus.publish("agent_tool", {"tool": "image_search", "count": 0},
                      task_id=str(task_id))

    # 1) 搜集：各主体 6 张；不足 10 张用整条 query 补搜
    from src.gateway.image_search import search_image
    collected: list[dict] = []
    seen_urls: set[str] = set()

    async def _search(q: str, n: int) -> None:
        try:
            for r in await search_image(q, count=n):
                u = r.get("image_url") or ""
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    collected.append({"image_url": u,
                                      "title": r.get("title", ""),
                                      "engine": r.get("engine", "search"),
                                      "subject": q})
        except Exception:  # noqa: BLE001
            traceback.print_exc()   # OpenSERP 不可达等：留空候选，由人工兜底

    try:
        for s in subjects:
            await _search(s, _PER_SUBJECT)
        if len(collected) < _TARGET_MIN:
            await _search(query, _PER_SUBJECT)
    except Exception:  # noqa: BLE001
        # 搜索通道整体故障：不挂起，放行降级（Agent 自行容错纯文生图）
        traceback.print_exc()
        return {"ref_gate": False, "candidates": 0,
                "reason": "搜图通道故障，跳过确认直接生产"}
    collected = collected[:_MAX_CANDIDATES]

    # 2) 下载本地化 + OCR 关键词初筛（命中的排前面供人工确认）
    from src.gateway.ocr import fetch_image_bytes, ocr_image
    from src.pipeline.nodes import _persist_image
    candidates, hits = [], 0
    for i, c in enumerate(collected, start=1):
        try:
            data, ctype = await fetch_image_bytes(c["image_url"])
            local_url = _persist_image(task_id, i, "ref", data, ctype)
            ocr_hit = ""
            if not settings.mock_image_gen:
                try:
                    r = await ocr_image(local_url)
                    text = r.get("raw_text", "") or ""
                    hit_words = [w for s in subjects for w in re.split(
                        r"[，,。；;\s]+", s) if len(w) >= 2 and w in text]
                    ocr_hit = ",".join(dict.fromkeys(hit_words))[:120]
                except Exception:  # noqa: BLE001
                    pass
            if ocr_hit:
                hits += 1
            candidates.append({
                "page_index": i, "local_url": local_url, "origin": c["image_url"],
                "title": c["title"][:120], "engine": c["engine"],
                "hash": hashlib.md5(data).hexdigest(), "ocr_hit": ocr_hit})
            await bus.publish("agent_tool",
                              {"tool": "image_search", "count": i,
                               "searched_images": i}, task_id=str(task_id))
        except Exception as e:  # noqa: BLE001
            print(f"[ref_collect] 候选 {c['image_url'][:60]} 处理失败: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue   # 下载失败直接剔除

    # 3) 候选落库（OCR 命中在前，人工按建议勾选）
    candidates.sort(key=lambda c: (not c["ocr_hit"],))
    async with SessionLocal() as session:
        # 清掉上一轮候选（重跑刷新），保留 confirmed/rejected 的人工结论
        await session.execute(delete(Asset).where(
            Asset.task_id == task_id, Asset.source_type == "official",
            Asset.selection_status == "candidate"))
        for rank, c in enumerate(candidates, start=1):
            session.add(Asset(
                task_id=task_id, page_index=rank, subject=query,
                source_type="official", copyright_status="unknown",
                hash=c["hash"], image_url=c["local_url"],
                origin_url=c["origin"], model_version=c["engine"],
                is_illustration=False,
                selection_status="candidate", ocr_hit=c["ocr_hit"] or None))
        await session.commit()

    await bus.publish("agent_tool",
                      {"tool": "image_search", "count": len(candidates),
                       "searched_images": len(candidates),
                       "ocr_hits": hits}, task_id=str(task_id))
    if not candidates:
        # 一张都没搜到：不挂起，放行给 Agent 自行容错（纯文生图降级）
        return {"ref_gate": False, "candidates": 0,
                "reason": "未搜到实景图，跳过确认直接生产"}
    return {"ref_gate": True, "candidates": len(candidates), "ocr_hits": hits}


async def _confirmed_refs(task_id) -> list[Asset]:
    async with SessionLocal() as session:
        return list((await session.execute(
            select(Asset).where(Asset.task_id == task_id,
                                Asset.source_type == "official",
                                Asset.selection_status == "confirmed")
            .order_by(Asset.page_index))).scalars().all())
