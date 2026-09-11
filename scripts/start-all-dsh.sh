#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# 图文生产平台 · dsh 版一键启停（Git Bash on Windows）
#
# 与 start-all.sh 的差异：Nanobot(:8900) 替换为 dsh_serve(:8901)，
# 后端以 NANOBOT_BASE_URL=http://127.0.0.1:8901/v1 启动（协议 1:1，后端零改动）。
# Nanobot 可并存（回退用），本脚本不管它。
#
# 用法：
#   bash scripts/start-all-dsh.sh          # 启动：PG → dsh_serve → 后端(:8003)
#   bash scripts/start-all-dsh.sh stop     # 停止 dsh_serve + 后端（PG 保留）
#   bash scripts/start-all-dsh.sh status   # 只看状态
# ─────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")/.."

# litellm 启动时不再远程拉价表（本机到 raw.githubusercontent.com 超时 ~13s，
# 拖慢每次后端重启；价表本就走本地兜底 + DB 费率表 020）
export LITELLM_LOCAL_MODEL_COST_MAP=true

ACTION="${1:-start}"
BACKEND_PORT=8003
DSH_PORT=8901
PG_CONTAINER=qvp-postgres
DOCKER_DESKTOP="/c/Program Files/Docker/Docker/Docker Desktop.exe"

GREEN='\033[32m'; RED='\033[31m'; YELLOW='\033[33m'; DIM='\033[2m'; OFF='\033[0m'
ok()   { printf "  ${GREEN}✓${OFF} %s\n" "$1"; }
bad()  { printf "  ${RED}✗${OFF} %s\n" "$1"; }
skip() { printf "  ${YELLOW}→${OFF} %s\n" "$1"; }
info() { printf "  %s\n" "$1"; }

http_up() { curl -s -m 3 "$1" >/dev/null 2>&1; }

port_pid() {
  netstat -ano 2>/dev/null | grep ":$1[[:space:]].*LISTENING" | awk '{print $5}' | head -1
}

wait_http() {  # $1=url  $2=超时秒  $3=名称  $4=日志提示
  local deadline=$((SECONDS + $2))
  while [ $SECONDS -lt $deadline ]; do
    http_up "$1" && return 0
    sleep 2
  done
  bad "$3 在 ${2}s 内未就绪（看日志：$4）"
  return 1
}

docker_ready() { docker info >/dev/null 2>&1; }

do_status() {
  echo "── 平台状态（dsh 版）────────────────────"
  if docker_ready; then
    st=$(docker ps -a --filter "name=^${PG_CONTAINER}$" --format "{{.Status}}" 2>/dev/null)
    [ -n "$st" ] && ok "PostgreSQL 容器（:5433）：$st" || bad "PostgreSQL 容器不存在"
  else
    bad "Docker 引擎未运行"
  fi
  if http_up "http://127.0.0.1:${DSH_PORT}/health"; then
    ok "dsh_serve 创作网关（:${DSH_PORT}）"
  else
    bad "dsh_serve（:${DSH_PORT}）未运行"
  fi
  if http_up "http://127.0.0.1:${BACKEND_PORT}/healthz"; then
    ok "后端 FastAPI（:${BACKEND_PORT}）"
  else
    bad "后端（:${BACKEND_PORT}）未运行"
  fi
  echo "──────────────────────────────────────────"
  echo -e "  平台地址  ${DIM}http://127.0.0.1:${BACKEND_PORT}${OFF}"
  echo -e "  回退方式  ${DIM}bash scripts/start-all.sh（Nanobot 版编排）${OFF}"
}

