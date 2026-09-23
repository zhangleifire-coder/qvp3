"""agent_shared：Agent 创作路径共享段（2026-09-09 抽取，行为零变化）。

monolith 大节点（agent_production）与 staged 分阶段节点（agent_stages）
共用的单一事实来源：
1. 提示词常量与指令组装（_AGENT_INSTRUCTIONS 等，提示词内容一个字未改）；
2. 输出契约防御性解析与校验；
3. 确定性收尾：图片本地化/文字扭曲质检/视觉主体审核/AI 双重审核；
4. 细粒度落库函数（claim/evidence/draft/page_copies/assets/ocr_results）
   ——monolith 在一个节点内顺序调用全部，staged 各阶段调用各自子集。
"""
import asyncio
import hashlib
import json
import traceback

from sqlalchemy import func, select

from src.config import settings
from src.gateway.skill_loader import (PAGE_MIN_CHARS, PAGE_MAX_CHARS,
                                      PAGE_MAX_DIFF, INFO_POINTS_MIN,
                                      INFO_POINTS_MAX)
from src.gateway.tool_ledger import consume_image_budget
from src.models.assets import Asset, OcrResult
from src.models.drafts import Draft, PageCopy
from src.models.entities import Claim, Evidence

_MODE_DESC = {
    "general": "通用模式：通用科普/教程内容，配图用纯文生图（无需搜参考图）",
    "single": "单品模式：单一产品深度实测，需先搜实物参考图，图生图保持产品外观一致",
    "compare": "对比模式：两个主体对比评测，需先搜两主体实景参考图，图生图保持外观一致",
}

_OUTPUT_CONTRACT = """{
  "evidence":  [{"title": "来源标题", "url": "https://...", "summary": "关键事实摘要"}],
  "content_style": "判定的内容风格（解读·经验分享/测评实测/攻略教程/避坑指南/观点杂谈）",
  "image_style": "选定的图片整体视觉风格（图片视觉风格库中的一种）",
  "draft":     "正文全文（400-700 字）",
  "pages":     ["第1页图上文案", "第2页图上文案", "第3页图上文案", "第4页图上文案", "第5页图上文案", "第6页图上文案"],
  "references": [{"image_url": "image_search 返回的图 URL", "title": "...", "engine": "..."}],
  "images":    [{"page_index": 1, "image_url": "generate_images 返回的本地路径", "prompt_used": "该页实际生图提示词"}],
  "ocr_texts": [{"page_index": 1, "text": "该页配图 OCR 识别出的文字"}],
  "notes":     "创作过程备注（工具调用与自检情况）"
}"""

# 合规红线段：monolith 大节点与 staged 各阶段指令共用此单一事实来源
_COMPLIANCE_RED_LINES = """【合规红线（规则机逐词扫描，出现即判不通过）】
1. 禁用词：绝对、100%、最、第一、唯一、永久、终身、安全、无害、无副作用、治疗、疗效、保证。
   注意「最」含一切搭配（最重要/最关键/最好…），请改用「很/十分/更/相对」等表述。
2. 字数：正文（不计空白字符）必须落在 400-700 字之间，写完自查一遍再输出。
""" + (
    # 字数契约段：由 skills/page-split/contract.txt 单点渲染（2026-09-03 阶段2）
    f"3. 图上文字：封面主标题 12-20 字；各页整页文字（含小标题与标点）"
    f"{PAGE_MIN_CHARS}-{PAGE_MAX_CHARS} 字，\n"
    f"   六页基本均衡（任意两页相差不超过 {PAGE_MAX_DIFF} 字）。每页围绕一个核心信息点讲透，\n"
    f"   至少带 {INFO_POINTS_MIN}-{INFO_POINTS_MAX} 个具体信息点（数字/价格/步骤/参数/时限），正文里的细节优先上图，\n"
    "   图上文案不得比正文更空泛。Query 与正文的核心概念词（对比轴两侧、风格/流派名、\n"
    "   品类名）必须原词保留、不得用例子词顶替（如「法式风」不得被「珍珠耳坠」顶替；\n"
    "   正确写法是概念词点名后再用单品举例）。各页须形成故事叙事链（承上启下）：封面=情境与结论钩子，\n"
    "   中间页按时间/逻辑层层推进，每页首句承接上一页结尾、结尾为下一页留承接点，\n"
    "   全套围绕同一核心物件/主体展开（故事线索），结尾页呼应封面收束；\n"
    "   各页信息任务仍互不重复（换新角度补充新信息除外）；\n"
    "   小标题从原文摘取不改写，小标题与本页画面内容一一对应。系统会逐页校验文字是否正确，出错会自动重生成，\n"
    "   因此每个字都要写规范简体中文。\n"
)

