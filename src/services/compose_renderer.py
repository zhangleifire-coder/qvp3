"""模板化程序版式渲染器（2026-09-24 页型模板库 P1）。

与 poster_compose.compose_page 的关系：compose_page 是写死的 7 分支版式
（保留为回退路径）；本模块按「模板 spec（JSON）+ 内容（PageSpec 字段）+
插画列表 + 主题色板」通用渲染——模板=数据，新增版式不再改渲染代码。

spec 词汇表（封闭集合，template_extract/VL 只能映射到这些词）：
- photo.arrangement: single / side_by_side / grid_2x2 / triple_row /
  one_big_two_small / none
- photo.frame: none / polaroid / tape
- photo.top_fraction: 0.45-0.75（照片区占画面高度比例；none 时忽略）
- text.bg: theme_light / theme_deep / scrim
- title.style: accent_bar / center / overlay_on_photo / center_quote
- section_title.style: number_badge / plain
- 正文元素: paragraph / points_col / points_highlight / capsule / separator
- decor: sticker_tl / sticker_tr / sticker_bl / sticker_br / banner /
  vs_badge / icon_row

字体解析顺序：COMPOSE_FONT_DIR → /app/data/fonts（容器）→ C:/Windows/Fonts
（本地开发）→ Noto 回退——本地渲染预览与容器正式渲染字形一致。
"""
import os
from pathlib import Path

from src.services.poster_compose import (
    W, H, extract_palette, _paste_cover, _wrap_px)

_MARGIN = 48
_INK = (34, 31, 40)            # 浅底文字主色
_INK_SOFT = (54, 50, 62)       # 段落正文
_LIGHT_BG = (247, 243, 234)    # 0922 参考浅米底（theme_light 基色）

_FONT_CANDIDATES = [
    Path(os.environ.get("COMPOSE_FONT_DIR", "")) if os.environ.get(
        "COMPOSE_FONT_DIR") else None,
    Path("/app/data/fonts"),
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/opentype/noto"),
]
_FONT_CACHE: dict = {}


def _find_font(bold: bool) -> Path | None:
    names = ("msyhbd.ttc", "NotoSansCJK-Bold.ttc") if bold else \
            ("msyh.ttc", "NotoSansCJK-Regular.ttc")
    for cand in _FONT_CANDIDATES:
        if not cand:
            continue
        for n in names:
            p = cand / n
            if p.exists():
                return p
    return None


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    from PIL import ImageFont
    fp = _find_font(bold)
    f = (ImageFont.truetype(str(fp), size) if fp
         else ImageFont.load_default())
    _FONT_CACHE[key] = f
    return f


# ── 装饰/元素绘制器（painter 词汇表，全部几何级）────────────────────────

def _paint_title_accent_bar(dr, title, y, max_w, accent):
    f = _font(54, True)
    for ln in _wrap_px(title, f, max_w - 46, dr)[:2]:
        dr.rectangle([_MARGIN, y + 6, _MARGIN + 12, y + 52], fill=accent)
        dr.text((_MARGIN + 34, y), ln, font=f, fill=_INK)
        y += int(54 * 1.3)
    return y + 14


def _paint_title_center(dr, img, title, y, max_w, accent, fg=None):
    f = _font(56, True)
    lines = _wrap_px(title, f, max_w, dr)[:2]
    for ln in lines:
        tw = dr.textlength(ln, font=f)
        dr.text(((W - tw) / 2, y), ln, font=f, fill=fg or _INK)
        y += int(56 * 1.3)
    # 标题下居中点缀短线
    y += 6
    dr.line([(W / 2 - 60, y), (W / 2 + 60, y)], fill=accent, width=6)
    return y + 20


def _paint_title_overlay(dr, title, photo_rect):
    """图上压字：照片区底部暗带 + 白字大结论（封面专用）。"""
    x0, y0, x1, y1 = photo_rect
    band_h = 150
    by0 = y1 - band_h
    for yy in range(by0, y1):
        a = int(190 * (yy - by0) / band_h)
        dr.line([(x0, yy), (x1, yy)], fill=(12, 10, 18, a))
    f = _font(58, True)
    lines = _wrap_px(title, f, (x1 - x0) - _MARGIN * 2, dr)[:2]
    ty = by0 + (band_h - 58 * min(len(lines), 2) * 1.25) / 2
    for ln in lines:
        dr.text((x0 + _MARGIN, ty), ln, font=f, fill=(252, 250, 246))
        ty += int(58 * 1.25)


