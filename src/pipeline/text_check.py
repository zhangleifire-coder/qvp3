"""text_check：query 中文自查 + 文案/生图描述起草（人工核查前置，2026-08-27）。

状态链：draft → ref_seed（搜图①自动，2026-09-07 两段式实景搜图）→
text_check（本模块，自动；有 text_ref 创作参考图时其信息段反哺起草提示词）
→ awaiting_text（人工最终核查）→ ref_collect（搜图②：text_ref 叠加新搜，
人工确认关 awaiting_refs）→ 放行进入生产（审图 → agent 生图）。

LLM 一次调用产出 JSON：
- query_clean：query 中文自查结果（错别字/语句/敏感/绝对化违规 + 修正建议）
- pages_draft：6 页图上文案草稿（人工核查基准，贴合 PAGES_PROMPT 规范）
- image_prompt_draft：生图描述草稿（人工核查基准）
- issues：发现的问题列表（空=通过，可直接放行）

起草解析成功后、落库前对 body_draft 做两轮 Kimi 校稿（2026-09-07
「DeepSeek 生文，Kimi 两轮校稿修正」）：round1 事实/真人感/字数/标题，
round2 终校；仅校正文（pages/image_prompts 不动），单轮失败/返回空/长度
护栏触发均不阻断，留痕存 text_review.polish。
"""
import json

from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.gateway.failover import call_with_failover, KIMI_MODEL, deepseek_model
from src.models.tasks import Task

# 起草三式提示词已搬入 skills/text-draft/（2026-09-03 阶段2重构，原样搬运）
from src.gateway import skill_loader as _skills

_TEXT_CHECK_PROMPT = _skills.fragment("text-draft", "check")

_MODE_DESC = {
    "general": "通用科普/教程，纯文生图",
    "single": "单一产品深度实测，图生图保持外观一致",
    "compare": "两个主体对比评测，图生图保持外观一致",
}

# 手工内容导入模式：用户自带正文，AI 只改写优化（保留事实与观点），不重写
_TEXT_REWRITE_PROMPT = _skills.fragment("text-draft", "rewrite")

# 驳回重写模式：人工核查员对具体条目标记修改意见 → 只改标记处，其余原样保留
_TEXT_FEEDBACK_PROMPT = _skills.fragment("text-draft", "feedback")

# 条目标记的中文说明（驳回意见注入用）
_TARGET_LABELS = {
    "query": "Query", "body": "正文",
}


def _target_label(target: str) -> str:
    if target in _TARGET_LABELS:
        return _TARGET_LABELS[target]
    kind, _, idx = target.partition(":")
    if kind == "page":
        return f"第{idx}页图上文案"
    if kind == "ip":
        return f"第{idx}页生图描述"
    return target

# 解析失败重试时附加的强约束（Kimi 偶发未转义英文双引号破坏 JSON）
_STRICT_JSON_SUFFIX = _skills.fragment("text-draft", "strict_json_suffix")


def _refs_feedback_section(refs) -> str:
    """已确认实景参考图反哺起草（2026-09-07 参考图前置，吸收 8002）：
    ref_collect 关卡人工确认的参考图信息段，追加到三式起草提示词之后——
    正文/页文案提及的场景、细节须与参考图实际画面贴合（图文一致）。
    风格仿 agent_production refs_section；无确认图时由调用方跳过（零差异）。
    """
    lines = []
    for i, a in enumerate(refs, 1):
        meta = []
        if a.origin_url:
            meta.append(f"来源: {a.origin_url}")
        if a.ocr_hit:
            meta.append(f"OCR命中: {a.ocr_hit}")
        if a.subject:
            meta.append(f"主体: {a.subject}")
        lines.append(f"  {i}. {a.image_url}"
                     + (f"（{'，'.join(meta)}）" if meta else ""))
    return ("\n【已确认实景参考图（人工筛选后保留）】\n"
            + "\n".join(lines)
            + "\n要求：正文与各页图上文案提及的场景、物体、颜色、结构等细节"
              "需与以上参考图的实际画面贴合，不得编写参考图里没有的内容。\n")


def _parse_json(text: str) -> dict | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return None


