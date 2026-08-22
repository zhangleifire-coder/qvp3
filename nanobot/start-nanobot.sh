#!/usr/bin/env bash
# 启动 Nanobot 创作网关（OpenAI 兼容 API :8900）
# 密钥从 code/.env 读取后注入 nanobot 进程环境（config.json 里用 ${VAR} 引用）
set -a
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  # 只导出 KEY/URL 类变量，避免把带空格/中文的值弄坏
  grep -E '^[A-Z_]+=' .env | grep -E 'API_KEY|TOKEN' > /dev/null && \
  eval "$(grep -E '^[A-Z_]+=' .env | grep -E 'API_KEY|TOKEN' | sed 's/^/export /')"
fi
set +a
exec .venv/Scripts/nanobot serve --config nanobot/config.json \
  --host 127.0.0.1 --port 8900 --timeout 2400 "$@"
