"""qvp_mcp v2：图文平台功能小项的独立调用包装（创作类 7 + 校验类 4）。

薄包装原则（2026-09-03 功能项独立化）：每个工具复用 src/ 里与流水线节点
完全相同的实现（共享函数提取点：nodes.run_draft_gen / run_page_split_llm /
page_balance_issue、services/regen.rewrite_page_copy、text_check.body_rule_issues
等），流水线内部调用路径零变化，本模块只是把它们暴露为可独立调用的 MCP 工具。

统一约定：
- 每个工具必带 task_id（配额申请与成本记账的键）；
- 返回 {"ok": bool, "data": dict | None, "error": str | None}——
  配额拒绝/调用异常一律 ok=False + error 说明，不抛异常给 Agent；
- 配额：LLM 类工具每任务默认各 5 次、校验类各 10 次
  （settings.mcp_max_llm_tools_per_task / mcp_max_check_tools_per_task，
  后端 src/gateway/tool_ledger.py 权威判定，后端不可达 fail-open）；
- LLM 成本按实际 usage（failover 返回的 cost_cny）经 report_usage 回调后端。
"""
from src.gateway.failover import (call_with_failover, DEEPSEEK_MODEL,
                                  KIMI_MODEL)
from src.gateway.prompt_versions import (system_prompt, default_prompt,
                                         _DRAFT_SHARED)
from src.pipeline.nodes import (run_draft_gen, run_page_split_llm,
                                page_balance_issue, _split_pages)
from src.pipeline.text_check import (_TEXT_CHECK_PROMPT, _MODE_DESC,
                                     _STRICT_JSON_SUFFIX, _parse_json,
                                     body_rule_issues)
from src.quality.rules import check_rules
from src.quality.cross_check import extract_key_fields, compare_field
from src.risk.classifier import classify
from src.services.combo import analyze_prompt, parse_analyzed_questions
from src.services.page_subject import extract_page_subjects
from src.services.regen import rewrite_page_copy
from src.services.visual_check import check_subject_match
from src.services.visual_writer import write_page_visuals

from .cost_report import report_usage
from .quotas import check_and_consume, QuotaExceededError
from .server import mcp


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data, "error": None}


def _fail(msg: str) -> dict:
    return {"ok": False, "data": None, "error": msg}


async def _acquire(task_id: str, kind: str):
    """配额申请：拒绝时返回 _fail 响应（调用方直接 return 它），通过返回 None。"""
    try:
        r = await check_and_consume(task_id, kind)
    except QuotaExceededError as e:
        return _fail(str(e))
    # 兼容配额服务以字典返回判定结果的情形（allowed=False 即拒绝）
    if isinstance(r, dict) and not r.get("allowed", True):
        return _fail(f"任务 {task_id} 的 {kind} 配额已用尽"
                     f"（上限 {r.get('limit')}，已用 {r.get('used')}）。"
                     "请停止继续调用该工具，用现有结果继续完成任务。")
    return None


async def _resolve_prompt(stage: str, mode: str | None = None) -> tuple:
    """系统级生效提示词（admin 覆盖优先）；DB 不可达回退代码内置默认。

    独立调用没有任务创建者上下文，不解析用户自定义模板（与流水线
    get_effective_prompt 的区别仅在这一级）。
    返回 (template, customized)。
    """
    try:
        return await system_prompt(stage, mode)
    except Exception:
        return default_prompt(stage, mode), False


# ──────────────────────────── 创作类（LLM 驱动） ────────────────────────────


