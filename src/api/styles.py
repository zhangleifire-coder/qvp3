"""风格关键词库 API：风格 → 关键词/描述词 的可训练映射（"知识训练"落地）。

- 用户在设置页维护或 CSV 批量导入训练数据（style_name,keywords,description）
- agent_production 风格判定时读取启用的条目注入提示词：Agent 按 query/检索
  信息命中关键词自动匹配最优风格（替代内置 8 风格；库空则回退内置）
"""
import csv
import io
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.styles import StyleKeyword
from src.services.activity import log_action

router = APIRouter()


@router.get("/api/styles")
async def list_styles():
    """风格关键词库（生成时的自动匹配选项来源）。"""
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(StyleKeyword).order_by(StyleKeyword.created_at))).scalars().all())
        return {"items": [{
            "id": str(r.id), "style_name": r.style_name, "keywords": r.keywords,
            "description": r.description, "enabled": r.enabled,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]}


class StyleIn(BaseModel):
    style_name: str
    keywords: str = ""
    description: str = ""
    enabled: bool = True


@router.post("/api/styles")
async def upsert_style(payload: StyleIn, actor: str = "anonymous"):
    """新增/更新风格条目（同名覆盖更新）。"""
    name = payload.style_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="风格名不能为空")
    async with SessionLocal() as session:
        row = (await session.execute(
            select(StyleKeyword).where(StyleKeyword.style_name == name))).scalars().first()
        if row:
            row.keywords = payload.keywords.strip()
            row.description = payload.description.strip()
            row.enabled = payload.enabled
        else:
            session.add(StyleKeyword(style_name=name, keywords=payload.keywords.strip(),
                                     description=payload.description.strip(),
                                     enabled=payload.enabled))
        await session.commit()
    await log_action(actor, "style_kb", f"保存风格「{name}」")
    return {"ok": True, "style_name": name}


@router.delete("/api/styles/{style_id}")
async def delete_style(style_id: str, actor: str = "anonymous"):
    try:
        sid = uuid.UUID(style_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid id")
    async with SessionLocal() as session:
        row = (await session.execute(
            select(StyleKeyword).where(StyleKeyword.id == sid))).scalars().first()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        name = row.style_name
        await session.delete(row)
        await session.commit()
    await log_action(actor, "style_kb", f"删除风格「{name}」")
    return {"ok": True}


@router.post("/api/styles/import")
async def import_styles(file: UploadFile = File(...), actor: str = "anonymous"):
    """CSV 批量导入训练数据：列 style_name,keywords,description（幂等同名覆盖）。"""
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    imported, errors = 0, []
    async with SessionLocal() as session:
        for row in reader:
            try:
                name = (row.get("style_name") or "").strip()
                if not name:
                    raise ValueError("style_name 为空")
                exist = (await session.execute(
                    select(StyleKeyword).where(
                        StyleKeyword.style_name == name))).scalars().first()
                if exist:
                    exist.keywords = (row.get("keywords") or "").strip()
                    exist.description = (row.get("description") or "").strip()
                else:
                    session.add(StyleKeyword(
                        style_name=name,
                        keywords=(row.get("keywords") or "").strip(),
                        description=(row.get("description") or "").strip()))
                imported += 1
            except Exception as e:  # noqa: BLE001
                errors.append({"row": dict(row), "error": str(e)})
        await session.commit()
    if imported:
        await log_action(actor, "style_kb",
                         f"批量导入风格关键词 {imported} 条（文件 {file.filename}）")
    return {"imported": imported, "errors": errors}


async def style_library_text() -> str | None:
    """Agent 风格判定注入文本：启用条目（名称+描述词）；库空返回 None（用内置）。"""
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(StyleKeyword).where(StyleKeyword.enabled)
            .order_by(StyleKeyword.created_at))).scalars().all())
    if not rows:
        return None
    return "\n".join(f"- {r.style_name}：{r.description}" for r in rows)


@router.get("/api/styles/template")
async def styles_template():
    """CSV 导入模板下载。"""
    from fastapi.responses import Response
    csv = ("style_name,keywords,description\n"
           "科技蓝调,手机,数码,芯片,参数,深蓝主色调配科技光感、几何线条、数据可视化元素\n"
           "暖木家居,家具,装修,木纹,客厅,暖木色系、自然光、居家生活场景\n")
    return Response(content=csv, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             "attachment; filename=style_keywords_template.csv"})
