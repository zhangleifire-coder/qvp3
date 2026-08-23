"""agent_production：全链创作 Agent 大节点（Nanobot 路径）。

一次 Nanobot 调用完成原 entity_bind/evidence_build/draft_gen/page_split/
asset_gen/ocr_read 六个 AI 节点的全部工作（检索取证→正文→分页→生图→OCR），
本节点负责：
1. 组装上下文（query/mode/提示词库模板/驳回反馈）与输出 JSON 契约；
2. 流式调用 Nanobot（过程文本实时上监控）；
3. 严格校验返回 JSON，失败带错误信息在同一 session 纠错重问一次；
4. 确定性收尾：图片本地化/内容哈希/尺寸校验/落库（claims/evidence/
   drafts/page_copies/assets/ocr_results）；
5. 成本合并：文本 usage + MCP 工具回调台账 → node_events 成本口径不变。

可靠性兜底（软件工程层）：
- Nanobot 不可达/超时/输出两次不合格 → 节点失败 → 任务 failed，
  重试幂等重跑；AGENT_PIPELINE_ENABLED=false 可整体切回 13 节点直连路径。
"""
import hashlib
import json
import traceback
import uuid

from sqlalchemy import func, select

from src.config import settings
from src.db.session import SessionLocal
from src.gateway import nanobot_client
from src.gateway.cost_tracker import estimate_cost
from src.gateway.prompt_versions import get_effective_prompt
from src.gateway.tool_ledger import tool_ledger
from src.models.assets import Asset, OcrResult
from src.models.drafts import Draft, PageCopy
from src.models.entities import Claim, Evidence
from src.models.tasks import Task

_MODE_DESC = {
    "general": "通用模式：通用科普/教程内容，配图用纯文生图（无需搜参考图）",
    "single": "单品模式：单一产品深度实测，需先搜实物参考图，图生图保持产品外观一致",
    "compare": "对比模式：两个主体对比评测，需先搜两主体实景参考图，图生图保持外观一致",
}

_OUTPUT_CONTRACT = """{
  "evidence":  [{"title": "来源标题", "url": "https://...", "summary": "关键事实摘要"}],
  "draft":     "正文全文（400-700 字）",
  "pages":     ["第1页图上文案", "第2页图上文案", "第3页图上文案", "第4页图上文案", "第5页图上文案", "第6页图上文案"],
  "references": [{"image_url": "image_search 返回的图 URL", "title": "...", "engine": "..."}],
  "images":    [{"page_index": 1, "image_url": "generate_images 返回的本地路径", "prompt_used": "该页实际生图提示词"}],
  "ocr_texts": [{"page_index": 1, "text": "该页配图 OCR 识别出的文字"}],
  "notes":     "创作过程备注（工具调用与自检情况）"
}"""

