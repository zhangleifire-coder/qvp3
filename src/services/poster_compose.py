"""海报混合渲染内核（正式模块，2026-09-21 并入主生图链路）。

职责：程序确定性渲染文字版式（字体字形，无伪汉字）+ 中部空区合成 AI 画面。
- 单栏版式（6 页通用）：页标题（文案首行）+ 要点列表（Remix Icon 圆底图标）
  + 中部 AI 画面空区 + 页脚；
- 双阵营版式（对比页）：竖线阵营头 + 双栏图标要点 + 中部画面 + 横线页脚；
- 底色按风格库条目分配：extract_palette(style_desc) 把底色句映射为程序色板，
  改风格库底色=改合成图底色；
- 画面生成：gen_textfree_illustration（文生图/图生图 + 宽松文字-Free 检查，
  VS/箭头/刻度豁免，失败重生一次）。

临时 API（src/api/posters_tmp.py）与本模块共用同一内核。
"""
import hashlib
import json
import textwrap
import uuid
from pathlib import Path

W, H = 1152, 1536          # 与平台 IMAGE_SIZE 一致（3:4）
FONT_DIR = Path("/app/data/fonts")
GENERATED = Path("/app/static/generated")
FONT_R = FONT_DIR / "msyh.ttc"
FONT_B = FONT_DIR / "msyhbd.ttc"
NOTO_R = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
NOTO_B = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
ICON_FONT = FONT_DIR / "remixicon.ttf"
ICON_MAP_F = FONT_DIR / "remixicon_map.json"

FG_TITLE = (248, 246, 250)
FG_SUB = (203, 197, 219)
FG_POINT = (233, 230, 240)
CTA_BG = (232, 131, 58)
GOLD = (219, 190, 140)
VIOLET = (176, 160, 205)

# 底色词 → 程序色板 (bg_top, bg_bot, accent)；风格库条目描述里的底色句在此映射，
# 未命中回退 DEFAULT。延续「背景按风格库分类分配」的决策。
PALETTES = {
    "深靛紫": ((47, 42, 79), (66, 51, 94), GOLD),
    "深藕紫": ((56, 44, 72), (74, 54, 92), GOLD),
    "深炭": ((44, 43, 52), (60, 55, 70), GOLD),
    "炭灰": ((48, 48, 56), (64, 62, 74), GOLD),
    "深褐": ((62, 46, 40), (84, 60, 48), GOLD),
    "深咖": ((58, 44, 38), (80, 58, 46), GOLD),
    "深棕": ((56, 42, 36), (78, 56, 44), GOLD),
    "深墨绿": ((30, 52, 44), (40, 68, 56), (200, 214, 160)),
    "墨绿": ((32, 54, 46), (42, 70, 58), (200, 214, 160)),
    "深藏青": ((28, 42, 66), (38, 56, 86), GOLD),
    "藏青": ((30, 44, 68), (40, 58, 88), GOLD),
    "深海军蓝": ((26, 40, 66), (36, 54, 88), GOLD),
    "黛蓝": ((36, 48, 74), (48, 62, 94), GOLD),
    "黛青": ((30, 52, 58), (40, 68, 76), GOLD),
    "深青": ((28, 50, 56), (38, 66, 74), GOLD),
    "深灰蓝": ((46, 52, 70), (60, 68, 90), GOLD),
    "炭黑": ((40, 40, 46), (54, 52, 62), GOLD),
    "深豆绿": ((38, 56, 44), (50, 72, 56), (214, 200, 150)),
    "深紫": ((52, 40, 74), (68, 52, 94), GOLD),
    "深暖灰": ((58, 52, 54), (76, 66, 68), GOLD),
    "深灰": ((48, 48, 54), (64, 62, 72), GOLD),
    "砖红": ((88, 48, 40), (110, 62, 48), (240, 220, 190)),
}
DEFAULT_PALETTE = ((47, 42, 79), (66, 51, 94), GOLD)

# 文字-Free 检查宽松版提示（VS/箭头/刻度豁免）
_TEXTFREE_PROMPT = (
    "图中是否有可读的汉字、单词或数字字符？"
    "注意：VS 对比字样、箭头、刻度线、虚线、引线、几何制图标记都不算文字。"
    "只回答 有 或 无，不要解释。")


def extract_palette(style_desc: str) -> tuple:
    """底色句 → 程序色板（第一个命中词）。"""
    for word, pal in PALETTES.items():
        if word in (style_desc or ""):
            return pal
    return DEFAULT_PALETTE


_ICON_CACHE: dict | None = None


def _icon_char(name: str) -> str | None:
    global _ICON_CACHE
    if _ICON_CACHE is None:
        _ICON_CACHE = json.loads(ICON_MAP_F.read_text(encoding="utf-8"))
    for cand in (name, name + "-line", name + "-fill",
                 name.replace("-fill", "-line")):
        cp = _ICON_CACHE.get(cand)
        if cp:
            return chr(cp)
    return None


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    if bold and FONT_B.exists():
        fp = FONT_B
    elif FONT_R.exists():
        fp = FONT_R
    elif bold and NOTO_B.exists():
        fp = NOTO_B
    elif NOTO_R.exists():
        fp = NOTO_R
    else:
        raise RuntimeError("poster_default_font_unavailable")
    return ImageFont.truetype(str(fp), size)


