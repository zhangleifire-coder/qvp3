"""提示词库接口：系统默认（代码内置）+ 用户自定义（prompt_templates 表）。

权限模型与全站一致：请求带 actor（用户名），服务端查库校验。
- 普通用户：管理自己的自定义提示词；
- admin：可见/可管所有人的自定义提示词。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

from src.db.session import SessionLocal
from src.gateway.prompt_versions import STAGE_CATALOG, system_prompt
from src.models.prompts import PromptTemplate
from src.services.activity import log_action

router = APIRouter()

_STAGE_MODES = {c["stage"]: c["modes"] for c in STAGE_CATALOG}


async def _actor(session, name: str):
    """按用户名查 (id, role)；查不到 → 401。"""
    if not name:
        raise HTTPException(status_code=401, detail="缺少 actor 参数")
    row = (await session.execute(
        text("SELECT id, role FROM users WHERE name = :n"), {"n": name})).first()
    if not row:
        raise HTTPException(status_code=401, detail=f"用户不存在: {name}")
    return row[0], row[1]


def _row(p: PromptTemplate, owner_name: str = None) -> dict:
    return {"id": str(p.id), "stage": p.stage, "mode": p.mode, "name": p.name,
            "content": p.content, "is_active": p.is_active,
            "owner_id": str(p.owner_id), "owner_name": owner_name,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


def _check_stage(stage: str, mode):
    if stage not in _STAGE_MODES:
        raise HTTPException(status_code=400, detail=f"未知环节: {stage}")
    if mode not in _STAGE_MODES[stage]:
        raise HTTPException(status_code=400, detail=f"环节 {stage} 不支持模式: {mode}")


@router.get("/api/prompts/catalog")
async def catalog():
    """环节目录 + 当前生效的系统默认提示词（含 admin 覆盖标记）。"""
    mode_label = {"general": "通用", "single": "单品", "compare": "对比", None: "通用"}
    stages = []
    for c in STAGE_CATALOG:
        items = []
        for m in c["modes"]:
            content, customized = await system_prompt(c["stage"], m)
            items.append({"mode": m, "mode_label": mode_label[m],
                          "system": content, "customized": customized})
        stages.append({**c, "items": items})
    return {"stages": stages}


class SystemPromptIn(BaseModel):
    actor: str
    stage: str
    mode: str | None = None
    content: str


async def _require_admin(session, actor: str):
    uid, role = await _actor(session, actor)
    if role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可修改系统默认提示词")
    return uid


@router.put("/api/prompts/system")
async def upsert_system_prompt(payload: SystemPromptIn):
    """admin 修改系统默认提示词（落库覆盖，全局生效）。"""
    _check_stage(payload.stage, payload.mode)
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    async with SessionLocal() as session:
        await _require_admin(session, payload.actor)
        row = (await session.execute(text(
            "SELECT id FROM prompt_templates "
            "WHERE stage = :s AND mode IS NOT DISTINCT FROM :m AND owner_id IS NULL"),
            {"s": payload.stage, "m": payload.mode})).first()
        if row:
            await session.execute(text(
                "UPDATE prompt_templates SET content = :c, updated_at = now() "
                "WHERE id = :id"), {"c": payload.content, "id": row[0]})
        else:
            await session.execute(text(
                "INSERT INTO prompt_templates (stage, mode, name, content, owner_id, is_active) "
                "VALUES (:s, :m, '系统默认', :c, NULL, false)"),
                {"s": payload.stage, "m": payload.mode, "c": payload.content})
        await session.commit()
        await log_action(payload.actor, "system_prompt_update",
                         f"修改系统默认提示词：{payload.stage}/{payload.mode or '通用'}")
        return {"ok": True, "customized": True}


@router.delete("/api/prompts/system")
async def restore_system_prompt(actor: str, stage: str, mode: str | None = None):
    """admin 恢复系统默认提示词为代码内置版本（删除覆盖）。"""
    _check_stage(stage, mode)
    async with SessionLocal() as session:
        await _require_admin(session, actor)
        await session.execute(text(
            "DELETE FROM prompt_templates "
            "WHERE stage = :s AND mode IS NOT DISTINCT FROM :m AND owner_id IS NULL"),
            {"s": stage, "m": mode})
        await session.commit()
        await log_action(actor, "system_prompt_restore",
                         f"恢复系统默认提示词为内置版本：{stage}/{mode or '通用'}")
        return {"ok": True, "customized": False}


@router.get("/api/prompts")
async def list_prompts(actor: str):
    """自定义提示词列表：普通用户只看自己的；admin 看全部（带归属人）。"""
    async with SessionLocal() as session:
        uid, role = await _actor(session, actor)
        # 只列用户自定义（owner_id 非空）；系统覆盖行走 /api/prompts/system
        stmt = (select(PromptTemplate)
                .where(PromptTemplate.owner_id.isnot(None))
                .order_by(PromptTemplate.updated_at.desc()))
        if role != "admin":
            stmt = stmt.where(PromptTemplate.owner_id == uid)
        rows = (await session.execute(stmt)).scalars().all()
        names = dict((await session.execute(
            text("SELECT id, name FROM users"))).all())
    return {"prompts": [_row(p, names.get(p.owner_id)) for p in rows]}


class PromptIn(BaseModel):
    actor: str
    stage: str
    mode: str | None = None
    name: str
    content: str
    is_active: bool = False


@router.post("/api/prompts")
async def create_prompt(payload: PromptIn):
    _check_stage(payload.stage, payload.mode)
    if not payload.name.strip() or not payload.content.strip():
        raise HTTPException(status_code=400, detail="名称和内容不能为空")
    async with SessionLocal() as session:
        uid, _ = await _actor(session, payload.actor)
        p = PromptTemplate(stage=payload.stage, mode=payload.mode,
                           name=payload.name.strip(), content=payload.content,
                           owner_id=uid, is_active=payload.is_active)
        session.add(p)
        if p.is_active:
            # 同环节同模式只启用一条：把本人其它条目置为停用
            await session.execute(text(
                "UPDATE prompt_templates SET is_active = false "
                "WHERE owner_id = :o AND stage = :s AND mode IS NOT DISTINCT FROM :m"),
                {"o": uid, "s": p.stage, "m": p.mode})
        await session.commit()
        await session.refresh(p)
        await log_action(payload.actor, "prompt_create",
                         f"新建自定义提示词「{p.name}」（{p.stage}/{p.mode or '通用'}"
                         f"{'，已启用' if p.is_active else ''}）")
        return {"ok": True, "prompt": _row(p, payload.actor)}


class PromptUpdate(BaseModel):
    actor: str
    name: str | None = None
    content: str | None = None
    is_active: bool | None = None


@router.put("/api/prompts/{prompt_id}")
async def update_prompt(prompt_id: str, payload: PromptUpdate):
    async with SessionLocal() as session:
        uid, role = await _actor(session, payload.actor)
        p = (await session.execute(select(PromptTemplate).where(
            PromptTemplate.id == prompt_id))).scalars().first()
        if not p:
            raise HTTPException(status_code=404, detail="提示词不存在")
        if role != "admin" and p.owner_id != uid:
            raise HTTPException(status_code=403, detail="只能修改自己的提示词")
        if payload.name is not None:
            p.name = payload.name.strip() or p.name
        if payload.content is not None:
            p.content = payload.content
        if payload.is_active is not None:
            p.is_active = payload.is_active
        p.updated_at = datetime.now(timezone.utc)
        if p.is_active:
            await session.execute(text(
                "UPDATE prompt_templates SET is_active = false "
                "WHERE owner_id = :o AND stage = :s AND mode IS NOT DISTINCT FROM :m "
                "AND id <> :id"),
                {"o": p.owner_id, "s": p.stage, "m": p.mode, "id": p.id})
        await session.commit()
        changes = []
        if payload.name is not None:
            changes.append("改名")
        if payload.content is not None:
            changes.append("改内容")
        if payload.is_active is not None:
            changes.append("启用" if payload.is_active else "停用")
        await log_action(payload.actor, "prompt_update",
                         f"{'/'.join(changes) or '更新'}自定义提示词「{p.name}」"
                         f"（{p.stage}/{p.mode or '通用'}）")
        return {"ok": True, "prompt": _row(p)}


@router.delete("/api/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str, actor: str):
    async with SessionLocal() as session:
        uid, role = await _actor(session, actor)
        p = (await session.execute(select(PromptTemplate).where(
            PromptTemplate.id == prompt_id))).scalars().first()
        if not p:
            raise HTTPException(status_code=404, detail="提示词不存在")
        if role != "admin" and p.owner_id != uid:
            raise HTTPException(status_code=403, detail="只能删除自己的提示词")
        await session.delete(p)
        await session.commit()
        await log_action(actor, "prompt_delete",
                         f"删除自定义提示词「{p.name}」（{p.stage}/{p.mode or '通用'}）")
        return {"ok": True}
