"""提示词库 + 用户管理接口测试（2026-08-20）。"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text

from src.api.auth import hash_password
from src.api.main import app
from src.db.session import SessionLocal
from src.gateway.prompt_versions import get_effective_prompt, default_prompt


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(name=None, role="A"):
    name = name or f"u-{_uniq()}"
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO users (name, role, password_hash) VALUES (:n, :r, :p)"),
            {"n": name, "r": role, "p": hash_password("pw-123456")})
        await s.commit()
    return name


# ---------- 提示词库 ----------

@pytest.mark.asyncio
async def test_catalog_returns_system_defaults():
    async with _client() as ac:
        r = await ac.get("/api/prompts/catalog")
    assert r.status_code == 200
    stages = {s["stage"]: s for s in r.json()["stages"]}
    assert set(stages) == {"draft_gen", "page_split", "image_gen", "page_regen"}
    assert len(stages["draft_gen"]["items"]) == 3       # 三个模式
    assert stages["page_split"]["items"][0]["mode"] is None
    assert stages["image_gen"]["items"][0]["system"]    # 系统默认非空
    assert stages["page_regen"]["items"][0]["system"]   # 单页重写默认非空


@pytest.mark.asyncio
async def test_custom_prompt_crud_and_visibility():
    owner = await _make_user()
    other = await _make_user()
    admin = await _make_user(role="admin")
    async with _client() as ac:
        # 创建
        r = await ac.post("/api/prompts", json={
            "actor": owner, "stage": "draft_gen", "mode": "general",
            "name": "测试版", "content": "自定义正文提示词 {q}", "is_active": True})
        assert r.status_code == 200, r.text
        pid = r.json()["prompt"]["id"]
        # 本人可见
        mine = (await ac.get(f"/api/prompts?actor={owner}")).json()["prompts"]
        assert [p["id"] for p in mine] == [pid]
        # 其它普通用户不可见
        others = (await ac.get(f"/api/prompts?actor={other}")).json()["prompts"]
        assert others == []
        # admin 可见全部且带归属
        allp = (await ac.get(f"/api/prompts?actor={admin}")).json()["prompts"]
        assert allp[0]["owner_name"] == owner
        # 非 owner 非 admin 不能改
        r = await ac.put(f"/api/prompts/{pid}", json={"actor": other, "name": "x"})
        assert r.status_code == 403
        # owner 改内容
        r = await ac.put(f"/api/prompts/{pid}",
                         json={"actor": owner, "content": "改版内容"})
        assert r.status_code == 200
        # owner 删除
        r = await ac.delete(f"/api/prompts/{pid}?actor={owner}")
        assert r.status_code == 200
        assert (await ac.get(f"/api/prompts?actor={owner}")).json()["prompts"] == []


@pytest.mark.asyncio
async def test_activate_excludes_siblings():
    owner = await _make_user()
    async with _client() as ac:
        ids = []
        for i in range(2):
            r = await ac.post("/api/prompts", json={
                "actor": owner, "stage": "image_gen", "mode": "compare",
                "name": f"v{i}", "content": f"内容{i}", "is_active": True})
            ids.append(r.json()["prompt"]["id"])
        prompts = (await ac.get(f"/api/prompts?actor={owner}")).json()["prompts"]
        actives = [p for p in prompts if p["is_active"]]
        assert len(actives) == 1 and actives[0]["id"] == ids[1]  # 后建的启用，先建的被顶掉


@pytest.mark.asyncio
async def test_effective_prompt_resolution():
    owner = await _make_user()
    async with SessionLocal() as s:
        uid = (await s.execute(text("SELECT id FROM users WHERE name = :n"),
                               {"n": owner})).scalar()
    # 无自定义 → 系统默认
    assert await get_effective_prompt("draft_gen", "general", uid) == \
        default_prompt("draft_gen", "general")
    # 有未启用自定义 → 仍系统默认
    async with _client() as ac:
        await ac.post("/api/prompts", json={
            "actor": owner, "stage": "draft_gen", "mode": "general",
            "name": "未启用", "content": "不用我", "is_active": False})
        assert await get_effective_prompt("draft_gen", "general", uid) == \
            default_prompt("draft_gen", "general")
        # 启用后 → 自定义生效；其它模式不受影响
        await ac.post("/api/prompts", json={
            "actor": owner, "stage": "draft_gen", "mode": "general",
            "name": "启用", "content": "用我的", "is_active": True})
        assert await get_effective_prompt("draft_gen", "general", uid) == "用我的"
        assert await get_effective_prompt("draft_gen", "single", uid) == \
            default_prompt("draft_gen", "single")
    # owner_id 为空 → 系统默认
    assert await get_effective_prompt("page_split", None, None) == \
        default_prompt("page_split", None)


@pytest.mark.asyncio
async def test_prompt_validation():
    owner = await _make_user()
    async with _client() as ac:
        r = await ac.post("/api/prompts", json={
            "actor": owner, "stage": "nope", "mode": None,
            "name": "x", "content": "y"})
        assert r.status_code == 400
        r = await ac.post("/api/prompts", json={
            "actor": owner, "stage": "page_split", "mode": "general",
            "name": "x", "content": "y"})
        assert r.status_code == 400  # page_split 不支持 mode
        r = await ac.post("/api/prompts?actor=", json={
            "actor": "", "stage": "draft_gen", "mode": "general",
            "name": "x", "content": "y"})
        assert r.status_code == 401


# ---------- 用户管理 ----------

@pytest.mark.asyncio
async def test_user_management_admin_only():
    admin = await _make_user(role="admin")
    plain = await _make_user()
    async with _client() as ac:
        # 非 admin 被拒
        r = await ac.get(f"/api/admin/users?actor={plain}")
        assert r.status_code == 403
        r = await ac.get("/api/admin/users?actor=ghost")
        assert r.status_code == 401
        # admin 列表
        r = await ac.get(f"/api/admin/users?actor={admin}")
        assert r.status_code == 200
        assert any(u["name"] == plain for u in r.json()["users"])
        # 创建
        r = await ac.post("/api/admin/users", json={
            "actor": admin, "name": f"new-{_uniq()}", "password": "abc12345", "role": "B"})
        assert r.status_code == 200, r.text
        # 重名被拒
        r = await ac.post("/api/admin/users", json={
            "actor": admin, "name": plain, "password": "abc12345", "role": "B"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_user_update_and_delete_guards():
    admin = await _make_user(role="admin")
    async with _client() as ac:
        users = (await ac.get(f"/api/admin/users?actor={admin}")).json()["users"]
        admin_id = [u for u in users if u["name"] == admin][0]["id"]
        # 不能删除自己
        r = await ac.delete(f"/api/admin/users/{admin_id}?actor={admin}")
        assert r.status_code == 400
        # 不能把唯一在职 admin 降级
        r = await ac.put(f"/api/admin/users/{admin_id}",
                         json={"actor": admin, "role": "A"})
        assert r.status_code == 400
        # 改名 + 重置密码 + 停用普通用户
        target = await _make_user()
        users = (await ac.get(f"/api/admin/users?actor={admin}")).json()["users"]
        tid = [u for u in users if u["name"] == target][0]["id"]
        r = await ac.put(f"/api/admin/users/{tid}", json={
            "actor": admin, "name": target + "-x", "password": "newpass123",
                         "role": "C", "active": False})
        assert r.status_code == 200
        users = (await ac.get(f"/api/admin/users?actor={admin}")).json()["users"]
        t = [u for u in users if u["id"] == tid][0]
        assert t["name"] == target + "-x" and t["role"] == "C" and t["active"] is False
        # 删除无数据的用户
        r = await ac.delete(f"/api/admin/users/{tid}?actor={admin}")
        assert r.status_code == 200


# ---------- 修改自己的密码 ----------

@pytest.mark.asyncio
async def test_change_own_password():
    name = await _make_user()
    async with _client() as ac:
        # 旧密码错误被拒
        r = await ac.post("/api/auth/change_password", json={
            "username": name, "old_password": "wrong", "new_password": "newpass123"})
        assert r.json()["ok"] is False
        # 太短被拒
        r = await ac.post("/api/auth/change_password", json={
            "username": name, "old_password": "pw-123456", "new_password": "short"})
        assert r.json()["ok"] is False
        # 正确流程
        r = await ac.post("/api/auth/change_password", json={
            "username": name, "old_password": "pw-123456", "new_password": "newpass123"})
        assert r.json()["ok"] is True
        # 新密码能登录，旧密码不能
        r = await ac.post("/api/auth/login",
                          json={"username": name, "password": "newpass123"})
        assert r.json()["ok"] is True
        r = await ac.post("/api/auth/login",
                          json={"username": name, "password": "pw-123456"})
        assert r.json()["ok"] is False


# ---------- 系统默认提示词（admin 覆盖） ----------

@pytest.mark.asyncio
async def test_system_prompt_admin_only_and_resolution():
    admin = await _make_user(role="admin")
    plain = await _make_user()
    async with _client() as ac:
        # 普通用户不能改系统默认
        r = await ac.put("/api/prompts/system", json={
            "actor": plain, "stage": "draft_gen", "mode": "general", "content": "x"})
        assert r.status_code == 403
        # admin 覆盖
        r = await ac.put("/api/prompts/system", json={
            "actor": admin, "stage": "draft_gen", "mode": "general",
            "content": "admin 改过的系统默认"})
        assert r.status_code == 200
        # catalog 反映覆盖 + customized 标记
        cat = (await ac.get("/api/prompts/catalog")).json()["stages"]
        item = [i for s in cat if s["stage"] == "draft_gen"
                for i in s["items"] if i["mode"] == "general"][0]
        assert item["system"] == "admin 改过的系统默认" and item["customized"] is True
        # 解析顺序：无用户自定义时 → admin 覆盖；其它模式不受影响
        assert await get_effective_prompt("draft_gen", "general", None) == \
            "admin 改过的系统默认"
        assert await get_effective_prompt("draft_gen", "single", None) == \
            default_prompt("draft_gen", "single")
        # 用户自定义启用时优先于 admin 覆盖
        await ac.post("/api/prompts", json={
            "actor": plain, "stage": "draft_gen", "mode": "general",
            "name": "我的", "content": "用户自定义", "is_active": True})
        async with SessionLocal() as s:
            uid = (await s.execute(text("SELECT id FROM users WHERE name = :n"),
                                   {"n": plain})).scalar()
        assert await get_effective_prompt("draft_gen", "general", uid) == "用户自定义"
        # 系统覆盖行不出现在自定义列表里
        ps = (await ac.get(f"/api/prompts?actor={admin}")).json()["prompts"]
        assert all(p["owner_id"] for p in ps)
        # admin 恢复内置默认
        r = await ac.delete("/api/prompts/system?actor=" + admin
                            + "&stage=draft_gen&mode=general")
        assert r.status_code == 200
        item = [i for s in (await ac.get("/api/prompts/catalog")).json()["stages"]
                if s["stage"] == "draft_gen"
                for i in s["items"] if i["mode"] == "general"][0]
        assert item["customized"] is False
        assert item["system"] == default_prompt("draft_gen", "general")


# ---------- admin 密码验证（debug 开关） ----------

@pytest.mark.asyncio
async def test_verify_admin_password():
    admin = await _make_user(role="admin")
    plain = await _make_user()  # 密码同为 pw-123456，但角色不是 admin
    async with _client() as ac:
        # admin 的密码通过
        r = await ac.post("/api/auth/verify_admin", json={"password": "pw-123456"})
        assert r.json()["ok"] is True and r.json()["name"] == admin
        # 错误密码拒绝
        r = await ac.post("/api/auth/verify_admin", json={"password": "nope"})
        assert r.json()["ok"] is False
        # 停用的 admin 不再通过
        async with SessionLocal() as s:
            await s.execute(text("UPDATE users SET active = false WHERE name = :n"),
                            {"n": admin})
            await s.commit()
        r = await ac.post("/api/auth/verify_admin", json={"password": "pw-123456"})
        assert r.json()["ok"] is False
