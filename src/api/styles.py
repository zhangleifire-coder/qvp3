"""风格关键词库 API：风格 → 关键词/描述词 的可训练映射（"知识训练"落地）。

- 用户在设置页维护或 CSV 批量导入训练数据（style_name,keywords,description）
- 直连路径 style_select / Agent 风格判定读取启用条目；库空回退代码内置库

两级库+偏好闭环（2026-08-31 移植 qvp-dev 8002 已验证方案，迁移 016）：
- owner_id IS NULL = admin 公共库；非空 = 个人库。GET 返回「我的 + 公共」。
- 普通用户只能写/删自己的条目；admin 额外可写/删公共条目（public=true）。
- users.default_style 个人钉选：style_select 最优先直用，POST/DELETE default 维护。
- /api/styles/stats 偏好统计：按人聚合历史任务 gen_image_style 的任务数/审核
  通过率/重生成次数（assets.is_history=true 即被替换的历史版本）。
- 越权写/删 → 403；条目不存在 → 404；变更操作 actor 缺失/未知 → 401。
"""
import csv
import io
import uuid

from fastapi import APIRouter, Form, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, text

from src.db.session import SessionLocal
from src.models.styles import StyleKeyword
from src.services.activity import log_action

router = APIRouter()


async def _actor(session, name: str, required: bool = True):
    """按用户名查 (id, role)；required 时缺失/查不到 → 401，否则返回 (None, None)。"""
    if not name:
        if required:
            raise HTTPException(status_code=401, detail="缺少 actor 参数")
        return None, None
    row = (await session.execute(
        text("SELECT id, role FROM users WHERE name = :n AND active"),
        {"n": name})).first()
    if not row:
        if required:
            raise HTTPException(status_code=401, detail=f"用户不存在: {name}")
        return None, None
    return row[0], row[1]


def _row(r: StyleKeyword, owner_name: str = None) -> dict:
    return {
        "id": str(r.id), "style_name": r.style_name, "keywords": r.keywords,
        "description": r.description, "enabled": r.enabled,
        "owner_id": str(r.owner_id) if r.owner_id else None,
        "owner_name": owner_name,           # NULL owner → None（前端显示「公共」）
        "scope": "public" if r.owner_id is None else "mine",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/api/styles")
async def list_styles(actor: str = ""):
    """风格关键词库：我的条目 + 公共条目（生成时自动匹配的选项来源）。

    actor 可选：有效用户 → 我的+公共；缺省/无效 → 仅公共（浏览不设门槛）。
    """
    async with SessionLocal() as session:
        uid, _ = await _actor(session, actor, required=False)
        cond = (((StyleKeyword.owner_id == uid) | StyleKeyword.owner_id.is_(None))
                if uid is not None else StyleKeyword.owner_id.is_(None))
        rows = list((await session.execute(
            select(StyleKeyword).where(cond)
            .order_by(StyleKeyword.created_at))).scalars().all())
        names = dict((await session.execute(
            text("SELECT id, name FROM users"))).all())
    return {"items": [_row(r, names.get(r.owner_id)) for r in rows]}


class StyleIn(BaseModel):
    style_name: str
    keywords: str = ""
    description: str = ""
    enabled: bool = True
    public: bool = False      # 写入公共库（仅 admin；默认写个人库）


def _scope_query(uid, public: bool):
    """同一 owner 作用域内的同名查询条件（公共=NULL owner）。"""
    if public:
        return StyleKeyword.owner_id.is_(None)
    return StyleKeyword.owner_id == uid


@router.post("/api/styles")
async def upsert_style(payload: StyleIn, actor: str = ""):
    """新增/更新风格条目（同 owner 内同名覆盖更新）。public=true 仅 admin。"""
    name = payload.style_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="风格名不能为空")
    async with SessionLocal() as session:
        uid, role = await _actor(session, actor)
        if payload.public and role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可维护公共风格库")
        row = (await session.execute(
            select(StyleKeyword).where(_scope_query(uid, payload.public),
                                       StyleKeyword.style_name == name))
        ).scalars().first()
        if row:
            row.keywords = payload.keywords.strip()
            row.description = payload.description.strip()
            row.enabled = payload.enabled
        else:
            session.add(StyleKeyword(
                owner_id=None if payload.public else uid,
                style_name=name, keywords=payload.keywords.strip(),
                description=payload.description.strip(),
                enabled=payload.enabled))
        await session.commit()
    await log_action(actor, "style_kb",
                     f"保存{'公共' if payload.public else '个人'}风格「{name}」")
    return {"ok": True, "style_name": name}


@router.delete("/api/styles/default")
async def clear_default_style(actor: str = ""):
    """取消个人默认风格（恢复自动随机选向）。
    注意：必须声明在 /api/styles/{style_id} 之前，否则 "default" 被当 id 解析。"""
    async with SessionLocal() as session:
        uid, _ = await _actor(session, actor)
        await session.execute(
            text("UPDATE users SET default_style = NULL WHERE id = :u"),
            {"u": str(uid)})
        await session.commit()
    await log_action(actor, "style_kb", "取消默认风格")
    return {"ok": True, "default_style": None}