def _icon_font(size: int):
    from PIL import ImageFont
    if not ICON_FONT.exists():
        raise RuntimeError("poster_icon_font_unavailable")
    return ImageFont.truetype(str(ICON_FONT), size)


def _fit_size(text, base, bold, max_w, draw):
    size = base
    while size > 24:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_w:
            return size
        size -= 4
    return size


def _paste_cover(img, ill_path, box, radius=28):
    from PIL import Image, ImageDraw
    x, y, bw, bh = box
    ill = Image.open(ill_path).convert("RGB")
    scale = max(bw / ill.width, bh / ill.height)
    nw, nh = int(ill.width * scale), int(ill.height * scale)
    ill = ill.resize((nw, nh), Image.LANCZOS)
    lft, top = (nw - bw) // 2, (nh - bh) // 2
    ill = ill.crop((lft, top, lft + bw, top + bh))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw, bh], radius=radius, fill=255)
    img.paste(ill, (x, y), mask)


def compose_page(title: str, points: list | None = None,
                 illustration: Path | None = None,
                 footer: str = "", style_desc: str = "",
                 camps: tuple | None = None,
                 out_dir: Path = GENERATED) -> Path:
    """渲染单页海报（程序文字 + 中部 AI 画面空区）。

    - camps=None → 单栏版式：页标题 + 图标要点列表；
    - camps=(left, right) → 双阵营版式（left/right 各 {"name","color","points"}）。
    返回输出 PNG 路径。
    """
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    bg_top, bg_bot, accent = extract_palette(style_desc)
    img = Image.new("RGB", (W, H))
    dr = ImageDraw.Draw(img)
    for yy in range(H):
        t = yy / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(bg_top, bg_bot))
        dr.line([(0, yy), (W, yy)], fill=c)

    margin = 76
    max_w = W - margin * 2
    card = tuple(min(255, c + 34) for c in bg_top)

    title = (title or "").strip()
    if not title:
        raise ValueError("title required")
    y = 96
    size = _fit_size(title.replace("\n", ""), 82, True, max_w, dr)
    f_title = _font(size, True)
    for line in title.split("\n"):
        for wrapped in textwrap.wrap(line, width=max(6, int(max_w / size * 1.7))) or [""]:
            w = dr.textlength(wrapped, font=f_title)
            dr.text(((W - w) / 2, y), wrapped, font=f_title, fill=FG_TITLE)
            y += int(size * 1.35)
    y += 16
    # 双色短横线装饰
    seg, gap = 72, 16
    x0 = (W - (seg * 2 + gap)) / 2
    dr.rounded_rectangle([x0, y, x0 + seg, y + 8], radius=4, fill=accent)
    dr.rounded_rectangle([x0 + seg + gap, y, x0 + seg * 2 + gap, y + 8],
                         radius=4, fill=VIOLET)
    y += 44

    if camps:
        y = _render_camps(dr, camps, margin, y)
    else:
        y = _render_points(dr, points or [], margin, y, accent)

    gap_top = y + 10
    gap_bot = H - 150
    if illustration and illustration.exists():
        gh = gap_bot - gap_top
        if gh > 200:
            _paste_cover(img, illustration, (margin, gap_top, max_w, gh))

    if footer:
        f_ft = _font(38, False)
        tw = dr.textlength(footer, font=f_ft)
        seg2 = 84
        total = seg2 * 2 + 80 + tw
        x0 = (W - total) / 2
        fy = H - 108
        ly = fy + 21
        dr.line([x0, ly, x0 + seg2, ly], fill=(150, 142, 168), width=2)
        dr.text((x0 + seg2 + 40, fy), footer, font=f_ft, fill=accent)
        dr.line([x0 + seg2 + 40 + tw + 40, ly, x0 + total, ly],
                fill=(150, 142, 168), width=2)

    out = out_dir / f"compose_{uuid.uuid4().hex[:10]}.png"
    img.save(out, "PNG")
    return out


def _render_points(dr, points, margin, y, accent):
    """单栏要点：竖线 accent 条 + Remix Icon 圆底图标 + 短词。"""
    f_pt = _font(34, False)
    row_h = 68
    d = 48
    for i, pt in enumerate(points[:5]):
        if isinstance(pt, str):
            icon, text = "", pt
        else:
            icon, text = pt.get("icon", ""), pt.get("text", "")
        cy = y + i * row_h
        dr.rounded_rectangle([margin, cy + 4, margin + 8, cy + 44],
                             radius=4, fill=accent)
        dr.ellipse([margin + 22, cy, margin + 22 + d, cy + d], fill=accent)
        ch = _icon_char(icon)
        if ch:
            dr.text((margin + 22 + d / 2, cy + d / 2), ch,
                    font=_icon_font(26), fill=(30, 28, 26) if accent == GOLD
                    else (255, 255, 255), anchor="mm")
        dr.text((margin + 22 + d + 18, cy + 6), text, font=f_pt, fill=FG_POINT)
    return y + min(len(points), 5) * row_h + 12


