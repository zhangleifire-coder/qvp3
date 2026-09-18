"""agent_production：全链创作 Agent 大节点（网关：dsh_serve）。

一次创作 Agent 调用完成原 entity_bind/evidence_build/draft_gen/page_split/
asset_gen/ocr_read 六个 AI 节点的全部工作（检索取证→正文→分页→生图→OCR），
本节点负责：
1. 组装上下文（query/mode/提示词库模板/驳回反馈）与输出 JSON 契约；
2. 流式调用创作 Agent（过程文本实时上监控）；
3. 严格校验返回 JSON，失败带错误信息在同一 session 纠错重问一次；
4. 确定性收尾：图片本地化/内容哈希/尺寸校验/落库（claims/evidence/
   drafts/page_copies/assets/ocr_results）；
5. 成本合并：文本 usage + MCP 工具回调台账 → node_events 成本口径不变。

可靠性兜底（软件工程层）：
- 网关不可达/超时/输出两次不合格 → 节点失败 → 任务 failed，
  重试幂等重跑；AGENT_PIPELINE_ENABLED=false 可整体切回 13 节点直连路径。

共享段（2026-09-09 抽取，行为零变化）：提示词常量/契约校验/本地化/质检链/
落库函数均在 src.pipeline.agent_shared，本文件 re-export 保持旧引用路径
（verify_skill_equivalence、ai_review 等）不破；staged 分阶段路径
（src.pipeline.agent_stages）复用同一份实现。
"""
import uuid

from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.gateway import dsh_client
from src.gateway.cost_tracker import estimate_cost
from src.gateway.prompt_versions import get_effective_prompt
from src.gateway.tool_ledger import tool_ledger
from src.models.assets import Asset
from src.models.tasks import Task
from src.pipeline.agent_shared import (  # noqa: F401
    _MODE_DESC, _OUTPUT_CONTRACT, _COMPLIANCE_RED_LINES, _AGENT_INSTRUCTIONS,
    _FEEDBACK_HEADER, _COMBO_SECTION, _build_combo_section,
    _CORRECTION_MESSAGE, _parse_agent_json, _validate_output, _compose_message,
    _localize_image, _fallback_ocr,
    _GARBLE_THRESHOLD, _GARBLE_MAX_REGEN, _text_similarity,
    _garble_check_and_regen, _subject_check_and_regen,
    _localize_all, _image_quality_chain, _localize_refs,
    _apply_style_backfill, _persist_claim_evidence, _persist_official_refs,
    _persist_review_marks, _persist_draft, _persist_page_copies,
    _persist_assets, _persist_ocr,
)