_AGENT_INSTRUCTIONS = """你是「图文生产平台」的创作 Agent，负责为一条 Query 生产完整的图文内容（1 篇正文 + 6 页配图）。

【任务】
- Query：{query}
- 生产模式：{mode}（{mode_desc}）
- task_id：{task_id}（每次调用工具时，task_id 参数必须原样传这个值，用于配额与成本记账）

【可用工具（按需调用，均有配额）】
- web_search(query, task_id)：网页检索事实证据（配额 {quota_search} 次，检索词要精准）
- image_search(query, task_id, count)：搜实景/实物参考图（仅 single/compare 模式需要）
- generate_images(task_id, pages, mode, image_template, reference_urls)：批量生成 6 张交付配图。
  必须传：pages=6 页文案原样列表、mode、image_template=下方生图模板原样字符串；
  single/compare 再传 reference_urls=image_search 结果里挑出的图片 URL。
- ocr_image(image_url, task_id)：OCR 识别配图文字。默认跳过——系统会自动做图文
  一致性校验；仅当某页文案含关键数字/型号必须重点核验时，对那一页调用

【工作流程（必须遵守）】
1. 先调 web_search 检索证据（1-2 次），整理成 evidence（没有可靠来源就给空数组，不要编造 URL）。
2. 严格按【正文创作规范】写正文 draft（400-700 字，事实以证据为准）。
3. 严格按【分页规范】把正文改写成恰好 6 页图上文案 pages（列表长度必须等于 6）。
4. single/compare 模式：调 image_search 搜参考图，把可用结果放进 references。
5. 调 generate_images 生成 6 张图（生成结果里的 image_url 是本地路径，输出时必须原样照抄）。
6. 默认不做 OCR（系统自动校验）。仅关键数字页需核验时，对该页调 ocr_image，结果写进 ocr_texts。
7. 只输出最终 JSON，不要输出 JSON 以外的任何解释文字。

【正文创作规范（系统提示词，必须遵守）】
{draft_template}

【合规红线（规则机逐词扫描，出现即判不通过）】
1. 禁用词：绝对、100%、最、第一、唯一、永久、终身、安全、无害、无副作用、治疗、疗效、保证。
   注意「最」含一切搭配（最重要/最关键/最好…），请改用「很/十分/更/相对」等表述。
2. 字数：正文（不计空白字符）必须落在 400-700 字之间，写完自查一遍再输出。

【分页规范（系统提示词，必须遵守）】
{pages_template}

【生图模板（原样作为 generate_images 的 image_template 参数传入，不要改写）】
{image_template}

{feedback_section}【输出 JSON 契约（字段名与类型必须完全一致）】
{output_contract}"""

_FEEDBACK_HEADER = """【审核驳回反馈（上一轮内容被人工驳回，必须逐条修正后再创作）】
{feedback_lines}

"""

_CORRECTION_MESSAGE = """你上一轮的输出未通过系统校验，问题如下：
{errors}

请严格按 JSON 契约重新输出完整的最终 JSON（直接以 {{ 开头，不要任何解释文字、不要 Markdown 代码块以外的内容）。"""


def _parse_agent_json(text: str) -> dict | None:
    """防御性解析：剥 ```json 围栏 / 截取首尾大括号 / json.loads。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return None


def _validate_output(data: dict) -> tuple[dict | None, list[str]]:
    """校验并归一化 Agent 输出；返回 (归一化结果, 错误列表)。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return None, ["输出不是 JSON 对象"]

    draft = data.get("draft")
    if not isinstance(draft, str) or len(draft.strip()) < 150:
        errors.append("draft 缺失或过短（须为 150 字以上的正文全文）")
        draft = draft.strip() if isinstance(draft, str) else ""

    pages = data.get("pages")
    if not isinstance(pages, list) or len(pages) != 6 \
            or any(not isinstance(p, str) or not p.strip() for p in pages):
        errors.append("pages 必须是恰好 6 条非空字符串（当前 %d 条）"
                      % (len(pages) if isinstance(pages, list) else 0))
        pages = [str(p).strip() for p in pages][:6] if isinstance(pages, list) else []
    else:
        pages = [p.strip() for p in pages]

    images_raw = data.get("images")
    images: list[dict] = []
    if isinstance(images_raw, list) and len(images_raw) == 6:
        for i, item in enumerate(images_raw, start=1):
            url = item.get("image_url") if isinstance(item, dict) else item
            if not isinstance(url, str) or not url.strip():
                errors.append(f"images 第 {i} 项缺少 image_url")
                continue
            images.append({"page_index": i, "image_url": url.strip(),
                           "origin_url": (item.get("origin_url") or "")
                           if isinstance(item, dict) else "",
                           "prompt_used": (item.get("prompt_used") or "")
                           if isinstance(item, dict) else ""})
    else:
        errors.append("images 必须是恰好 6 项（generate_images 的返回逐页照抄）")

    evidence = data.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = []
        errors.append("evidence 若提供须为数组（没有可靠证据给空数组）")
    evidence = [{"title": str(e.get("title", ""))[:200],
                 "url": str(e.get("url", "") or "no-url")[:500],
                 "summary": str(e.get("summary", ""))[:500]}
                for e in evidence if isinstance(e, dict)][:12]

    references = data.get("references") or []
    if not isinstance(references, list):
        references = []
    references = [{"image_url": str(r.get("image_url", ""))[:800],
                   "title": str(r.get("title", ""))[:200],
                   "engine": str(r.get("engine", "search"))[:50]}
                  for r in references if isinstance(r, dict)
                  and r.get("image_url")][:12]

    ocr_texts = data.get("ocr_texts") or []
    if not isinstance(ocr_texts, list):
        ocr_texts = []
    ocr_map: dict[int, str] = {}
    for o in ocr_texts:
        if isinstance(o, dict) and o.get("page_index") and o.get("text") is not None:
            try:
                ocr_map[int(o["page_index"])] = str(o["text"])
            except (TypeError, ValueError):
                continue

    if errors:
        return None, errors
    return {"draft": draft, "pages": pages, "images": images,
            "evidence": evidence, "references": references,
            "ocr_map": ocr_map, "notes": str(data.get("notes", ""))[:1000]}, []