def _render_camps(dr, camps, margin, y):
    """双阵营：竖线色条+阵营名 + 双栏圆底图标要点。"""
    col_w = (W - margin * 2 - 48) / 2
    for camp, color, x in camps:
        name = (camp.get("name") or "").strip()
        f_cap = _font(42, True)
        dr.rounded_rectangle([x, y + 4, x + 10, y + 50], radius=5, fill=color)
        dr.text((x + 28, y), name, font=f_cap, fill=FG_TITLE)
    y += 74
    f_pt = _font(32, False)
    row_h = 66
    d = 46
    sides = []
    for camp, color, x in camps:
        pts = camp.get("points") or []
        norm = [p if isinstance(p, str) else
                {"icon": p.get("icon", ""), "text": p.get("text", "")}
                for p in pts]
        sides.append((norm, color, x))
    max_pts = max(len(s[0]) for s in sides) if sides else 0
    for i in range(max_pts):
        for pts, color, x in sides:
            if i >= len(pts):
                continue
            cy = y + i * row_h
            dr.ellipse([x, cy, x + d, cy + d], fill=color)
            ch = _icon_char(pts[i]["icon"])
            if ch:
                dr.text((x + d / 2, cy + d / 2), ch, font=_icon_font(24),
                        fill=(255, 255, 255), anchor="mm")
            dr.text((x + d + 16, cy + 6), pts[i]["text"], font=f_pt, fill=FG_POINT)
    return y + max_pts * row_h + 10


async def gen_textfree_illustration(prompt: str, style_desc: str = "",
                                    ref_urls: list | None = None,
                                    size: str = "1536x1024") -> Path | None:
    """生成无文字 AI 画面：生图 → 宽松文字-Free 检查（VS/刻度豁免）
    → 不过则重生一次；仍不过返回 None（调用方决定占位或重试）。
    风格画面句（文字类句子过滤后）与背景保持自动注入。
    """
    from src.gateway.image_gen import generate_image
    from src.gateway.ocr import fetch_image_bytes

    # 风格画面句注入（剔除标题/排版类文字句，防出字）
    tail = ""
    if style_desc:
        drop = ("标题", "文字", "字体", "黑体", "字号", "留白", "排版",
                "分栏", "标签", "结论", "胶囊", "逐字")
        segs = [s for s in style_desc.replace("；", "，").split("，")
                if s.strip() and not any(k in s for k in drop)]
        if segs:
            tail = "。画面风格：" + "，".join(segs[:6])
    no_text = ("，纯画面、无排版、无标题、无文字、无字母、无数字、"
               "无符号、无水印、无界面元素")

    async def _once(p_text):
        try:
            r = await generate_image(p_text, size=size,
                                     reference_image_urls=ref_urls or None)
            data, _ = await fetch_image_bytes(r["image_url"])
        except Exception:
            return None
        fp = GENERATED / f"compose_ill_{uuid.uuid4().hex[:10]}.png"
        fp.write_bytes(data)
        return fp

    async def _text_free(fp: Path) -> bool:
        try:
            from src.config import settings
            from src.gateway.ocr import _image_to_data_url
            from src.gateway.http_client import get_client
            data_url = await _image_to_data_url(
                f"/static/generated/{fp.name}")
            resp = await get_client("visual_check", timeout=60).post(
                f"{settings.ocr_base_url}/chat/completions",
                headers={"Authorization":
                         f"Bearer {settings.dashscope_api_key}"},
                json={"model": settings.visual_check_model,
                      "messages": [{"role": "user", "content": [
                          {"type": "image_url", "image_url": {"url": data_url}},
                          {"type": "text", "text": _TEXTFREE_PROMPT}]}],
                      "max_tokens": 20})
            if resp.status_code != 200:
                return True
            txt = resp.json()["choices"][0]["message"]["content"].strip()
            return "无" in txt[:6] and "有" not in txt[:4]
        except Exception:
            return True

    full = prompt + tail + no_text
    fp = await _once(full)
    if fp and await _text_free(fp):
        return fp
    fp2 = await _once(full)   # 重生一次
    if fp2 and await _text_free(fp2):
        return fp2
    return fp2 or fp          # 两次都不过：返回最后产物由调用方决定


def split_title_points(body: str) -> tuple[str, list[str]]:
    """分页文案 → (标题, 要点列表)：首行=标题，其余行=要点（长行截断）。"""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    if not lines:
        return "", []
    title = lines[0]
    points = []
    for ln in lines[1:]:
        for seg in textwrap.wrap(ln, width=20) or [ln]:
            points.append(seg[:24])
    return title, points[:5]
