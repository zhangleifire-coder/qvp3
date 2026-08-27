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
2. pages_draft：把 Query 主题起草成 6 页图上文案（第1页封面主标题12-20字+钩子；第2-5页每页一个核心信息点25-50字；第6页总结20-40字；纯文本无 markdown）。
3. image_prompt_draft：6 页配图的生图描述草稿（每页一句，竖版3:4图文卡片，与对应页文案呼应；不要出现具体品牌 logo/人脸）。

【Query】
{query}

【生产模式】{mode_desc}

输出 JSON 结构：
{{"query_clean": {{"issues": [], "suggested": ""}},
  "pages_draft": ["P1", "P2", "P3", "P4", "P5", "P6"],
  "image_prompt_draft": ["P1描述", "P2描述", "P3描述", "P4描述", "P5描述", "P6描述"]}}"""

_MODE_DESC = {
    "general": "通用科普/教程，纯文生图",
    "single": "单一产品深度实测，图生图保持外观一致",
    "compare": "两个主体对比评测，图生图保持外观一致",
}


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
    """draft → text_check → 存 text_review → awaiting_text。返回产出摘要。"""
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id))).scalar_one()
        query, mode = task.query, (task.mode or "general")

    prompt = _TEXT_CHECK_PROMPT.format(query=query,
                                       mode_desc=_MODE_DESC.get(mode, mode))
    result = await call_with_failover(prompt)
    data = _parse_json(result["text"]) or {}
    qc = data.get("query_clean") or {"issues": [], "suggested": ""}
    pages = [str(p)[:200] for p in (data.get("pages_draft") or [])][:6]
    imgs = [str(p)[:300] for p in (data.get("image_prompt_draft") or [])][:6]
    review = {
        "query": query,
        "query_clean": {"issues": [str(i)[:120] for i in qc.get("issues", [])],
                        "suggested": str(qc.get("suggested", ""))[:300]},
        "pages_draft": pages,
        "image_prompt_draft": imgs,
        "model": result.get("model_version"),
        "auto_ok": not qc.get("issues"),
    }
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
        "pages": ov.get("pages") or rv.get("pages_draft") or [],
        "image_prompts": ov.get("image_prompts") or rv.get("image_prompt_draft") or [],
    }