def _paint_section_badge(dr, section_title, no, y, max_w, accent):
    badge = 44
    dr.rounded_rectangle([_MARGIN, y, _MARGIN + badge, y + badge],
                         radius=12, fill=accent)
    f_no = _font(28, True)
    nw = dr.textlength(str(no), font=f_no)
    dr.text((_MARGIN + (badge - nw) / 2, y + (badge - 28) / 2 - 2),
            str(no), font=f_no, fill=(255, 255, 255))
    f_se = _font(34, True)
    for ln in _wrap_px(section_title, f_se, max_w - badge - 18, dr)[:1]:
        dr.text((_MARGIN + badge + 18, y + 5), ln, font=f_se, fill=_INK)
    return y + badge + 26


def _paint_section_plain(dr, section_title, y, max_w, accent):
    f_se = _font(34, True)
    for ln in _wrap_px(section_title, f_se, max_w - 20, dr)[:1]:
        dr.rectangle([_MARGIN, y + 4, _MARGIN + 8, y + 36], fill=accent)
        dr.text((_MARGIN + 20, y), ln, font=f_se, fill=_INK)
    return y + 52


def _paint_paragraph(dr, paragraph, y, max_w, size=30, color=None,
                     max_lines=8):
    f = _font(size, False)
    for ln in _wrap_px(paragraph, f, max_w, dr)[:max_lines]:
        dr.text((_MARGIN, y), ln, font=f, fill=color or _INK_SOFT)
        y += int(size * 1.53)
    return y + 6


def _paint_points_col(dr, points, y, max_w, accent, highlight=False):
    for pt in points[:5]:
        if highlight:
            f = _font(30, True)
            tw = dr.textlength(pt, font=f)
            dr.rectangle([_MARGIN - 8, y - 4, _MARGIN + tw + 14, y + 40],
                         fill=(255, 236, 176))
            dr.text((_MARGIN, y), pt, font=f, fill=_INK)
        else:
            dr.ellipse([_MARGIN, y + 8, _MARGIN + 16, y + 24], fill=accent)
            f = _font(30, False)
            for j, ln in enumerate(_wrap_px(pt, f, max_w - 30, dr)[:2]):
                dr.text((_MARGIN + 30, y + (0 if j == 0 else 34)), ln,
                        font=f, fill=_INK_SOFT)
        y += 56 if not highlight else 58
    return y + 4


def _paint_capsule(dr, text, y, accent):
    f = _font(30, True)
    tw = dr.textlength(text, font=f)
    bw = tw + 44
    dr.rounded_rectangle([(W - bw) / 2, y, (W + bw) / 2, y + 48],
                         radius=24, fill=accent)
    dr.text(((W - tw) / 2, y + 7), text, font=f, fill=(255, 255, 255))
    return y + 66


def _paint_separator(dr, y, accent):
    for x in range(_MARGIN, W - _MARGIN, 26):
        dr.line([(x, y), (x + 14, y)], fill=accent, width=4)
    return y + 26


def _paint_banner(dr, text, y, accent):
    """文字区顶部细横幅：主题色底白字短句（对比结论/关键提示）。"""
    f = _font(30, True)
    tw = dr.textlength(text, font=f)
    dr.rectangle([_MARGIN, y, W - _MARGIN, y + 46], fill=accent)
    dr.text(((W - tw) / 2, y + 6), text, font=f, fill=(255, 255, 255))
    return y + 62


def _paint_sticker(dr, corner, text, accent):
    """角落圆角贴纸（2-4 字要点词）。"""
    f = _font(30, True)
    tw = dr.textlength(text, font=f)
    bw, bh = tw + 36, 52
    pad = 26
    x0 = (_MARGIN + pad if "l" in corner else W - _MARGIN - pad - bw)
    y0 = (H - 150 if "b" in corner else 0) + (pad if "t" in corner else 0)
    dr.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=14,
                         fill=accent)
    dr.text((x0 + 18, y0 + 9), text, font=f, fill=(255, 255, 255))


def _paint_vs_badge(dr, photo_rect):
    x0, y0, x1, y1 = photo_rect
    cx, cy, r = (x0 + x1) // 2, (y0 + y1) // 2, 56
    dr.ellipse([cx - r - 7, cy - r - 7, cx + r + 7, cy + r + 7],
               fill=(252, 250, 246))
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(206, 112, 54))
    f = _font(54, True)
    tw = dr.textlength("VS", font=f)
    dr.text((cx - tw / 2, cy - f.size * 0.62), "VS", font=f,
            fill=(255, 255, 255))


def _paint_center_quote(dr, title, paragraph, accent):
    """结尾金句页：整页深底 + 居中大字 + 下方小字说明。"""
    f_q = _font(64, True)
    lines = _wrap_px(title, f_q, W - _MARGIN * 2 - 40, dr)[:3]
    block_h = len(lines) * int(64 * 1.35)
    y = (H - block_h - 200) / 2
    for ln in lines:
        tw = dr.textlength(ln, font=f_q)
        dr.text(((W - tw) / 2, y), ln, font=f_q, fill=(250, 247, 240))
        y += int(64 * 1.35)
    y += 56
    dr.line([(W / 2 - 70, y), (W / 2 + 70, y)], fill=accent, width=6)
    y += 44
    f_p = _font(30, False)
    for ln in _wrap_px(paragraph, f_p, W - _MARGIN * 2, dr)[:6]:
        tw = dr.textlength(ln, font=f_p)
        dr.text(((W - tw) / 2, y), ln, font=f_p, fill=(214, 208, 196))
        y += 46


