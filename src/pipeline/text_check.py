"""text_check：query 中文自查 + 文案/生图描述起草（人工核查前置，2026-08-27）。

状态链：draft → text_check（本模块，自动）→ awaiting_text（人工最终核查）
→ 放行进入生产（审图 → agent 生图）。

LLM 一次调用产出 JSON：
- query_clean：query 中文自查结果（错别字/语句/敏感/绝对化违规 + 修正建议）
- pages_draft：6 页图上文案草稿（人工核查基准，贴合 PAGES_PROMPT 规范）
- image_prompt_draft：生图描述草稿（人工核查基准）
- issues：发现的问题列表（空=通过，可直接放行）
"""
import json

from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.gateway.failover import call_with_failover
from src.models.tasks import Task

_TEXT_CHECK_PROMPT = """你是图文生产平台的内容核查编辑。针对下面的 Query，产出三样东西（只输出 JSON，不要其它文字）：

1. query_clean：Query 的中文自查——错别字、语句通顺、敏感词、绝对化违规表述（最/第一/唯一/100%/保证…）。
   若有问题：query_clean = {{"issues": ["问题1", "问题2"], "suggested": "修正后的 query"}}
   若无问题：query_clean = {{"issues": [], "suggested": "（原样，无需修改）"}}
2. body_draft：围绕 Query 起草一篇图文正文（400-700字，不计空白；无绝对化表述（禁：最/第一/唯一/100%/保证）、无 emoji、中文标点、不用 markdown 符号）。结构不拘，写作风格严格按文末【人设与真人感】【信息密度】要求执行。
3. pages_draft：把正文精炼成 6 页图上文案（第1页封面主标题12-20字+钩子；第2-5页每页一个核心信息点25-50字；第6页总结20-40字；纯文本无 markdown）。
4. image_prompt_draft：6 页配图的生图描述草稿（每页一句，竖版3:4图文卡片，与对应页文案呼应；不要出现具体品牌 logo/人脸）。

【Query】
{query}

【生产模式】{mode_desc}

输出 JSON 结构：
{{"query_clean": {{"issues": [], "suggested": ""}},
  "body_draft": "正文全文（400-700字）",
  "pages_draft": ["P1", "P2", "P3", "P4", "P5", "P6"],
  "image_prompt_draft": ["P1描述", "P2描述", "P3描述", "P4描述", "P5描述", "P6描述"]}}"""

_MODE_DESC = {
    "general": "通用科普/教程，纯文生图",
    "single": "单一产品深度实测，图生图保持外观一致",
    "compare": "两个主体对比评测，图生图保持外观一致",
}

# 手工内容导入模式：用户自带正文，AI 只改写优化（保留事实与观点），不重写
_TEXT_REWRITE_PROMPT = """你是图文生产平台的内容编辑。用户已根据 Query 手写了一篇正文。
你的任务是在【完整保留用户全部事实、观点与信息】的前提下改写优化这篇正文，并产出配套核查内容（只输出 JSON，不要其它文字）：

1. query_clean：Query 的中文自查——错别字、语句通顺、敏感词、绝对化违规表述（最/第一/唯一/100%/保证…）。
   若有问题：query_clean = {{"issues": ["问题1", "问题2"], "suggested": "修正后的 query"}}
   若无问题：query_clean = {{"issues": [], "suggested": "（原样，无需修改）"}}
2. body_draft：改写优化后的正文（400-700字，不计空白）。改写原则：保留用户的全部事实与观点，不新增未经用户提及的事实；
   优化结构为总分总、每段加小标题；表达流畅客观；无绝对化表述（禁：最/第一/唯一/100%/保证）、无 emoji、中文标点、不用 markdown 符号。
3. pages_draft：把改写后正文精炼成 6 页图上文案（第1页封面主标题12-20字+钩子；第2-5页每页一个核心信息点25-50字；第6页总结20-40字；纯文本无 markdown）。
4. image_prompt_draft：6 页配图的生图描述草稿（每页一句，竖版3:4图文卡片，与对应页文案呼应；不要出现具体品牌 logo/人脸）。

【Query】
{query}

【生产模式】{mode_desc}

【用户手写正文（改写底稿，事实以此为准）】
{user_body}

输出 JSON 结构：
{{"query_clean": {{"issues": [], "suggested": ""}},
  "body_draft": "改写优化后的正文全文（400-700字）",
  "pages_draft": ["P1", "P2", "P3", "P4", "P5", "P6"],
  "image_prompt_draft": ["P1描述", "P2描述", "P3描述", "P4描述", "P5描述", "P6描述"]}}"""