@router.delete("/api/styles/{style_id}")
async def delete_style(style_id: str, actor: str = ""):
    try:
        sid = uuid.UUID(style_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid id")
    async with SessionLocal() as session:
        uid, role = await _actor(session, actor)
        row = (await session.execute(
            select(StyleKeyword).where(StyleKeyword.id == sid))).scalars().first()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if role != "admin":
            if row.owner_id is None:
                raise HTTPException(status_code=403, detail="仅管理员可删除公共风格")
            if row.owner_id != uid:
                raise HTTPException(status_code=403, detail="只能删除自己的风格")
        name = row.style_name
        await session.delete(row)
        await session.commit()
    await log_action(actor, "style_kb", f"删除风格「{name}」")
    return {"ok": True}


@router.post("/api/styles/import")
async def import_styles(file: UploadFile = File(...), actor: str = Form(""),
                        public: bool = Form(False)):
    """CSV 批量导入训练数据：列 style_name,keywords,description（同 owner 内同名
    覆盖）。默认导入操作者个人库；admin 传 public=true 导入公共库。"""
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    imported, errors = 0, []
    async with SessionLocal() as session:
        uid, role = await _actor(session, actor)
        if public and role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可导入公共风格库")
        for row in reader:
            try:
                name = (row.get("style_name") or "").strip()
                if not name:
                    raise ValueError("style_name 为空")
                exist = (await session.execute(
                    select(StyleKeyword).where(_scope_query(uid, public),
                                               StyleKeyword.style_name == name))
                ).scalars().first()
                if exist:
                    exist.keywords = (row.get("keywords") or "").strip()
                    exist.description = (row.get("description") or "").strip()
                else:
                    session.add(StyleKeyword(
                        owner_id=None if public else uid,
                        style_name=name,
                        keywords=(row.get("keywords") or "").strip(),
                        description=(row.get("description") or "").strip()))
                imported += 1
            except Exception as e:  # noqa: BLE001
                errors.append({"row": dict(row), "error": str(e)})
        await session.commit()
    if imported:
        await log_action(actor, "style_kb",
                         f"批量导入{'公共' if public else '个人'}风格关键词 "
                         f"{imported} 条（文件 {file.filename}）")
    return {"imported": imported, "errors": errors}


async def style_library_text() -> str | None:
    """Agent 风格判定注入文本：启用的公共条目（名称+描述词）；库空返回 None（用内置）。"""
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(StyleKeyword).where(StyleKeyword.enabled,
                                       StyleKeyword.owner_id.is_(None))
            .order_by(StyleKeyword.created_at))).scalars().all())
    if not rows:
        return None
    return "\n".join(f"- {r.style_name}：{r.description}" for r in rows)


@router.get("/api/styles/template")
async def styles_template():
    """CSV 导入模板下载。"""
    from fastapi.responses import Response
    csv_text = ("style_name,keywords,description\n"
                "科技蓝调,手机,数码,芯片,参数,深蓝主色调配科技光感、几何线条、数据可视化元素\n"
                "暖木家居,家具,装修,木纹,客厅,暖木色系、自然光、居家生活场景\n")
    return Response(content=csv_text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             "attachment; filename=style_keywords_template.csv"})


class DefaultStyleIn(BaseModel):
    style_name: str


@router.post("/api/styles/default")
async def set_default_style(payload: DefaultStyleIn, actor: str = ""):
    """钉选个人默认风格（users.default_style）：style_select 最优先直接使用，
    跳过随机选向。风格名无需在库中（描述词反查不到时回退正常选择）。"""
    name = payload.style_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="风格名不能为空")
    async with SessionLocal() as session:
        uid, _ = await _actor(session, actor)
        await session.execute(
            text("UPDATE users SET default_style = :s WHERE id = :u"),
            {"s": name, "u": str(uid)})
        await session.commit()
    await log_action(actor, "style_kb", f"钉选默认风格「{name}」")
    return {"ok": True, "default_style": name}


@router.get("/api/styles/lookup")
async def lookup_style(style_name: str, actor: str = ""):
    """按风格名反查描述词（个人→公共→内置），给「存为我的风格」预填表单用。"""
    from src.services.style_select import style_desc_for
    async with SessionLocal() as session:
        uid, _ = await _actor(session, actor, required=False)
    return {"style_name": style_name,
            "description": await style_desc_for(style_name, uid) or ""}


@router.get("/api/styles/stats")
async def style_stats(actor: str):
    """我的风格偏好统计（移植 8002，适配 qvp2 口径）：按历史任务的
    gen_image_style 聚合——任务数、审核通过数/率（tasks.status：approved=通过，
    rejected=驳回，通过率=通过/(通过+驳回)，无已审任务为 None）、重生成次数
    （被替换的 AI 配图历史版本数，assets.is_history=true）。附带当前默认风格。"""
    async with SessionLocal() as session:
        uid, _ = await _actor(session, actor)
        rows = (await session.execute(text(
            "SELECT gen_image_style AS style, count(*) AS total,"
            " count(*) FILTER (WHERE status = 'approved') AS approved,"
            " count(*) FILTER (WHERE status = 'rejected') AS rejected"
            " FROM tasks"
            " WHERE created_by = :u AND gen_image_style IS NOT NULL"
            "   AND gen_image_style <> ''"
            " GROUP BY gen_image_style ORDER BY total DESC"),
            {"u": str(uid)})).all()
        regen = dict((await session.execute(text(
            "SELECT t.gen_image_style, count(*)"
            " FROM assets a JOIN tasks t ON t.id = a.task_id"
            " WHERE t.created_by = :u AND a.source_type = 'ai_generated'"
            "   AND a.is_history = true AND t.gen_image_style IS NOT NULL"
            "   AND t.gen_image_style <> ''"
            " GROUP BY t.gen_image_style"),
            {"u": str(uid)})).all())
        default = (await session.execute(
            text("SELECT default_style FROM users WHERE id = :u"),
            {"u": str(uid)})).scalar()
    items = []
    for style, total, approved, rejected in rows:
        reviewed = approved + rejected
        items.append({
            "style_name": style, "total": total, "approved": approved,
            "rejected": rejected,
            "approval_rate": round(approved / reviewed, 4) if reviewed else None,
            "regen_count": int(regen.get(style, 0)),
        })
    return {"items": items, "default_style": default}