do_start() {
  echo "── 启动平台（dsh 版）────────────────────"

  if docker_ready; then
    ok "Docker 引擎已运行"
  elif [ -f "$DOCKER_DESKTOP" ]; then
    info "拉起 Docker Desktop…"
    cmd //c start "" "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe" >/dev/null 2>&1
    local deadline=$((SECONDS + 120))
    until docker_ready || [ $SECONDS -ge $deadline ]; do sleep 5; done
    docker_ready && ok "Docker 引擎就绪" || { bad "Docker 120s 未就绪"; exit 1; }
  else
    bad "Docker 未安装"; exit 1
  fi

  local pg_state
  pg_state=$(docker ps -a --filter "name=^${PG_CONTAINER}$" --format "{{.State}}" 2>/dev/null)
  if [ "$pg_state" = "running" ]; then
    ok "PostgreSQL 容器运行中"
  elif [ -n "$pg_state" ]; then
    docker start "$PG_CONTAINER" >/dev/null 2>&1 && ok "PostgreSQL 已启动" || { bad "PG 启动失败"; exit 1; }
  else
    bad "找不到容器 $PG_CONTAINER"; exit 1
  fi
  for i in $(seq 1 15); do
    docker exec "$PG_CONTAINER" pg_isready -U qvp -d qvp >/dev/null 2>&1 && break
    sleep 2
  done
  docker exec "$PG_CONTAINER" pg_isready -U qvp -d qvp >/dev/null 2>&1 \
    && ok "PostgreSQL 接受连接（:5433）" || { bad "PG 30s 未就绪"; exit 1; }

  # dsh_serve 创作网关（qvp_mcp 冷启动约 52s，等待时间给足 180s）
  if http_up "http://127.0.0.1:${DSH_PORT}/health"; then
    skip "dsh_serve 已在运行，跳过"
  else
    local stale_pid; stale_pid=$(port_pid "$DSH_PORT")
    [ -n "$stale_pid" ] && taskkill //F //PID "$stale_pid" >/dev/null 2>&1
    info "启动 dsh_serve 创作网关（:${DSH_PORT}）…"
    nohup bash dsh_serve/start-local.sh > dsh_serve/service.log 2>&1 &
    wait_http "http://127.0.0.1:${DSH_PORT}/health" 180 "dsh_serve" "dsh_serve/service.log" || exit 1
    ok "dsh_serve 就绪（qvp_mcp 工具随进程挂载）"
  fi

  # 后端（创作 Agent 调用指向 dsh_serve）
  if http_up "http://127.0.0.1:${BACKEND_PORT}/healthz"; then
    skip "后端已在运行，跳过"
  else
    local stale_pid; stale_pid=$(port_pid "$BACKEND_PORT")
    [ -n "$stale_pid" ] && taskkill //F //PID "$stale_pid" >/dev/null 2>&1
    info "启动后端 FastAPI（:${BACKEND_PORT}，NANOBOT_BASE_URL→dsh_serve）…"
    NANOBOT_BASE_URL="http://127.0.0.1:${DSH_PORT}/v1" \
      nohup .venv/Scripts/python -m uvicorn src.api.main:app \
      --host 127.0.0.1 --port "$BACKEND_PORT" > backend.log 2>&1 &
    wait_http "http://127.0.0.1:${BACKEND_PORT}/healthz" 40 "后端" "backend.log" || exit 1
    ok "后端就绪"
  fi

  echo "──────────────────────────────────────────"
  do_status
}

do_stop() {
  echo "── 停止平台（dsh 版，PG 容器保留） ───────"
  local pid
  pid=$(port_pid "$DSH_PORT")
  [ -n "$pid" ] && taskkill //F //PID "$pid" >/dev/null 2>&1 && ok "dsh_serve 已停止（PID $pid）" || skip "dsh_serve 未在运行"
  pid=$(port_pid "$BACKEND_PORT")
  [ -n "$pid" ] && taskkill //F //PID "$pid" >/dev/null 2>&1 && ok "后端已停止（PID $pid）" || skip "后端未在运行"
  info "PostgreSQL 容器保留运行"
  echo "──────────────────────────────────────────"
}

case "$ACTION" in
  start) do_start ;;
  stop)  do_stop ;;
  status) do_status ;;
  *) echo "用法: bash scripts/start-all-dsh.sh [start|stop|status]"; exit 1 ;;
esac