async def node_agent_production(input_data: dict) -> dict:
    task_id = input_data["task_id"]
    from src.stream.bus import bus
    tid = str(task_id)

    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, mode, owner_id = task.query, (task.mode or "general"), task.created_by
        combo_section = _build_combo_section(task)
        fixed_style = task.gen_style
        confirmed_refs = list((await session.execute(
            select(Asset).where(Asset.task_id == task_id,
                                Asset.source_type == "official",
                                Asset.selection_status == "confirmed")
            .order_by(Asset.page_index))).scalars().all())
        # 人工核定正文（文字核查关卡产出）：生图时直接使用，Agent 不再重写
        from src.pipeline.text_check import effective_texts
        eff = effective_texts(task)
        confirmed_body = eff.get("body") or ""
        # 标杆交付规范（81 条成功案例共性提炼，scripts/analyze_benchmark.py 产出）；
        # DB 无行时回退 skills/bench-spec/default_{mode}.txt 默认基线（2026-09-03 新增兜底）
        from sqlalchemy import text as _txt
        from src.gateway.skill_loader import fragment as _skill_fragment
        bench_rule = (await session.execute(_txt(
            "SELECT content FROM prompt_templates WHERE stage = :s "
            "AND owner_id IS NULL AND is_active ORDER BY updated_at DESC LIMIT 1"),
            {"s": f"bench_{mode}"})).scalar() \
            or _skill_fragment("bench-spec", f"default_{mode}", default="")

    # 提示词库仍然后端主管：解析顺序 用户自定义 → admin 系统覆盖 → 代码默认
    draft_tpl = await get_effective_prompt("draft_gen", mode, owner_id)
    pages_tpl = await get_effective_prompt("page_split", None, owner_id)
    image_tpl = await get_effective_prompt("image_gen", mode, owner_id)
    # 人设化共享段（2026-09-01 直连路径已有；2026-08-24 补齐 Agent 路径——
    # 生产任务走 Agent，此前真人感不足的根因之一就是这里漏追加了）：
    # 仅系统默认模板追加，用户自定义模板代表显式意图不覆盖（防负优化）
    from src.gateway.prompt_versions import default_prompt, _DRAFT_SHARED
    if draft_tpl == default_prompt("draft_gen", mode):
        draft_tpl = draft_tpl + "\n" + _DRAFT_SHARED

    regen = input_data.get("regen") or {}
    feedbacks = regen.get("feedback") or []

    if not await dsh_client.health():
        raise RuntimeError(
            f"dsh_serve 不可达（创作网关 :8901 未就绪），"
            f"请启动 dsh_serve 或设 AGENT_PIPELINE_ENABLED=false 回退直连路径")

    # 新一轮生产：重置该任务的 MCP 工具配额（中断/失败后续跑重新获得全额预算；
    # 配额权威在后端，MCP 进程重启/残留状态都不会把配额锁死）
    from src.gateway.tool_ledger import task_quotas
    await task_quotas.reset(tid)

    body_section = ""
    if confirmed_body:
        body_section = (
            "【正文（人工核定版，必须原样作为 draft 输出，不要重写、不要增删改）】\n"
            + confirmed_body[:3000] + "\n\n")
    refs_section = ""
    if confirmed_refs:
        # 阶段2（人工确认后重跑）：参考图已确认，注入指令直接使用，跳过搜图
        lines = "\n".join(
            f"  {i}. {a.image_url}（OCR命中: {a.ocr_hit or '无'}）"
            for i, a in enumerate(confirmed_refs, 1))
        refs_section = ("【已确认实景参考图（人工筛选后保留，必须使用）】\n"
                        + lines
                        + "\n要求：跳过 image_search，把以上图片路径原样作为 "
                          "generate_images 的 reference_urls 参数传入做图生图；"
                          "不要增删替换。各页文案与正文提及的场景、细节需与"
                          "参考图实际画面一致，不得写参考图里没有的内容。\n\n")
    session_id = f"qvp-task-{tid}-{uuid.uuid4().hex[:8]}"
    # 风格关键词库（用户"知识训练"数据）：非空时替代内置风格库供 Agent 自动匹配
    from src.api.styles import style_library_text
    kb_text = await style_library_text()
    bench_section = (bench_rule + "\n\n") if bench_rule else ""
    user_msg = _compose_message(tid, query, mode, draft_tpl, pages_tpl,
                                image_tpl, feedbacks,
                                bench_section + body_section + combo_section + refs_section,
                                fixed_style=fixed_style, style_kb_text=kb_text)
    await bus.publish("agent_progress", {"message": "已连接 dsh_serve，开始创作…",
                                         "session_id": session_id}, task_id=tid)

    last_emit_len = 0

    def _on_delta(piece: str, total: str):
        # 流式过程按 120 字符节流上报监控：字符数 + token 估算 + 输出尾部
        # （120 字符/帧 ≈ 每 1-2 秒一帧，兼顾实时感与事件量；
        # token=字符/1.7 与 dsh_client 的成本估算口径一致）
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
    validated, errors = _validate_output(parsed or {})
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
        validated, errors = _validate_output(parsed or {})
        if validated is None:
            # Agent notes 常带真实失败原因（如生图通道余额不足），拼进报错
            # 避免只见「缺 image_url」这类表象、排障要多挖一层（2026-09-03 实例）
            agent_notes = str((parsed or {}).get("notes") or "")[:300]
            raise RuntimeError(
                f"Agent 输出两次未通过校验: {errors}"
                + (f"｜Agent备注: {agent_notes}" if agent_notes else ""))

    out = validated
    prompt_version = (f"agent_{mode}_v1"
                      + (f"_regen{regen.get('round', 1)}" if regen else ""))
    text_cost = estimate_cost(result["model_version"],
                              usage["prompt_tokens"], usage["completion_tokens"],
                              cache_hit_tokens=result.get("cache_hit_tokens"))

    # ── 确定性收尾：图片本地化 → 质检链（扭曲/主体/AI 双重审核）→ 参考图本地化 ──
    localized = await _localize_all(task_id, out["images"])
    localized, garbled = await _image_quality_chain(
        task_id, out["pages"], localized, image_tpl, mode, confirmed_refs)
    refs_localized = await _localize_refs(task_id, out["references"])

    # ── 落库：一次性原子提交全部产物 ──
    async with SessionLocal() as session:
        task_row = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        await _apply_style_backfill(task_row, out["content_style"],
                                    out["image_style"])
        await _persist_claim_evidence(session, task_id, query, out["evidence"])
        await _persist_official_refs(session, task_id, query, refs_localized)
        await _persist_review_marks(session, task_id, localized, garbled)
        await _persist_draft(session, task_id, out["draft"],
                             result["model_version"], prompt_version)
        await _persist_page_copies(session, task_id, out["pages"])
        await _persist_assets(session, task_id, query, localized,
                              result["model_version"])
        ocr_cost = await _persist_ocr(session, task_id, out["ocr_map"])
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
