from sqlalchemy import text


async def get_or_create_user(session, name: str, role: str):
    """按名字查找用户，不存在则创建，返回 user id（UUID）。"""
    result = await session.execute(
        text("SELECT id FROM users WHERE name = :name LIMIT 1"), {"name": name})
    row = result.first()
    if row:
        return row[0]
    uid = (await session.execute(
        text("INSERT INTO users (name, role) VALUES (:name, :role) RETURNING id"),
        {"name": name, "role": role})).scalar_one()
    return uid
