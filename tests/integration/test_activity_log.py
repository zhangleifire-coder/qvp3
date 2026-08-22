"""操作审计日志：埋点写入 + 可见性（用户看自己 / admin 看全部）。"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text

from src.api.auth import hash_password
from src.api.main import app
from src.db.session import SessionLocal


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(name=None, role="A", pw="pw-123456"):
    name = name or f"u-{_uniq()}"
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO users (name, role, password_hash) VALUES (:n, :r, :p)"),
            {"n": name, "r": role, "p": hash_password(pw)})
        await s.commit()
    return name


@pytest.mark.asyncio
async def test_login_and_actions_are_logged():
    name = await _make_user()
    async with _client() as ac:
        await ac.post("/api/auth/login", json={"username": name, "password": "wrong"})
        await ac.post("/api/auth/login", json={"username": name, "password": "pw-123456"})
        await ac.post("/api/tasks/import_queries", json={
            "queries": ["测试日志 query"], "mode": "general", "actor": name})
        r = await ac.get(f"/api/activity?actor={name}")
        actions = [l["action"] for l in r.json()["logs"]]
        assert "login_failed" in actions and "login" in actions
        assert "import_tasks" in actions
        imp = [l for l in r.json()["logs"] if l["action"] == "import_tasks"][0]
        assert "测试日志 query" in imp["detail"]


@pytest.mark.asyncio
async def test_visibility_user_sees_own_admin_sees_all():
    u1 = await _make_user()
    u2 = await _make_user()
    admin = await _make_user(role="admin")
    async with _client() as ac:
        await ac.post("/api/auth/login", json={"username": u1, "password": "pw-123456"})
        await ac.post("/api/auth/login", json={"username": u2, "password": "pw-123456"})
        # 普通用户只能看自己（即使带 user 参数也被收敛）
        r = await ac.get(f"/api/activity?actor={u1}&user={u2}")
        assert all(l["actor_name"] == u1 for l in r.json()["logs"])
        assert r.json()["is_admin"] is False
        # admin 看全部
        r = await ac.get(f"/api/activity?actor={admin}")
        names = {l["actor_name"] for l in r.json()["logs"]}
        assert {u1, u2} <= names and r.json()["is_admin"] is True
        # admin 按用户过滤
        r = await ac.get(f"/api/activity?actor={admin}&user={u2}")
        assert all(l["actor_name"] == u2 for l in r.json()["logs"])
        # 未登录/不存在用户
        r = await ac.get("/api/activity?actor=ghost")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_action_filter_and_admin_ops_logged():
    admin = await _make_user(role="admin")
    async with _client() as ac:
        await ac.post("/api/auth/login", json={"username": admin, "password": "pw-123456"})
        # admin 建一个用户 → user_create 落日志
        await ac.post("/api/admin/users", json={
            "actor": admin, "name": f"n-{_uniq()}", "password": "abc12345", "role": "B"})
        r = await ac.get(f"/api/activity?actor={admin}&action=user_create")
        assert r.json()["total"] == 1
        assert r.json()["logs"][0]["action"] == "user_create"
        # 动作过滤器不影响其它动作
        r = await ac.get(f"/api/activity?actor={admin}&action=login")
        assert all(l["action"] == "login" for l in r.json()["logs"])
