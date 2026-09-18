"""超级管理控制台 API（2026-09-10）：模型供给配置的独立门禁管理面。

与 /api/system/settings（行为开关，admin 即可改）的边界：这里集中「模型供给」——
各 LLM/生图/OCR/搜索 provider 的 API Key 与地址、文本主模型、备用链开关、
生图通道顺序、创作网关指向。

门禁双保险（独立于登录态之外的第二道验证）：
①登录账号须为 active admin（actor 校验）；
②独立超级密码（与任何账号密码无关；首次进入时输入的密码即被设为超级密码，
sha256 存 system_settings 表 `_super_admin_pwd_hash`，该键不属配置白名单、
永不经配置接口回显）。验证通过发 30 分钟内存 token（X-Super-Token，
单进程 asyncio 架构下内存态即全局态），全部写操作与明文查看都要求携带；
连续错 5 次锁 5 分钟。

即时生效机制：字段全部落 system_settings 持久化（重启由 lifespan 的
load_model_overrides 覆盖回内存）+ setattr(settings, ...) 即时生效——
各网关（image_gen/_channels、dsh_client、failover._api_key_for 等）
均为调用时读 settings；failover 链已改每次调用解析（2026-09-10），
主模型/备用链切换无需重启。

密钥安全：GET 只回掩码（前4+****+后4）；明文须经 /reveal 并记 activity_logs
审计；写接口对密钥空串/掩码回传一律忽略（不允许经此接口清空密钥，防误触断产）。
"""
import secrets
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

from src.config import settings
from src.db.session import SessionLocal
from src.services.activity import log_action

router = APIRouter()

_PWD_KEY = "_super_admin_pwd_hash"      # 超级密码哈希（不入配置白名单/不回显）
_TOKEN_TTL = 30 * 60                    # 超管会话 30 分钟（滑动续期）
_MAX_FAILS, _LOCK_SECONDS = 5, 300

_tokens: dict[str, float] = {}          # token -> 过期时刻（内存态，重启即失效）
_fails: dict[str, list] = {}            # actor -> [连续失败次数, 锁定截止时刻]

# ── 字段注册表（白名单 = 可持久化 + 可经 web 修改的全部范围）──────────────
_SECRET_FIELDS = {
    "deepseek_api_key": "DeepSeek Key（文本主模型）",
    "kimi_api_key": "Kimi 开放平台 Key（备1）",
    "kimi_code_api_key": "Kimi Code Key（备2）",
    "dashscope_api_key": "DashScope Key（视觉审核/OCR）",
    "fusionai_api_key": "FusionAI Key（生图·fusion）",
    "openai_image_api_key": "LinkAI Key（生图·linkai）",
    "moacode_api_key": "Moacode Key（生图·moacode）",
    "openox_api_key": "Openox Key（生图·openox）",
    "doubao_search_key": "豆包搜索 Key（证据包）",
    "doubao_ark_key": "豆包方舟 Key（搜实景图·预留）",
    "dsh_serve_api_key": "创作网关 Bearer（仅非回环地址需要）",
}
_PLAIN_FIELDS = {
    "deepseek_model": "文本主模型",
    "image_model": "生图模型名",
    "image_gen_channels": "生图通道顺序（逗号分隔，首位=主通道）",
    "ocr_model": "OCR 模型",
    "ocr_base_url": "OCR 网关地址",
    "visual_check_model": "视觉审核模型",
    "dsh_serve_base_url": "创作网关地址（含 /v1）",
    "dsh_serve_model": "网关模型覆盖（空=网关默认预设）",
}
_BOOL_FIELDS = {
    "text_fallback1_enabled": "备1 Kimi-k3 启用",
    "text_fallback2_enabled": "备2 Kimi-Code 启用",
}
_KNOWN_FIELDS = set(_SECRET_FIELDS) | set(_PLAIN_FIELDS) | set(_BOOL_FIELDS)

CHANNELS = ("fusion", "linkai", "moacode", "openox")
_GATEWAY_URLS = {"dsh": "http://127.0.0.1:8901/v1"}


