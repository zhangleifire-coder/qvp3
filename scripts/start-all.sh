#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# 图文生产平台 · 一键启停（Git Bash on Windows）
#
# 用法：
#   bash scripts/start-all.sh          # 启动（默认）：PG → Nanobot → 后端，幂等
#   bash scripts/start-all.sh stop     # 停止 Nanobot + 后端（PG 保留，其它项目可能在用）
#   bash scripts/start-all.sh status   # 只看状态，不动任何服务
#   双击 start.bat = start
# ─────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")/.."

# litellm 启动时不再远程拉价表（本机到 raw.githubusercontent.com 超时 ~13s，
# 拖慢每次后端重启；价表本就走本地兜底 + DB 费率表 020）
export LITELLM_LOCAL_MODEL_COST_MAP=true

ACTION="${1:-start}"
BACKEND_PORT=8003
NANOBOT_PORT=8900
PG_CONTAINER=qvp-postgres
DOCKER_DESKTOP="/c/Program Files/Docker/Docker/Docker Desktop.exe"

GREEN='\033[32m'; RED='\033[31m'; YELLOW='\033[33m'; DIM='\033[2m'; OFF='\033[0m'
ok()   { printf "  ${GREEN}✓${OFF} %s\n" "$1"; }
bad()  { printf "  ${RED}✗${OFF} %s\n" "$1"; }
skip() { printf "  ${YELLOW}→${OFF} %s\n" "$1"; }
info() { printf "  %s\n" "$1"; }

# ── 工具函数 ──────────────────────────────────────────────────────────
http_up() { curl -s -m 3 "$1" >/dev/null 2>&1; }

port_pid() {
  netstat -ano 2>/dev/null | grep ":$1[[:space:]].*LISTENING" | awk '{print $5}' | head -1
}

wait_http() {  # $1=url  $2=超时秒  $3=名称
  local deadline=$((SECONDS + $2))
  while [ $SECONDS -lt $deadline ]; do
    http_up "$1" && return 0
    sleep 2
  done
  bad "$3 在 ${2}s 内未就绪（看日志：$4）"
  return 1
}

docker_ready() { docker info >/dev/null 2>&1; }

# ── status ───────────────────────────────────────────────────────────
do_status() {
  echo "── 平台状态 ──────────────────────────────"
  if docker_ready; then
    st=$(docker ps -a --filter "name=^${PG_CONTAINER}$" --format "{{.Status}}" 2>/dev/null)
    [ -n "$st" ] && ok "PostgreSQL 容器（:5433）：$st" || bad "PostgreSQL 容器不存在（首次部署见 docs/改造说明）"
  else
    bad "Docker 引擎未运行"
  fi
  if http_up "http://127.0.0.1:${NANOBOT_PORT}/health"; then
    ok "Nanobot 创作网关（:${NANOBOT_PORT}）"
  else
    bad "Nanobot（:${NANOBOT_PORT}）未运行"
  fi
  if http_up "http://127.0.0.1:${BACKEND_PORT}/healthz"; then
    ok "后端 FastAPI（:${BACKEND_PORT}）"
  else
    bad "后端（:${BACKEND_PORT}）未运行"
  fi
  echo "──────────────────────────────────────────"
  echo -e "  平台地址  ${DIM}http://127.0.0.1:${BACKEND_PORT}${OFF}"
  echo -e "  登录账号  ${DIM}张三/李四/王五（1qaz@WSX）· admin（root_admin_1234）${OFF}"
}