def _compose_message(task_id: str, query: str, mode: str, draft_tpl: str,
                     pages_tpl: str, image_tpl: str, feedbacks: list[str]) -> str:
    lines = "\n".join(f"{i}. {r}" for i, r in enumerate(feedbacks, 1))
    feedback_section = (_FEEDBACK_HEADER.format(feedback_lines=lines)
                        if feedbacks else "")
    return _AGENT_INSTRUCTIONS.format(
        query=query, mode=mode, mode_desc=_MODE_DESC.get(mode, mode),
        task_id=task_id,
        quota_search=settings.mcp_max_web_searches_per_task,
        draft_template=draft_tpl, pages_template=pages_tpl,
        image_template=image_tpl, feedback_section=feedback_section,
        output_contract=_OUTPUT_CONTRACT)


async def _localize_image(task_id, page_index: int, image_url: str,
                          origin_url: str) -> tuple[str, str, str, bool]:
    """取图字节→本地化→内容哈希→尺寸校验。

    MCP 工具产出的 /static/generated/ 路径已是本地文件（校验存在即可）；
    远程 URL（Agent 越过工具直接给上游地址时）则下载落盘。
    返回 (本地路径, 内容hash, origin_url, size_ok)。
    """
    import io
    from src.gateway.ocr import fetch_image_bytes
    from src.pipeline.nodes import _persist_image
    data, ctype = await fetch_image_bytes(image_url)
    content_hash = hashlib.md5(data).hexdigest()
    if image_url.startswith("/static/generated/"):
        local_url = image_url
        origin = origin_url or ""
    else:
        local_url = _persist_image(task_id, page_index, "p", data, ctype)
        origin = origin_url or image_url
    size_ok = True
    try:
        from PIL import Image
        w, h = Image.open(io.BytesIO(data)).size
        size_ok = abs(w / h - 0.75) <= 0.05
    except Exception:  # noqa: BLE001
        pass
    return local_url, content_hash, origin, size_ok


async def _fallback_ocr(rows: list[tuple]) -> tuple[list, float]:
    """Agent 未提供 OCR 时由后端兜底识别（保证 cross_check 有数据）。

    3 路信号量并发：6 张串行约 30-60s → 并发后约 15-25s。
    """
    import asyncio
    from src.gateway.ocr import ocr_image
    sem = asyncio.Semaphore(3)

    async def _one(asset_id, page_index, image_url) -> tuple[OcrResult, float]:
        if settings.mock_image_gen:
            return OcrResult(asset_id=asset_id, raw_text=f"page {page_index}",
                             key_fields={"page": str(page_index)},
                             confidence=0.95), 0.0
        try:
            async with sem:
                r = await ocr_image(image_url)
            return OcrResult(asset_id=asset_id, raw_text=r["raw_text"],
                             key_fields={"page": str(page_index),
                                         "ocr_model": r["model"]},
                             confidence=0.9), r["cost_cny"]
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return OcrResult(asset_id=asset_id, raw_text="",
                             key_fields={"page": str(page_index)},
                             confidence=0.0), 0.0

    outs = await asyncio.gather(*[_one(*row) for row in rows])
    return [o for o, _ in outs], round(sum(c for _, c in outs), 6)