def body_rule_issues(body: str) -> list[str]:
    """正文自动规则检查：禁词 + 字数（人工核查提示，不阻断）。
    run_text_check 与 qvp_mcp text_draft 工具共用同一实现。"""
    body_issues = []
    for w in ("绝对", "100%", "第一", "唯一", "永久", "终身", "保证", "疗效"):
        if w in body:
            body_issues.append(f"正文含禁用词「{w}」")
    if "最" in body:
        body_issues.append("正文含「最」（含一切搭配，请改「很/十分/更/相对」）")
    import re as _re
    n_chars = len(_re.sub(r"\s", "", body))
    if body and not (400 <= n_chars <= 700):
        body_issues.append(f"正文字数 {n_chars}（要求 400-700 字）")
    return body_issues


# 校稿长度护栏：校后正文不足校前 60% 视为截断/跑偏，弃用该轮结果保留前文
# （与 nodes.run_draft_gen 的 DRAFT_POLISH 护栏同语义）；两轮各自独立判定
_POLISH_MIN_RATIO = 0.6


async def _polish_body(task_id, body: str, owner_id, on_delta=None) -> tuple:
    """Kimi 两轮校稿（DeepSeek 生文 → Kimi 主校两轮修正，2026-09-07）。

    - 模型：Kimi 主、DeepSeek 备（call_with_failover(KIMI, DEEPSEEK)）。
    - 提示词：get_effective_prompt("polish_round1/2") 三级覆盖（用户自定义
      → admin 库覆盖 → skills/polish 代码默认），{body} 引用待校正文。
      校稿可能带出 Markdown 标记（#/## 小标题）——属预期，前端按文档格式
      渲染（static/md.js），后端不剥离（2026-09-08 用户决策）。
    - 容错：单轮调用异常/返回空 → failed，跳过该轮不阻断（保留校前文本）；
      长度护栏（校后 < 校前 60%）→ skipped，弃用该轮结果。
    返回 (校后正文, 留痕 dict)。
    """
    from src.gateway.prompt_versions import get_effective_prompt
    from src.pipeline.nodes import _emit_progress
    trace = {"round1": "skipped", "round2": "skipped", "model": {},
             "cost_cny": 0.0}
    text = body
    for n in (1, 2):
        _emit_progress(task_id, "text_check",
                       msg=f"Kimi 校稿·第{n}轮" + ("（终校）" if n == 2 else ""))
        try:
            tpl = await get_effective_prompt(f"polish_round{n}", None, owner_id)
            r = await call_with_failover(tpl.replace("{body}", text),
                                         KIMI_MODEL, deepseek_model(),
                                         max_retries=1, on_delta=on_delta)
        except Exception as e:  # 校稿失败不阻断起草落库
            trace[f"round{n}"] = "failed"
            trace.setdefault("errors", []).append(
                f"round{n}: {type(e).__name__}: {e}"[:200])
            continue
        trace["cost_cny"] += r.get("cost_cny") or 0
        trace["model"][f"round{n}"] = r.get("model_version")
        polished = (r.get("text") or "").strip()
        if not polished:
            trace[f"round{n}"] = "failed"
            continue
        if len(polished) < len(text) * _POLISH_MIN_RATIO:
            trace[f"round{n}"] = "skipped"   # 护栏触发：弃用该轮，保留前文
            continue
        text = polished
        trace[f"round{n}"] = "ok"
    return text, trace


