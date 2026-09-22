"""agent_stages：staged（分阶段）Agent 路径——创作段 4 个独立 Agent 节点
（AGENT_PIPELINE_VARIANT=staged 时启用，2026-09-09）。

把 agent_production 大节点拆成 agent_evidence（取证+风格判定）→ agent_draft
（正文）→ agent_pages（分页）→ agent_assets（配图生成）四个独立节点：
各自独立 dsh 会话（qvp-task-{tid}-{stage}-{hex8}，纠错重问复用同 session）、
独立契约 JSON、独立幂等/重试/成本帧（node_events 按阶段拆分可见）。

设计边界（与 monolith 同源，提示词内容零改动）：
- 阶段间只经 DB 传数据（不经会话记忆）：每阶段落库后下一阶段读库，
  中间产物可人工查看/修正，失败重跑从断点阶段续；
- 提示词单一事实来源不变：正文/分页/生图模板仍走 prompt_versions +
  skills/，指令包装段从 _AGENT_INSTRUCTIONS 对应段拆用（合规红线段直接
  引用 agent_shared._COMPLIANCE_RED_LINES，不复制）；
- 确定性收尾不 Agent 化：本地化/扭曲质检/主体审核/AI 双重审核/OCR 兜底/
  落库全部复用 agent_shared 的同一份实现；
- 配额 reset 点上移：task 级 MCP 配额在 agent_evidence 入口 reset
  （monolith 路径仍在 agent_production 入口，互不干扰）。
"""
import hashlib
import uuid

from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.gateway import dsh_client
from src.gateway.cost_tracker import estimate_cost
from src.gateway.prompt_versions import get_effective_prompt
from src.gateway.tool_ledger import tool_ledger
from src.models.assets import Asset
from src.models.drafts import PageCopy
from src.models.entities import Claim, Evidence
from src.models.tasks import Task
from src.pipeline.agent_shared import (
    _MODE_DESC, _COMPLIANCE_RED_LINES, _FEEDBACK_HEADER, _build_combo_section,
    _CORRECTION_MESSAGE, _parse_agent_json,
    _localize_all, _image_quality_chain, _localize_refs,
    _apply_style_backfill, _persist_claim_evidence, _persist_official_refs,
    _persist_review_marks, _persist_draft, _persist_page_copies,
    _persist_assets, _persist_ocr,
)

# ── 各阶段输出契约（大契约按阶段拆小，字段归一化口径与 _validate_output 一致）──
_EVIDENCE_CONTRACT = """{
  "evidence":  [{"title": "来源标题", "url": "https://...", "summary": "关键事实摘要"}],
  "content_style": "判定的内容风格（解读·经验分享/测评实测/攻略教程/避坑指南/观点杂谈）",
  "image_style": "选定的图片整体视觉风格（图片视觉风格库中的一种）",
  "notes":     "取证过程备注（检索词与命中情况）"
}"""

_DRAFT_CONTRACT = """{
  "draft":     "正文全文（400-700 字）",
  "notes":     "撰写过程备注（证据引用与自检情况）"
}"""

_PAGES_CONTRACT = """{
  "pages":     [{"title": "≤18字页标题（query 直接回答式）", "section_title": "≤14字小节标题（封面/结尾可空串）", "paragraph": "80-100字整段正文（3-4句实操干货）", "subject": "≤20字本页画面主体（具体可画的生活场景名词短语）", "info_task": "本页信息任务一句话"}],
  "notes":     "分页过程备注（字数自查情况）"
}
（pages 为恰好 {page_count} 个上述结构页对象的数组）"""

_ASSETS_CONTRACT = """{
  "references": [{"image_url": "image_search 返回的图 URL", "title": "...", "engine": "..."}],
  "images":    [{"page_index": 1, "image_url": "generate_images 返回的本地路径", "prompt_used": "该页实际生图提示词"}],
  "ocr_texts": [{"page_index": 1, "text": "该页配图 OCR 识别出的文字"}],
  "notes":     "配图过程备注（工具调用与自检情况）"
}"""

# ── 各阶段指令模板（包装段从 _AGENT_INSTRUCTIONS 对应段拆用，措辞保持一致；
# 合规红线段引用 agent_shared._COMPLIANCE_RED_LINES 单一事实来源）────────────
_EVIDENCE_INSTRUCTIONS = """你是「图文生产平台」创作流水线的取证与风格判定 Agent（第 1/4 阶段），只负责：检索事实证据、判定内容风格与图片整体视觉风格。正文撰写、分页、配图由后续阶段完成，本阶段不要做。

【任务】
- Query：{query}
- 生产模式：{mode}（{mode_desc}）
- task_id：{task_id}（每次调用工具时，task_id 参数必须原样传这个值，用于配额与成本记账）

【可用工具（按需调用，均有配额）】
- web_search(query, task_id)：网页检索事实证据（配额 {quota_search} 次，检索词要精准）

【图片视觉风格库（风格判定时从中选择）】
{image_style_library}

【工作流程（必须遵守）】
1. 先调 web_search 检索证据（1-2 次），整理成 evidence（没有可靠来源就给空数组，不要编造 URL）。
2. 风格判定（自适应，写进 content_style / image_style 两个字段）：
   a. 内容风格：{style_rule}结合 Query 与上一步检索到的信息，从
      「解读·经验分享 / 测评实测 / 攻略教程 / 避坑指南 / 观点杂谈」中判定最贴合的一种；
      多个都适配时随机选一种（不同次生成允许不同，保证内容多样性）。
   b. 图片整体视觉风格：从上方【图片视觉风格库】选一种最贴合内容气质的视觉风格；
      结合各风格的适用条件与忌讳条款选择（条目附「适用/忌讳」时务必参考）；
      多个适配时同样随机选一种。选中风格的忌讳条款在后续配图阶段必须原样保留进
      image_template。后续正文行文按内容风格执行。
3. 只输出最终 JSON，不要输出 JSON 以外的任何解释文字。

{feedback_section}【输出 JSON 契约（字段名与类型必须完全一致）】
{output_contract}"""