# ── start ────────────────────────────────────────────────────────────
do_start() {
  echo "── 启动平台 ──────────────────────────────"

  # 1) Docker 引擎
  if docker_ready; then
    ok "Docker 引擎已运行"
  elif [ -f "$DOCKER_DESKTOP" ]; then
    info "Docker 引擎未运行，拉起 Docker Desktop…（首次约 30-60s）"
    cmd //c start "" "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe" >/dev/null 2>&1
    local deadline=$((SECONDS + 120))
    until docker_ready || [ $SECONDS -ge $deadline ]; do sleep 5; done
    docker_ready && ok "Docker 引擎就绪" || { bad "Docker 引擎 120s 未就绪，手动打开 Docker Desktop 后重试"; exit 1; }
  else
    bad "Docker 未安装或路径非默认，请手动启动后重试"; exit 1
  fi

  # 2) PostgreSQL 容器
  local pg_state
  pg_state=$(docker ps -a --filter "name=^${PG_CONTAINER}$" --format "{{.State}}" 2>/dev/null)
  if [ "$pg_state" = "running" ]; then
    ok "PostgreSQL 容器运行中"
  elif [ -n "$pg_state" ]; then
    info "PostgreSQL 容器 $pg_state，启动中…"
    docker start "$PG_CONTAINER" >/dev/null 2>&1 || { bad "PG 容器启动失败"; exit 1; }
    ok "PostgreSQL 容器已启动"
  else
    bad "找不到容器 $PG_CONTAINER（首次部署命令见 docs/改造说明-Nanobot全链Agent.md 第五节）"
    exit 1
  fi
  for i in $(seq 1 15); do
    docker exec "$PG_CONTAINER" pg_isready -U qvp -d qvp >/dev/null 2>&1 && break
    sleep 2
  done
  docker exec "$PG_CONTAINER" pg_isready -U qvp -d qvp >/dev/null 2>&1 \
    && ok "PostgreSQL 接受连接（:5433）" || { bad "PG 30s 未就绪"; exit 1; }

  # 3) Nanobot 网关
  if http_up "http://127.0.0.1:${NANOBOT_PORT}/health"; then
    skip "Nanobot 已在运行，跳过"
  else
    local stale_pid; stale_pid=$(port_pid "$NANOBOT_PORT")
    [ -n "$stale_pid" ] && taskkill //F //PID "$stale_pid" >/dev/null 2>&1
    info "启动 Nanobot 创作网关（:${NANOBOT_PORT}）…"
    mkdir -p nanobot
    nohup bash nanobot/start-nanobot.sh > nanobot/nanobot.log 2>&1 &
    wait_http "http://127.0.0.1:${NANOBOT_PORT}/health" 60 "Nanobot" "nanobot/nanobot.log" || exit 1
    ok "Nanobot 就绪（qvp_mcp 工具随进程挂载）"
  fi

  # 4) 后端
  if http_up "http://127.0.0.1:${BACKEND_PORT}/healthz"; then
    skip "后端已在运行，跳过"
  else
    local stale_pid; stale_pid=$(port_pid "$BACKEND_PORT")
    [ -n "$stale_pid" ] && taskkill //F //PID "$stale_pid" >/dev/null 2>&1
    info "启动后端 FastAPI（:${BACKEND_PORT}）…"
    nohup .venv/Scripts/python -m uvicorn src.api.main:app \
      --host 127.0.0.1 --port "$BACKEND_PORT" > backend.log 2>&1 &
    wait_http "http://127.0.0.1:${BACKEND_PORT}/healthz" 40 "后端" "backend.log" || exit 1
    ok "后端就绪"
  fi

  echo "──────────────────────────────────────────"
  do_status
}

# ── stop ─────────────────────────────────────────────────────────────
do_stop() {
  echo "── 停止平台（PG 容器保留） ───────────────"
  local pid
  pid=$(port_pid "$NANOBOT_PORT")
  if [ -n "$pid" ]; then taskkill //F //PID "$pid" >/dev/null 2>&1 && ok "Nanobot 已停止（PID $pid）"; else skip "Nanobot 未在运行"; fi
  pid=$(port_pid "$BACKEND_PORT")
  if [ -n "$pid" ]; then taskkill //F //PID "$pid" >/dev/null 2>&1 && ok "后端已停止（PID $pid）"; else skip "后端未在运行"; fi
  info "PostgreSQL 容器保留运行（其它进程可能依赖；如需停止：docker stop $PG_CONTAINER）"
  echo "──────────────────────────────────────────"
}

case "$ACTION" in
  start) do_start ;;
  stop)  do_stop ;;
  status) do_status ;;
  *) echo "用法: bash scripts/start-all.sh [start|stop|status]"; exit 1 ;;
esac