@mcp.tool
async def draft_write(query: str, task_id: str, mode: str = "general",
                      feedback: list[str] | None = None) -> dict:
    """独立写正文（不跑流水线、不写 drafts 表）：给一个选题产出中文正文草稿。

    何时用：只要一段正文（调试创作提示词、人工辅助创作、单独重写），不需要
    完整流水线时。要分页用 page_split；要重写某页图上文案用 page_regen。
    与流水线 draft_gen 节点共用同一实现（nodes.run_draft_gen）：系统默认模板
    自动追加人设化共享段，含校稿润色二段式（润色稿不足原稿 60% 自动沿用原稿）。

    参数：
    - query: 选题/创作要求全文
    - task_id: 配额与成本记账键（必填）
    - mode: general / single / compare，默认 general
    - feedback: 此前的驳回/修改意见列表（可选，逐条注入提示词要求修正，
      对应流水线的驳回重生成路径）

    配额：LLM 类，每任务默认 5 次。
    返回 {"ok", "data": {"text", "model_version", "prompt_version",
    "cost_cny", "degraded"}, "error"}。
    """
    deny = await _acquire(task_id, "draft_write")
    if deny:
        return deny
    try:
        template, customized = await _resolve_prompt("draft_gen", mode)
        if not customized:
            template = template + "\n" + _DRAFT_SHARED
        r = await run_draft_gen(query, mode, template, feedbacks=feedback or [])
        await report_usage(task_id, "draft_write", r["cost_cny"],
                           {"mode": mode, "model": r["model_version"],
                            "degraded": r["degraded"]})
        return _ok(r)
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def page_split(draft: str, task_id: str) -> dict:
    """把整篇正文独立分成 6 页图上文案（不写 page_copies 表）。

    何时用：已有正文、只需要分页结果（调试分页提示词、人工辅助排版）。
    与流水线 page_split 节点共用同一实现（nodes.run_page_split_llm）：
    LLM 按页写文案 → 字数/均衡校验（每页 80-130 字、任意两页相差≤40 字，
    口径见 skills/page-split/contract.txt），不合格带意见重试一次；
    LLM 解析/调用失败自动退回机械切割（仍返回 pages，source=mechanical）。

    参数：draft 整篇正文；task_id 配额/记账键。
    配额：LLM 类，每任务默认 5 次。
    返回 data: {"pages": [6 条], "model_version", "source": "llm"|"mechanical",
    "balance_issue": 字数均衡校验结论（空串=合格）, "cost_cny"}。
    """
    deny = await _acquire(task_id, "page_split")
    if deny:
        return deny
    try:
        template, _ = await _resolve_prompt("page_split")
        r = await run_page_split_llm(draft, template)
        pages, source = r["pages"], "llm"
        if pages is None:
            pages, source = _split_pages(draft, 6), "mechanical"
        await report_usage(task_id, "page_split", r["cost_cny"],
                           {"source": source, "model": r["model_version"]})
        return _ok({"pages": pages, "model_version": r["model_version"],
                    "source": source, "balance_issue": page_balance_issue(pages),
                    "cost_cny": r["cost_cny"]})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def page_regen(task_id: str, page_index: int, old_copy: str,
                     draft_body: str, feedback: list[str] | None = None,
                     sibling_pages: list[dict] | None = None) -> dict:
    """单页图上文案独立重写（驳回定点重生成的独立版，不写 page_copies 表）。

    何时用：某一页文案被驳回/不满意，只重写这一页。整篇重写用 draft_write。
    与 services/regen.py 定点重生成共用同一实现（rewrite_page_copy）：
    page_regen 提示词模板填入正文/原文案/审核意见，给足各页字数均衡参照。

    参数：
    - page_index: 要重写的页码（1-6）
    - old_copy: 该页现有文案
    - draft_body: 整篇正文（供模型对齐上下文与事实）
    - feedback: 审核/修改意见列表（可选，逐条注入）
    - sibling_pages: 其余各页现状 [{"page_index": 2, "body": "..."}]（可选；
      提供后模型按每页 80-130 字且与各页相差≤40 字对齐）

    配额：LLM 类，每任务默认 5 次。
    返回 data: {"page_index", "body": 新文案, "model_version", "cost_cny"}。
    """
    deny = await _acquire(task_id, "page_regen")
    if deny:
        return deny
    try:
        template, _ = await _resolve_prompt("page_regen")
        lens = {int(d["page_index"]): len(str(d.get("body") or "").strip())
                for d in (sibling_pages or []) if d.get("page_index")}
        r = await rewrite_page_copy(template, int(page_index), draft_body,
                                    old_copy, list(feedback or []),
                                    sibling_lens=lens)
        await report_usage(task_id, "page_regen", r["cost_cny"],
                           {"page_index": page_index, "model": r["model_version"]})
        return _ok({"page_index": int(page_index), **r})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def visual_write(pages: list[str], task_id: str, style_name: str = "",
                       style_desc: str = "",
                       notes: list[str] | None = None) -> dict:
    """6 页中文页文案 → 英文视觉描述扩写（gpt-image 生图前的 prompt 增强层）。

    何时用：拿到分页文案后想独立查看/调试英文视觉扩写结果，或单独复用该能力。
    与 services/visual_writer 同一实现：dsh 固定记忆会话优先（跨任务沉淀
    风格取向与审图反馈笔记），90s 超时/失败回退 DeepSeek/Kimi failover。

    参数：
    - pages: 6 页中文文案（顺序即页序，不足 6 条自动补空）
    - style_name / style_desc: 本篇视觉风格名与描述（可选，来自风格库）
    - notes: 审图反馈笔记（可选，注入记忆会话上下文）

    配额：LLM 类，每任务默认 5 次。
    返回 data: {"visuals": {"style_en", "pages": [6 条英文描述]} 或 None,
    "fallback_to_cn_skeleton": bool}——visuals=None 表示两级扩写均未产出
    可解析结果（流水线语义=回退中文骨架生图，不算调用失败）。
    成本：内部经记忆会话/回退调用，usage 不上抛，本工具记账 0。
    """
    deny = await _acquire(task_id, "visual_write")
    if deny:
        return deny
    try:
        visuals = await write_page_visuals(style_name, style_desc, pages, notes)
        await report_usage(task_id, "visual_write", 0,
                           {"pages": len(pages or []),
                            "success": visuals is not None})
        return _ok({"visuals": visuals,
                    "fallback_to_cn_skeleton": visuals is None})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def text_draft(query: str, task_id: str, mode: str = "general") -> dict:
    """核查关卡起草三式（独立版，不写库）：query 中文自查 + 正文草稿 +
    6 页分页草稿 + 6 条生图描述草稿，一次 LLM 调用产出。

    何时用：选题进入人工文字核查前先独立起草/自查，或调试 text-draft 提示词。
    与 pipeline/text_check.py「全新起草」分支同一提示词与解析逻辑（含非法
    JSON 加强约束重试一次、正文禁词/字数机检 body_rule_issues）。
    注意：独立调用不写 tasks.text_review、不改任务状态——草稿只在返回值里，
    要落库走流水线。

    参数：query 选题全文；mode general/single/compare；task_id 配额/记账键。
    配额：LLM 类，每任务默认 5 次。
    返回 data: {"query_clean": {"issues", "suggested"}, "body_draft",
    "body_issues", "pages_draft": [≤6], "image_prompt_draft": [≤6],
    "model", "auto_ok", "cost_cny"}；模型输出无法解析时 ok=False。
    """
    deny = await _acquire(task_id, "text_draft")
    if deny:
        return deny
    try:
        prompt = (_TEXT_CHECK_PROMPT.format(
            query=query, mode_desc=_MODE_DESC.get(mode, mode))
            + "\n" + _DRAFT_SHARED)
        result = await call_with_failover(prompt)
        total_cost = result["cost_cny"]
        data = _parse_json(result["text"])
        if data is None:
            # 模型偶发输出非法 JSON：带强约束重试一次（同 run_text_check 口径）
            result = await call_with_failover(prompt + _STRICT_JSON_SUFFIX)
            total_cost += result["cost_cny"]
            data = _parse_json(result["text"])
        if data is None:
            await report_usage(task_id, "text_draft", total_cost,
                               {"mode": mode, "parse_failed": True})
            return _fail("模型输出无法解析（疑似截断/格式异常），请重试")
        qc = data.get("query_clean") or {"issues": [], "suggested": ""}
        body = str(data.get("body_draft") or "")[:3000]
        pages = [str(p)[:200] for p in (data.get("pages_draft") or [])][:6]
        imgs = [str(p)[:300] for p in (data.get("image_prompt_draft") or [])][:6]
        body_issues = body_rule_issues(body)
        out = {
            "query_clean": {"issues": [str(i)[:120] for i in qc.get("issues", [])],
                            "suggested": str(qc.get("suggested", ""))[:300]},
            "body_draft": body,
            "body_issues": body_issues,
            "pages_draft": pages,
            "image_prompt_draft": imgs,
            "model": result.get("model_version"),
            "auto_ok": not qc.get("issues") and not body_issues,
            "cost_cny": total_cost,
        }
        await report_usage(task_id, "text_draft", total_cost,
                           {"mode": mode, "model": out["model"],
                            "auto_ok": out["auto_ok"]})
        return _ok(out)
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def prompt_analyze(query: str, task_id: str, count: int = 20) -> dict:
    """泛化问题池智能分析：从一个原始 query（长情境）生成一批泛化补充问题。

    何时用：组合生成导入前扩充问题池。与 /api/tasks/analyze_query 同一实现
    （skills/prompt-analyze 提示词 → failover → 编号列表容错解析）。

    参数：query 原始提问（≥4 字）；count 生成数（5-30，默认 20）；
    task_id 配额/记账键。
    配额：LLM 类，每任务默认 5 次。
    返回 data: {"query", "questions": [...], "model", "cost_cny"}；
    模型未返回有效问题列表时 ok=False。
    """
    deny = await _acquire(task_id, "prompt_analyze")
    if deny:
        return deny
    query = (query or "").strip()
    if len(query) < 4:
        return _fail("query 太短，无法分析")
    count = max(5, min(int(count or 20), 30))
    try:
        result = await call_with_failover(analyze_prompt(query, count))
        questions = parse_analyzed_questions(result["text"])
        await report_usage(task_id, "prompt_analyze", result["cost_cny"],
                           {"count": count, "questions": len(questions)})
        if not questions:
            return _fail("模型未返回有效问题列表，请重试或手工填写")
        return _ok({"query": query, "questions": questions,
                    "model": result.get("model_version"),
                    "cost_cny": result["cost_cny"]})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def page_subject(pages: list[str], task_id: str) -> dict:
    """从 6 页分页文案提取每页画面主体（生图主体锚定用，不写库）。

    何时用：独立调试/复用「分页文案 → 画面主体」提取（无参考图模式下防止
    生图主体漂移）。与 services/page_subject 同一实现：一次 LLM 调用产出
    6 个主体短语，解析失败/数量不符返回 subjects=None（流水线语义=沿用通用
    锚定条款，不算调用失败）。

    参数：pages 6 页分页文案（顺序即页序）；task_id 配额/记账键。
    配额：LLM 类，每任务默认 5 次。
    返回 data: {"subjects": [6 条] 或 None, "model", "cost_cny"}。
    """
    deny = await _acquire(task_id, "page_subject")
    if deny:
        return deny
    try:
        captured: dict = {}

        async def _llm(prompt):
            r = await call_with_failover(prompt, DEEPSEEK_MODEL, KIMI_MODEL,
                                         max_retries=1)
            captured.update(r)
            return r

        subjects = await extract_page_subjects(pages, llm_call=_llm)
        cost = captured.get("cost_cny", 0.0)
        await report_usage(task_id, "page_subject", cost,
                           {"pages": len(pages or []),
                            "success": subjects is not None})
        return _ok({"subjects": subjects,
                    "model": captured.get("model_version"),
                    "cost_cny": cost})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