_DRAFT_INSTRUCTIONS = """你是「图文生产平台」创作流水线的正文撰写 Agent（第 2/4 阶段），只负责：按正文创作规范撰写正文 draft。分页、配图由后续阶段完成，本阶段不要做。

【任务】
- Query：{query}
- 生产模式：{mode}（{mode_desc}）
- task_id：{task_id}
- 内容风格：{content_style}（上一阶段已判定，正文严格按该风格行文，不要另行选择）

【检索证据（上一阶段产出，事实以证据为准，不得编造来源）】
{evidence_section}

【工作流程（必须遵守）】
1. 严格按【正文创作规范】写正文 draft（400-700 字，事实以证据为准）。
2. 只输出最终 JSON，不要输出 JSON 以外的任何解释文字。

【正文创作规范（系统提示词，必须遵守）】
{draft_template}

""" + _COMPLIANCE_RED_LINES + """
{body_section}{feedback_section}【输出 JSON 契约（字段名与类型必须完全一致）】
{output_contract}"""

_PAGES_INSTRUCTIONS = """你是「图文生产平台」创作流水线的分页 Agent（第 3/4 阶段），只负责：把正文改写成恰好 {page_count} 页结构化图上文案。配图由后续阶段完成，本阶段不要做。

【任务】
- Query：{query}
- task_id：{task_id}

【正文（上一阶段产出，是分页的事实依据，图上文案不得比正文更空泛）】
{draft}

【工作流程（必须遵守）】
1. 严格按【分页规范】把正文改写成恰好 {page_count} 页结构化图上文案 pages（数组长度必须等于 {page_count}，每页为 title/subtitle/points/subject/info_task 五字段对象）。
2. 只输出最终 JSON，不要输出 JSON 以外的任何解释文字。

""" + _COMPLIANCE_RED_LINES + """
【分页规范（系统提示词，必须遵守）】
{pages_template}

{feedback_section}【输出 JSON 契约（字段名与类型必须完全一致）】
{output_contract}"""

_ASSETS_INSTRUCTIONS = """你是「图文生产平台」创作流水线的配图生成 Agent（第 4/4 阶段），只负责：为 {page_count} 页文案生成 {page_count} 张交付配图（single/compare 模式用参考图做图生图保持外观一致）。

【任务】
- Query：{query}
- 生产模式：{mode}（{mode_desc}）
- task_id：{task_id}（每次调用工具时，task_id 参数必须原样传这个值，用于配额与成本记账）
- 图片整体视觉风格：{image_style}（上一阶段已判定{image_style_desc}）

【{page_count} 页图上文案（上一阶段产出；generate_images 的 pages 参数必须原样传这个列表）】
{pages_section}

【可用工具（按需调用，均有配额）】
- image_search(query, task_id, count)：搜实景/实物参考图（仅 single/compare 模式需要）
- generate_images(task_id, pages, mode, image_template, reference_urls)：批量生成 {page_count} 张交付配图。
  参考图按页分配（铁律）：reference_urls 不要整表传给每一页——第 i 页取列表中
  第 (i-1)%N、i%N 两张作为该页参考子集（N=参考图张数），保证相邻页参考图不同、
  单页不堆砌全部实景图。参考图仅 1-2 张时允许重复。
  必须传：pages={page_count} 页文案原样列表、mode、image_template=下方生图模板（按第 2 步替换风格句后的版本）；
  single/compare 再传 reference_urls=image_search 结果里挑出的图片 URL。
- ocr_image(image_url, task_id)：OCR 识别配图文字。默认跳过——系统会自动做图文
  一致性校验；仅当某页文案含关键数字/型号必须重点核验时，对那一页调用

【工作流程（必须遵守）】
1. single/compare 模式：调 image_search 搜参考图，把可用结果放进 references
   （下方已给出【已确认实景参考图】时跳过搜图，直接使用已确认图）。
2. 调 generate_images 生成 {page_count} 张图（生成结果里的 image_url 是本地路径，输出时必须原样照抄）。
   传 image_template 时：把模板中的风格句「坚韧治愈风、高清、极简高级」替换为上方给定的
   图片整体视觉风格的描述词，其余约束原样保留——
   这样 {page_count} 张配图统一为给定的整体视觉风格。
3. 默认不做 OCR（系统自动校验）。仅关键数字页需核验时，对该页调 ocr_image，结果写进 ocr_texts。
4. 只输出最终 JSON，不要输出 JSON 以外的任何解释文字。

{bench_section}{refs_section}【生图模板（原样作为 generate_images 的 image_template 参数传入，不要改写）】
{image_template}

{feedback_section}【输出 JSON 契约（字段名与类型必须完全一致）】
{output_contract}"""