_AGENT_INSTRUCTIONS = """你是「图文生产平台」的创作 Agent，负责为一条 Query 生产完整的图文内容（1 篇正文 + 6 页配图）。

【任务】
- Query：{query}
- 生产模式：{mode}（{mode_desc}）
- task_id：{task_id}（每次调用工具时，task_id 参数必须原样传这个值，用于配额与成本记账）

【可用工具（按需调用，均有配额）】
- web_search(query, task_id)：网页检索事实证据（配额 {quota_search} 次，检索词要精准）
- image_search(query, task_id, count)：搜实景/实物参考图（仅 single/compare 模式需要）
- generate_images(task_id, pages, mode, image_template, reference_urls)：批量生成 6 张交付配图。
  参考图按页分配（铁律）：reference_urls 不要整表传给每一页——第 i 页取列表中
  第 (i-1)%N、i%N 两张作为该页参考子集（N=参考图张数），保证相邻页参考图不同、
  单页不堆砌全部实景图。参考图仅 1-2 张时允许重复。
  必须传：pages=6 页文案原样列表、mode、image_template=下方生图模板（按第 6 步替换风格句后的版本）；
  single/compare 再传 reference_urls=image_search 结果里挑出的图片 URL。
- ocr_image(image_url, task_id)：OCR 识别配图文字。默认跳过——系统会自动做图文
  一致性校验；仅当某页文案含关键数字/型号必须重点核验时，对那一页调用

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
      多个适配时同样随机选一种。选中风格的忌讳条款必须原样保留进 image_template
      （第 6 步替换风格句时一并带上）。后续正文行文按内容风格执行。
3. 严格按【正文创作规范】写正文 draft（400-700 字，事实以证据为准）。
4. 严格按【分页规范】把正文改写成恰好 6 页图上文案 pages（列表长度必须等于 6）。
5. single/compare 模式：调 image_search 搜参考图，把可用结果放进 references。
6. 调 generate_images 生成 6 张图（生成结果里的 image_url 是本地路径，输出时必须原样照抄）。
   传 image_template 时：把模板中的风格句「坚韧治愈风、高清、极简高级」替换为你选定的
   视觉风格的描述词（风格库里该风格名后的整段描述），其余约束原样保留——
   这样 6 张配图统一为你选定的整体视觉风格。
7. 默认不做 OCR（系统自动校验）。仅关键数字页需核验时，对该页调 ocr_image，结果写进 ocr_texts。
8. 只输出最终 JSON，不要输出 JSON 以外的任何解释文字。

【正文创作规范（系统提示词，必须遵守）】
{draft_template}

""" + _COMPLIANCE_RED_LINES + """
【分页规范（系统提示词，必须遵守）】
{pages_template}

【生图模板（原样作为 generate_images 的 image_template 参数传入，不要改写）】
{image_template}

{feedback_section}【输出 JSON 契约（字段名与类型必须完全一致）】
{output_contract}"""

_FEEDBACK_HEADER = """【审核驳回反馈（上一轮内容被人工驳回，必须逐条修正后再创作）】
{feedback_lines}

"""