def _paint_tip_box(dr, img, title, paragraph, y0, y1, accent):
    """贴士框：圆角描边 + 浅色底，框标题小圆点 + 段落正文。"""
    from PIL import ImageDraw
    box = [_MARGIN, y0, W - _MARGIN, y1 - 36]
    dr.rounded_rectangle(box, radius=22, outline=accent, width=4,
                         fill=(252, 250, 244))
    ty = y0 + 26
    if title:
        dr.ellipse([_MARGIN + 26, ty + 8, _MARGIN + 42, ty + 24],
                   fill=accent)
        f_t = _font(36, True)
        for ln in _wrap_px(title, f_t, W - _MARGIN * 2 - 90, dr)[:1]:
            dr.text((_MARGIN + 56, ty), ln, font=f_t, fill=_INK)
        ty += 58
    ty = _paint_paragraph(
        ImageDraw.Draw(img), paragraph, ty, W - _MARGIN * 2 - 44,
        color=_INK_SOFT, max_lines=6)
    return ty


# ── 照片区排布 ───────────────────────────────────────────────────────────

def _cell_rects(arrangement: str, box) -> list:
    """照片区内按排布切格子（返回每格像素矩形）。"""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    gap = 16
    if arrangement == "single":
        return [box]
    if arrangement == "side_by_side":
        iw = (bw - gap) / 2
        return [(x0, y0, x0 + iw, y1), (x0 + iw + gap, y0, x1, y1)]
    if arrangement == "grid_2x2":
        iw, ih = (bw - gap) / 2, (bh - gap) / 2
        return [(x0, y0, x0 + iw, y0 + ih),
                (x0 + iw + gap, y0, x1, y0 + ih),
                (x0, y0 + ih + gap, x0 + iw, y1),
                (x0 + iw + gap, y0 + ih + gap, x1, y1)]
    if arrangement == "triple_row":
        iw = (bw - gap * 2) / 3
        return [(x0 + i * (iw + gap), y0, x0 + (i + 1) * iw + i * gap, y1)
                for i in range(3)]
    if arrangement == "one_big_two_small":
        big_w = bw * 0.58
        small_h = (bh - gap) / 2
        return [(x0, y0, x0 + big_w, y1),
                (x0 + big_w + gap, y0, x1, y0 + small_h),
                (x0 + big_w + gap, y0 + small_h + gap, x1, y1)]
    return [box]