async def node_agent_production(input_data: dict) -> dict:
    task_id = input_data["task_id"]
    from src.stream.bus import bus
    tid = str(task_id)

    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, mode, owner_id = task.query, (task.mode or "general"), task.created_by

    # 提示词库仍然后端主管：解析顺序 用户自定义 → admin 系统覆盖 → 代码默认
    draft_tpl = await get_effective_prompt("draft_gen", mode, owner_id)
    pages_tpl = await get_effective_prompt("page_split", None, owner_id)
    image_tpl = await get_effective_prompt("image_gen", mode, owner_id)

    regen = input_data.get("regen") or {}
    feedbacks = regen.get("feedback") or []

    if not await nanobot_client.health():
        raise RuntimeError(
            f"Nanobot 不可达（{settings.nanobot_base_url}），"
            f"请启动 Nanobot 或设 AGENT_PIPELINE_ENABLED=false 回退直连路径")

    # 新一轮生产：重置该任务的 MCP 工具配额（中断/失败后续跑重新获得全额预算；
    # 配额权威在后端，MCP 进程重启/残留状态都不会把配额锁死）
    from src.gateway.tool_ledger import task_quotas
    await task_quotas.reset(tid)

    session_id = f"qvp-task-{tid}-{uuid.uuid4().hex[:8]}"
    user_msg = _compose_message(tid, query, mode, draft_tpl, pages_tpl,
                                image_tpl, feedbacks)
    await bus.publish("agent_progress", {"message": "已连接 Nanobot，开始创作…",
                                         "session_id": session_id}, task_id=tid)

    last_emit_len = 0

    def _on_delta(piece: str, total: str):
        # 流式过程按 300 字节流上报监控：字符数 + token 估算 + 输出尾部
        # （token=字符/1.7 与 nanobot_client 的成本估算口径一致）
        nonlocal last_emit_len
        if len(total) - last_emit_len >= 300:
            last_emit_len = len(total)
            import asyncio as _a
            try:
                loop = _a.get_running_loop()
                loop.create_task(bus.publish(
                    "agent_progress", {
                        "chars": len(total),
                        "tokens_est": int(len(total) / 1.7),
                        "preview": total[-400:],
                    }, task_id=tid))
            except RuntimeError:
                pass

    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    result = await nanobot_client.call_agent(user_msg, session_id=session_id,
                                             on_delta=_on_delta)
    usage["prompt_tokens"] += result["prompt_tokens"]
    usage["completion_tokens"] += result["completion_tokens"]

    parsed = _parse_agent_json(result["text"])
    validated, errors = _validate_output(parsed or {})
    correction_rounds = 0
    if validated is None:
        correction_rounds = 1
        await bus.publish("agent_progress",
                          {"message": f"输出校验失败，纠错重问：{errors[:3]}"}, task_id=tid)
        retry = await nanobot_client.call_agent(
            _CORRECTION_MESSAGE.format(errors="\n".join(f"- {e}" for e in errors)),
            session_id=session_id)
        usage["prompt_tokens"] += retry["prompt_tokens"]
        usage["completion_tokens"] += retry["completion_tokens"]
        parsed = _parse_agent_json(retry["text"])
        validated, errors = _validate_output(parsed or {})
        if validated is None:
            raise RuntimeError(f"Agent 输出两次未通过校验: {errors}")

    out = validated
    prompt_version = (f"agent_{mode}_v1"
                      + (f"_regen{regen.get('round', 1)}" if regen else ""))
    text_cost = estimate_cost(result["model_version"],
                              usage["prompt_tokens"], usage["completion_tokens"])

    # ── 确定性收尾：图片本地化（在落库前完成，失败即节点失败，不留半截产物）──
    localized = []
    for img in out["images"]:
        local_url, content_hash, origin, size_ok = await _localize_image(
            task_id, img["page_index"], img["image_url"], img["origin_url"])
        localized.append({**img, "image_url": local_url, "hash": content_hash,
                          "origin_url": origin, "size_ok": size_ok})

    # 参考图（compare/single）：本地化后存 official 素材，供审核追溯与图生图复核
    refs_localized = []
    for i, ref in enumerate(out["references"], start=1):
        try:
            local_url, _, _, _ = await _localize_image(task_id, i, ref["image_url"], "")
            refs_localized.append({**ref, "image_url": local_url})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            refs_localized.append(ref)  # 下载失败保留原地址，展示层走代理兜底

    # ── 落库：一次性原子提交全部产物 ──
    async with SessionLocal() as session:
        claim = Claim(task_id=task_id, claim_text=query, risk_level="P1", position=1)
        session.add(claim)
        await session.flush()
        for e in out["evidence"]:
            session.add(Evidence(claim_id=claim.id, source_url=e["url"],
                                 source_level="P2", excerpt=e["summary"],
                                 supports=True))
        for i, ref in enumerate(refs_localized, start=1):
            session.add(Asset(
                task_id=task_id, page_index=i, subject=query,
                source_type="official", copyright_status="unknown",
                hash=hashlib.md5(ref["image_url"].encode()).hexdigest(),
                image_url=ref["image_url"], origin_url=ref.get("image_url"),
                model_version=ref.get("engine") or "search", is_illustration=False))
        max_v = (await session.execute(
            select(func.max(Draft.version)).where(
                Draft.task_id == task_id))).scalar() or 0
        session.add(Draft(task_id=task_id, version=max_v + 1, body=out["draft"],
                          model_version=result["model_version"],
                          prompt_version=prompt_version))
        for idx, body in enumerate(out["pages"], start=1):
            session.add(PageCopy(task_id=task_id, page_index=idx, body=body,
                                 claim_ids=[]))
        for img in localized:
            mv = result["model_version"]
            if not img["size_ok"]:
                mv += "|badsize"
            session.add(Asset(
                task_id=task_id, page_index=img["page_index"], subject=query,
                source_type="ai_generated", copyright_status="clear",
                hash=img["hash"], image_url=img["image_url"],
                origin_url=img["origin_url"] or None,
                model_version=mv, is_illustration=False))
        await session.flush()
        # OCR：Agent 自检结果优先；Agent 未覆盖的页由后端兜底补齐
        # （cross_check 按全页 OCR 对撞，缺页会被判「识别失败」拉高风险分级）
        assets = (await session.execute(
            select(Asset.id, Asset.page_index, Asset.image_url)
            .where(Asset.task_id == task_id,
                   Asset.source_type == "ai_generated")
            .order_by(Asset.page_index))).all()
        ocr_cost = 0.0
        missing_rows = []
        for asset_id, page_index, image_url in assets:
            text = out["ocr_map"].get(page_index)
            if text is None:
                missing_rows.append((asset_id, page_index, image_url))
                continue
            session.add(OcrResult(
                asset_id=asset_id, raw_text=text,
                key_fields={"page": str(page_index), "source": "agent"},
                confidence=0.9 if text else 0.0))
        if missing_rows:
            rows, ocr_cost = await _fallback_ocr(missing_rows)
            session.add_all(rows)
        await session.commit()

    # ── 成本合并：文本 usage + MCP 工具回调台账（node_events 口径不变）──
    tool_cost, tool_items = await tool_ledger.drain(task_id)
    total_cost = round(text_cost + tool_cost + ocr_cost, 6)

    return {"text": out["draft"], "preview": out["draft"][:220],
            "length": len(out["draft"]), "model": result["model_version"],
            "model_version": result["model_version"],
            "prompt_version": prompt_version, "cost_cny": total_cost,
            "evidence_count": len(out["evidence"]),
            "references_count": len(refs_localized),
            "page_count": len(out["pages"]), "asset_count": len(localized),
            "image_urls": [img["image_url"] for img in localized],
            "tool_calls": len(tool_items), "tool_cost_cny": tool_cost,
            "correction_rounds": correction_rounds, "session_id": session_id,
            "degraded": False}
