"""参考图 → 页型模板提取（2026-09-24 P3，封闭词汇表管线）。

用户随时丢参考图/效果图 → VL 分析版式结构（只能从封闭词汇表选词+量比例）
→ 确定性映射器生成 spec 草稿 → validate_spec 校验 → 入库（enabled=false，
人工预览确认后启用）。渲染能力有界=提取结果有界。

VL 输出的 analysis JSON：
{"page_role": "cover|content|ending", "photo_count": 0-6,
 "arrangement": "single|side_by_side|grid_2x2|triple_row|one_big_two_small|none",
 "top_fraction": 0.45-0.75, "frame": "none|polaroid|tape",
 "text_bg": "theme_light|theme_deep|scrim",
 "title_style": "accent_bar|center|overlay_on_photo",
 "section_style": "number_badge|none",
 "body": "paragraph|points_col|points_highlight|tip_box",
 "decor": ["sticker|banner|dashes|vs_badge|polaroid|tape"],
 "title_text": "图中标题转录（≤18字）",
 "body_gist": "文字区形态一句话（段落/条目/高亮）"}
"""
import json

from src.config import settings
from src.gateway.http_client import get_client
from src.services.compose_templates import validate_spec

_EXTRACT_PROMPT = """你是版式分析员。分析这张小红书图文卡的版式结构，输出结构化 JSON。
只能从给定词汇表中选词，不许自由发挥；比例目测估计。

词汇表：
- photo_count：照片子图数量（0=纯文字排版页）
- arrangement：single（单大图）/ side_by_side（左右双图）/ grid_2x2（四宫格）/
  triple_row（三联横排）/ one_big_two_small（一大两小）/ none（无照片）
- top_fraction：照片区占画面总高比例（0.45-0.75 之间一位小数；none 填 0.57）
- frame：none / polaroid（子图带白边框）/ tape（子图带胶带贴角）
- text_bg：theme_light（浅色底）/ theme_deep（整页深色底）/ scrim（文字压在图上暗色渐变）
- title_style：accent_bar（标题旁色条/色块）/ center（居中大标题）/
  overlay_on_photo（标题压在照片上）
- section_style：number_badge（小节标题带序号圆点/徽章）/ none
- body：paragraph（整段文字）/ points_col（条目列表）/ points_highlight（荧光高亮条目）/
  tip_box（圆角贴士框内文字）
- decor（多选）：sticker（角落贴纸）/ banner（横幅条）/ dashes（虚线分隔）/
  vs_badge（VS 对比徽章）/ polaroid / tape / 空数组

判定基准：这是封面（第1页，标题最大最醒目）填 cover；纯总结/金句收尾页填 ending；
普通内容页填 content。同时转录图中标题文字（title_text，≤18字）与文字区形态
一句话（body_gist）。

只输出严格 JSON，不要任何其他文字：
{"page_role": "...", "photo_count": 0, "arrangement": "...", "top_fraction": 0.6,
 "frame": "...", "text_bg": "...", "title_style": "...", "section_style": "...",
 "body": "...", "decor": [], "title_text": "...", "body_gist": "..."}"""


async def analyze_layout(image_url: str) -> dict | None:
    """VL 分析单图版式。复用 visual_check 网关（dashscope qwen-vl）。
    失败/解析失败返回 None。"""
    try:
        from src.gateway.ocr import _image_to_data_url
        data_url = await _image_to_data_url(image_url)
        payload = {
            "model": settings.visual_check_model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": _EXTRACT_PROMPT},
            ]}],
            "max_tokens": 500,
        }
        resp = await get_client("visual_check", timeout=60).post(
            f"{settings.ocr_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json=payload)
        if resp.status_code != 200:
            return None
        raw = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        obj = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        return obj if isinstance(obj.get("arrangement"), str) else None
    except Exception:
        import traceback
        traceback.print_exc()
        return None


def _norm_tf(v) -> float:
    try:
        tf = float(v)
    except (TypeError, ValueError):
        return 0.57
    return min(0.75, max(0.45, round(tf, 2)))


def analysis_to_spec(analysis: dict, template_id: str, name: str,
                     notes: str = "") -> tuple[dict, list[str]]:
    """确定性映射：analysis → 模板 spec（不做任何自由生成）。"""
    arr = analysis.get("arrangement", "single")
    if arr not in ("single", "side_by_side", "grid_2x2", "triple_row",
                   "one_big_two_small", "none"):
        arr = "single"
    body = analysis.get("body", "paragraph")
    title_style = analysis.get("title_style", "accent_bar")
    if title_style not in ("accent_bar", "center", "overlay_on_photo"):
        title_style = "accent_bar"
    role = analysis.get("page_role", "content")
    if role not in ("cover", "content", "ending"):
        role = "content"

    elements: list[dict] = [{"type": "title",
                             "style": ("center_quote" if arr == "none"
                                       else title_style)}]
    if analysis.get("section_style") == "number_badge":
        elements.append({"type": "section_title", "style": "number_badge"})
    if body == "tip_box":
        elements.append({"type": "tip_box"})
    elif body == "points_col":
        elements.append({"type": "points_col"})
    elif body == "points_highlight":
        elements.append({"type": "points_highlight"})
    else:
        elements.append({"type": "paragraph"})

    decor_map = {"sticker": "sticker_br", "banner": "banner",
                 "vs_badge": "vs_badge"}
    decor = []
    for d in analysis.get("decor") or []:
        if d in decor_map and decor_map[d] not in decor:
            decor.append(decor_map[d])

    text_bg = ("theme_deep" if arr == "none"
               or analysis.get("text_bg") == "theme_deep"
               and role == "ending" else "theme_light")

    spec = {
        "template_id": template_id, "name": name,
        "page_role": role, "tags": ["提取"],
        "photo": {"arrangement": arr, "top_fraction": _norm_tf(
            analysis.get("top_fraction", 0.57)),
            "frame": analysis.get("frame") if analysis.get("frame")
            in ("none", "polaroid", "tape") else "none"},
        "text": {"bg": text_bg, "elements": elements},
        "decor": decor,
        "palette_rule": "theme_light" if text_bg != "theme_deep" else "theme_deep",
        "source": "vl_extracted",
        "notes": notes or (f"标题转录：{str(analysis.get('title_text', ''))[:20]}"
                           f"｜形态：{str(analysis.get('body_gist', ''))[:40]}"),
    }
    return spec, validate_spec(spec)


async def extract_template(image_url: str, template_id: str, name: str
                           ) -> dict | None:
    """完整提取：分析→映射→校验。返回 {spec, errors}；分析失败返回 None。"""
    analysis = await analyze_layout(image_url)
    if not analysis:
        return None
    spec, errs = analysis_to_spec(analysis, template_id, name)
    return {"spec": spec, "errors": errs, "analysis": analysis}
