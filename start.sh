#!/bin/bash
# 产能验证平台 一键启动脚本
# 用法：bash start.sh
set -e

echo "=== 产能验证平台 启动 ==="
PGBIN="/opt/homebrew/opt/postgresql@16/bin"

# 1. 启动 PostgreSQL
echo "--- 1. 检查 PostgreSQL ---"
if pg_isready >/dev/null 2>&1; then
  echo "PostgreSQL 已在运行"
else
  echo "启动 PostgreSQL..."
  brew services start postgresql@16 2>/dev/null || "$PGBIN/pg_ctl" -D /opt/homebrew/var/postgresql@16 -l /tmp/pg16.log start
  sleep 2
fi

# 2. 启动 Redis
echo "--- 2. 检查 Redis ---"
if redis-cli ping >/dev/null 2>&1; then
  echo "Redis 已在运行"
else
  echo "启动 Redis..."
  brew services start redis 2>/dev/null || redis-server --daemonize yes
  sleep 1
fi

# 3. 初始化数据库（首次）
echo "--- 3. 检查数据库 ---"
if PGPASSWORD=qvp "$PGBIN/psql" -h localhost -U qvp -d qvp -c "SELECT 1" >/dev/null 2>&1; then
  echo "数据库 qvp 已就绪"
else
  echo "创建数据库和用户..."
  "$PGBIN/psql" -d postgres -c "CREATE USER qvp WITH PASSWORD 'qvp';" 2>/dev/null || true
  "$PGBIN/psql" -d postgres -c "CREATE DATABASE qvp OWNER qvp;" 2>/dev/null || true
fi

# 4. 初始化表（首次，幂等）
echo "--- 4. 初始化数据表 ---"
PGPASSWORD=qvp "$PGBIN/psql" -h localhost -U qvp -d qvp -f migrations/001_initial_schema.sql >/dev/null 2>&1
echo "表结构已就绪（21 张表）"

# 5. 初始化三个用户（幂等）
echo "--- 5. 初始化账号 ---"
uv run python -c "
import asyncio, hashlib
from sqlalchemy import text
from src.db.session import SessionLocal

async def main():
    async with SessionLocal() as s:
        pw = hashlib.sha256('1qaz@WSX'.encode()).hexdigest()
        for n, r in [('张三','A'),('李四','B'),('王五','C')]:
            r0 = await s.execute(text('SELECT id FROM users WHERE name=:n'), {'n':n})
            if r0.first():
                await s.execute(text('UPDATE users SET password_hash=:p, role=:r WHERE name=:n'), {'p':pw,'r':r,'n':n})
            else:
                await s.execute(text('INSERT INTO users (name, role, password_hash) VALUES (:n,:r,:p)'), {'n':n,'r':r,'p':pw})
        await s.commit()
asyncio.run(main())
" 2>/dev/null
echo "账号就绪（张三/李四/王五，密码 1qaz@WSX）"

# 6. 启动 OpenSERP（搜图，可选）
echo "--- 6. 检查 OpenSERP ---"
if curl -s http://127.0.0.1:7001/health >/dev/null 2>&1; then
  echo "OpenSERP 已运行（端口 7001）"
else
  echo "启动 OpenSERP（搜图服务）..."
  docker rm -f openserp >/dev/null 2>&1 || true
  docker run -d --name openserp -p 7001:7000 karust/openserp serve -a 0.0.0.0 -p 7000 >/dev/null 2>&1 || echo "OpenSERP 启动失败（可跳过，搜图会用默认）"
fi

# 7. 启动 Web 服务
echo "--- 7. 启动 Web 服务 ---"
IP=$(ipconfig getifaddr en0 2>/dev/null || echo "127.0.0.1")
echo ""
echo "=============================================="
echo "  服务启动中..."
echo "  本机访问:   http://localhost:8000"
echo "  局域网访问: http://$IP:8000"
echo "  登录账号:   张三 / 李四 / 王五（密码 1qaz@WSX）"
echo "=============================================="
echo ""
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000
