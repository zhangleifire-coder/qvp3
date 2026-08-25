#!/bin/sh
# qvp2 应用容器入口：等库建表 → 起 Nanobot(:8900 仅容器内,守护重启) → 起 后端(:8003 对外)
set -e
cd /app
mkdir -p static/generated exports

echo "[entrypoint] 初始化数据库（迁移 001-008 + 账号，幂等）…"
python init_db.py

echo "[entrypoint] 启动 Nanobot 创作网关 :8900（守护循环）…"
(
  while true; do
    nanobot serve --config deploy/nanobot.config.server.json \
      --host 127.0.0.1 --port 8900 --timeout 2400
    echo "[entrypoint] Nanobot 退出，2s 后重启" >&2
    sleep 2
  done
) &

# 等 Nanobot 端口就绪（最长 90s）
i=0
while [ $i -lt 90 ]; do
  if python -c "import socket;s=socket.create_connection(('127.0.0.1',8900),2)" 2>/dev/null; then
    echo "[entrypoint] Nanobot 就绪"; break
  fi
  i=$((i+1)); sleep 1
done

echo "[entrypoint] 启动后端 :8003 …"
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8003
