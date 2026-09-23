"""页型模板库管理 API（2026-09-24 P2）。

- GET  /api/compose-templates：列表（?enabled=&source=）
- POST /api/compose-templates：新建（manual 或 vl_extracted，spec 校验）
- PATCH /api/compose-templates/{template_id}：改名/spec/启停（seed_* 模板
  名称/启停可改，spec 改动应走仓库 JSON——返回 409 提示）
- POST /api/compose-templates/preview：传 spec（或 template_id）→ 用样例
  内容+占位插画渲染 PNG 返回路径
- POST /api/compose-templates/extract：传 image_url → VL 提取 spec 草稿
  （不入库；前端确认后走 POST 入库）
"""
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from src.db.session import SessionLocal
from src.services.compose_templates import (
    list_templates, validate_spec, load_seeds)
from src.services.template_extract import extract_template

router = APIRouter()

# 预览样例内容（各字段齐全，渲染器自动按模板元素流取用）
_SAMPLE_CONTENT = {
    "title": "冷淡风更百搭，通勤首选小圆环",
    "section_title": "冷感耳环：通勤主力",
    "section_no": 2,
    "paragraph": ("上班戴得多的是冷感耳环。银色、枪色、哑光钛钢用线条和几何"
                  "撑气质，直径2到3厘米小圆环，单只别超5克。"),
    "points": ["直径2-3厘米最稳妥", "单只不超5克", "耳针认准925银"],
    "subtitle": "通勤优先小圆环",
    "sticker_text": "通勤首选",
}


def _placeholder_ills(n: int) -> list[Path]:
    """占位插画：static/assets 下任意既有图片（无则纯色 PNG 现造）。"""
    import tempfile
    from PIL import Image, ImageDraw
    out = []
    for i in range(max(1, n)):
        p = Path(tempfile.gettempdir()) / f"compose_ph_{i}.png"
        if not p.exists():
            im = Image.new("RGB", (1024, 1024),
                           ((210, 190, 170) if i % 2 == 0
                            else (176, 196, 186)))
            d = ImageDraw.Draw(im)
            d.ellipse([256, 256, 768, 768], outline=(120, 100, 90), width=8)
            im.save(p)
        out.append(p)
    return out


class TemplateIn(BaseModel):
    template_id: str
    name: str
    spec: dict
    notes: str = ""


class PreviewIn(BaseModel):
    spec: dict | None = None
    template_id: str | None = None


class ExtractIn(BaseModel):
    image_url: str
    template_id: str = ""
    name: str = ""


@router.get("/api/compose-templates")
async def get_templates(enabled: bool = False, source: str = None):
    rows = await list_templates(enabled_only=enabled, source=source)
    out = []
    for r in rows:
        spec = r.get("spec")
        if isinstance(spec, str):
            spec = json.loads(spec)
        out.append({"template_id": r["template_id"], "name": r["name"],
                    "page_role": r.get("page_role") or
                    (spec or {}).get("page_role", "content"),
                    "tags": r.get("tags") or (spec or {}).get("tags") or [],
                    "source": r.get("source", "seed_11"),
                    "enabled": bool(r.get("enabled")),
                    "notes": r.get("notes", "") or (spec or {}).get("notes", ""),
                    "spec": spec})
    return {"templates": out}


@router.post("/api/compose-templates")
async def create_template(body: TemplateIn):
    errs = validate_spec(body.spec)
    if errs:
        raise HTTPException(status_code=422, detail="spec 非法：" + "；".join(errs))
    async with SessionLocal() as session:
        exists = await session.execute(text(
            "SELECT 1 FROM compose_templates WHERE template_id=:t"),
            {"t": body.template_id})
        if exists.first():
            raise HTTPException(status_code=409, detail="template_id 已存在")
        await session.execute(text("""
            INSERT INTO compose_templates
              (template_id, name, spec, page_role, tags, source, enabled,
               notes, version)
            VALUES (:tid, :name, CAST(:spec AS jsonb), :role,
                    CAST(:tags AS text[]), :src, false, :notes, 1)
        """), {"tid": body.template_id, "name": body.name,
               "spec": json.dumps(body.spec, ensure_ascii=False),
               "role": body.spec.get("page_role", "content"),
               "tags": body.spec.get("tags") or ["通用"],
               "src": body.spec.get("source", "manual"),
               "notes": body.notes})
        await session.commit()
    return {"ok": True}


@router.patch("/api/compose-templates/{template_id}")
async def patch_template(template_id: str, body: dict):
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT source, enabled FROM compose_templates"
            " WHERE template_id=:t"), {"t": template_id})).first()
        if not row:
            raise HTTPException(status_code=404, detail="模板不存在")
        if "spec" in body and row[0] == "seed_11":
            raise HTTPException(
                status_code=409,
                detail="预置模板 spec 以仓库 data/compose_templates.json 为"
                       "单一事实源，请改仓库文件后重启（sync 幂等同步）")
        sets, params = [], {"t": template_id}
        if "name" in body:
            sets.append("name=:name"); params["name"] = str(body["name"])[:60]
        if "enabled" in body:
            sets.append("enabled=:en"); params["en"] = bool(body["enabled"])
        if "notes" in body:
            sets.append("notes=:no"); params["no"] = str(body["notes"])[:200]
        if not sets:
            raise HTTPException(status_code=422, detail="无可更新字段")
        sets.append("updated_at=now()")
        await session.execute(text(
            f"UPDATE compose_templates SET {', '.join(sets)}"
            " WHERE template_id=:t"), params)
        await session.commit()
    return {"ok": True}


@router.post("/api/compose-templates/preview")
async def preview_template(body: PreviewIn):
    from src.services.compose_renderer import render_card
    spec = body.spec
    if not spec and body.template_id:
        for t in await list_templates():
            if t["template_id"] == body.template_id:
                spec = t["spec"]
                if isinstance(spec, str):
                    spec = json.loads(spec)
                break
    if not spec:
        raise HTTPException(status_code=422, detail="缺 spec 或 template_id")
    errs = validate_spec(spec)
    if errs:
        raise HTTPException(status_code=422, detail="spec 非法：" + "；".join(errs))
    arr = (spec.get("photo") or {}).get("arrangement", "single")
    n_map = {"single": 1, "side_by_side": 2, "grid_2x2": 4,
             "triple_row": 3, "one_big_two_small": 3, "none": 0}
    out = render_card(spec, _SAMPLE_CONTENT,
                      _placeholder_ills(n_map.get(arr, 1)), style_desc="")
    return {"ok": True, "preview_url": f"/static/generated/{out.name}"}


@router.post("/api/compose-templates/extract")
async def extract(body: ExtractIn):
    tid = body.template_id or f"ext_{uuid.uuid4().hex[:10]}"
    name = body.name or f"提取·{tid[-6:]}"
    result = await extract_template(body.image_url, tid, name)
    if not result:
        raise HTTPException(status_code=502, detail="VL 分析失败（看服务端日志）")
    return result
