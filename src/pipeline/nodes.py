import hashlib
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from src.config import settings
from src.db.session import SessionLocal
from src.gateway.failover import call_with_failover, DEEPSEEK_MODEL, KIMI_MODEL
from src.quality.rules import check_rules

# 生成图本地持久化目录（通过 /static 挂载直接可访问）
GENERATED_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "generated"

_EXT_BY_CTYPE = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _persist_image(task_id, page_index, tag: str, data: bytes, ctype: str) -> str:
    """把图片字节落到 static/generated/，返回可浏览的本地路径。

    上游生图代理的 URL 会过期（实测隔天 404），必须在产出时立即本地化。
    """
    ext = _EXT_BY_CTYPE.get(ctype, ".png")
    name = f"{task_id}_{tag}{page_index}_{uuid.uuid4().hex[:8]}{ext}"
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    (GENERATED_DIR / name).write_bytes(data)
    return f"/static/generated/{name}"

NODES = [
    "task_import", "entity_bind", "evidence_build", "draft_gen",
    "rule_check", "page_split", "asset_gen", "ocr_read",
    "cross_check", "risk_classify", "review_queue",
    "batch_signoff", "publish_snapshot"
]


async def execute_node(task_id, node_name: str, input_data: dict, node_fn=None):
    from src.pipeline.idempotency import check_or_record_node_event
    from src.stream.bus import bus
    tid = str(task_id)
    async with SessionLocal() as session:
        event = await check_or_record_node_event(
            session, task_id, node_name, input_data)
        if event is None:
            return {"skipped": True}
        start = datetime.now(timezone.utc)
        event.started_at = start
        await bus.publish("node_started", {"node": node_name}, task_id=tid)
        try:
            if node_fn:
                output = await node_fn(input_data)
            else:
                output = {"node": node_name, "input": input_data}
            event.finished_at = datetime.now(timezone.utc)
            event.cost_estimate_cny = output.get("cost_cny", 0)
            event.model_version = output.get("model_version")
            event.prompt_version = output.get("prompt_version")
            await session.commit()
            summary = _node_summary(node_name, output)
            summary["elapsed"] = round((event.finished_at - start).total_seconds(), 2)
            await bus.publish("node_finished", summary, task_id=tid)
            return output
        except Exception as e:
            event.finished_at = datetime.now(timezone.utc)
            event.error_class = type(e).__name__
            event.retry_count = (event.retry_count or 0) + 1
            await session.commit()
            await bus.publish("node_failed", {
                "node": node_name,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "elapsed": round((event.finished_at - start).total_seconds(), 2),
            }, task_id=tid)
            # 限流类错误即时反馈给并发限制器（乘性减），不必等整条任务失败收尾
            from src.stream.scheduler import scheduler, is_throttled
            if is_throttled(e):
                await scheduler.limiter.report_throttle()
            raise


def _node_summary(node_name: str, output: dict) -> dict:
    """抽取节点输出的可读摘要 + 实际内容片段，供流式前端展示。"""
    s: dict = {"node": node_name}
    if node_name == "draft_gen":
        text = output.get("text", "")
        s["preview"] = text[:220]
        s["length"] = len(text)
        s["model"] = output.get("model_version")
    elif node_name == "asset_gen":
        s["count"] = output.get("asset_count", 0)
        s["image_urls"] = output.get("image_urls", [])
    elif node_name == "evidence_build":
        s["evidence_count"] = output.get("evidence_count", 0)
        s["conflicts"] = output.get("conflicts", [])
    elif node_name == "risk_classify":
        s["level"] = output.get("level")
        s["reasons"] = output.get("reasons", [])
    elif node_name == "entity_bind":
        s["searched_images"] = output.get("searched_images", 0)
    else:
        for k, v in output.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                s[k] = v
    return s


async def _latest_draft_body(session, task_id):
    from src.models.drafts import Draft
    result = await session.execute(
        select(Draft).where(Draft.task_id == task_id).order_by(Draft.version.desc()))
    draft = result.scalars().first()
    return draft.body if draft else ""