# ── 各阶段契约校验（比大契约简单；归一化口径与 agent_shared._validate_output 一致）──
def _norm_evidence(data: dict) -> tuple[list, list[str]]:
    evidence = data.get("evidence") or []
    errors: list[str] = []
    if not isinstance(evidence, list):
        evidence = []
        errors.append("evidence 若提供须为数组（没有可靠证据给空数组）")
    evidence = [{"title": str(e.get("title", ""))[:200],
                 "url": str(e.get("url", "") or "no-url")[:500],
                 "summary": str(e.get("summary", ""))[:500]}
                for e in evidence if isinstance(e, dict)][:12]
    return evidence, errors


def _norm_references(data: dict) -> list:
    references = data.get("references") or []
    if not isinstance(references, list):
        references = []
    return [{"image_url": str(r.get("image_url", ""))[:800],
             "title": str(r.get("title", ""))[:200],
             "engine": str(r.get("engine", "search"))[:50]}
            for r in references if isinstance(r, dict)
            and r.get("image_url")][:12]


def _norm_ocr_map(data: dict) -> dict[int, str]:
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
    return ocr_map


def _validate_evidence(data: dict) -> tuple[dict | None, list[str]]:
    if not isinstance(data, dict):
        return None, ["输出不是 JSON 对象"]
    evidence, errors = _norm_evidence(data)
    content_style = str(data.get("content_style") or "").strip()[:30]
    image_style = str(data.get("image_style") or "").strip()[:30]
    if not content_style:
        errors.append("content_style 缺失（须判定一种内容风格）")
    if not image_style:
        errors.append("image_style 缺失（须从图片视觉风格库选一种）")
    if errors:
        return None, errors
    return {"evidence": evidence, "content_style": content_style,
            "image_style": image_style,
            "notes": str(data.get("notes", ""))[:1000]}, []


def _validate_draft(data: dict) -> tuple[dict | None, list[str]]:
    if not isinstance(data, dict):
        return None, ["输出不是 JSON 对象"]
    errors: list[str] = []
    draft = data.get("draft")
    if not isinstance(draft, str) or len(draft.strip()) < 150:
        errors.append("draft 缺失或过短（须为 150 字以上的正文全文）")
        draft = draft.strip() if isinstance(draft, str) else ""
    else:
        draft = draft.strip()
    if errors:
        return None, errors
    return {"draft": draft, "notes": str(data.get("notes", ""))[:1000]}, []


def _validate_pages(data: dict) -> tuple[dict | None, list[str]]:
    """分页输出校验（v0.1.4 P2 双格式）：结构化页对象数组优先
    （page_schema.PageSpec），旧版字符串数组走兼容层机械结构化；
    页数 = settings.page_count。返回 pages 为 rendered 纯文本口径。"""
    from src.config import settings
    from src.services.page_schema import (
        normalize_spec, spec_from_plain, specs_from_rendered)
    if not isinstance(data, dict):
        return None, ["输出不是 JSON 对象"]
    errors: list[str] = []
    n = settings.page_count
    pages = data.get("pages")
    specs = None
    if isinstance(pages, list) and len(pages) == n:
        if all(isinstance(p, dict) for p in pages):
            try:
                specs = [normalize_spec(p) for p in pages]
            except Exception as exc:
                errors.append(f"pages 页对象非法：{exc}")
                specs = None
        elif all(isinstance(p, str) and p.strip() for p in pages):
            specs = [spec_from_plain(p.strip()) for p in pages]
        else:
            errors.append(f"pages 必须是恰好 {n} 个页对象"
                          f"（或旧版 {n} 条非空字符串；当前 {len(pages)} 项混型）")
    else:
        errors.append(f"pages 必须是恰好 {n} 个页对象"
                      f"（当前 {len(pages) if isinstance(pages, list) else 0} 项）")
    if errors or specs is None:
        return None, errors or ["pages 解析失败"]
    return {"pages": specs_from_rendered(specs), "specs": specs,
            "notes": str(data.get("notes", ""))[:1000]}, []


def _validate_assets(data: dict) -> tuple[dict | None, list[str]]:
    from src.config import settings as _st
    if not isinstance(data, dict):
        return None, ["输出不是 JSON 对象"]
    errors: list[str] = []
    n_pages = _st.page_count   # v0.1.4 P2：页数可配
    images_raw = data.get("images")
    images: list[dict] = []
    if isinstance(images_raw, list) and len(images_raw) == n_pages:
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
        errors.append(f"images 必须是恰好 {n_pages} 项（generate_images 的返回逐页照抄）")
    if errors:
        return None, errors
    return {"images": images, "references": _norm_references(data),
            "ocr_map": _norm_ocr_map(data),
            "notes": str(data.get("notes", ""))[:1000]}, []


