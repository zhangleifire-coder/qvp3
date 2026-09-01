"""系统参数 API（2026-09-01，迁移 018）：行为开关 web 化。

- GET /api/system/settings：当前各开关状态与中文说明（admin/登录可见）
- PUT /api/system/settings：切换开关（仅 admin）——写 system_settings 表 +
  同步改 settings 内存值，**即时生效无需重启**；启动时 lifespan 从表加载
  覆盖内存（.env 降级为未配置时的初始默认）。
- 白名单键：只暴露行为开关，密钥类配置一律不上 web。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

from src.config import settings
from src.db.session import SessionLocal
from src.services.activity import log_action

router = APIRouter()

# 白名单：key → (中文标题, 中文说明)
_SETTING_KEYS = {
    "ref_for_general_enabled": (
        "通用模式启用实景图",
        "开启后 general（科普/教程）任务也走「搜真实照片→人工审图确认→带参考图生成」"
        "流程（与对比/单品模式一致）。注意：①general 任务会多一道实景审图关卡，"
        "需人工确认后才继续生产；②概念类题材（方法/观点/情绪等）网上可能搜不到"
        "合适实拍图。关闭=保持纯 AI 文生图（全自动、无审图关卡）。"),
    "draft_polish_enabled": (
        "正文自动校稿润色",
        "正文创作后自动加一轮校稿润色（删存疑精确数字与夸大表述、去 AI 腔提真人感、"
        "补免责声明）。护栏：润色稿不足原稿 60% 时自动弃用原稿。关闭=只创作不润色。"),
    "asset_library_reuse": (
        "素材库复用",
        "搜实景图前先按关键词匹配历史任务已确认的实图，命中直接复用（免搜索免下载，"
        "省 API 成本）。审图候选中显示「素材库复用」标签。关闭=每次纯搜索。"),
    "visual_subject_check_enabled": (
        "视觉主体审核",
        "每页配图生成后用视觉模型看图比对「图中主体 vs 该页文案主题」：不符自动重画"
        "一次，仍不符标记「主体待审」交人工复核（治图文脱节，如文案说猫图里画狗）。"
        "关闭=不做图文一致性自动审核。"),
}


async def load_settings_from_db() -> None:
    """启动时加载：system_settings 表覆盖 settings 内存值（不存在的键保持 .env/默认）。"""
    from src.models.system_setting import SystemSetting
    try:
        async with SessionLocal() as session:
            rows = (await session.execute(select(SystemSetting))).scalars().all()
        for r in rows:
            if r.key in _SETTING_KEYS:
                setattr(settings, r.key, r.value == "true")
    except Exception:
        import traceback
        traceback.print_exc()   # 表未建好等：保持默认值启动，不阻塞服务


@router.get("/api/system/settings")
async def get_system_settings(actor: str = ""):
    """系统参数当前状态（含中文说明）。"""
    async with SessionLocal() as session:
        row = None
        if actor:
            row = (await session.execute(text(
                "SELECT role FROM users WHERE name = :n AND active"),
                {"n": actor})).first()
    is_admin = bool(row and row[0] == "admin")
    return {"items": [
        {"key": k, "value": bool(getattr(settings, k)),
         "title": t, "desc": d, "editable": is_admin}
        for k, (t, d) in _SETTING_KEYS.items()
    ]}


class SettingIn(BaseModel):
    key: str
    value: bool


@router.put("/api/system/settings")
async def set_system_setting(payload: SettingIn, actor: str = ""):
    """切换开关（仅 admin）：落库 + 内存即时生效。"""
    if payload.key not in _SETTING_KEYS:
        raise HTTPException(status_code=404, detail=f"未知参数：{payload.key}")
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT id, role FROM users WHERE name = :n AND active"),
            {"n": actor})).first()
        if not row:
            raise HTTPException(status_code=401, detail=f"用户不存在: {actor or '缺少 actor'}")
        if row[1] != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可修改系统参数")
        from src.models.system_setting import SystemSetting
        cur = (await session.execute(
            select(SystemSetting).where(SystemSetting.key == payload.key))
        ).scalars().first()
        val = "true" if payload.value else "false"
        if cur:
            cur.value = val
            cur.updated_by = actor
        else:
            session.add(SystemSetting(key=payload.key, value=val, updated_by=actor))
        await session.commit()
    setattr(settings, payload.key, payload.value)   # 即时生效
    await log_action(actor, "system_setting",
                     f"系统参数 {payload.key} → {payload.value}")
    return {"ok": True, "key": payload.key, "value": payload.value}