# ── 纯函数（tests/unit/test_superadmin.py 直测）──────────────────────────
def mask_secret(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "****"
    return f"{v[:4]}****{v[-4:]}"


def reorder_channels_first(channels: str, channel: str) -> str:
    """把 channel 提到通道顺序首位，其余保持原相对顺序（不增不减不去重）。"""
    cur = [c.strip() for c in (channels or "").split(",") if c.strip()]
    if channel not in cur:
        raise ValueError(f"通道 {channel} 不在当前顺序里：{channels}")
    return ",".join([channel] + [c for c in cur if c != channel])


def validate_field(key: str, value: str) -> str:
    """plain 字段写入前校验，返回规范化值；非法抛 ValueError。"""
    v = (value or "").strip()
    if not v:
        raise ValueError(f"{key} 不能为空")
    if key == "image_gen_channels":
        parts = [c.strip() for c in v.split(",") if c.strip()]
        bad = [c for c in parts if c not in CHANNELS]
        if bad:
            raise ValueError(f"未知通道：{bad}（可选 {list(CHANNELS)}）")
        if len(parts) != len(set(parts)):
            raise ValueError("通道顺序有重复")
        return ",".join(parts)
    if key.endswith("_base_url") and not v.startswith(("http://", "https://")):
        raise ValueError(f"{key} 必须以 http(s):// 开头")
    return v


# ── 门禁 ─────────────────────────────────────────────────────────────────
def _check_token(super_token: str) -> None:
    if not super_token or super_token not in _tokens or _tokens[super_token] < time.time():
        raise HTTPException(status_code=401, detail="超级管理会话无效或已过期，请重新验证")
    _tokens[super_token] = time.time() + _TOKEN_TTL   # 滑动续期


async def _require(actor: str, super_token: str) -> None:
    """写操作/明文查看的双保险：超管 token + active admin 账号。"""
    _check_token(super_token)
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT 1 FROM users WHERE name = :n AND role = 'admin' AND active"),
            {"n": actor or ""})).first()
    if not row:
        raise HTTPException(status_code=403, detail="仅管理员账号可操作超级管理控制台")


async def _persist(key: str, value: str, actor: str) -> None:
    from src.models.system_setting import SystemSetting
    async with SessionLocal() as session:
        cur = (await session.execute(
            select(SystemSetting).where(SystemSetting.key == key))).scalars().first()
        if cur:
            cur.value = value
            cur.updated_by = actor
        else:
            session.add(SystemSetting(key=key, value=value, updated_by=actor))
        await session.commit()


async def load_model_overrides() -> None:
    """lifespan 启动加载：本模块白名单字段的 DB 值覆盖 settings 内存。

    与 system.load_settings_from_db 并列（那个只管 bool 行为开关）。
    密钥行从不写空值，故 DB 空串跳过即可，不会盖掉 .env 提供的默认。
    """
    from src.models.system_setting import SystemSetting
    try:
        async with SessionLocal() as session:
            rows = (await session.execute(select(SystemSetting))).scalars().all()
        for r in rows:
            if r.key == _PWD_KEY or r.key not in _KNOWN_FIELDS:
                continue
            if r.key in _BOOL_FIELDS:
                setattr(settings, r.key, r.value == "true")
            elif r.value != "":
                setattr(settings, r.key, r.value)
    except Exception:
        import traceback
        traceback.print_exc()   # 表未建好等：保持 .env/默认值启动，不阻塞服务


# ── 门禁端点 ─────────────────────────────────────────────────────────────
class VerifyIn(BaseModel):
    actor: str
    password: str


@router.post("/api/superadmin/verify")
async def super_verify(payload: VerifyIn):
    """验证独立超级密码（首次进入即初始化），发超管会话 token。"""
    actor = payload.actor.strip()
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT 1 FROM users WHERE name = :n AND role = 'admin' AND active"),
            {"n": actor})).first()
    if not row:
        raise HTTPException(status_code=403, detail="仅管理员账号可进入超级管理控制台")

    now = time.time()
    fails, lock_until = _fails.get(actor, [0, 0.0])
    if lock_until > now:
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，请 {int(lock_until - now) + 1} 秒后再试")

    from src.api.auth import hash_password
    from src.models.system_setting import SystemSetting
    async with SessionLocal() as session:
        stored = (await session.execute(text(
            "SELECT value FROM system_settings WHERE key = :k"),
            {"k": _PWD_KEY})).scalar()
    initialized = bool(stored)
    if stored and stored != hash_password(payload.password):
        _fails[actor] = [fails + 1, now + (_LOCK_SECONDS if fails + 1 >= _MAX_FAILS else 0.0)]
        await log_action(actor, "super_admin", "超级密码验证失败"
                         f"（连续第 {fails + 1} 次）")
        return {"ok": False, "error": "超级密码错误"}
    if not stored:
        async with SessionLocal() as session:
            session.add(SystemSetting(key=_PWD_KEY,
                                      value=hash_password(payload.password),
                                      updated_by=actor))
            await session.commit()
        await log_action(actor, "super_admin", "初始化超级管理密码")
    _fails.pop(actor, None)

    token = secrets.token_urlsafe(32)
    _tokens[token] = now + _TOKEN_TTL
    await log_action(actor, "super_admin", "进入超级管理控制台")
    return {"ok": True, "token": token, "expires_in": _TOKEN_TTL}


