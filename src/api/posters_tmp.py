"""临时海报 API v3（2026-09-20，验证用）：确定性文字 + AI 画面 + 图标库 + 示意图层。

v3 相对 v2：
- 要点图标库（Remix Icon 字体，2850 个）：points 支持 {"icon","text"}，
  圆形色底 + 白色线性图标（替代序号数字）；
- 去文字胶囊：阵营头改为「竖线色条+阵营名」，CTA pill 不再绘制；
- 示意图层：illustration 端点支持 diagram_prompt（可选），主图+示意图
  上下排列同过文字-Free 检查后合成；
- 参考风格关键词库：style_name 指定风格（默认杂志风产品对比卡），
  风格描述自动注入 AI 插图 prompt（背景/质感与程序深底色协调）。

验证完可整文件删除（main.py 里那行挂载一并删）。
"""
import json
import textwrap
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

W, H = 1080, 1440
FONT_DIR = Path("/app/data/fonts")
GENERATED = Path("/app/static/generated")
FONT_R = FONT_DIR / "msyh.ttc"
FONT_B = FONT_DIR / "msyhbd.ttc"
# 版权字体仅本地开发可用；容器分发走系统 Noto Sans CJK（OFL，apt 安装）
NOTO_R = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
NOTO_B = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
ICON_FONT = FONT_DIR / "remixicon.ttf"
ICON_MAP = FONT_DIR / "remixicon_map.json"

# 底色：深而不黑的深靛紫（避免近黑；金/紫阵营色在其上对比充足）
BG_TOP = (47, 42, 79)
BG_BOT = (66, 51, 94)
FG_TITLE = (248, 246, 250)
FG_SUB = (203, 197, 219)
FG_POINT = (233, 230, 240)
CARD_BG = (84, 74, 116)
LINE_SOFT = (159, 148, 185)
FOOTER_GOLD = (219, 190, 140)


def _hex(c: str, default: tuple) -> tuple:
    try:
        c = c.lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:  # noqa: BLE001
        return default


class PtIn(BaseModel):
    icon: str = ""    # remixicon 名（如 "fire-fill"）；空=纯文字
    text: str


class CampIn(BaseModel):
    name: str
    color: str = ""
    points: list[PtIn | str] = []   # 兼容纯字符串（无图标）


class PosterIn(BaseModel):
    title: str
    subtitle: str = ""
    tags: list[str] = []
    left: CampIn | None = None
    right: CampIn | None = None
    footer: str = ""
    style_name: str = "杂志风产品对比卡"   # 风格关键词库条目（插图 prompt 注入其描述）
    # 旧版式兼容字段
    body: str = ""
    cta: str = ""


class IllustrationIn(BaseModel):
    prompt: str
    diagram_prompt: str = ""   # 可选：示意图层（放主图下方）


_ICON_CACHE: dict = {}


def _icon_font(size: int):
    from PIL import ImageFont
    if not ICON_FONT.exists():
        raise HTTPException(422, "poster_icon_font_unavailable")
    return ImageFont.truetype(str(ICON_FONT), size)


def _icon_char(name: str) -> str | None:
    """remixicon 图标名 → unicode 字符；自动补 -line 优先线性版。"""
    if not _ICON_CACHE:
        _ICON_CACHE.update(json.loads(ICON_MAP.read_text(encoding="utf-8")))
    for cand in (name, name.replace("-fill", "-line"),
                 name + "-line", name + "-fill"):
        cp = _ICON_CACHE.get(cand)
        if cp:
            return chr(cp)
    return None


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    # 优先级：本地微软雅黑（开发）> 系统 Noto Sans CJK（容器分发，OFL）
    if bold and FONT_B.exists():
        fp = FONT_B
    elif FONT_R.exists():
        fp = FONT_R
    elif bold and NOTO_B.exists():
        fp = NOTO_B
    elif NOTO_R.exists():
        fp = NOTO_R
    else:
        raise HTTPException(422, "poster_default_font_unavailable")
    return ImageFont.truetype(str(fp), size)


def _fit_size(text: str, base: int, bold: bool, max_w: int, draw) -> int:
    size = base
    while size > 24:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_w:
            return size
        size -= 4
    return size


def _paste_cover(img, ill_path: Path, box: tuple, radius: int = 28):
    """把图 cover-裁切圆角贴入 box=(x, y, w, h)。"""
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


