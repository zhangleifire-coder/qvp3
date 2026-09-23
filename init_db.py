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

        # WS3 公共风格库单一事实来源：data/styles.json → 公共条目幂等 upsert。
        # 纪律：公共库改动一律改 json；管理页只管个人库与临时停用（enabled 不被冲掉）。
        sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
        from sync_styles import sync_public_styles
        n = await sync_public_styles(conn)
        print(f"[init] 公共风格库已同步 {n} 条（data/styles.json）")

        # 页型模板库预置模板（2026-09-24）：data/compose_templates.json →
        # compose_templates 表幂等同步；失败不阻塞（template_select 有种子文件回退）
        try:
            from src.services.compose_templates import sync_seeds
            n2 = await sync_seeds()
            print(f"[init] compose 模板种子已同步（{n2} 条）")
        except Exception as exc:  # noqa: BLE001
            print(f"[init] compose 模板种子同步失败（不阻塞）: {exc}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