async def node_entity_bind(input_data: dict) -> dict:
    """搜实景图/实物图，存为 official 素材（compare/single 作参考图；general 跳过）。"""
    import hashlib
    from src.models.tasks import Task
    from src.models.assets import Asset
    from src.gateway.image_search import search_image
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == input_data["task_id"]))).scalar_one()
        query = task.query
        mode = task.mode or "general"
    if mode == "general":
        return {"searched_images": 0}
    from src.gateway.ocr import fetch_image_bytes
    images = await search_image(query, count=6)
    async with SessionLocal() as session:
        for i, img in enumerate(images, start=1):
            image_url = img["image_url"]
            origin_url = None
            if not settings.mock_image_gen:
                try:
                    # 搜图也立即本地化：图床防盗链/失效不影响后续审核与导出
                    data, ctype = await fetch_image_bytes(image_url)
                    origin_url = image_url
                    image_url = _persist_image(
                        input_data["task_id"], i, "ref", data, ctype)
                except Exception:
                    pass  # 下载失败保留原地址，展示层走代理兜底
            session.add(Asset(
                task_id=input_data["task_id"], page_index=i,
                subject=query, source_type="official", copyright_status="unknown",
                hash=hashlib.md5(image_url.encode()).hexdigest(),
                image_url=image_url, origin_url=origin_url,
                model_version=img.get("engine", "search"),
                is_illustration=False))
        await session.commit()
    return {"searched_images": len(images),
            "cost_cny": settings.openserp_cost_per_call}


async def node_evidence_build(input_data: dict) -> dict:
    from src.models.tasks import Task
    from src.models.entities import Claim, Evidence
    from src.models.review import Issue
    from src.gateway.web_search import web_search, deepseek_verify, detect_conflict
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == input_data["task_id"]))).scalar_one()
        query = task.query
    # 1. 豆包检索（结构化来源）
    results = await web_search(query, count=6)
    # 2. DeepSeek 联网验证（交叉校验）
    deepseek_text, verify_cost = await deepseek_verify(query)
    # 3. 争议检测：豆包来源 vs DeepSeek 结论的关键数字不一致
    conflicts = detect_conflict([r["summary"] or "" for r in results], deepseek_text)
    async with SessionLocal() as session:
        claim = Claim(task_id=input_data["task_id"], claim_text=query,
                      risk_level="P1", position=1)
        session.add(claim)
        await session.flush()
        for r in results:
            session.add(Evidence(claim_id=claim.id, source_url=r["url"] or "no-url",
                                 source_level="P2", excerpt=(r["summary"] or "")[:500],
                                 supports=True))
        # 4. 争议预警：创建 P1 问题单（事实审核 A 域）
        if conflicts:
            session.add(Issue(task_id=input_data["task_id"], role="A", priority="P1",
                              description="证据争议: " + "; ".join(conflicts)))
        await session.commit()
    return {"evidence_built": True, "evidence_count": len(results),
            "conflicts": conflicts,
            "cost_cny": settings.doubao_search_cost_per_call + verify_cost}


async def node_draft_gen(input_data: dict) -> dict:
    from src.models.tasks import Task
    from src.models.drafts import Draft
    from src.gateway.prompt_versions import get_effective_prompt
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == input_data["task_id"]))).scalar_one()
        query = task.query
        mode = task.mode or "general"
        owner_id = task.created_by
    template = await get_effective_prompt("draft_gen", mode, owner_id)
    prompt = template + "\n\n" + query
    prompt_version = f"draft_{mode}_v1"
    # 驳回重生成：审核意见与系统提示词、任务 query 一起作为处理依据，
    # 要求模型逐条修正，避免同类问题遗留到下一轮审核。
    regen = input_data.get("regen") or {}
    feedbacks = regen.get("feedback") or []
    if feedbacks:
        lines = "\n".join(f"{i}. {r}" for i, r in enumerate(feedbacks, 1))
        prompt += ("\n\n【重要：审核驳回反馈】本内容此前在人工审核中被驳回，"
                   "以下是审核员提出的全部修改意见：\n" + lines +
                   "\n请逐条针对性修正上述问题后重新创作，确保新内容不再出现同类问题。")
        prompt_version = f"draft_{mode}_v1_regen{regen.get('round', 1)}"
    result = await call_with_failover(prompt, DEEPSEEK_MODEL, KIMI_MODEL)
    async with SessionLocal() as session:
        from sqlalchemy import func
        max_v = (await session.execute(
            select(func.max(Draft.version)).where(
                Draft.task_id == input_data["task_id"]))).scalar() or 0
        session.add(Draft(
            task_id=input_data["task_id"], version=max_v + 1, body=result["text"],
            model_version=result["model_version"], prompt_version=prompt_version))
        await session.commit()
    return {"text": result["text"], "model_version": result["model_version"],
            "prompt_version": prompt_version, "cost_cny": result["cost_cny"],
            "degraded": result["degraded"]}


