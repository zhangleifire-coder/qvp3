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

_TARGET_MIN = 12          # 至少搜集 12 张（2026-08-30 用户口径，原 10）
_PER_SUBJECT = 12         # 每主体搜 12 张
_MAX_CANDIDATES = 24      # 候选上限（防页面过长；下载/OCR 剔除后仍保 12+）

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


# ── 素材库复用（2026-09-01 吸收 8002）：搜图前先匹配历史 official 实图 ──
# 历史任务沉淀的实图就是本地素材库：Asset.subject 存的是来源任务的 query，
# 规范化（剥搜索修饰词与空白）后与当前 subjects 双向包含、或其 ocr_hit 命中
# 当前主体词即复用；共享本地文件与内容 hash，免重复搜索与下载。
# 安全边界：仅复用本地化文件仍在磁盘的（死链不挂）；confirmed 优先（人工认
# 可质量）；单任务 ≤12 张；可用 asset_library_reuse 关闭回退纯搜索。
_LIB_STRIP = re.compile(r"(高清|细节|侧面|实拍|外观|场景|（补搜）|怎么选|哪个好|对比|区别)")
_LIB_SCAN = 500      # 库扫描上限（最近收录优先）
_LIB_LIMIT = 12      # 单任务最多复用张数


def _lib_norm(s: str) -> str:
    return re.sub(r"\s+", "", _LIB_STRIP.sub("", s or ""))


async def _library_matches(query: str, subjects: list[str]) -> list[dict]:
    """从素材库匹配可复用实图，返回与候选同构的 dict 列表（已本地化免下载）。"""
    from src.models.assets import Asset
    from pathlib import Path
    gen_dir = Path(__file__).resolve().parent.parent.parent / "static" / "generated"
    norms = [_lib_norm(x) for x in subjects if _lib_norm(x)]
    qn = _lib_norm(query)
    if not norms and not qn:
        return []
    out, seen_hash = [], set()
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Asset).where(
                Asset.source_type == "official",
                Asset.is_history == False,           # noqa: E712
                Asset.image_url.isnot(None))
            .order_by(Asset.created_at.desc()).limit(_LIB_SCAN))).scalars().all()
    for a in rows:
        if len(out) >= _LIB_LIMIT:
            break
        if a.hash in seen_hash or not a.image_url:
            continue
        # 本地文件必须仍在磁盘（ref 图为 /static/generated/ 本地化路径）
        fname = (a.image_url or "").rsplit("/", 1)[-1]
        if "/" not in (a.image_url or "") or not (gen_dir / fname).exists():
            continue
        sub_n = _lib_norm(a.subject or "")
        hit = any(sub_n and (sub_n in n or n in sub_n) for n in norms if n)
        if not hit and qn and sub_n:
            hit = sub_n in qn or qn in sub_n
        if not hit and a.ocr_hit:
            hit = any(n and n in (a.ocr_hit or "") for n in norms)
        if not hit:
            continue
        seen_hash.add(a.hash)
        out.append({"local_url": a.image_url, "origin": a.origin_url,
                    "title": f"素材库复用（源：{(a.subject or '')[:24]}）",
                    "engine": "library", "hash": a.hash,
                    "ocr_hit": (a.ocr_hit or "")[:120]})
    return out


async def node_ref_collect(input_data: dict) -> dict:
    task_id = input_data["task_id"]
    from src.stream.bus import bus

    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        mode, query = (task.mode or "general"), task.query

    # 关卡仅对参考图模式生效；已有确认参考图（确认后重跑）直接跳过。
    # ref_for_general_enabled（默认关）开启后 general 也走实景搜集（产品方向开关）
    if mode not in ("compare", "single") and not settings.ref_for_general_enabled:
        return {"skipped": True, "reason": "general 无需参考图确认"}
    confirmed = await _confirmed_refs(task_id)
    if confirmed:
        return {"skipped": True, "reason": f"已有 {len(confirmed)} 张确认参考图"}

    subjects = split_subjects(query, mode)
    await bus.publish("agent_tool", {"tool": "image_search", "count": 0},
                      task_id=str(task_id))

    # 0) 素材库复用（2026-09-01 吸收 8002）：先匹配历史实图，命中免搜索免下载；
    #    库中量已够目标则跳过搜索（省 API），不足按缺口补搜
    lib_hits: list[dict] = []
    if settings.asset_library_reuse:
        try:
            lib_hits = await _library_matches(query, subjects)
            if lib_hits:
                await bus.publish("agent_tool",
                                  {"tool": "asset_library", "count": len(lib_hits),
                                   "searched_images": len(lib_hits)},
                                  task_id=str(task_id))
        except Exception:  # noqa: BLE001
            traceback.print_exc()   # 库匹配失败不阻塞，走正常搜索

    # 1) 搜集：各主体 6 张；不足 10 张用整条 query 补搜（素材库已覆盖则按缺口缩减）
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
        deficit = max(0, _TARGET_MIN - len(lib_hits))
        if deficit > 0:
            per = max(2, _PER_SUBJECT * deficit // max(1, _TARGET_MIN))
            for s in subjects:
                await _search(s, per)
            if len(collected) < deficit:
                await _search(query, _PER_SUBJECT)
    except Exception:  # noqa: BLE001
        # 搜索通道整体故障：素材库命中仍可用；全空则放行降级（纯文生图）
        traceback.print_exc()
        if not lib_hits:
            return {"ref_gate": False, "candidates": 0,
                    "reason": "搜图通道故障，跳过确认直接生产"}
    collected = collected[:max(0, _MAX_CANDIDATES - len(lib_hits))]

    # 2) 素材库命中直接成候选（已本地化，免下载免 OCR）+ 新搜项下载本地化 + OCR 初筛
    from src.gateway.ocr import fetch_image_bytes, ocr_image
    from src.pipeline.nodes import _persist_image
    candidates, hits = [], 0
    lib_hashes = set()
    for i, c in enumerate(lib_hits, start=1):
        lib_hashes.add(c["hash"])
        if c["ocr_hit"]:
            hits += 1
        candidates.append({
            "page_index": i, "local_url": c["local_url"], "origin": c["origin"],
            "title": c["title"][:120], "engine": c["engine"],
            "hash": c["hash"], "ocr_hit": c["ocr_hit"]})
    page_seq = len(candidates)
    for c in collected:
        try:
            data, ctype = await fetch_image_bytes(c["image_url"])
            digest = hashlib.md5(data).hexdigest()
            if digest in lib_hashes:
                continue   # 与素材库命中同图（不同来源 URL）：保留库版本即可
            page_seq += 1
            local_url = _persist_image(task_id, page_seq, "ref", data, ctype)
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
                "page_index": page_seq, "local_url": local_url, "origin": c["image_url"],
                "title": c["title"][:120], "engine": c["engine"],
                "hash": digest, "ocr_hit": ocr_hit})
            await bus.publish("agent_tool",
                              {"tool": "image_search", "count": page_seq,
                               "searched_images": page_seq}, task_id=str(task_id))
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