def _paste_cell(img, ill, rect, frame: str, accent):
    """贴单格插画 + 可选装饰边框（polaroid 白边 / tape 角贴）。"""
    from PIL import ImageDraw
    x0, y0, x1, y1 = [int(v) for v in rect]
    if frame == "polaroid":
        pad = 12
        ImageDraw.Draw(img).rectangle(
            [x0 - pad, y0 - pad, x1 + pad, y1 + pad + 16], fill=(252, 250, 246))
        x0, y0, x1, y1 = x0 - 4, y0 - 4, x1 + 4, y1 + 4
    _paste_cover(img, ill, (x0, y0, x1, y1), radius=18 if frame != "polaroid" else 6)
    if frame == "tape":
        dr = ImageDraw.Draw(img)
        tw, th = 84, 26
        for cx, cy in ((x0 + 26, y0 - th // 2), (x1 - 26 - tw, y1 - th // 2)):
            dr.rectangle([cx, cy, cx + tw, cy + th], fill=(255, 244, 204, 200))


# ── 主渲染入口 ───────────────────────────────────────────────────────────

def render_card(spec: dict, content: dict, illustrations: list,
                style_desc: str = "", out_path: Path | None = None) -> Path:
    """按模板 spec 渲染一页卡片。

    content：{title, section_title, section_no, paragraph, points,
              subtitle, quote, sticker_text}（缺省字段自动降级）。
    illustrations：Path 列表（数量不足时循环复用）。
    返回 PNG 路径（GENERATED/ 下 uuid 命名）。
    """
    import uuid
    from PIL import Image, ImageDraw

    from src.services.poster_compose import GENERATED
    out_path = out_path or (GENERATED / f"card-{uuid.uuid4().hex}.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    bg_top, bg_bot, accent = extract_palette(style_desc)
    photo = spec.get("photo") or {}
    arrangement = photo.get("arrangement", "single")
    frame = photo.get("frame", "none")
    tspec = spec.get("text") or {}
    elements = tspec.get("elements") or [{"type": "title"}, {"type": "paragraph"}]

    img = Image.new("RGB", (W, H), _LIGHT_BG)
    dr = ImageDraw.Draw(img)
    ills = [p for p in illustrations if p and Path(p).exists()]
    photo_rect = None
    text_y0 = 0

    # 金句页（无照片）：整页深底 + 居中排版
    if arrangement == "none" or tspec.get("bg") == "theme_deep":
        deep_a, deep_b = (bg_top, bg_bot) if sum(bg_top) < 400 else \
            ((47, 42, 79), (66, 51, 94))
        for yy in range(H):
            t = yy / H
            c = tuple(int(a + (b - a) * t) for a, b in zip(deep_a, deep_b))
            dr.line([(0, yy), (W, yy)], fill=c)
        _paint_center_quote(dr, content.get("title", ""),
                            content.get("paragraph", ""), accent)
        img.save(out_path)
        return out_path

    # 照片区
    if ills:
        tf = float(photo.get("top_fraction", 0.57))
        tf = min(0.75, max(0.45, tf))
        split_y = int(H * tf)
        photo_rect = (0, 0, W, split_y)
        cells = _cell_rects(arrangement, photo_rect)
        # 需要张数与可用张数对齐：循环复用
        for i, cell in enumerate(cells):
            ill = ills[i % len(ills)]
            _paste_cell(img, ill, cell, frame, accent)
        dr = ImageDraw.Draw(img)
        text_y0 = split_y
    else:
        text_y0 = 60   # 无插画兜底：纯文字版式

    # 文字区底色
    tbg = tspec.get("bg", "theme_light")
    if tbg == "theme_light":
        dr.rectangle([0, text_y0, W, H], fill=_LIGHT_BG)
    elif tbg == "scrim":
        for yy in range(text_y0, H):
            a = int(200 * (yy - text_y0) / max(1, H - text_y0))
            dr.line([(0, yy), (W, yy)], fill=(12, 10, 18, a))

    # 图上压字标题（封面型）：画在照片区，文字区跳过 title 元素
    title = (content.get("title") or "").strip()
    overlay_first = (elements and elements[0].get("type") == "title"
                     and elements[0].get("style") == "overlay_on_photo")
    if overlay_first and photo_rect:
        _paint_title_overlay(dr, title, photo_rect)

    # banner 装饰（文字区顶部）
    y = text_y0 + 40
    max_w = W - _MARGIN * 2
    if "banner" in (spec.get("decor") or []) and content.get("subtitle"):
        y = _paint_banner(dr, str(content["subtitle"])[:14], y, accent)

    # 元素流
    for el in elements:
        et, esty = el.get("type", ""), el.get("style", "")
        if et == "title":
            if overlay_first:
                continue   # 已画在图上
            if esty == "center":
                y = _paint_title_center(dr, img, title, y, max_w, accent)
            else:
                y = _paint_title_accent_bar(dr, title, y, max_w, accent)
        elif et == "section_title":
            st = content.get("section_title") or ""
            if not st:
                continue
            no = content.get("section_no") or 1
            y = (_paint_section_badge(dr, st, no, y, max_w, accent)
                 if esty == "number_badge" else
                 _paint_section_plain(dr, st, y, max_w, accent))
        elif et == "tip_box":
            y = _paint_tip_box(dr, img, content.get("section_title") or "小贴士",
                               content.get("paragraph", ""), y, H - 60, accent)
            break   # 贴士框即正文终点
        elif et == "paragraph":
            y = _paint_paragraph(dr, content.get("paragraph", ""), y, max_w)
        elif et == "points_col":
            pts = content.get("points") or []
            if pts:
                y = _paint_points_col(dr, pts, y, max_w, accent,
                                      highlight=False)
            else:   # 段落式文案无条目 → 降级 paragraph（0922 文案契约）
                y = _paint_paragraph(dr, content.get("paragraph", ""), y, max_w)
        elif et == "points_highlight":
            pts = content.get("points") or []
            if pts:
                y = _paint_points_col(dr, pts, y, max_w, accent,
                                      highlight=True)
            else:
                y = _paint_paragraph(dr, content.get("paragraph", ""), y, max_w)
        elif et == "capsule":
            if content.get("subtitle"):
                y = _paint_capsule(dr, str(content["subtitle"])[:12], y, accent)
        elif et == "separator":
            y = _paint_separator(dr, y, accent)

    # 角落装饰
    for d in spec.get("decor") or []:
        if d.startswith("sticker_"):
            txt = (content.get("sticker_text")
                   or content.get("section_title") or title)[:4]
            _paint_sticker(dr, d[-2:], txt, accent)
        elif d == "vs_badge" and arrangement == "side_by_side" and photo_rect:
            _paint_vs_badge(dr, photo_rect)

    img.save(out_path)
    return out_path