class PasswordIn(BaseModel):
    actor: str
    old_password: str
    new_password: str


@router.post("/api/superadmin/password")
async def change_super_password(payload: PasswordIn,
                                super_token: str = Header(default="", alias="X-Super-Token")):
    await _require(payload.actor, super_token)
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=422, detail="新超级密码至少 8 位")
    from src.api.auth import hash_password
    from src.models.system_setting import SystemSetting
    async with SessionLocal() as session:
        stored = (await session.execute(text(
            "SELECT value FROM system_settings WHERE key = :k"),
            {"k": _PWD_KEY})).scalar()
    if stored != hash_password(payload.old_password):
        raise HTTPException(status_code=403, detail="原超级密码错误")
    async with SessionLocal() as session:
        cur = (await session.execute(
            select(SystemSetting).where(SystemSetting.key == _PWD_KEY))).scalars().first()
        cur.value = hash_password(payload.new_password)
        cur.updated_by = payload.actor
        await session.commit()
    _tokens.clear()   # 改密后全部超管会话失效，需重新验证
    await log_action(payload.actor, "super_admin", "修改超级管理密码（全部会话已注销）")
    return {"ok": True}


# ── 配置读写 ─────────────────────────────────────────────────────────────
@router.get("/api/superadmin/model-config")
async def get_model_config(actor: str = "",
                           super_token: str = Header(default="", alias="X-Super-Token")):
    await _require(actor, super_token)
    fields = []
    for k, label in _SECRET_FIELDS.items():
        v = getattr(settings, k, "")
        fields.append({"key": k, "kind": "secret", "label": label,
                       "masked": mask_secret(v), "set": bool(v)})
    for k, label in _PLAIN_FIELDS.items():
        fields.append({"key": k, "kind": "plain", "label": label,
                       "value": getattr(settings, k, "")})
    for k, label in _BOOL_FIELDS.items():
        fields.append({"key": k, "kind": "bool", "label": label,
                       "value": bool(getattr(settings, k, True))})
    # 创作网关恒为 dsh_serve；地址经 dsh_client 解析
    from src.gateway.dsh_client import _base_url
    base = _base_url()
    gateway = "dsh"
    return {"fields": fields, "effective": {
        "channels": [c.strip() for c in settings.image_gen_channels.split(",") if c.strip()],
        "gateway": gateway,
        "gateway_url": base,
        "primary_model": settings.deepseek_model,
        "fallback1_enabled": bool(settings.text_fallback1_enabled),
        "fallback2_enabled": bool(settings.text_fallback2_enabled),
        "fallback2_has_key": bool(settings.kimi_code_api_key),
    }}


class ConfigItem(BaseModel):
    key: str
    value: str


class ConfigIn(BaseModel):
    actor: str
    items: list[ConfigItem]


@router.put("/api/superadmin/model-config")
async def save_model_config(payload: ConfigIn,
                            super_token: str = Header(default="", alias="X-Super-Token")):
    await _require(payload.actor, super_token)
    changed = []
    for item in payload.items:
        if item.key not in _KNOWN_FIELDS:
            raise HTTPException(status_code=404, detail=f"未知配置字段：{item.key}")
        if item.key in _SECRET_FIELDS:
            # 空/掩码回传=不修改；本接口不允许清空密钥（防误触断产）
            if not item.value or "****" in item.value:
                continue
            new_val = item.value.strip()
        elif item.key in _BOOL_FIELDS:
            new_val = "true" if item.value.strip().lower() in ("true", "1", "on", "是") else "false"
            setattr(settings, item.key, new_val == "true")
        else:
            try:
                new_val = validate_field(item.key, item.value)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
            setattr(settings, item.key, new_val)
        await _persist(item.key, new_val, payload.actor)
        changed.append(item.key)
    await log_action(payload.actor, "super_admin",
                     f"模型配置更新：{','.join(changed) if changed else '无有效变更'}")
    return {"ok": True, "changed": changed}