async def _run_stage(task_id, stage: str, user_msg: str, validate_fn,
                     progress_msg: str) -> dict:
    """单阶段 Agent 调用：健康检查 → 独立 stage session 流式调用 → 契约校验
    → 失败带错误信息在同一 session 纠错重问一次（与 monolith 同语义）。"""
    from src.stream.bus import bus
    tid = str(task_id)
    if not await dsh_client.health():
        raise RuntimeError(
            f"dsh_serve 不可达（创作网关 :8901 未就绪），"
            f"请启动 dsh_serve 或设 AGENT_PIPELINE_ENABLED=false 回退直连路径")
    session_id = f"qvp-task-{tid}-{stage}-{uuid.uuid4().hex[:8]}"
    await bus.publish("agent_progress", {"message": progress_msg,
                                         "session_id": session_id}, task_id=tid)

    last_emit_len = 0

    def _on_delta(piece: str, total: str):
        # 流式过程按 120 字符节流上报监控（与 monolith 同口径）
        nonlocal last_emit_len
        if len(total) - last_emit_len >= 120:
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
    result = await dsh_client.call_agent(user_msg, session_id=session_id,
                                             on_delta=_on_delta)
    usage["prompt_tokens"] += result["prompt_tokens"]
    usage["completion_tokens"] += result["completion_tokens"]

    parsed = _parse_agent_json(result["text"])
    validated, errors = validate_fn(parsed or {})
    correction_rounds = 0
    if validated is None:
        correction_rounds = 1
        await bus.publish("agent_progress",
                          {"message": f"输出校验失败，纠错重问：{errors[:3]}"}, task_id=tid)
        retry = await dsh_client.call_agent(
            _CORRECTION_MESSAGE.format(errors="\n".join(f"- {e}" for e in errors)),
            session_id=session_id)
        usage["prompt_tokens"] += retry["prompt_tokens"]
        usage["completion_tokens"] += retry["completion_tokens"]
        parsed = _parse_agent_json(retry["text"])
        validated, errors = validate_fn(parsed or {})
        if validated is None:
            agent_notes = str((parsed or {}).get("notes") or "")[:300]
            raise RuntimeError(
                f"Agent 输出两次未通过校验: {errors}"
                + (f"｜Agent备注: {agent_notes}" if agent_notes else ""))

    return {"out": validated, "model_version": result["model_version"],
            "usage": usage, "cache_hit_tokens": result.get("cache_hit_tokens"),
            "correction_rounds": correction_rounds, "session_id": session_id}


def _feedback_section(input_data: dict) -> tuple[str, str]:
    """驳回反馈段 + prompt_version 后缀（monolith 同口径）。"""
    regen = input_data.get("regen") or {}
    feedbacks = regen.get("feedback") or []
    lines = "\n".join(f"{i}. {r}" for i, r in enumerate(feedbacks, 1))
    section = (_FEEDBACK_HEADER.format(feedback_lines=lines)
               if feedbacks else "")
    suffix = f"_regen{regen.get('round', 1)}" if regen else ""
    return section, suffix


async def _stage_cost(r: dict, extra_cost: float = 0.0) -> tuple[float, float, int]:
    """阶段成本合并：文本 usage + 本阶段 MCP 工具回调台账（drain 取走即清，
    各阶段成本因此按节点拆分归属）→ (总成本, 工具成本, 工具调用数)。"""
    text_cost = estimate_cost(r["model_version"],
                              r["usage"]["prompt_tokens"],
                              r["usage"]["completion_tokens"],
                              cache_hit_tokens=r["cache_hit_tokens"])
    tool_cost, tool_items = await tool_ledger.drain(r["task_id"])
    return round(text_cost + tool_cost + extra_cost, 6), tool_cost, len(tool_items)


async def node_agent_evidence(input_data: dict) -> dict:
    """staged 第 1 阶段：web_search 取证 + 内容/图片风格判定 → claims/evidences
    + task.gen_* 回填（配额 reset 点上移到本节点入口）。"""
    task_id = input_data["task_id"]
    tid = str(task_id)
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, mode = task.query, (task.mode or "general")
        combo_section = _build_combo_section(task)
        fixed_style = task.gen_style

    # staged 路径配额 reset 点（monolith 在 agent_production 入口）：新一轮
    # 生产重置该任务的 MCP 工具配额，中断/失败后续跑重新获得全额预算
    from src.gateway.tool_ledger import task_quotas
    await task_quotas.reset(tid)

    if fixed_style:
        style_rule = (f"本任务已指定内容风格「{fixed_style}」，content_style 直接填该风格，"
                      f"正文严格按该风格行文，不要另行选择。")
    else:
        style_rule = ""
    # 风格关键词库（用户"知识训练"数据）：非空时替代内置风格库供 Agent 自动匹配
    from src.api.styles import style_library_text
    from src.services.combo import image_style_library_text
    kb_text = await style_library_text()
    feedback_section, regen_suffix = _feedback_section(input_data)
    user_msg = _EVIDENCE_INSTRUCTIONS.format(
        query=query, mode=mode, mode_desc=_MODE_DESC.get(mode, mode),
        task_id=tid, quota_search=settings.mcp_max_web_searches_per_task,
        image_style_library=kb_text or image_style_library_text(),
        style_rule=style_rule,
        feedback_section=combo_section + feedback_section,
        output_contract=_EVIDENCE_CONTRACT)
    r = await _run_stage(task_id, "evidence", user_msg, _validate_evidence,
                         "已连接 dsh_serve，取证与风格判定…")
    r["task_id"] = task_id
    out = r["out"]
    prompt_version = f"agent_evidence_v1{regen_suffix}"

    async with SessionLocal() as session:
        task_row = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        await _apply_style_backfill(task_row, out["content_style"],
                                    out["image_style"])
        await _persist_claim_evidence(session, task_id, query, out["evidence"])
        await session.commit()

    total_cost, tool_cost, tool_calls = await _stage_cost(r)
    return {"evidence_count": len(out["evidence"]),
            "content_style": out["content_style"],
            "image_style": out["image_style"],
            "model": r["model_version"], "model_version": r["model_version"],
            "prompt_version": prompt_version, "cost_cny": total_cost,
            "tool_calls": tool_calls, "tool_cost_cny": tool_cost,
            "correction_rounds": r["correction_rounds"],
            "session_id": r["session_id"], "degraded": False}


