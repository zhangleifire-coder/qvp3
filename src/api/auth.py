import hashlib
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from src.db.session import SessionLocal
from src.services.activity import log_action

router = APIRouter()


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
async def login(payload: LoginIn):
    async with SessionLocal() as session:
        r = await session.execute(
            text("SELECT id, name, role FROM users WHERE name = :n"),
            {"n": payload.username})
        row = r.first()
        if not row:
            return {"ok": False, "error": "用户不存在"}
        r2 = await session.execute(
            text("SELECT password_hash FROM users WHERE name = :n"),
            {"n": payload.username})
        stored = r2.scalar()
        if stored != hash_password(payload.password):
            await log_action(payload.username, "login_failed", "密码错误")
            return {"ok": False, "error": "密码错误"}
        await log_action(payload.username, "login", "登录成功")
        return {"ok": True, "name": row[1], "role": row[2]}


class RegisterIn(BaseModel):
    username: str
    password: str
    role: str


class ChangePasswordIn(BaseModel):
    username: str
    old_password: str
    new_password: str


class VerifyAdminIn(BaseModel):
    password: str


@router.post("/api/auth/verify_admin")
async def verify_admin(payload: VerifyAdminIn):
    """验证密码是否属于任一在职 admin 账号（前端 debug 开关等轻量提权用）。"""
    h = hash_password(payload.password)
    async with SessionLocal() as session:
        row = (await session.execute(
            text("SELECT name FROM users "
                 "WHERE role = 'admin' AND active AND password_hash = :p"),
            {"p": h})).first()
    if not row:
        return {"ok": False, "error": "密码错误"}
    return {"ok": True, "name": row[0]}


@router.post("/api/auth/change_password")
async def change_password(payload: ChangePasswordIn):
    """用户修改自己的密码：必须验证旧密码。"""
    if len(payload.new_password) < 8:
        return {"ok": False, "error": "新密码至少 8 位"}
    async with SessionLocal() as session:
        stored = (await session.execute(
            text("SELECT password_hash FROM users WHERE name = :n"),
            {"n": payload.username})).scalar()
        if stored is None:
            return {"ok": False, "error": "用户不存在"}
        if stored != hash_password(payload.old_password):
            return {"ok": False, "error": "原密码错误"}
        await session.execute(
            text("UPDATE users SET password_hash = :p WHERE name = :n"),
            {"p": hash_password(payload.new_password), "n": payload.username})
        await session.commit()
        await log_action(payload.username, "change_password", "修改自己的密码")
        return {"ok": True}


@router.post("/api/auth/register")
async def register(payload: RegisterIn):
    async with SessionLocal() as session:
        r = await session.execute(
            text("SELECT id FROM users WHERE name = :n"), {"n": payload.username})
        if r.first():
            return {"ok": False, "error": "用户名已存在"}
        await session.execute(
            text("INSERT INTO users (name, role, password_hash) VALUES (:n, :r, :p)"),
            {"n": payload.username, "r": payload.role, "p": hash_password(payload.password)})
        await session.commit()
        await log_action(payload.username, "register", f"注册账号，角色 {payload.role}")
        return {"ok": True, "name": payload.username, "role": payload.role}