# ──────────────────────── 校验类（确定性代码 / VL） ────────────────────────


@mcp.tool
async def rule_check(draft: str, task_id: str, title: str = "") -> dict:
    """规则机校验（确定性代码，不调 LLM）：字数 400-700 / 标题≤25 字 /
    绝对化用语 / 含安全承诺词时必须有免责声明。

    何时用：任何正文草稿的快速合规自检。与流水线 rule_check 节点同一实现
    （quality/rules.check_rules）；独立调用不写 rule_results 表。
    参数：draft 正文全文；title 标题（留空=取正文首行前 25 字，同节点口径）；
    task_id 配额/记账键。
    配额：校验类，每任务默认 10 次。
    返回 data: {"rule_results": [{"rule_name", "passed", "details"}],
    "all_passed": bool}。
    """
    deny = await _acquire(task_id, "rule_check")
    if deny:
        return deny
    try:
        if not title:
            title = draft.split("\n")[0][:25] if draft else ""
        results = check_rules(draft, title)
        await report_usage(task_id, "rule_check", 0,
                           {"all_passed": all(r["passed"] for r in results)})
        return _ok({"rule_results": results,
                    "all_passed": all(r["passed"] for r in results)})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def cross_check(pages: list[str], ocr_texts: list[str],
                      task_id: str) -> dict:
    """OCR 关键字段对撞（确定性代码）：分页文案里的关键数字/年份/型号 vs
    对应页配图 OCR 文字，逐页比对。

    何时用：独立复核「图上文字与分页文案一致性」。与流水线 cross_check 节点
    同一实现（quality/cross_check.extract_key_fields + compare_field）；
    独立调用不写 cross_checks 表。
    参数：
    - pages: 各页文案（顺序即页序 1..n）
    - ocr_texts: 与 pages 等长的各页 OCR 原始文字（空串/缺失=该页识别失败，
      记一条 ocr mismatch，同节点 confidence=0 口径）
    - task_id: 配额/记账键
    配额：校验类，每任务默认 10 次。
    返回 data: {"mismatches": [{"field_name", "expected", "actual",
    "matched"}], "mismatch_count": int}。
    """
    deny = await _acquire(task_id, "cross_check")
    if deny:
        return deny
    try:
        mismatches = []
        for i, body in enumerate(pages or []):
            ocr_text = ocr_texts[i] if i < len(ocr_texts or []) else ""
            if not ocr_text:
                mismatches.append({"field_name": "ocr", "expected": "可识别",
                                   "actual": "识别失败", "matched": False})
                continue
            expected = extract_key_fields(body or "")
            actual = extract_key_fields(ocr_text)
            mismatches.extend(compare_field(expected, actual))
        await report_usage(task_id, "cross_check", 0,
                           {"pages": len(pages or []),
                            "mismatch_count": len(mismatches)})
        return _ok({"mismatches": mismatches,
                    "mismatch_count": len(mismatches)})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def risk_classify(task_id: str, rule_results: list[dict] | None = None,
                        cross_results: list[dict] | None = None,
                        evidence_complete: bool = True,
                        has_p0_issue: bool = False) -> dict:
    """红/黄/绿风险分级（纯函数，不调 LLM）。

    何时用：已有规则校验/交叉校验结果，想独立试算风险等级（调试分级口径、
    人工预判交付风险）。与 risk/classifier.classify 同一实现。
    注意：独立调用接受显式输入、不读 DB 任务上下文（流水线节点是从库组装
    同结构输入后调同一函数）；独立调用不写 risk_classifications 表。

    参数：
    - rule_results: [{"passed": bool, "rule_name": str}]（rule_check 的输出）
    - cross_results: [{"matched": bool}]（cross_check 的 mismatches 即可）
    - evidence_complete: P0/P1 关键事实点是否都有支撑证据（默认 True）
    - has_p0_issue: 是否存在未关闭的 P0 问题单（默认 False，直接判红）
    配额：校验类，每任务默认 10 次。
    返回 data: {"level": "red"|"yellow"|"green", "reasons": [...]}。
    """
    deny = await _acquire(task_id, "risk_classify")
    if deny:
        return deny
    try:
        level, reasons = classify(rule_results or [], cross_results or [],
                                  bool(evidence_complete), bool(has_p0_issue))
        await report_usage(task_id, "risk_classify", 0, {"level": level})
        return _ok({"level": level, "reasons": reasons})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")


@mcp.tool
async def visual_check(image_url: str, page_copy: str, task_id: str) -> dict:
    """VL 图文一致性/主体匹配审核：看图回答图中主体是什么、是否与该页
    文案主题一致（services/visual_check.check_subject_match 同一实现，
    dashscope qwen-vl，复用 OCR 网关）。

    何时用：独立复核某张配图与某页文案的主体一致性。image_url 可传
    generate_images 返回的本地路径（/static/generated/...）或 http(s) URL。
    参数：image_url 图片地址；page_copy 该页文案；task_id 配额/记账键。
    配额：校验类，每任务默认 10 次。
    返回 data: {"verdict": {"ok": bool, "actual": 图中主体} 或 None,
    "skipped": bool}——verdict=None 表示 VL 审核未启用或调用/解析失败
    （流水线语义=跳过不阻塞，人工关卡兜底）。
    """
    deny = await _acquire(task_id, "visual_check")
    if deny:
        return deny
    try:
        verdict = await check_subject_match(image_url, page_copy)
        await report_usage(task_id, "visual_check", 0,
                           {"image_url": image_url[:200],
                            "verdict": verdict})
        return _ok({"verdict": verdict, "skipped": verdict is None})
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}")