class RevealIn(BaseModel):
    actor: str
    key: str


@router.post("/api/superadmin/reveal")
async def reveal_secret(payload: RevealIn,
                        super_token: str = Header(default="", alias="X-Super-Token")):
    if payload.key not in _SECRET_FIELDS:
        raise HTTPException(status_code=404, detail=f"非密钥字段：{payload.key}")
    await _require(payload.actor, super_token)
    await log_action(payload.actor, "super_admin", f"查看密钥明文：{payload.key}")
    return {"key": payload.key, "value": getattr(settings, payload.key, "")}


# ── 快捷切换（同样落库 + 内存即时生效）────────────────────────────────────
class SwitchModelIn(BaseModel):
    actor: str
    model: str


@router.post("/api/superadmin/switch/primary-model")
async def switch_primary_model(payload: SwitchModelIn,
                               super_token: str = Header(default="", alias="X-Super-Token")):
    await _require(payload.actor, super_token)
    try:
        new_val = validate_field("deepseek_model", payload.model)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    old = settings.deepseek_model
    setattr(settings, "deepseek_model", new_val)
    await _persist("deepseek_model", new_val, payload.actor)
    await log_action(payload.actor, "super_admin", f"文本主模型切换：{old} → {new_val}")
    return {"ok": True, "old": old, "new": new_val}


class ChannelIn(BaseModel):
    actor: str
    channel: str


@router.post("/api/superadmin/switch/channel-primary")
async def switch_channel_primary(payload: ChannelIn,
                                 super_token: str = Header(default="", alias="X-Super-Token")):
    await _require(payload.actor, super_token)
    if payload.channel not in CHANNELS:
        raise HTTPException(status_code=422,
                            detail=f"未知通道：{payload.channel}（可选 {list(CHANNELS)}）")
    old = settings.image_gen_channels
    new_val = reorder_channels_first(old, payload.channel)
    setattr(settings, "image_gen_channels", new_val)
    await _persist("image_gen_channels", new_val, payload.actor)
    await log_action(payload.actor, "super_admin",
                     f"生图主通道切换：{old.split(',')[0]} → {payload.channel}（{new_val}）")
    return {"ok": True, "channels": new_val.split(",")}


class GatewayIn(BaseModel):
    actor: str
    target: str   # dsh（唯一网关目标）


@router.post("/api/superadmin/switch/gateway")
async def switch_gateway(payload: GatewayIn,
                         super_token: str = Header(default="", alias="X-Super-Token")):
    await _require(payload.actor, super_token)
    if payload.target != "dsh":
        raise HTTPException(status_code=422, detail="target 只能是 dsh")
    from src.gateway.dsh_client import _base_url
    old = _base_url()
    new_val = _GATEWAY_URLS["dsh"]
    setattr(settings, "dsh_serve_base_url", new_val)
    await _persist("dsh_serve_base_url", new_val, payload.actor)
    await log_action(payload.actor, "super_admin",
                     f"创作网关切换：{old} → {new_val}（dsh_serve）")
    return {"ok": True, "old": old, "new": new_val}


class FallbackIn(BaseModel):
    actor: str
    level: str    # fallback1 / fallback2
    enabled: bool


@router.post("/api/superadmin/switch/fallback")
async def switch_fallback(payload: FallbackIn,
                          super_token: str = Header(default="", alias="X-Super-Token")):
    await _require(payload.actor, super_token)
    key = {"fallback1": "text_fallback1_enabled",
           "fallback2": "text_fallback2_enabled"}.get(payload.level)
    if not key:
        raise HTTPException(status_code=422, detail="level 只能是 fallback1 或 fallback2")
    setattr(settings, key, payload.enabled)
    await _persist(key, "true" if payload.enabled else "false", payload.actor)
    label = _BOOL_FIELDS[key]
    await log_action(payload.actor, "super_admin",
                     f"备用链切换：{label} → {'启用' if payload.enabled else '停用'}")
    return {"ok": True, "key": key, "enabled": payload.enabled}