# 组合生成任务（007）：query 是随机抽中的泛化问题，创作时需带上原始情境/风格/垂类
_COMBO_SECTION = """【组合创作上下文（本任务由「组合生成导入」产生）】
- 用户原始提问情境（成文需照顾该情境，可自然化用其中的细节）：
{source_query}
- 本篇创作角度（即下方 Query，是本篇的标题与主线）：{supplement_question}
- 内容风格：{style}{category_line}
写作要求：以「创作角度」为主线成文；{style_hint}{category_req}正文口吻与组织方式按上述风格执行。

"""


def _build_combo_section(task) -> str:
    """组合任务注入组合上下文；普通任务返回空串（提示词不受影响）。"""
    if not (task.source_query or task.gen_style or task.gen_category):
        return ""
    from src.services.combo import style_hint
    category = (task.gen_category or "").strip()
    return _COMBO_SECTION.format(
        source_query=(task.source_query or "（未提供）").strip(),
        supplement_question=(task.supplement_question or task.query).strip(),
        style=(task.gen_style or "通用").strip(),
        category_line=f"\n- 垂类领域：{category}（受众与用词贴合该垂类）" if category else "",
        style_hint=style_hint(task.gen_style) or "按所选风格行文",
        category_req="" if category else "")

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

    # 风格字段（缺失不报错：旧契约兼容，取默认值）
    content_style = str(data.get("content_style") or "").strip()[:30]
    image_style = str(data.get("image_style") or "").strip()[:30]

    if errors:
        return None, errors
    return {"draft": draft, "pages": pages, "images": images,
            "evidence": evidence, "references": references,
            "ocr_map": ocr_map, "notes": str(data.get("notes", ""))[:1000],
            "content_style": content_style, "image_style": image_style}, []


