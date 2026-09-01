# 系统参数 web 化（2026-09-01，迁移 018）：
# GET 返回白名单开关状态；PUT 仅 admin、落库 + 内存即时生效；启动加载覆盖。
import uuid

import pytest
from sqlalchemy import text

from src.config import settings
from src.db.session import SessionLocal


async def _mk_user(name: str, role: str = "A"):
    async with SessionLocal() as s:
        uid = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO users (id, name, role, active) VALUES (:i, :n, :r, true) "
            "ON CONFLICT DO NOTHING"), {"i": str(uid), "n": name, "r": role})
        await s.commit()
        return uid


@pytest.mark.asyncio
async def test_get_returns_whitelist():
    from src.api.system import get_system_settings
    r = await get_system_settings(actor="")
    keys = {i["key"] for i in r["items"]}
    assert "ref_for_general_enabled" in keys and "draft_polish_enabled" in keys
    assert all(not i["editable"] for i in r["items"])   # 匿名不可编辑
    assert all(i["title"] and i["desc"] for i in r["items"])  # 中文说明齐全


@pytest.mark.asyncio
async def test_put_admin_only_and_takes_effect():
    from src.api.system import set_system_setting, SettingIn
    from fastapi import HTTPException
    admin, user = f"sysadmin-{uuid.uuid4().hex[:5]}", f"sysuser-{uuid.uuid4().hex[:5]}"
    await _mk_user(admin, "admin")
    await _mk_user(user, "A")
    # 非 admin → 403；未知用户 → 401
    with pytest.raises(HTTPException) as e:
        await set_system_setting(SettingIn(key="draft_polish_enabled", value=False),
                                 actor=user)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        await set_system_setting(SettingIn(key="draft_polish_enabled", value=False),
                                 actor="nobody")
    assert e.value.status_code == 401
    # admin 切换：内存即时生效 + 落库
    orig = settings.draft_polish_enabled
    try:
        r = await set_system_setting(SettingIn(key="draft_polish_enabled",
                                               value=not orig), actor=admin)
        assert r["ok"]
        assert settings.draft_polish_enabled == (not orig)
        async with SessionLocal() as s:
            v = (await s.execute(text(
                "SELECT value FROM system_settings WHERE key='draft_polish_enabled'")
            )).scalar()
        assert v == ("true" if not orig else "false")
    finally:
        await set_system_setting(SettingIn(key="draft_polish_enabled", value=orig),
                                 actor=admin)
        assert settings.draft_polish_enabled == orig
        async with SessionLocal() as s:
            await s.execute(text(
                "DELETE FROM system_settings WHERE key='draft_polish_enabled'"))
            await s.commit()


@pytest.mark.asyncio
async def test_unknown_key_rejected():
    from src.api.system import set_system_setting, SettingIn
    from fastapi import HTTPException
    admin = f"sysadmin2-{uuid.uuid4().hex[:5]}"
    await _mk_user(admin, "admin")
    with pytest.raises(HTTPException) as e:
        await set_system_setting(SettingIn(key="deepseek_api_key", value=True),
                                 actor=admin)   # 白名单外（含密钥类）
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_startup_loader_applies_db_over_env():
    from src.api.system import load_settings_from_db
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO system_settings (key, value) VALUES "
            "('asset_library_reuse', 'false') ON CONFLICT (key) DO UPDATE SET value='false'"))
        await s.commit()
    orig = settings.asset_library_reuse
    try:
        assert settings.asset_library_reuse is True   # .env 默认开
        await load_settings_from_db()
        assert settings.asset_library_reuse is False  # 被 DB 覆盖
    finally:
        settings.asset_library_reuse = orig
        async with SessionLocal() as s:
            await s.execute(text("DELETE FROM system_settings WHERE key='asset_library_reuse'"))
            await s.commit()