# 驳回重写模式：人工核查员对具体条目标记修改意见 → 只改标记处，其余原样保留
_TEXT_FEEDBACK_PROMPT = """你是图文生产平台的内容编辑。人工核查员对当前草稿的【以下条目】提出了驳回标记与修改意见。
请只针对这些被标记的条目进行修改（严格按意见执行）；未标记的条目必须原样保留、一字不改。其余仍需满足通用规范：无绝对化表述（禁：最/第一/唯一/100%/保证）、无 emoji、中文标点、不用 markdown 符号。

【驳回标记与修改意见】
{marks_block}

【当前草稿】
Query：{query}
正文：
{body}

6 页图上文案：
{pages}

6 条生图描述：
{image_prompts}
{manual_section}
输出 JSON 结构（完整输出修改后的全部内容，未修改条目原样包含）：
{{"query_clean": {{"issues": [], "suggested": ""}},
  "body_draft": "修改后的正文全文（400-700字）",
  "pages_draft": ["P1", "P2", "P3", "P4", "P5", "P6"],
  "image_prompt_draft": ["P1描述", "P2描述", "P3描述", "P4描述", "P5描述", "P6描述"]}}"""

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
_STRICT_JSON_SUFFIX = ("\n\n【重要】上一次输出无法通过 JSON 解析。请确保：只输出一个合法 JSON 对象；"
                       "字符串内部如需引用请使用中文引号“”，严禁未转义的英文双引号；"
                       "全部输出必须在 JSON 的最后一个 } 处结束。")


def _parse_json(text: str) -> dict | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return None


async def run_text_check(task_id) -> dict:
    """draft → text_check → 存 text_review → awaiting_text。返回产出摘要。

    手工内容导入的任务（text_review.source == "manual"）：走改写模式——
    以用户手写正文为底稿优化，保留事实，并保留 user_body 供核查对照。
    """
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, mode = task.query, (task.mode or "general")
        prev = task.text_review or {}
        user_body = str(prev.get("user_body") or "") if prev.get("source") == "manual" else ""
        feedback = [f for f in (prev.get("feedback") or [])
                    if isinstance(f, dict) and f.get("target") and f.get("note")]

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
            manual_section=manual_section)
    elif user_body:
        prompt = _TEXT_REWRITE_PROMPT.format(query=query,
                                             mode_desc=_MODE_DESC.get(mode, mode),
                                             user_body=user_body)
    else:
        # 全新起草：追人设化共享段（2026-08-24 补齐第三条路径——用户在
        # 文字核查关卡看到、可编辑的草稿正是本提示词产出的，此前真人感
        # 不足的根因之一就是这里没吃 _DRAFT_SHARED）
        from src.gateway.prompt_versions import _DRAFT_SHARED
        prompt = (_TEXT_CHECK_PROMPT.format(query=query,
                                            mode_desc=_MODE_DESC.get(mode, mode))
                  + "\n" + _DRAFT_SHARED)
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
    # 正文自动规则检查：禁词 + 字数（人工核查提示，不阻断）
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
    review = {
        "query": query,
        "query_clean": {"issues": [str(i)[:120] for i in qc.get("issues", [])],
                        "suggested": str(qc.get("suggested", ""))[:300]},
        "body_draft": body,
        "body_issues": body_issues,
        "pages_draft": pages,
        "image_prompt_draft": imgs,
        "model": result.get("model_version"),
        "auto_ok": not qc.get("issues") and not body_issues,
    }
    if user_body:   # 手工导入：标记来源并保留原稿（核查页可对照）
        review["source"] = "manual"
        review["user_body"] = user_body
    if feedback:    # 驳回重写的意见留痕（已处理，feedback 本身不再写入=自然清除）
        review["last_feedback"] = feedback
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        task.text_review = review
        task.status = "awaiting_text"
        await session.commit()
    return {"candidates_pages": len(pages), "issues": len(review["query_clean"]["issues"]),
            "auto_ok": review["auto_ok"]}


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
