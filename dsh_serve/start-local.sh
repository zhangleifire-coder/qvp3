#!/usr/bin/env bash
# dsh_serve 本地联调启动脚本（常驻）。密钥由组件 pydantic 配置从 code/.env
# 直接读取（cwd=code 根），本脚本不含也不回显任何密钥。
# 用法：bash dsh_serve/start-local.sh   （前台）；后台：nohup bash ... &
set -e
CODE="$(cd "$(dirname "$0")/.." && pwd)"
export DSH_HOME="$(dirname "$CODE")/dsh-home"        # 固定目录：tp2create1.0/dsh-home/
export DSH_SERVE_PORT=8901
export DSH_MAX_CONCURRENT=4
export MCP_ENABLED=true
export MCP_COMMAND="$CODE/.venv/Scripts/python.exe"
export MCP_ARGS='["-m", "qvp_mcp"]'
export MCP_PYTHONPATH="$CODE"
export MCP_TOOL_TIMEOUT_MS=1800000
export MCP_EXTRA_ENV='{"LITELLM_LOCAL_MODEL_COST_MAP": "true"}'
export DSH_WORKSPACE="$CODE"
cd "$CODE"
exec ./dsh_serve/.venv/Scripts/python.exe -m dsh_serve