def render_poster(p: PosterIn, illustration: Path | None = None,
                  diagram: Path | None = None) -> Path:
    from PIL import Image, ImageDraw

    GENERATED.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H))
    dr = ImageDraw.Draw(img)
    for yy in range(H):
        t = yy / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT))
        dr.line([(0, yy), (W, yy)], fill=c)

    margin = 72
    max_w = W - margin * 2
    gold = _hex(p.left.color if p.left else "", (201, 169, 106))
    violet = _hex(p.right.color if p.right else "", (139, 124, 166))

    if p.left and p.right:
        y = _render_compare_header(dr, p, margin, max_w, gold, violet)
        y = _render_camps(dr, p, margin, y, gold, violet)
        gap_top = y + 8
    else:
        y = _render_simple_header(dr, p, margin, max_w)
        body = (p.body or "").strip()
        if body:
            f_body = _font(34, False)
            lines = textwrap.wrap(body, width=26) or [body]
            lh, pad = 52, 36
            ch = lh * len(lines) + pad * 2
            dr.rounded_rectangle([margin - 16, y, W - margin + 16, y + ch],
                                 radius=24, fill=CARD_BG)
            ty = y + pad
            for ln in lines:
                dr.text((margin, ty), ln, font=f_body, fill=FG_POINT)
                ty += lh
            y += ch + 32
        gap_top = y + 8

    gap_bot = H - 150   # v3 无 CTA 胶囊，页脚占底部

    if illustration and illustration.exists():
        gap_h = gap_bot - gap_top
        if diagram and diagram.exists():
            main_h = int(gap_h * 0.62)
            dia_h = gap_h - main_h - 16
            _paste_cover(img, illustration, (margin, gap_top, max_w, main_h))
            _paste_cover(img, diagram,
                         (margin, gap_top + main_h + 16, max_w, dia_h), radius=20)
        else:
            _paste_cover(img, illustration, (margin, gap_top, max_w, gap_h))

    footer = (p.footer or p.cta or "").strip()
    if footer:
        f_ft = _font(40, False)
        tw = dr.textlength(footer, font=f_ft)
        seg = 90
        total = seg + 40 + tw + 40 + seg
        x0 = (W - total) / 2
        fy = H - 110
        ly = fy + 22
        dr.line([x0, ly, x0 + seg, ly], fill=LINE_SOFT, width=2)
        dr.text((x0 + seg + 40, fy), footer, font=f_ft, fill=FOOTER_GOLD)
        dr.line([x0 + seg + 40 + tw + 40, ly, x0 + total, ly],
                fill=LINE_SOFT, width=2)

    out = GENERATED / f"poster_{uuid.uuid4().hex[:10]}.png"
    img.save(out, "PNG")
    return out


def _render_compare_header(dr, p, margin, max_w, gold, violet) -> int:
    title = (p.title or "").strip()
    if not title:
        raise HTTPException(422, "poster_title_required")
    y = 100
    size = _fit_size(title.replace("\n", ""), 80, True, max_w, dr)
    f_title = _font(size, True)
    for line in title.split("\n"):
        for wrapped in textwrap.wrap(line, width=max(6, int(max_w / size * 1.7))) or [""]:
            w = dr.textlength(wrapped, font=f_title)
            dr.text(((W - w) / 2, y), wrapped, font=f_title, fill=FG_TITLE)
            y += int(size * 1.35)
    y += 18
    seg_w, gap = 70, 16
    total = seg_w * 2 + gap
    x0 = (W - total) / 2
    dr.rounded_rectangle([x0, y, x0 + seg_w, y + 8], radius=4, fill=gold)
    dr.rounded_rectangle([x0 + seg_w + gap, y, x0 + total, y + 8],
                         radius=4, fill=violet)
    y += 40

    sub = (p.subtitle or "").strip()
    if sub:
        f_sub = _font(38, False)
        for wrapped in textwrap.wrap(sub, width=26) or [sub]:
            w = dr.textlength(wrapped, font=f_sub)
            dr.text(((W - w) / 2, y), wrapped, font=f_sub, fill=FG_SUB)
            y += 54
        y += 12

    for tag in (p.tags or [])[:3]:
        f_tag = _font(34, False)
        bar_w = 8
        dr.rounded_rectangle([margin, y, margin + bar_w, y + 40],
                             radius=4, fill=gold)
        dr.text((margin + bar_w + 18, y + 2), tag, font=f_tag, fill=FG_SUB)
        y += 62
    return y + 14