def _compose_message(task_id: str, query: str, mode: str, draft_tpl: str,
                     pages_tpl: str, image_tpl: str, feedbacks: list[str],
                     combo_section: str = "",
                     fixed_style: str | None = None,
                     style_kb_text: str | None = None) -> str:
    from src.services.combo import image_style_library_text
    lines = "\n".join(f"{i}. {r}" for i, r in enumerate(feedbacks, 1))
    feedback_section = (_FEEDBACK_HEADER.format(feedback_lines=lines)
                        if feedbacks else "")
    if fixed_style:
        style_rule = (f"本任务已指定内容风格「{fixed_style}」，content_style 直接填该风格，"
                      f"正文严格按该风格行文，不要另行选择。")
    else:
        style_rule = ""
    return _AGENT_INSTRUCTIONS.format(
        query=query, mode=mode, mode_desc=_MODE_DESC.get(mode, mode),
        task_id=task_id,
        quota_search=settings.mcp_max_web_searches_per_task,
        image_style_library=style_kb_text or image_style_library_text(),
        style_rule=style_rule,
        draft_template=draft_tpl, pages_template=pages_tpl,
        image_template=image_tpl,
        feedback_section=combo_section + feedback_section,
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
        w, h = await asyncio.to_thread(
            lambda: Image.open(io.BytesIO(data)).size)   # 不占事件循环（P2-9）
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


# ── P0-2：图上文字扭曲机器质检 + 有限重生成（2026-08-26）────────────
# gpt-image-2.5 渲染中文仍不可靠：出图后逐页跑 OCR，与分页文案做字符级比对，
# 不合格换构图重生成（最多 2 次），仍失败打 text_garble 标记进审核队列。
# 2026-09-11 用户硬要求（错别字/异体字 100% 审核标准）：判定口径由
# 「字符相似度 ≥0.82 即过」提升为「归一化（繁简/异体字映射 + 全半角统一 +
# 去空白，见 src/quality/text_norm.py）后逐字 100% 相等才过」——不一致触发
# 重生成，重生成仍不符打标记交人工，不得降级放行。
_GARBLE_THRESHOLD = 1.0  # 归一化后逐字全等（100% 标准）；低于 1.0 即拦
_GARBLE_MAX_REGEN = 1      # 每页最多换构图重生 1 次（9-14 P1：与交接文档
                           # 「自动重试一次」口径对齐；重生有效性靠提示词修正
                           # （逐字复现）保障，不再靠次数）。
                           # 2026-09-15：该次重生固定用 gpt-image-2.5-sunburst
                           # 精修文字错误，只修一次、不到顶不继续。
_SUNBURST_MODEL = "gpt-image-2.5-sunburst"


def _text_similarity(a: str, b: str) -> float:
    """字符级相似度：a=OCR 识别文本，b=分页文案（应出现在图上的文字）。

    2026-09-11 起双方先做 100% 标准归一化（繁简/异体字 + 全半角 + 去空白）：
    归一化后逐字相等 → 1.0（唯一放行口径）；不等 → 按 b 的字符命中数返回
    命中比例（<1.0，仅供诊断/日志；OCR 多识别出的内容同样导致不等而被拦）。
    """
    from src.quality.text_norm import normalize_text
    A, B = normalize_text(a), normalize_text(b)
    if not B:
        return 1.0           # 文案本就无字 → 不算扭曲
    if not A:
        return 0.0           # 该有字却识别不出 → 视为失败
    if A == B:
        return 1.0           # 100% 标准：归一化后逐字全等
    common = sum(1 for ch in B if ch in A)
    # 诊断值必 <1.0（即使 b 是 a 的真子集——OCR 多识别内容同样判不等被拦）
    return min(common / len(B), 0.999)


async def _garble_check_and_regen(task_id, pages: list[str], localized: list[dict],
                                  image_tpl: str, mode: str) -> tuple[list[dict], dict[int, str]]:
    """对每页配图 OCR 判定文字是否扭曲；不合格换构图重生（配额内 2 次）。

    返回 (更新后的 localized, {page_index: 'text_garble'} 需进审核队列的页)。
    """
    from src.gateway.ocr import ocr_image, fetch_image_bytes as _fetch_bytes
    from src.gateway.image_gen import generate_image
    from src.pipeline.nodes import _persist_image
    garbled: dict[int, str] = {}
    if settings.mock_image_gen:
        return localized, garbled
    for img in localized:
        idx = img.get("page_index")
        # 防御：Agent 给的 page_index 可能越界（0/>6/缺失），跳过该页不整链失败
        if not isinstance(idx, int) or not (1 <= idx <= len(pages)):
            continue
        page_text = pages[idx - 1]
        try:
            r = await ocr_image(img["image_url"])
            sim = _text_similarity(r["raw_text"], page_text)
        except Exception:  # noqa: BLE001
            sim = 1.0        # OCR 本身失败不误杀（cross_check 兜底）
        img["first_sim"] = sim          # 024 首过率治理：首轮 sim 落库供统计
        # ── 逐字字形校验（2026-09-18）：VL 逐字核对「标准文案完整性 + 图中每个
        # 汉字规范性」。抓相似度对撞的盲区：LOCKED 文案字形变形、自由文字
        # 错字（实测 烘培/烘焙 类错字 sim 满分放行）。VL 不可用返回 None 不误杀。
        from src.services.visual_check import check_text_match, verify_page_glyphs
        gv = await verify_page_glyphs(img["image_url"], page_text)
        glyph_bad = gv is not None and (
            not gv["locked_ok"] or gv["glyph_errors"] or not gv.get("render_ok", True))
        if glyph_bad:
            # 留痕：glyphverify/lockerr/glypherr/rendererr 供审图与统计追溯
            marks = ["glyphverify"]
            if gv is not None and not gv["locked_ok"]:
                marks.append("lockerr")
            if gv is not None and gv["glyph_errors"]:
                marks.append("glypherr")
            if gv is not None and not gv.get("render_ok", True):
                marks.append("rendererr")
            img["prompt_used"] = (img.get("prompt_used", "") + "|" + "|".join(marks)).strip("|")
        if sim >= _GARBLE_THRESHOLD and not glyph_bad:
            continue
        # ── VL 申诉通道（2026-09-14 P2）：OCR 判不合格的页先经 qwen-vl-max 复核
        # 「图中文字是否与文案逐字一致」——一致即放行（OCR 误判申诉成功，图中文字
        # 实际渲染正确，重生无意义只会再烧一张）；VL 说不一致或不可用才进重生。
        # 放行口径仍是 100%，只是给 OCR 误判一个复核出口。
        # 注意：glyph_bad（字形校验已发现错字）时不走申诉，直接重生。
        if not glyph_bad:
            appeal = await check_text_match(img["image_url"], page_text)
            if appeal is not None and appeal["ok"]:
                img["prompt_used"] = (img.get("prompt_used", "") + "|vlappeal").strip("|")
                continue
        # 换构图重生（最多 _GARBLE_MAX_REGEN 次）：9-14 P1 修正——重生图必须与
        # 整页文案逐字全等（100% 标准），所以提示词要求逐字复现原文案重排版，
        # 绝不能再让模型「只保留核心一句」（≤20 字对 80-130 字整页文案永远
        # 不可能 100% 相等，9-11~9-14 期间每次重生必败纯烧钱，WS4 实测单任务
        # 46 张）。每次重生前扣任务级出图总预算，到顶即停、打标记进人工。
        ok = False
        for attempt in range(1, _GARBLE_MAX_REGEN + 1):
            try:
                if not await consume_image_budget(task_id):
                    break
                regen_prompt = (
                    image_tpl.replace("{page_body}", page_text)
                    + "（重新排版：图中文字必须逐字复现上述文案，一字不得增删改、"
                      "不得精简替换；换一个与之前不同的构图与配色，"
                      "确保每个字清晰可辨、标准黑体不变形。"
                      "If visual beauty conflicts with Chinese character accuracy, "
                      "sacrifice visual beauty and preserve the exact Chinese characters.）")
                # 单次 Sunburst 精修：文字错误不再多次换构图烧钱
                r2 = await generate_image(regen_prompt,
                                          model=_SUNBURST_MODEL, channel="fusion")
                data, ctype = await _fetch_bytes(r2["image_url"])
                local_url = _persist_image(task_id, idx, "p", data, ctype)
                try:
                    r3 = await ocr_image(local_url)
                    sim2 = _text_similarity(r3["raw_text"], page_text)
                except Exception:  # noqa: BLE001
                    sim2 = 1.0
                if sim2 >= _GARBLE_THRESHOLD:
                    img["image_url"] = local_url
                    img["hash"] = hashlib.md5(data).hexdigest()
                    img["prompt_used"] = (img.get("prompt_used", "") + "|regen").strip("|")
                    ok = True
                    break
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        if not ok:
            garbled[idx] = "text_garble"
    return localized, garbled


async def _subject_check_and_regen(task_id, pages: list[str], localized: list[dict],
                                   image_tpl: str, mode: str) -> list[dict]:
    """视觉主体审核（2026-09-02 补齐 Agent 路径——此前仅直连路径有，用户反馈
    「生图与标题不匹配、实物照片牛头不对马嘴」的主要来源即创作 Agent 生成的图）。

    每页 VL 看图比对「图中主体 vs 该页文案主题」：不符→带主体强调重画一次→
    仍不符→img['subject_mismatch']=True + 打 RejectMark 进人工审核队列
    （role=系统质检，与文字扭曲同通道）。VL 不可用→跳过不误杀。
    """
    from src.services.visual_check import check_subject_match
    from src.gateway.ocr import fetch_image_bytes as _fetch_bytes
    from src.gateway.image_gen import generate_image
    from src.pipeline.nodes import _persist_image
    if settings.mock_image_gen:
        return localized
    for img in localized:
        idx = img.get("page_index")
        if not isinstance(idx, int) or not (1 <= idx <= len(pages)):
            continue
        page_text = pages[idx - 1]
        try:
            verdict = await check_subject_match(img["image_url"], page_text)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            verdict = None
        if verdict is None or verdict["ok"]:
            continue
        # 主体不符：带强调重画一次（先扣任务级出图总预算，到顶即停不打断整链）
        try:
            if not await consume_image_budget(task_id):
                img["subject_mismatch"] = True
                img.setdefault("_subj_flag_reason",
                               "视觉主体审核未通过，且任务出图预算已用尽，待人工复核")
                continue
            regen_prompt = (
                image_tpl.replace("{page_body}", page_text)
                + f"（画面主体必须与文案严格一致：{page_text[:60]}；"
                  f"禁止画其他事物）")
            r2 = await generate_image(regen_prompt)
            data, ctype = await _fetch_bytes(r2["image_url"])
            local_url = _persist_image(task_id, idx, "p", data, ctype)
            verdict2 = await check_subject_match(local_url, page_text)
            if verdict2 is None or verdict2["ok"]:
                img.update(image_url=local_url,
                           hash=hashlib.md5(data).hexdigest(),
                           prompt_used=(img.get("prompt_used", "") + "|subjregen").strip("|"))
                continue
            # 重画仍不符：标记 + 人工队列
            img.update(image_url=local_url,
                       hash=hashlib.md5(data).hexdigest(),
                       subject_mismatch=True,
                       prompt_used=(img.get("prompt_used", "") + "|subj!").strip("|"))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            img["subject_mismatch"] = True   # 重画通道故障：原图标记待人工
        img.setdefault("_subj_flag_reason",
                       f"视觉主体审核：图中主体与文案不符（{verdict.get('actual', '?')}），重画后仍存疑")
    return localized


# ── 组合收尾/落库（monolith 节点内顺序调用全部；staged 各阶段调用子集）────


async def _localize_all(task_id, images: list[dict]) -> list[dict]:
    """逐页本地化 Agent 给出的配图（失败即节点失败，不留半截产物）。"""
    localized = []
    for img in images:
        local_url, content_hash, origin, size_ok = await _localize_image(
            task_id, img["page_index"], img["image_url"], img["origin_url"])
        localized.append({**img, "image_url": local_url, "hash": content_hash,
                          "origin_url": origin, "size_ok": size_ok})
    return localized


async def _image_quality_chain(task_id, pages: list[str], localized: list[dict],
                               image_tpl: str, mode: str,
                               confirmed_refs: list) -> tuple[list[dict], dict]:
    """配图质检链（2026-09-21 三链合一版）：每页一次 VL 综合校验
    （文字逐字+字形+渲染 / 主体一致性 / 图文协调，原 garble/subject/
    ai_review 三链合并）——不合格带意见重生一次（Sunburst 精修），
    仍不过打标记进人工审核。OCR 对撞环节已按用户决策移除
    （文字铁律 + VL 逐字校验已覆盖，OCR 开源识别不再参与配图质检）。

    返回 (更新后的 localized, {page_index: 标记} 需进人工审核队列的页)。
    """
    flags: dict[int, list[str]] = {}
    if settings.mock_image_gen:
        return localized, flags
    from src.services.visual_check import comprehensive_page_check
    for img in localized:
        idx = img.get("page_index")
        if not isinstance(idx, int) or not (1 <= idx <= len(pages)):
            continue
        page_text = pages[idx - 1]
        chk = await comprehensive_page_check(
            img["image_url"], page_text, mode in ("compare", "single"))
        if chk is None or chk["ok"]:
            continue
        # 不合格：换构图重生一次（沿用 Sunburst 文字精修通道）
        regen_ok = False
        try:
            from src.gateway.image_gen import generate_image
            from src.gateway.ocr import fetch_image_bytes as _fb
            from src.pipeline.nodes import _persist_image as _pi
            regen_prompt = (
                image_tpl.replace("{page_body}", page_text)
                + "（重新排版：换一个与之前不同的构图与配色，"
                  "文字逐字复现上述文案，一字不得增删改；"
                  "每个字笔画分明清晰可辨）")
            r2 = await generate_image(regen_prompt, model=_SUNBURST_MODEL,
                                      channel="fusion")
            data, ctype = await _fb(r2["image_url"])
            local_url = _pi(task_id, idx, "p", data, ctype)
            img["image_url"] = local_url
            img["hash"] = hashlib.md5(data).hexdigest()
            img["prompt_used"] = (img.get("prompt_used", "") + "|regen").strip("|")
            regen_ok = True
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        if not regen_ok:
            flags[idx] = chk["issues"] or ["综合质检未通过"]
        else:
            # 重生后再查一次，仍不过则打标记
            chk2 = await comprehensive_page_check(
                img["image_url"], page_text, mode in ("compare", "single"))
            if chk2 is not None and not chk2["ok"]:
                flags[idx] = (chk2["issues"] or chk["issues"]
                              or ["重生后仍未通过综合质检"])
    # 跨页批量质检（v0.1.4 P3）：拼版一次 VL 查跨页信息/视觉重复、风格
    # 一致性、深底页数——告警进 RejectMark 人工队列，不自动重生。
    # 覆盖 staged 直出分支与 monolith（本函数为两路共享质检链）
    if len(localized) >= 2:
        try:
            from src.services.cross_page_check import (
                cross_page_check, cross_flags)
            cp = await cross_page_check(
                task_id, [im.get("image_url") for im in localized], pages)
            if cp is not None and not cp["ok"]:
                for idx, msgs in cross_flags(cp, len(localized)).items():
                    flags.setdefault(idx, []).extend(msgs)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    return localized, flags


async def _localize_refs(task_id, references: list[dict]) -> list[dict]:
    """参考图（compare/single）：本地化后存 official 素材，供审核追溯与图生图复核。"""
    refs_localized = []
    for i, ref in enumerate(references, start=1):
        try:
            local_url, _, _, _ = await _localize_image(task_id, i, ref["image_url"], "")
            refs_localized.append({**ref, "image_url": local_url})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            refs_localized.append(ref)  # 下载失败保留原地址，展示层走代理兜底
    return refs_localized


async def _apply_style_backfill(task_row, content_style: str,
                                image_style: str) -> None:
    """风格自适应回填：图片视觉风格始终记录本轮 Agent 判定；
    内容风格仅普通导入（未显式指定时）回填，组合导入的显式风格不覆盖。"""
    if image_style:
        task_row.gen_image_style = image_style
        # 描述词快照同步落库（015/016 口径）：否则直连重生成按名反查不到
        # 会误触发重选，风格漂移与首图不一致
        if not task_row.gen_image_style_desc:
            from src.services.style_select import style_desc_for
            d = await style_desc_for(image_style, task_row.created_by)
            if d:
                task_row.gen_image_style_desc = d
    if content_style and not task_row.gen_style:
        task_row.gen_style = content_style


async def _persist_claim_evidence(session, task_id, query: str,
                                  evidence: list[dict]) -> None:
    from src.pipeline.nodes import source_level_for
    claim = Claim(task_id=task_id, claim_text=query, risk_level="P1", position=1)
    session.add(claim)
    await session.flush()
    for e in evidence:
        session.add(Evidence(claim_id=claim.id, source_url=e["url"],
                             source_level=source_level_for(e["url"]), excerpt=e["summary"],
                             supports=True))


async def _persist_official_refs(session, task_id, query: str,
                                 refs_localized: list[dict]) -> None:
    for i, ref in enumerate(refs_localized, start=1):
        session.add(Asset(
            task_id=task_id, page_index=i, subject=query,
            source_type="official", copyright_status="unknown",
            hash=hashlib.md5(ref["image_url"].encode()).hexdigest(),
            image_url=ref["image_url"], origin_url=ref.get("image_url"),
            model_version=ref.get("engine") or "search", is_illustration=False))


async def _persist_review_marks(session, task_id, localized: list[dict],
                                garbled: dict) -> None:
    """P0-2：文字扭曲重生仍失败的页 → 打 open 标记进审核队列（机器先拦，人再复核）；
    视觉主体审核仍存疑的页：与文字扭曲同通道进人工审核队列。"""
    subj_flags = {img["page_index"]: img["_subj_flag_reason"]
                  for img in localized if img.pop("_subj_flag_reason", None)}
    if subj_flags:
        from src.models.review import RejectMark as _RM
        for page_idx, reason in subj_flags.items():
            session.add(_RM(task_id=task_id, role="系统质检",
                            item_type="image", page_index=page_idx,
                            reason=reason))
    if garbled:
        from src.models.review import RejectMark
        for page_idx, flags in garbled.items():
            reason = ("图上文字扭曲：OCR 与分页文案逐字不一致（归一化后 100% 相等标准），"
                      "换构图重生成 2 次仍不符，请人工复核"
                      if isinstance(flags, str)
                      else f"AI 审核未通过：{'；'.join(flags)[:120]}，请人工复核")
            session.add(RejectMark(
                task_id=task_id, role="AI审核" if isinstance(flags, list) else "系统质检",
                item_type="image", page_index=page_idx, reason=reason,
                status="open"))


async def _persist_draft(session, task_id, body: str, model_version: str,
                         prompt_version: str) -> None:
    max_v = (await session.execute(
        select(func.max(Draft.version)).where(
            Draft.task_id == task_id))).scalar() or 0
    session.add(Draft(task_id=task_id, version=max_v + 1, body=body,
                      model_version=model_version,
                      prompt_version=prompt_version))


async def _persist_page_copies(session, task_id, pages: list[str]) -> None:
    for idx, body in enumerate(pages, start=1):
        session.add(PageCopy(task_id=task_id, page_index=idx, body=body,
                             claim_ids=[]))


async def _persist_assets(session, task_id, query: str, localized: list[dict],
                          model_version: str) -> None:
    for img in localized:
        mv = model_version
        if not img["size_ok"]:
            mv += "|badsize"
        session.add(Asset(
            task_id=task_id, page_index=img["page_index"], subject=query,
            source_type="ai_generated", copyright_status="clear",
            hash=img["hash"], image_url=img["image_url"],
            origin_url=img["origin_url"] or None,
            model_version=mv, is_illustration=False,
            subject_mismatch=bool(img.get("subject_mismatch")),
            first_sim=img.get("first_sim"),   # 024 首过率治理（garble 首轮 sim）
            prompt_used=img.get("prompt_used") or None))   # 定点修改/AI审核要复用原提示词


async def _persist_ocr(session, task_id, ocr_map: dict[int, str]) -> float:
    """OCR 落库：Agent 自检结果优先；Agent 未覆盖的页由后端兜底补齐
    （cross_check 按全页 OCR 对撞，缺页会被判「识别失败」拉高风险分级）。
    返回兜底 OCR 成本。"""
    await session.flush()
    assets = (await session.execute(
        select(Asset.id, Asset.page_index, Asset.image_url)
        .where(Asset.task_id == task_id,
               Asset.source_type == "ai_generated")
        .order_by(Asset.page_index))).all()
    ocr_cost = 0.0
    missing_rows = []
    for asset_id, page_index, image_url in assets:
        text = ocr_map.get(page_index)
        if text is None:
            missing_rows.append((asset_id, page_index, image_url))
            continue
        session.add(OcrResult(
            asset_id=asset_id, raw_text=text,
            key_fields={"page": str(page_index), "source": "agent"},
            confidence=0.9 if text else 0.0))
    if missing_rows:
        # 2026-09-21 用户决策：不再做生图后 OCR 兜底（cross_check 不依赖
        # OCR 结果做重活，文字质检已由 VL 综合校验承担）——缺页直接置空。
        rows, ocr_cost = [], 0.0
        session.add_all(rows)
    return ocr_cost
