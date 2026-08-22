#!/bin/sh
set -e

echo "=== 初始化数据库（建表 + 账号，幂等）==="
python init_db.py

echo "=== 启动 Web 服务 ==="
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000
