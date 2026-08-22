"""Docker 首次启动时的数据库初始化：建表 + 三个账号（幂等，可重复执行）。"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg

USERS = [("张三", "A"), ("李四", "B"), ("王五", "C")]


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://qvp:qvp@postgres:5432/qvp")
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def main() -> None:
    dsn = _dsn()
    conn = None
    for attempt in range(30):
        try:
            conn = await asyncpg.connect(dsn)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[init] 等待数据库就绪 ({attempt + 1}/30): {exc}")
            await asyncio.sleep(2)
    if conn is None:
        print("[init] 数据库连接失败，退出")
        sys.exit(1)

    try:
        # 按文件名顺序应用全部迁移（均幂等）；排除 macOS AppleDouble（._*）等垃圾文件
        migrations = [m for m in sorted(Path("migrations").glob("*.sql"))
                      if not m.name.startswith(".")]
        for m in migrations:
            await conn.execute(m.read_text())
        print(f"[init] 迁移已应用（{len(migrations)} 个文件）")

        # 账号只补不重置：已存在的用户保留其当前密码（admin 可能在后台改过）
        pw = hashlib.sha256("1qaz@WSX".encode()).hexdigest()
        for name, role in USERS:
            exists = await conn.fetchval("SELECT id FROM users WHERE name = $1", name)
            if not exists:
                await conn.execute(
                    "INSERT INTO users (name, role, password_hash) VALUES ($1, $2, $3)",
                    name, role, pw,
                )
        print("[init] 账号就绪（张三/李四/王五，新部署初始密码 1qaz@WSX，已存在账号不重置）")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
