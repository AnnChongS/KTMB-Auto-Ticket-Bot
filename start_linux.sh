#!/usr/bin/env bash
# ============================================================
#  KTMB Ticket Commander - Linux launcher
#  usage:  ./start_linux.sh            start web panel + bot
#          ./start_linux.sh stop       stop web panel (bot logs out first)
#          ./start_linux.sh status     show status
#          ./start_linux.sh restart    restart
# ============================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"
PY="$VENV_DIR/bin/python"
BROWSERS_DIR="$SCRIPT_DIR/browsers"
PID_FILE="$SCRIPT_DIR/.ktmb_web.pid"
LOG_FILE="$SCRIPT_DIR/web_server.log"

export PYTHONIOENCODING=utf-8
export PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR"
WEB_PORT="${KTMB_WEB_PORT:-5000}"
WEB_HOST="${KTMB_WEB_HOST:-127.0.0.1}"

info()  { printf '\033[36m[系统]\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m[警告]\033[0m %s\n' "$*"; }
error() { printf '\033[31m[错误]\033[0m %s\n' "$*"; }

port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1$"
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1 && { exec 3>&-; return 0; } || return 1
  fi
}

pick_port() {
  local p="$1"
  while port_busy "$p"; do p=$((p + 1)); done
  echo "$p"
}

kill_stray_bots() {
  # 面板被强杀 / 重启时，机器人可能变成"孤儿进程"，这里顺手收掉
  local pids
  pids="$(pgrep -u "$(id -u)" -f 'ktmb_auto\.py' 2>/dev/null || true)"
  [ -z "$pids" ] && return 0
  warn "发现残留的机器人进程: $pids，正在安全停止..."
  printf '{"action":"logout"}' > "${TMPDIR:-/tmp}/ktmb_remote_command.json" 2>/dev/null || true
  for pid in $pids; do
    kill -TERM "$pid" 2>/dev/null
  done
  for _ in $(seq 1 30); do
    pids="$(pgrep -u "$(id -u)" -f 'ktmb_auto\.py' 2>/dev/null || true)"
    [ -z "$pids" ] && break
    sleep 1
  done
  pids="$(pgrep -u "$(id -u)" -f 'ktmb_auto\.py' 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    warn "仍有机器人进程未退出，强制结束: $pids"
    kill -KILL $pids 2>/dev/null
  fi
}

stop_panel() {
  if [ ! -f "$PID_FILE" ]; then
    warn "没有找到 PID 文件，服务可能未通过本脚本启动"
    kill_stray_bots
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    info "正在停止 Web 面板 (PID $pid)，机器人会先安全登出 KTMB..."
    kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 60); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      warn "进程未响应，强制结束"
      kill -KILL "$pid" 2>/dev/null
    fi
    info "已停止"
  else
    warn "PID 文件中的进程不存在"
  fi
  rm -f "$PID_FILE"
  kill_stray_bots
}

ensure_venv() {
  if [ ! -x "$PY" ]; then
    info "创建虚拟环境..."
    python3 -m venv "$VENV_DIR" || { error "创建 venv 失败，请先安装 python3-venv"; exit 1; }
  fi
}

ensure_python_deps() {
  info "安装 Python 依赖..."
  "$PY" -m pip install -q --disable-pip-version-check -r requirements.txt || {
    error "依赖安装失败"; exit 1; }
}

ensure_system_libs() {
  local missing=0
  for pkg in libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1 libasound2 libxshmfence1 libcups2; do
    dpkg -l 2>/dev/null | grep -q "^ii  $pkg " || missing=1
  done
  [ "$missing" -eq 0 ] && return 0
  if ! command -v apt-get >/dev/null 2>&1; then
    warn "缺少浏览器系统依赖，且未检测到 apt-get，请手动安装"
    return 0
  fi
  info "补充安装 Chromium 系统依赖（需要 sudo）..."
  sudo apt-get update -qq && sudo apt-get install -y -qq \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1 libasound2 || \
    warn "系统依赖安装未完成，若浏览器启动失败请手动安装"
}

ensure_browser() {
  if ls "$BROWSERS_DIR"/chromium-* >/dev/null 2>&1; then
    info "Playwright 浏览器已就绪"
    return 0
  fi
  info "下载 Playwright Chromium..."
  "$PY" -m playwright install chromium || { error "下载浏览器失败"; exit 1; }
}

start_panel() {
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
    warn "服务已在运行 (PID $(cat "$PID_FILE"))，如需重启请用 ./start_linux.sh restart"
    exit 0
  fi

  if port_busy "$WEB_PORT"; then
    local newport
    newport="$(pick_port "$WEB_PORT")"
    warn "端口 $WEB_PORT 已被占用，改用 $newport"
    WEB_PORT="$newport"
  fi

  ensure_venv
  ensure_python_deps
  ensure_system_libs
  ensure_browser

  info "启动 Web 管理面板: http://$WEB_HOST:$WEB_PORT"
  info "远程控制页面: http://$WEB_HOST:$WEB_PORT/remote"
  KTMB_WEB_PORT="$WEB_PORT" KTMB_WEB_HOST="$WEB_HOST" \
    nohup "$PY" app.py >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"

  sleep 3
  if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo
    echo "==================================================="
    echo "  🎉 启动成功"
    echo "  🌐 管理面板 : http://$WEB_HOST:$WEB_PORT"
    echo "  🎮 远程控制 : http://$WEB_HOST:$WEB_PORT/remote"
  echo "  🔐 默认密码 : admin123 (可用 KTMB_WEB_PASSWORD 修改)"
    echo "  📋 面板日志 : tail -f $LOG_FILE"
    echo "  🛑 停止服务 : ./start_linux.sh stop"
    echo "==================================================="
  else
    error "启动失败，请查看 $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi
}

case "${1:-start}" in
  start)   start_panel ;;
  stop)    stop_panel ;;
  restart) stop_panel; sleep 1; start_panel ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
      info "运行中 (PID $(cat "$PID_FILE"))"
    else
      info "未运行"
    fi
    ;;
  *) echo "用法: $0 {start|stop|restart|status}"; exit 1 ;;
esac
