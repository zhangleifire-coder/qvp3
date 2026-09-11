#!/bin/sh
# qvp3 应用容器入口：等库建表 → 起 dsh_serve(:8901 仅容器内,守护重启) → 起 后端(:8005 对外)
# 与 qvp2 entrypoint.sh 的差异：Nanobot(:8900) 替换为 dsh_serve(:8901)。
set -e
cd /app
mkdir -p static/generated exports /data/dsh

echo "[entrypoint] 初始化数据库（迁移幂等应用）…"
python init_db.py

echo "[entrypoint] 启动 dsh_serve 创作网关 :${DSH_SERVE_PORT:-8901}（守护循环）…"
(
  while true; do
    python -m dsh_serve
    echo "[entrypoint] dsh_serve 退出，2s 后重启" >&2
    sleep 2
  done
) &

# 等 dsh_serve 端口就绪（最长 90s）
i=0
while [ $i -lt 90 ]; do
  if python -c "import socket;s=socket.create_connection(('127.0.0.1',${DSH_SERVE_PORT:-8901}),2);s.close()" 2>/dev/null; then
    echo "[entrypoint] dsh_serve 就绪"; break
  fi
  i=$((i+1)); sleep 1
done

echo "[entrypoint] 启动后端 :8005 …"
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8005