async def node_agent_draft(input_data: dict) -> dict:
    """staged 第 2 阶段：按 draft-write 规范写正文 → drafts 表。
    人工核定正文（text_override/body_draft）有则直通落库，不再调 Agent。"""
    task_id = input_data["task_id"]
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, mode, owner_id = task.query, (task.mode or "general"), task.created_by
        combo_section = _build_combo_section(task)
        content_style = (task.gen_style or "").strip() or "（未判定，按 Query 自适应选择一种最贴合的）"
        # 人工核定正文（文字核查关卡产出）：直接使用，Agent 不再重写
        from src.pipeline.text_check import effective_texts
        eff = effective_texts(task)
        confirmed_body = eff.get("body") or ""
        # 上一阶段证据（阶段间只经 DB 传数据）
        ev_rows = (await session.execute(
            select(Evidence).join(Claim, Evidence.claim_id == Claim.id)
            .where(Claim.task_id == task_id))).scalars().all()

    feedback_section, regen_suffix = _feedback_section(input_data)
    prompt_version = f"agent_draft_{mode}_v1{regen_suffix}"

    # 人工核定正文直通：确定性落库，不烧 Agent 调用
    if confirmed_body:
        async with SessionLocal() as session:
            await _persist_draft(session, task_id, confirmed_body,
                                 "human_confirmed", prompt_version + "_override")
            await session.commit()
        return {"text": confirmed_body, "preview": confirmed_body[:220],
                "length": len(confirmed_body), "model": "human_confirmed",
                "model_version": "human_confirmed",
                "prompt_version": prompt_version + "_override", "cost_cny": 0.0,
                "correction_rounds": 0, "degraded": False}

    # 提示词库仍然后端主管：解析顺序 用户自定义 → admin 系统覆盖 → 代码默认
    draft_tpl = await get_effective_prompt("draft_gen", mode, owner_id)
    # 人设化共享段：仅系统默认模板追加（与 monolith 同口径，防负优化）
    from src.gateway.prompt_versions import default_prompt, _DRAFT_SHARED
    if draft_tpl == default_prompt("draft_gen", mode):
        draft_tpl = draft_tpl + "\n" + _DRAFT_SHARED

    evidence_section = "\n".join(
        f"- {e.excerpt}（来源：{e.source_url}）" for e in ev_rows[:12]) \
        or "（无检索证据：不得编造具体数字与来源，按通识写作）"
    user_msg = _DRAFT_INSTRUCTIONS.format(
        query=query, mode=mode, mode_desc=_MODE_DESC.get(mode, mode),
        task_id=str(task_id), content_style=content_style,
        evidence_section=evidence_section, draft_template=draft_tpl,
        body_section="", feedback_section=combo_section + feedback_section,
        output_contract=_DRAFT_CONTRACT)
    r = await _run_stage(task_id, "draft", user_msg, _validate_draft,
                         "正文撰写中…")
    r["task_id"] = task_id
    out = r["out"]

    async with SessionLocal() as session:
        await _persist_draft(session, task_id, out["draft"],
                             r["model_version"], prompt_version)
        await session.commit()

    total_cost, tool_cost, tool_calls = await _stage_cost(r)
    return {"text": out["draft"], "preview": out["draft"][:220],
            "length": len(out["draft"]), "model": r["model_version"],
            "model_version": r["model_version"],
            "prompt_version": prompt_version, "cost_cny": total_cost,
            "tool_calls": tool_calls, "tool_cost_cny": tool_cost,
            "correction_rounds": r["correction_rounds"],
            "session_id": r["session_id"], "degraded": False}


async def node_agent_pages(input_data: dict) -> dict:
    """staged 第 3 阶段：按 page-split 规范把正文改写成 6 页图上文案
    → page_copies 表。缺上一阶段正文即报错（断点语义明确）。"""
    task_id = input_data["task_id"]
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, owner_id = task.query, task.created_by
        from src.pipeline.nodes import _latest_draft_body
        draft_body = await _latest_draft_body(session, task_id)
    if not draft_body or len(draft_body.strip()) < 150:
        raise RuntimeError(
            "agent_pages 缺少上一阶段正文（drafts 表无有效记录），"
            "请先完成 agent_draft 阶段")

    pages_tpl = await get_effective_prompt("page_split", None, owner_id)
    from src.config import settings as _st
    from src.services.page_schema import fill_page_template
    n = _st.page_count
    feedback_section, regen_suffix = _feedback_section(input_data)
    user_msg = _PAGES_INSTRUCTIONS.format(
        query=query, task_id=str(task_id), draft=draft_body.strip()[:3000],
        page_count=n,
        pages_template=fill_page_template(pages_tpl, n),
        feedback_section=feedback_section,
        output_contract=fill_page_template(_PAGES_CONTRACT, n))
    r = await _run_stage(task_id, "pages", user_msg, _validate_pages,
                         "分页文案改写中…")
    r["task_id"] = task_id
    out = r["out"]
    prompt_version = f"agent_pages_v2{regen_suffix}"

    async with SessionLocal() as session:
        await _persist_page_copies(session, task_id, out["pages"])
        # 结构化文案快照（v0.1.4 P2）：compose 直连消费 subject/标题/要点
        if out.get("specs"):
            from sqlalchemy import update as _update
            await session.execute(_update(Task).where(Task.id == task_id)
                                  .values(page_specs=out["specs"]))
        await session.commit()

    total_cost, tool_cost, tool_calls = await _stage_cost(r)
    return {"page_count": len(out["pages"]), "pages_preview": out["pages"][0][:60],
            "model": r["model_version"], "model_version": r["model_version"],
            "prompt_version": prompt_version, "cost_cny": total_cost,
            "tool_calls": tool_calls, "tool_cost_cny": tool_cost,
            "correction_rounds": r["correction_rounds"],
            "session_id": r["session_id"], "degraded": False}


