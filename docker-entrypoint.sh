#!/bin/sh
set -e

# litellm 启动时不再远程拉价表（容器外网到 raw.githubusercontent.com 可能超时，
# 拖慢每次容器启动；价表本就走本地兜底 + DB 费率表）
export LITELLM_LOCAL_MODEL_COST_MAP=true

echo "=== 初始化数据库（建表 + 账号，幂等）==="
python init_db.py

echo "=== 启动 Web 服务 ==="
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000