def _render_camps(dr, p, margin, y, gold, violet) -> int:
    """v3 阵营头：竖线色条+阵营名（无文字胶囊）；要点：圆底白色线性图标。"""
    col_w = (W - margin * 2 - 48) / 2
    camps = [(p.left, gold, margin), (p.right, violet, margin + col_w + 48)]

    for camp, color, x in camps:
        name = (camp.name or "").strip()
        if not name:
            continue
        f_cap = _font(44, True)
        dr.rounded_rectangle([x, y + 4, x + 10, y + 52], radius=5, fill=color)
        dr.text((x + 28, y), name, font=f_cap, fill=FG_TITLE)
    y += 76

    def _norm(pts):
        out = []
        for it in pts or []:
            if isinstance(it, str):
                out.append({"icon": "", "text": it})
            else:
                out.append({"icon": it.icon, "text": it.text})
        return out

    l_pts = _norm(p.left.points)
    r_pts = _norm(p.right.points)
    max_pts = max(len(l_pts), len(r_pts), 1)
    row_h = 68
    d = 48
    f_pt = _font(34, False)
    for i in range(max_pts):
        for pts, color, x in ((l_pts, gold, margin), (r_pts, violet, margin + col_w + 48)):
            if i >= len(pts):
                continue
            cy = y + i * row_h
            dr.ellipse([x, cy, x + d, cy + d], fill=color)
            icon = _icon_char(pts[i]["icon"])
            if icon:
                f_ic = _icon_font(26)
                dr.text((x + d / 2, cy + d / 2), icon, font=f_ic,
                        fill=(255, 255, 255), anchor="mm")
            else:
                no = str(i + 1)
                f_no = _font(26, True)
                nw = dr.textlength(no, font=f_no)
                dr.text((x + (d - nw) / 2, cy + (d - 30) / 2 - 3), no,
                        font=f_no, fill=(255, 255, 255))
            dr.text((x + d + 20, cy + 6), pts[i]["text"], font=f_pt, fill=FG_POINT)
    return y + max_pts * row_h + 10


def _render_simple_header(dr, p, margin, max_w) -> int:
    title = (p.title or "").strip()
    if not title:
        raise HTTPException(422, "poster_title_required")
    y = 150
    size = _fit_size(title.replace("\n", ""), 84, True, max_w, dr)
    f_title = _font(size, True)
    for line in title.split("\n"):
        for wrapped in textwrap.wrap(line, width=max(6, int(max_w / size * 1.7))) or [""]:
            w = dr.textlength(wrapped, font=f_title)
            dr.text(((W - w) / 2, y), wrapped, font=f_title, fill=FG_TITLE)
            y += int(size * 1.35)
    y += 24
    sub = (p.subtitle or "").strip()
    if sub:
        f_sub = _font(40, False)
        for wrapped in textwrap.wrap(sub, width=24) or [sub]:
            w = dr.textlength(wrapped, font=f_sub)
            dr.text(((W - w) / 2, y), wrapped, font=f_sub, fill=FG_SUB)
            y += 56
        y += 16
    return y


def _save_meta(poster_id: str, p: PosterIn):
    (GENERATED / f"{poster_id}.json").write_text(
        p.model_dump_json(), encoding="utf-8")


def _load_meta(poster_id: str) -> PosterIn:
    f = GENERATED / f"{poster_id}.json"
    if not f.exists():
        raise HTTPException(422, "poster_not_found")
    return PosterIn(**json.loads(f.read_text(encoding="utf-8")))


@router.post("/api/posters")
async def create_poster(p: PosterIn):
    out = render_poster(p)
    _save_meta(out.stem, p)
    return {
        "ok": True,
        "summary": "海报已创建：succeeded",
        "payload": {
            "poster_id": out.stem,
            "title": p.title,
            "texts": p.model_dump(),
            "status": "succeeded",
            "image_url": f"/static/generated/{out.name}",
            "format": "png",
        },
        "blocking": [],
    }


@router.get("/api/posters/{poster_id}")
async def poster_detail(poster_id: str):
    f = GENERATED / f"{poster_id}.png"
    if not f.exists():
        raise HTTPException(422, "poster_not_found")
    return {"ok": True,
            "payload": {"poster_id": poster_id, "status": "succeeded",
                        "image_url": f"/static/generated/{f.name}",
                        "format": "png"}}