async def node_agent_assets(input_data: dict) -> dict:
    """staged 第 4 阶段：按 image-prompt 规范 + bench 交付规范生成 6 张配图
    （single/compare 用确认参考图做图生图）→ assets/ocr_results。
    确定性收尾（本地化/扭曲质检/主体审核/AI 双重审核/OCR 兜底）复用
    agent_shared 同一份实现，不 Agent 化。"""
    task_id = input_data["task_id"]
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, mode, owner_id = task.query, (task.mode or "general"), task.created_by
        image_style = (task.gen_image_style or "").strip()
        image_style_desc = (task.gen_image_style_desc or "").strip()
        confirmed_refs = list((await session.execute(
            select(Asset).where(Asset.task_id == task_id,
                                Asset.source_type == "official",
                                Asset.selection_status == "confirmed")
            .order_by(Asset.page_index))).scalars().all())
        page_rows = (await session.execute(
            select(PageCopy).where(PageCopy.task_id == task_id)
            .order_by(PageCopy.page_index))).scalars().all()
        # 标杆交付规范（与 monolith 同口径：DB 覆盖 → skills 默认基线）
        from sqlalchemy import text as _txt
        from src.gateway.skill_loader import fragment as _skill_fragment
        bench_rule = (await session.execute(_txt(
            "SELECT content FROM prompt_templates WHERE stage = :s "
            "AND owner_id IS NULL AND is_active ORDER BY updated_at DESC LIMIT 1"),
            {"s": f"bench_{mode}"})).scalar() \
            or _skill_fragment("bench-spec", f"default_{mode}", default="")

    pages = [p.body for p in page_rows]
    n_expect = settings.page_count   # v0.1.4：页数可配（0922 参考样式=5）
    if len(pages) != n_expect or any(not (p or "").strip() for p in pages):
        raise RuntimeError(
            f"agent_assets 缺少上一阶段分页文案（page_copies 当前 "
            f"{len(pages)} 条，须恰好 {n_expect} 条非空），请先完成 agent_pages 阶段")

    if image_style and not image_style_desc:
        from src.services.style_select import style_desc_for
        image_style_desc = (await style_desc_for(image_style, owner_id)) or ""
    # 迁移 023：选中风格的忌讳条款随描述词注入，提示 Agent 原样保留进 image_template
    style_pitfalls = ""
    if image_style:
        from src.services.style_select import style_extras_for
        _, style_pitfalls = await style_extras_for(image_style, owner_id)

    image_tpl = await get_effective_prompt("image_gen", mode, owner_id)
    feedback_section, regen_suffix = _feedback_section(input_data)

    refs_section = ""
    if confirmed_refs:
        # 参考图已确认（人工筛选）：注入指令直接使用，跳过搜图（monolith 同口径）
        lines = "\n".join(
            f"  {i}. {a.image_url}（OCR命中: {a.ocr_hit or '无'}）"
            for i, a in enumerate(confirmed_refs, 1))
        refs_section = ("【已确认实景参考图（人工筛选后保留，必须使用）】\n"
                        + lines
                        + "\n要求：跳过 image_search，把以上图片路径原样作为 "
                          "generate_images 的 reference_urls 参数传入做图生图；"
                          "不要增删替换。各页文案与正文提及的场景、细节需与"
                          "参考图实际画面一致，不得写参考图里没有的内容。\n\n")
    pages_section = "\n".join(f"{i}. {p}" for i, p in enumerate(pages, 1))
    bench_section = (bench_rule + "\n\n") if bench_rule else ""
    user_msg = _ASSETS_INSTRUCTIONS.format(
        query=query, mode=mode, mode_desc=_MODE_DESC.get(mode, mode),
        task_id=str(task_id), page_count=settings.page_count,
        image_style=image_style or "（未判定，按内容气质从风格库选一种）",
        image_style_desc=(f"，描述词：{image_style_desc}" if image_style_desc else "")
                          + (f"，忌讳（原样保留进 image_template）：{style_pitfalls}"
                             if style_pitfalls else ""),
        pages_section=pages_section,
        bench_section=bench_section, refs_section=refs_section,
        image_template=image_tpl, feedback_section=feedback_section,
        output_contract=_ASSETS_CONTRACT)
    prompt_version = f"agent_assets_{mode}_v1{regen_suffix}"

    # ── 混合生图模式（2026-09-21 并入，开关 image_compose_mode 默认开）──
    # 程序渲染文字版式（消灭模型伪汉字）+ 每页一次 AI 无文字画面（VS/刻度豁免
    # 文字-Free 检查）；画面 prompt 复用 text_check 的 image_prompt_draft。
    # 成本与生图直出持平（6 张画面 1:1 替换 6 张整图），重生率反而更低。
    if settings.image_compose_mode and not settings.mock_image_gen:
        r = await _compose_mode_assets(task_id, query, mode, pages,
                                       image_style, image_style_desc,
                                       confirmed_refs, image_tpl,
                                       prompt_version, regen_suffix,
                                       style_pitfalls, bench_rule)
        if r is not None:
            return r

    r = await _run_stage(task_id, "assets", user_msg, _validate_assets,
                         "配图生成中（generate_images）…")
    r["task_id"] = task_id
    out = r["out"]

    # ── 确定性收尾：本地化 → 质检链（扭曲/主体/AI 双重审核）→ 参考图本地化 ──
    localized = await _localize_all(task_id, out["images"])
    localized, garbled = await _image_quality_chain(
        task_id, pages, localized, image_tpl, mode, confirmed_refs)
    refs_localized = await _localize_refs(task_id, out["references"])

    async with SessionLocal() as session:
        await _persist_official_refs(session, task_id, query, refs_localized)
        await _persist_review_marks(session, task_id, localized, garbled)
        await _persist_assets(session, task_id, query, localized,
                              r["model_version"])
        ocr_cost = await _persist_ocr(session, task_id, out["ocr_map"])
        await session.commit()

    total_cost, tool_cost, tool_calls = await _stage_cost(r, ocr_cost)
    return {"asset_count": len(localized),
            "references_count": len(refs_localized),
            "image_urls": [img["image_url"] for img in localized],
            "model": r["model_version"], "model_version": r["model_version"],
            "prompt_version": prompt_version, "cost_cny": total_cost,
            "tool_calls": tool_calls, "tool_cost_cny": tool_cost,
            "correction_rounds": r["correction_rounds"],
            "session_id": r["session_id"], "degraded": False}