async def node_rule_check(input_data: dict) -> dict:
    from src.models.drafts import RuleResult
    async with SessionLocal() as session:
        text = await _latest_draft_body(session, input_data["task_id"])
    title = text.split("\n")[0][:25] if text else ""
    results = check_rules(text, title)
    async with SessionLocal() as session:
        for r in results:
            session.add(RuleResult(
                task_id=input_data["task_id"], rule_name=r["rule_name"],
                passed=r["passed"], details=r["details"]))
        await session.commit()
    return {"rule_results": results, "all_passed": all(r["passed"] for r in results)}


def _split_pages(text: str, n: int = 6) -> list:
    """把整篇正文切成 n 页：优先按段落边界均衡分配，段落不足按句子，再退按字数硬切。
    不再截断正文（旧实现只取前 350 字、每页 58 字，句子被拦腰切断）。"""
    import re
    text = (text or "").strip()
    if not text:
        return [""] * n

    def balance(units: list) -> list:
        """按原顺序把 units 切成 n 段，长度尽量均衡（保阅读顺序）。"""
        total = sum(len(u) for u in units)
        target = max(1, -(-total // n))
        buckets, cur, cur_len = [], [], 0
        for u in units:
            if cur and cur_len + len(u) > target and len(buckets) < n - 1:
                buckets.append(cur)
                cur, cur_len = [], 0
            cur.append(u)
            cur_len += len(u)
        buckets.append(cur)
        return ["".join(b) for b in buckets]

    paras = [p for p in re.split(r"(\n+)", text) if p.strip()]
    if len([p for p in paras if not p.isspace()]) >= n and len(paras) >= n:
        pages = balance(paras)
    else:
        sents = re.split(r"(?<=[。！？；!?;])", text)
        sents = [s for s in sents if s.strip()]
        if len(sents) >= n:
            pages = balance(sents)
        else:
            chunk = max(1, -(-len(text) // n))
            pages = [text[i:i + chunk] for i in range(0, len(text), chunk)]
    while len(pages) < n:
        pages.append("")
    return pages[:n]


async def node_page_split(input_data: dict) -> dict:
    from src.models.drafts import PageCopy
    from src.models.tasks import Task
    from src.gateway.prompt_versions import get_effective_prompt
    async with SessionLocal() as session:
        text = await _latest_draft_body(session, input_data["task_id"])
        owner_id = (await session.execute(
            select(Task.created_by).where(Task.id == input_data["task_id"]))).scalar()
    # 首选 LLM 按页写图上文案；解析失败/调用失败退回机械切割（保证节点不卡死）
    pages, model_version, cost = None, "mechanical", 0.0
    try:
        import json as _json
        template = await get_effective_prompt("page_split", None, owner_id)
        llm_prompt = (template.replace("{body}", text) if "{body}" in template
                      else template + "\n\n" + text)
        result = await call_with_failover(llm_prompt, DEEPSEEK_MODEL, KIMI_MODEL)
        raw = result["text"].strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        arr = _json.loads(raw[raw.index("["):raw.rindex("]") + 1])
        arr = [str(p).strip() for p in arr if str(p).strip()]
        if len(arr) >= 6:
            pages = arr[:6]
            model_version = result["model_version"]
            cost = result["cost_cny"]
    except Exception:
        traceback.print_exc()
    if pages is None:
        pages = _split_pages(text, 6)
    async with SessionLocal() as session:
        for i, body in enumerate(pages, start=1):
            session.add(PageCopy(task_id=input_data["task_id"], page_index=i, body=body, claim_ids=[]))
        await session.commit()
    return {"page_count": len(pages), "model_version": model_version,
            "prompt_version": "page_split_llm_v1", "cost_cny": cost}


async def _generate_single_asset(task_id, page_index: int, prompt: str,
                                 reference_image_urls=None) -> dict:
    from src.gateway.image_gen import generate_image
    r = await generate_image(prompt, reference_image_urls=reference_image_urls)
    return {"task_id": task_id, "page_index": page_index, "hash": r["hash"],
            "image_url": r["image_url"],
            "source_type": "ai_generated", "copyright_status": "clear",
            "model_version": r["model_version"], "is_illustration": False}


async def _dedupe_and_validate(asset: dict, prompt: str, reference_urls,
                               task_id, page_index: int, seen_hashes: set) -> tuple:
    """内容级去重 + 尺寸校验：下载图片字节算内容 hash，与本任务已出图重复则换构图重生成一次；
    宽高比偏离 3:4 则在 model_version 上标记（交付导出时会统一归一到 1152x1536）。
    返回 (asset, 额外生成次数, 图片字节)。"""
    import io
    from src.gateway.ocr import fetch_image_bytes
    extra = 0
    data = None
    try:
        data, ctype = await fetch_image_bytes(asset["image_url"])
        content_hash = hashlib.md5(data).hexdigest()
        if content_hash in seen_hashes:
            r2 = await _generate_single_asset(
                task_id, page_index, prompt + "（请换一种与之前不同的构图和视角）",
                reference_urls)
            extra += 1
            data2, ctype = await fetch_image_bytes(r2["image_url"])
            asset = r2
            data = data2
            content_hash = hashlib.md5(data).hexdigest()
        seen_hashes.add(content_hash)
        asset["hash"] = content_hash
        from PIL import Image
        w, h = Image.open(io.BytesIO(data)).size
        if abs(w / h - 0.75) > 0.05:
            asset["model_version"] += f"|badsize:{w}x{h}"
        # 立即本地化：上游生成 URL 会过期，落盘后 image_url 指向本地副本
        asset["origin_url"] = asset["image_url"]
        asset["image_url"] = _persist_image(task_id, page_index, "p", data, ctype)
    except Exception:
        # 下载/解析失败不阻塞出图（OCR 节点会再暴露问题），但必须留痕，
        # 否则去重/校验/持久化静默失效（2026-08-20 缺失 hashlib 导入的教训）
        traceback.print_exc()
    return asset, extra


async def node_asset_gen(input_data: dict) -> dict:
    import asyncio
    from src.models.tasks import Task
    from src.models.assets import Asset
    from src.models.drafts import PageCopy
    from src.gateway.prompt_versions import get_image_prompt, get_effective_prompt
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == input_data["task_id"]))).scalar_one()
        mode = task.mode or "general"
        owner_id = task.created_by
        pages = await session.execute(
            select(PageCopy).where(PageCopy.task_id == input_data["task_id"]))
        page_list = pages.scalars().all()
        reference_urls = None
        if mode in ("compare", "single"):
            refs = await session.execute(
                select(Asset).where(Asset.task_id == input_data["task_id"],
                                    Asset.source_type == "official",
                                    Asset.is_illustration == False))
            reference_urls = [a.image_url for a in refs.scalars() if a.image_url]
    # 自定义生图模板（提示词库启用的）替代系统模板；排版轮换仍由代码追加
    image_template = await get_effective_prompt("image_gen", mode, owner_id)
    prompts = [get_image_prompt(mode, p.body or "", i, template=image_template)
               for i, p in enumerate(page_list, start=1)]
    while len(prompts) < 6:
        prompts.append(get_image_prompt(mode, "", len(prompts) + 1,
                                        template=image_template))
    # 串行 + 间隔生成：避免测试账户限流，保证每张图有足够处理时间
    results = []
    seen_hashes = set()
    extra_gen = 0
    for i in range(1, 7):
        r = await _generate_single_asset(
            input_data["task_id"], i, prompts[i - 1], reference_urls)
        if not settings.mock_image_gen:
            r, extra = await _dedupe_and_validate(r, prompts[i - 1], reference_urls,
                                                  input_data["task_id"], i, seen_hashes)
            extra_gen += extra
        results.append(r)
        await asyncio.sleep(settings.image_gen_delay_seconds)
    async with SessionLocal() as session:
        for r in results:
            session.add(Asset(**r))
        await session.commit()
    return {"asset_count": len(results),
            "image_urls": [r.get("image_url") for r in results if r.get("image_url")],
            "cost_cny": 0 if settings.mock_image_gen
                        else (len(results) + extra_gen) * settings.image_cost_per_image_cny}


async def node_ocr_read(input_data: dict) -> dict:
    from src.models.assets import OcrResult, Asset
    from src.gateway.ocr import ocr_image
    async with SessionLocal() as session:
        assets = await session.execute(
            select(Asset).where(Asset.task_id == input_data["task_id"],
                                Asset.source_type == "ai_generated"))
        rows = [(a.id, a.page_index, a.image_url) for a in assets.scalars()]
    if settings.mock_image_gen:
        # mock 生图时配图是占位 SVG，无真实文字可识别，沿用桩逻辑
        async with SessionLocal() as session:
            for asset_id, page_index, _ in rows:
                session.add(OcrResult(asset_id=asset_id, raw_text=f"page {page_index}",
                                      key_fields={"page": str(page_index)},
                                      confidence=0.95))
            await session.commit()
        return {"ocr_completed": True, "cost_cny": 0}
    results = []
    total_cost = 0.0
    for asset_id, page_index, image_url in rows:
        try:
            r = await ocr_image(image_url)
            results.append(OcrResult(asset_id=asset_id, raw_text=r["raw_text"],
                                     key_fields={"page": str(page_index),
                                                 "ocr_model": r["model"]},
                                     confidence=0.9))
            total_cost += r["cost_cny"]
        except Exception:
            # 单张识别失败不拖垮整条任务，置信度记 0 供交叉校验判风险
            results.append(OcrResult(asset_id=asset_id, raw_text="",
                                     key_fields={"page": str(page_index)},
                                     confidence=0.0))
    async with SessionLocal() as session:
        session.add_all(results)
        await session.commit()
    return {"ocr_completed": True, "cost_cny": total_cost}


async def node_cross_check(input_data: dict) -> dict:
    from src.models.assets import CrossCheck, OcrResult, Asset
    from src.models.drafts import PageCopy
    from src.quality.cross_check import extract_key_fields, compare_field
    async with SessionLocal() as session:
        pages = await session.execute(
            select(PageCopy).where(PageCopy.task_id == input_data["task_id"]))
        page_list = pages.scalars().all()
        # 每页配图的真实 OCR 文字（按 page_index 对齐）
        ocr_rows = await session.execute(
            select(Asset.page_index, OcrResult.raw_text, OcrResult.confidence)
            .join(OcrResult, OcrResult.asset_id == Asset.id)
            .where(Asset.task_id == input_data["task_id"],
                   Asset.source_type == "ai_generated"))
        ocr_map = {r.page_index: (r.raw_text or "", r.confidence)
                   for r in ocr_rows.all()}
        all_mismatches = []
        for p in page_list:
            expected = extract_key_fields(p.body)
            ocr_text, confidence = ocr_map.get(p.page_index, ("", 0.0))
            if confidence == 0.0:
                m = {"field_name": "ocr", "expected": "可识别",
                     "actual": "识别失败", "matched": False}
                session.add(CrossCheck(task_id=input_data["task_id"], **m))
                all_mismatches.append(m)
                continue
            actual = extract_key_fields(ocr_text)
            mismatches = compare_field(expected, actual)
            for m in mismatches:
                session.add(CrossCheck(task_id=input_data["task_id"], **m))
                all_mismatches.append(m)
        await session.commit()
    return {"mismatch_count": len(all_mismatches)}


async def node_risk_classify(input_data: dict) -> dict:
    from src.models.assets import CrossCheck
    from src.models.drafts import RuleResult
    from src.models.review import RiskClassification, Issue
    from src.models.entities import Claim, Evidence
    from src.risk.classifier import classify
    async with SessionLocal() as session:
        rules = await session.execute(select(RuleResult).where(RuleResult.task_id == input_data["task_id"]))
        checks = await session.execute(select(CrossCheck).where(CrossCheck.task_id == input_data["task_id"]))
        rule_list = [{"passed": r.passed, "rule_name": r.rule_name} for r in rules.scalars()]
        check_list = [{"matched": c.matched} for c in checks.scalars()]
        # 证据完整性：P0/P1 关键事实点必须有支撑证据，否则 evidence_complete=False
        evidence_complete = True
        claims = await session.execute(
            select(Claim).where(Claim.task_id == input_data["task_id"],
                                Claim.risk_level.in_(["P0", "P1"])))
        for claim in claims.scalars():
            ev = await session.execute(select(Evidence).where(Evidence.claim_id == claim.id))
            if ev.first() is None:
                evidence_complete = False
                break
        # P0 问题：存在未关闭的 P0 问题单即 has_p0_issue=True
        p0 = await session.execute(
            select(Issue).where(Issue.task_id == input_data["task_id"],
                                Issue.priority == "P0",
                                Issue.status == "open"))
        has_p0_issue = p0.first() is not None
        level, reasons = classify(rule_list, check_list, evidence_complete, has_p0_issue)
        rc = RiskClassification(task_id=input_data["task_id"], level=level, reasons=reasons)
        session.add(rc)
        await session.commit()
    return {"level": level, "reasons": reasons}


async def node_review_queue(input_data: dict) -> dict:
    from src.models.review import ReviewSession
    async with SessionLocal() as session:
        for role in ["A", "B", "C"]:
            session.add(ReviewSession(task_id=input_data["task_id"], role=role))
        await session.commit()
    return {"queued": ["A", "B", "C"]}


async def node_batch_signoff(input_data: dict) -> dict:
    from src.models.review import Batch
    async with SessionLocal() as session:
        b = Batch(risk_level="green", sampling_rate=0.20, member_count=1)
        session.add(b)
        await session.commit()
    return {"batch_id": str(b.id)}


async def node_publish_snapshot(input_data: dict) -> dict:
    from src.models.snapshots import PublishSnapshot
    from src.models.assets import Asset
    from src.models.review import Issue
    async with SessionLocal() as session:
        # 交付合同校验：页数=6、顺序、一页一图（缺页/错序/两图同页必驳回）
        # 只校验 AI 生成的交付配图；compare/single 的实景参考图（official）不计入交付页数
        assets = await session.execute(
            select(Asset).where(Asset.task_id == input_data["task_id"],
                                Asset.source_type == "ai_generated")
            .order_by(Asset.page_index))
        asset_list = assets.scalars().all()
        delivery_errors = []
        page_indexes = [a.page_index for a in asset_list]
        if len(asset_list) != 6:
            delivery_errors.append(f"缺页或多余页：期望 6 页，实际 {len(asset_list)} 页")
        if sorted(page_indexes) != [1, 2, 3, 4, 5, 6]:
            delivery_errors.append(f"页序错误：{page_indexes}")
        if len(page_indexes) != len(set(page_indexes)):
            delivery_errors.append(f"两图同页：{page_indexes}")
        if delivery_errors:
            # 交付不合格转红色：写入 P0 问题单，不生成快照
            for err in delivery_errors:
                session.add(Issue(task_id=input_data["task_id"], role="B",
                                  priority="P0", description=err))
            await session.commit()
            return {"delivery_errors": delivery_errors, "snapshot_created": False}
        s = PublishSnapshot(task_id=input_data["task_id"], snapshot_data={"frozen": True})
        session.add(s)
        await session.commit()
    return {"snapshot_id": str(s.id), "delivery_errors": []}
