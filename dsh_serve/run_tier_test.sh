#!/usr/bin/env bash
# 三级链路实测 2：主路由指死端口，验证降级到备1 kimi-k3（开放平台，已充值）。
set -e
CODE="/c/Users/Zhang/OneDrive/Desktop/ANJU项目/图文平台_20260903/tp2create1.0/code"
export DEEPSEEK_BASE_URL="http://127.0.0.1:9"
export DSH_SERVE_PORT=8902
export MCP_ENABLED=false
export DSH_HOME="$(dirname "$CODE")/dsh-home"
cd "$CODE"
exec ./dsh_serve/.venv/Scripts/python.exe -m dsh_serve