async def run_text_check(task_id) -> dict:
    """draft → text_check → 存 text_review → awaiting_text。返回产出摘要。

    手工内容导入的任务（text_review.source == "manual"）：走改写模式——
    以用户手写正文为底稿优化，保留事实，并保留 user_body 供核查对照。
    """
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, mode = task.query, (task.mode or "general")
        owner_id = task.created_by
        prev = task.text_review or {}
        user_body = str(prev.get("user_body") or "") if prev.get("source") == "manual" else ""
        feedback = [f for f in (prev.get("feedback") or [])
                    if isinstance(f, dict) and f.get("target") and f.get("note")]

    # 搜图①反哺（2026-09-07 两段式实景搜图）：ref_seed 自动搜集的创作参考图
    # （selection_status='text_ref'）在起草时注入——有图时三分支提示词末尾统一
    # 追加参考图信息段；无图零差异。
    # 2026-09-18 用户决策：生成文案不依赖实景图（避免"不得编写参考图里没有
    # 的内容"限制文案发挥）——默认关闭注入，开关可回滚。ref_seed 节点保留，
    # 其图仍作为 ref_collect 候选池供生图环节使用。
    refs_block = ""
    if settings.text_check_use_refs:
        from src.pipeline.ref_collect import _text_refs
        confirmed = await _text_refs(task_id)
        refs_block = _refs_feedback_section(confirmed) if confirmed else ""

    if feedback:
        # 驳回重写：只改人工标记的条目（意见注入），其余原样保留
        marks_block = "\n".join(
            f"- {_target_label(str(m['target']))}：（意见：{str(m['note'])[:300]}）"
            for m in feedback)
        manual_section = ("\n【事实底线】用户手写正文（被标记条目的事实也以此为准）：\n"
                          + user_body) if user_body else ""
        prompt = _TEXT_FEEDBACK_PROMPT.format(
            marks_block=marks_block, query=query,
            body=str(prev.get("body_draft") or ""),
            pages="\n".join(f"P{i+1}：{p}" for i, p in
                            enumerate(prev.get("pages_draft") or [])),
            image_prompts="\n".join(f"P{i+1}：{p}" for i, p in
                                    enumerate(prev.get("image_prompt_draft") or [])),
            manual_section=manual_section) + refs_block
    elif user_body:
        prompt = _TEXT_REWRITE_PROMPT.format(query=query,
                                             mode_desc=_MODE_DESC.get(mode, mode),
                                             user_body=user_body) + refs_block
    else:
        # 全新起草：追人设化共享段（2026-08-24 补齐第三条路径——用户在
        # 文字核查关卡看到、可编辑的草稿正是本提示词产出的，此前真人感
        # 不足的根因之一就是这里没吃 _DRAFT_SHARED）；
        # 参考图信息段追加在人设共享段之后
        from src.gateway.prompt_versions import _DRAFT_SHARED
        prompt = (_TEXT_CHECK_PROMPT.format(query=query,
                                            mode_desc=_MODE_DESC.get(mode, mode))
                  + "\n" + _DRAFT_SHARED + refs_block)
    from src.pipeline.nodes import _stream_reporter, _emit_progress
    branch = ("驳回定向修改" if feedback else
              "手工底稿改写" if user_body else "全新起草")
    _emit_progress(task_id, "text_check", msg=f"文字自查·{branch}（LLM 流式生成）")
    _report = _stream_reporter(task_id, "text_check")
    result = await call_with_failover(prompt, on_delta=_report)
    data = _parse_json(result["text"])
    if data is None:
        # 模型偶发输出非法 JSON（未转义引号等，间歇性）：带强约束重试一次
        _emit_progress(task_id, "text_check", msg="输出格式异常，带强约束重试")
        result = await call_with_failover(prompt + _STRICT_JSON_SUFFIX,
                                          on_delta=_report)
        data = _parse_json(result["text"])
    if data is None:
        # 输出无法解析（截断/格式异常）：显式标记为失败，禁止静默空草稿放行——
        # 人工核查页会提示"AI 起草失败"，可点「重新起草」重试（2026-08-29 事故修复）
        raw_len = len(result.get("text") or "")
        async with SessionLocal() as session:
            task = (await session.execute(
                select(Task).where(Task.id == task_id))).scalar_one()
            task.text_review = {
                "query": query,
                "query_clean": {"issues": ["AI 起草失败：模型输出无法解析"
                                           f"（{result.get('model_version')}，{raw_len} 字符，疑似截断），"
                                           "请点「重新起草」重试"], "suggested": ""},
                "body_draft": "", "body_issues": [],
                "pages_draft": [], "image_prompt_draft": [],
                "model": result.get("model_version"),
                "auto_ok": False, "draft_error": True,
                "raw_head": (result.get("text") or "")[:400],  # 失败原文头部（诊断用）
                # 驳回重写模式失败：意见留痕并不残留 feedback（否则前端完成检测卡住）
                **({"last_feedback": feedback} if feedback else {}),
            }
            task.status = "awaiting_text"
            await session.commit()
        return {"candidates_pages": 0, "issues": 1, "auto_ok": False}
    qc = data.get("query_clean") or {"issues": [], "suggested": ""}
    body = str(data.get("body_draft") or "")[:3000]
    pages = [str(p)[:200] for p in (data.get("pages_draft") or [])][:6]
    imgs = [str(p)[:300] for p in (data.get("image_prompt_draft") or [])][:6]
    # Kimi 两轮校稿（仅校正文，pages/image_prompts 不动；失败/护栏不阻断）
    polish_trace = {"round1": "skipped", "round2": "skipped", "model": {},
                    "cost_cny": 0.0}
    if body.strip():
        body, polish_trace = await _polish_body(task_id, body, owner_id, _report)
    # 正文自动规则检查：禁词 + 字数（人工核查提示，不阻断）。
    # 校稿后再跑——校后文本才是人工看到的终稿，规则提示以终稿为准
    body_issues = body_rule_issues(body)
    review = {
        "query": query,
        "query_clean": {"issues": [str(i)[:120] for i in qc.get("issues", [])],
                        "suggested": str(qc.get("suggested", ""))[:300]},
        "body_draft": body,
        "body_issues": body_issues,
        "pages_draft": pages,
        "image_prompt_draft": imgs,
        "model": result.get("model_version"),
        "polish": polish_trace,
        "auto_ok": not qc.get("issues") and not body_issues,
    }
    if user_body:   # 手工导入：标记来源并保留原稿（核查页可对照）
        review["source"] = "manual"
        review["user_body"] = user_body
    if feedback:    # 驳回重写的意见留痕（已处理，feedback 本身不再写入=自然清除）
        review["last_feedback"] = feedback
    issue_n = len(review["query_clean"]["issues"]) + len(body_issues)
    _emit_progress(
        task_id, "text_check",
        msg=f"起草完成：正文 {len(body)} 字 · 页文案 {len(pages)} 页 · "
            f"校稿 R1 {polish_trace['round1']}/R2 {polish_trace['round2']} · "
            + ("自动通过" if review["auto_ok"] else f"{issue_n} 项待人工核查"))
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        task.text_review = review
        task.status = "awaiting_text"
        await session.commit()
        task_query = task.query

    # 全绿（query_clean 无 issues + 正文规则无 issues）自动放行，进入生产
    if review["auto_ok"]:
        from src.stream.scheduler import scheduler
        from src.services.activity import log_action
        async with SessionLocal() as session:
            task = (await session.execute(
                select(Task).where(Task.id == task_id))).scalar_one()
            task.status = "draft"
            await session.commit()
        await scheduler.enqueue(task_id, task_query, kind="pipeline")
        await log_action("system", "text_auto_confirm",
                         "文字核查全绿，自动放行进入生产", task_id=task_id)
        return {"candidates_pages": len(pages),
                "issues": len(review["query_clean"]["issues"]),
                "auto_ok": True, "auto_confirmed": True,
                "cost_cny": (result.get("cost_cny") or 0) + polish_trace["cost_cny"],
                "model_version": result.get("model_version")}

    return {"candidates_pages": len(pages), "issues": len(review["query_clean"]["issues"]),
            "auto_ok": review["auto_ok"],
            # 节点成本（execute_node 从返回 dict 提取入 node_events）：
            # 起草 + 两轮校稿合计
            "cost_cny": (result.get("cost_cny") or 0) + polish_trace["cost_cny"],
            "model_version": result.get("model_version")}


def effective_texts(task) -> dict:
    """人工核查后的最终生效文本：人工修改版优先，否则自动自查草稿。"""
    ov = task.text_override or {}
    rv = task.text_review or {}
    return {
        "query": (ov.get("query") or rv.get("query") or task.query).strip(),
        "body": (ov.get("body") or rv.get("body_draft") or "").strip(),
        "pages": ov.get("pages") or rv.get("pages_draft") or [],
        "image_prompts": ov.get("image_prompts") or rv.get("image_prompt_draft") or [],
    }