async def _compose_mode_assets(task_id, query, mode, pages, image_style,
                               image_style_desc, confirmed_refs, image_tpl,
                               prompt_version, regen_suffix,
                               style_pitfalls="", bench_rule=""):
    """混合生图：程序文字版式 + AI 无文字画面（poster_compose 内核）。

    v3.1（2026-09-21）：旧直出流程的风格要求经 visual_brief 抽离层复用——
    风格库忌讳条款 + 描述画面句 + 标杆规范配图句 → 纯画面 brief 注入插图
    prompt（文字/排版类自动剔除，程序版式已承担）；重生同样带 brief。
    其他同 v2：6 页插图并行；质检只对照程序实际渲染文字；不合格重合成
    不回退模型直出。返回与 node_agent_assets 相同结构；异常返回 None
    回退模型直出路径（可靠性优先）。
    """
    try:
        import asyncio as _asyncio

        from src.services.page_schema import (
            spec_from_plain, render_page_text)
        from src.services.poster_compose import (
            compose_page, gen_textfree_illustration,
            with_default_icons, visual_brief, pick_layout, pick_img_count,
            sub_prompts, ill_size_for)
        from src.services.visual_check import comprehensive_page_check
        from src.pipeline.nodes import _persist_image
        from src.services.style_select import page_refs as _pref

        # 画面 prompt：text_check 的 image_prompt_draft 优先；
        # 结构化分页快照（v0.1.4 P2）：与页数匹配则直连消费（subject 做
        # 插图主体锚定），否则 spec_from_plain 兼容层（旧任务/直连路径）
        ill_prompts: list[str] = []
        specs_raw: list = []
        async with SessionLocal() as session:
            task_row = (await session.execute(
                select(Task).where(Task.id == task_id))).scalar_one()
            tr = task_row.text_review or {}
            ill_prompts = [str(x) for x in (tr.get("image_prompt_draft") or [])][:len(pages)]
            ps = task_row.page_specs
            if isinstance(ps, list) and len(ps) == len(pages) \
                    and all(isinstance(x, dict) and x.get("title") for x in ps):
                specs_raw = ps
        style_desc = (image_style_desc or "").strip()
        brief = visual_brief(style_desc, style_pitfalls or "",
                             bench_rule or "")

        ref_all = [a.image_url for a in confirmed_refs]
        from src.stream.bus import bus
        await bus.publish("agent_progress",
                          {"message": "配图生成中（程序文字 + AI 画面）…"},
                          task_id=str(task_id))

        sem = _asyncio.Semaphore(
            max(2, min(6, int(getattr(settings, "image_gen_parallel", 2) or 2))))
        ctx: dict[int, dict] = {}
        # 实际生图张数计数器（含文字-Free/质检重生）——成本按此记账，
        # 不再按页数×单价（v0.1.4 P0.4 修复低估）
        gen_stat: dict = {"gen_calls": 0}

        async def _build_page(i: int, body: str):
            spec = (specs_raw[i - 1] if i - 1 < len(specs_raw)
                    else spec_from_plain(body))
            title, subtitle = spec["title"], spec.get("subtitle", "")
            raw_points = spec.get("points") or []
            points = with_default_icons(raw_points)
            paragraph = spec.get("paragraph") or ""
            # 0922 参考样式：段落式文案 → ref 版式（上图下文）+ 恒单张生活实拍图
            is_ref = bool(paragraph)
            ill_prompt = (ill_prompts[i - 1] if i - 1 < len(ill_prompts)
                          else f"{query} {title} 产品场景画面")
            if is_ref:
                ill_prompt += "，生活实拍感，自然光，真实生活场景"
            multi_hint = (not is_ref) and any(
                k in style_desc for k in ("拼贴", "宫格", "多张", "错落"))
            n_img = 1 if is_ref else pick_img_count(task_id, i, multi_hint)
            layout = ("ref" if is_ref
                      else pick_layout(task_id, i, n_img))
            subs = sub_prompts(ill_prompt, n_img, f"{task_id}:{i}",
                               subject=spec.get("subject") or "")
            size = ("1536x1024" if is_ref
                    else ill_size_for(layout, n_img))

            async def _one(j: int, sub: str):
                # 参考图只给首张子画面，避免成组图片彼此雷同
                refs = (_pref(ref_all, i) if ref_all else None) if j == 0 else None
                async with sem:
                    return await gen_textfree_illustration(
                        sub, style_desc, refs, brief=brief, size=size,
                        stat=gen_stat)

            ills = [p for p in await _asyncio.gather(
                *[_one(j, s) for j, s in enumerate(subs)]) if p]
            out_path = compose_page(
                title, points, illustration=ills,
                style_desc=style_desc, subtitle=subtitle, layout=layout,
                section_title=spec.get("section_title") or "",
                paragraph=paragraph,
                section_no=max(1, i - 1))
            ctx[i] = {"title": title, "points": points,
                      "point_texts": raw_points, "ill_prompt": ill_prompt,
                      "layout": layout, "n_img": n_img, "subs": subs,
                      "subtitle": subtitle,
                      "section_title": spec.get("section_title") or "",
                      "paragraph": paragraph,
                      "rendered": render_page_text(spec)}
            return i, out_path

        built = dict(await _asyncio.gather(
            *[_build_page(i, b) for i, b in enumerate(pages, 1)]))

        localized = []
        for i in range(1, len(pages) + 1):
            data = built[i].read_bytes()
            local_url = _persist_image(task_id, i, "p", data, "image/png")
            localized.append({
                "page_index": i, "image_url": local_url,
                "hash": hashlib.md5(data).hexdigest(),
                "origin_url": "", "size_ok": True,
                "prompt_used": f"compose:{ctx[i]['ill_prompt'][:180]}",
            })

        # 质检：只对「程序渲染的文字」（标题+要点）做 VL 综合校验；
        # 不合格重合成一次（新插图），仍不过打标记进人工审核
        flags: dict[int, list[str]] = {}
        ref_mode = mode in ("compare", "single")
        for img in localized:
            idx = img["page_index"]
            c = ctx[idx]
            rendered = c.get("rendered") or (
                c["title"] + "\n" + "\n".join(c["point_texts"]))
            chk = await comprehensive_page_check(
                img["image_url"], rendered, ref_mode)
            if chk is None or chk["ok"]:
                continue
            try:
                async def _reone(j: int, sub: str):
                    async with sem:
                        return await gen_textfree_illustration(
                            sub + "（换一个不同的构图角度）",
                            style_desc,
                            (_pref(ref_all, idx) if ref_all else None) if j == 0 else None,
                            brief=brief,
                            size=ill_size_for(c["layout"], c["n_img"]),
                            stat=gen_stat)

                ills2 = [p for p in await _asyncio.gather(
                    *[_reone(j, s) for j, s in enumerate(c["subs"])]) if p]
                out_path = compose_page(c["title"], c["points"],
                                        illustration=ills2,
                                        style_desc=style_desc,
                                        subtitle=c.get("subtitle", ""),
                                        layout=c["layout"],
                                        section_title=c.get("section_title", ""),
                                        paragraph=c.get("paragraph", ""),
                                        section_no=max(1, idx - 1))
                data = out_path.read_bytes()
                img["image_url"] = _persist_image(task_id, idx, "p", data,
                                                  "image/png")
                img["hash"] = hashlib.md5(data).hexdigest()
                img["prompt_used"] = (img.get("prompt_used", "")
                                      + "|recompose").strip("|")
                chk2 = await comprehensive_page_check(
                    img["image_url"], rendered, ref_mode)
                if chk2 is not None and not chk2["ok"]:
                    flags[idx] = (chk2["issues"] or chk["issues"]
                                  or ["重生后仍未通过综合质检"])
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                flags[idx] = chk["issues"] or ["综合质检未通过"]

        # 跨页批量质检（v0.1.4 P3）：拼版一次 VL 查跨页信息/视觉重复、
        # 风格一致性、深底页数——告警进 RejectMark 人工队列，不自动重生
        if len(localized) >= 2:
            from src.services.cross_page_check import (
                cross_page_check, cross_flags)
            cp = await cross_page_check(
                task_id, [im["image_url"] for im in localized], pages)
            if cp is not None and not cp["ok"]:
                for idx, msgs in cross_flags(cp, len(localized)).items():
                    flags.setdefault(idx, []).extend(msgs)

        async with SessionLocal() as session:
            await _persist_review_marks(session, task_id, localized, flags)
            await _persist_assets(session, task_id, query, localized,
                                  "compose:v1")
            await session.commit()
        # 成本估算：按实际生图调用数计（宫格每页 1-6 张 + 重生张数，
        # gen_stat 由 gen_textfree_illustration 内部累计）
        from src.gateway.cost_tracker import per_call_cost
        from src.config import settings as _st
        unit = per_call_cost(_st.image_model,
                             fallback=_st.image_cost_per_image_cny)
        return {"asset_count": len(localized), "references_count": 0,
                "image_urls": [im["image_url"] for im in localized],
                "model": "compose:v1", "model_version": "compose:v1",
                "prompt_version": prompt_version + "_compose" + regen_suffix,
                "cost_cny": round(unit * gen_stat["gen_calls"], 4),
                "tool_calls": 0, "tool_cost_cny": 0.0,
                "correction_rounds": 0, "session_id": "compose-mode",
                "degraded": False}
    except Exception:
        import traceback
        traceback.print_exc()
        return None