@router.post("/api/posters/{poster_id}/illustration")
async def add_illustration(poster_id: str, payload: IllustrationIn):
    """AI 配图链 v3：主图(+示意图) → 各自文字-Free 检查 → 合成重渲染。

    prompt 自动注入风格关键词库（style_name）的画面描述，插图背景/质感
    与程序深底色协调。
    """
    meta = _load_meta(poster_id)
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(422, "poster_no_fields")

    # 风格关键词库注入（参考风格库生图文）
    style_desc = ""
    try:
        from src.services.style_select import style_desc_for
        style_desc = (await style_desc_for(
            (meta.style_name or "杂志风产品对比卡").strip(), None) or "")
    except Exception:  # noqa: BLE001
        pass
    # 风格库描述是「整页海报」口径（含标题/排版指令）——插图只取画面质感
    # 部分：剔除文字/排版类句子，保留背景/光影/质感/构图，避免诱导出字。
    style_tail = ""
    if style_desc:
        _DROP = ("标题", "文字", "字体", "黑体", "字号", "留白", "排版",
                 "分栏", "标签", "结论", "胶囊", "栏目标签", "逐字")
        kept = [seg for seg in style_desc.replace("；", "，").split("，")
                if seg.strip() and not any(k in seg for k in _DROP)]
        if kept:
            style_tail = "。画面风格：" + "，".join(kept[:6])
    no_text = ("，纯场景画面、无排版、无标题、无文字、无字母、无数字、"
               "无符号、无水印、无界面元素")

    from src.gateway.image_gen import generate_image
    from src.gateway.ocr import fetch_image_bytes

    async def _gen(p_text: str) -> tuple[Path | None, str | None]:
        try:
            r = await generate_image(p_text, size="1536x1024")
            data, _ = await fetch_image_bytes(r["image_url"])
        except Exception as e:  # noqa: BLE001
            return None, str(e)[:200]
        fp = GENERATED / f"poster_ill_{uuid.uuid4().hex[:10]}.png"
        fp.write_bytes(data)
        return fp, None

    ill, err = await _gen(prompt + style_tail + no_text)
    if not ill:
        return {"ok": False, "summary": "配图生成失败",
                "blocking": [{"code": "media_unavailable", "message": err}]}

    dia = None
    dprompt = (payload.diagram_prompt or "").strip()
    if dprompt:
        dia, err = await _gen(dprompt + style_tail + no_text)
        if not dia:
            dia = None   # 示意图失败不阻塞主图

    # 文字-Free 检查：主图严格（任何文字符号都拦）；
    # 示意图宽松（刻度线/箭头/引线等制图标记不算文字）。
    if not await _check_text_free(f"/static/generated/{ill.name}", strict=False):
        return _blocked(poster_id, ill)
    if dia and not await _check_text_free(f"/static/generated/{dia.name}",
                                          strict=False):
        return _blocked(poster_id, dia)

    out = render_poster(meta, illustration=ill, diagram=dia)
    _save_meta(out.stem, meta)
    return {
        "ok": True,
        "summary": "配图已合成：succeeded",
        "payload": {
            "poster_id": out.stem,
            "status": "succeeded",
            "source_poster_id": poster_id,
            "illustration_url": f"/static/generated/{ill.name}",
            "diagram_url": (f"/static/generated/{dia.name}" if dia else ""),
            "image_url": f"/static/generated/{out.name}",
            "format": "png",
        },
        "blocking": [],
    }


def _blocked(poster_id: str, ill_path: Path) -> dict:
    return {
        "ok": False,
        "summary": "配图含文字，已拦截（未合成）",
        "payload": {"poster_id": poster_id, "status": "blocked",
                    "illustration_url": f"/static/generated/{ill_path.name}"},
        "blocking": [{"code": "generated_element_text_present",
                      "message": "VLM 发现图中存在文字/符号"}],
    }


async def _check_text_free(image_url: str, strict: bool = True) -> bool:
    try:
        from src.config import settings
        from src.gateway.ocr import _image_to_data_url
        from src.gateway.http_client import get_client
        data_url = await _image_to_data_url(image_url)
        resp = await get_client("visual_check", timeout=60).post(
            f"{settings.ocr_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json={
                "model": settings.visual_check_model,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text":
                     ("图中是否存在任何可读文字、字母、数字、汉字或符号？"
                      "只回答 有 或 无，不要解释。" if strict else
                      "图中是否有可读的汉字、单词或数字字符？"
                      "注意：VS 对比字样、箭头、刻度线、虚线、引线、几何制图标记都不算文字。"
                      "只回答 有 或 无，不要解释。")},
                ]}],
                "max_tokens": 20,
            })
        if resp.status_code != 200:
            return True
        txt = resp.json()["choices"][0]["message"]["content"].strip()
        return "无" in txt[:6] and "有" not in txt[:4]
    except Exception:  # noqa: BLE001
        return True
