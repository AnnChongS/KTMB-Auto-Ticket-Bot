# -*- coding: utf-8 -*-
#
# KTMB Auto Ticket Bot - Web Management Panel
# Copyright (C) 2025 AnnChongS
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
from flask import (Flask, render_template, request, jsonify, session, redirect,
                   url_for, Response)
import json
import os
import subprocess
import sys
import time
import signal
import secrets
import logging
import remote_control
from remote_control import (SCREENSHOT_PATH, send_command, request_screenshot,
                            read_result, screenshot_state, mark_viewer)
import proxy_setup

# ================= 📝 日志系统 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('KTMB_Web')

def _load_secret_key():
    """会话密钥持久化：以前每次重启 app.py 都会随机换 key，所有登录状态失效"""
    env_key = os.environ.get('FLASK_SECRET_KEY')
    if env_key:
        return env_key
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.flask_secret')
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                key = f.read().strip()
            if key:
                return key
        key = secrets.token_hex(32)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(key)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return key
    except Exception:
        return secrets.token_hex(32)


app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

CONFIG_FILE = "config.json"
LOG_FILE = "bot.log"
BOT_PID_FILE = ".ktmb_bot.pid"

# Web 面板访问密码（可通过环境变量设置，默认为 admin123）
WEB_PASSWORD = os.environ.get('KTMB_WEB_PASSWORD', 'admin123')

# 日志最大返回行数
MAX_LOG_LINES = int(os.environ.get('KTMB_MAX_LOG_LINES', '200'))

bot_process = None
bot_log_file_handle = None  # 保存日志文件句柄引用
bot_started_at = None

if not os.path.exists(LOG_FILE):
    open(LOG_FILE, 'w', encoding='utf-8').close()


_BROWSER_PROBE = ("import os,sys;"
                  "from playwright.sync_api import sync_playwright as s;"
                  "pw=s().start();e=pw.chromium.executable_path;pw.stop();"
                  "print(e);sys.exit(0 if os.path.exists(e) else 3)")


# ================= 🧩 机器人进程管理 =================

def _read_bot_pid():
    try:
        with open(BOT_PID_FILE, 'r', encoding='utf-8') as f:
            return int((f.read() or '').strip())
    except Exception:
        return None


def _write_bot_pid(pid):
    try:
        with open(BOT_PID_FILE, 'w', encoding='utf-8') as f:
            f.write(str(pid))
    except Exception:
        pass


def _clear_bot_pid():
    try:
        if os.path.exists(BOT_PID_FILE):
            os.remove(BOT_PID_FILE)
    except Exception:
        pass


def _no_window_kwargs():
    """Windows 上调 tasklist/wmic/taskkill 不要闪黑框"""
    if os.name == 'nt':
        try:
            return {'creationflags': subprocess.CREATE_NO_WINDOW}
        except Exception:
            return {}
    return {}


def _pid_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        try:
            out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}'],
                                 capture_output=True, text=True, timeout=10,
                                 **_no_window_kwargs())
            return str(pid) in (out.stdout or '')
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _pid_is_our_bot(pid):
    """确认这个 PID 真的是 ktmb_auto.py，避免误杀别人的进程"""
    if not pid:
        return False
    try:
        if os.name == 'nt':
            for cmd in (['wmic', 'process', 'where', f'processid={pid}', 'get', 'commandline'],
                        ['powershell', '-NoProfile', '-Command',
                         f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"]):
                try:
                    out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                         **_no_window_kwargs())
                    text = (out.stdout or '') + (out.stderr or '')
                    if 'ktmb_auto.py' in text:
                        return True
                    if 'ProcessId' in text or 'No Instance' in text:
                        return False
                except Exception:
                    continue
            return False
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            return b'ktmb_auto.py' in f.read()
    except Exception:
        # 查不到命令行时保守处理：Windows 上不冒险强杀，Linux 上允许
        return os.name != 'nt'


def _kill_pid(pid):
    if not pid:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                           capture_output=True, text=True, timeout=15,
                           **_no_window_kwargs())
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception as e:
        logger.warning(f"强制结束进程 {pid} 失败: {e}")


def _reap_bot():
    """机器人自己退出后：关掉日志句柄、清掉 PID 文件"""
    global bot_process, bot_log_file_handle, bot_started_at
    if bot_process is not None and bot_process.poll() is not None:
        logger.info(f"机器人进程已退出（退出码 {bot_process.returncode}）")
        if bot_log_file_handle:
            try:
                bot_log_file_handle.close()
            except Exception:
                pass
            bot_log_file_handle = None
        bot_process = None
        bot_started_at = None
        _clear_bot_pid()
        return True
    return False


def _bot_heartbeat_fresh(seconds=20):
    """机器人每几秒就会写一帧截图；文件很新 = 它确实活着

    这是 Windows 上 wmic 被移除 / 权限不足时的兜底判断。
    """
    try:
        state = screenshot_state()
        age = state.get("age")
        pid = state.get("pid")
        if not state.get("has_screenshot") or age is None or age > seconds or not pid:
            return False
        return _pid_alive(int(pid))
    except Exception:
        return False


def bot_is_running():
    """机器人是否在跑（面板重启过也能认出来）"""
    _reap_bot()
    if bot_process is not None and bot_process.poll() is None:
        return True
    pid = _read_bot_pid()
    if pid and _pid_alive(pid) and _pid_is_our_bot(pid):
        return True
    if pid and not _pid_alive(pid):
        _clear_bot_pid()
    return _bot_heartbeat_fresh()


def ensure_playwright_browser(env):
    """确保机器人要用的 Chromium 真的在 PLAYWRIGHT_BROWSERS_PATH 里，缺了自动下载

    专门防止 "Executable doesn't exist at ...\browsers\chromium_headless_shell-XXXX"：
    只要下载目录和运行目录不一致（或版本不对），这里会自动补齐。
    """
    target = env.get('PLAYWRIGHT_BROWSERS_PATH') or '(默认缓存)'
    try:
        probe = subprocess.run([sys.executable, '-c', _BROWSER_PROBE], env=env,
                               capture_output=True, text=True, timeout=90)
        if probe.returncode == 0:
            logger.info(f"浏览器就绪: {probe.stdout.strip()}")
            return True
        logger.warning(f"浏览器缺失（{target}），正在自动下载 Chromium ...")
        install = subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'],
                                 env=env, capture_output=True, text=True, timeout=1800)
        if install.returncode != 0:
            logger.error(f"Chromium 下载失败: {(install.stderr or install.stdout or '')[-300:]}")
            return False
        probe = subprocess.run([sys.executable, '-c', _BROWSER_PROBE], env=env,
                               capture_output=True, text=True, timeout=90)
        ok = probe.returncode == 0
        logger.info(("浏览器已就绪: " if ok else "浏览器仍然缺失: ") + (probe.stdout or '').strip())
        return ok
    except Exception as e:
        logger.warning(f"浏览器自检跳过: {e}")
        return True


def _local_proxy_hostport(url):
    """只对本机代理做端口预检（远程代理没法简单判断）"""
    try:
        from urllib.parse import urlparse
        p = urlparse(url if "://" in url else "http://" + url)
        host = (p.hostname or "").lower()
        if host in ("127.0.0.1", "localhost", "::1"):
            default_port = 1080 if p.scheme.startswith("socks") else 7890
            return host, int(p.port or default_port)
    except Exception:
        pass
    return None, None


def load_config():
    """加载配置文件，支持 UTF-8 BOM"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"配置文件 JSON 格式错误: {e}")
        return {}
    except PermissionError:
        logger.error(f"没有权限读取配置文件 {CONFIG_FILE}")
        return {}
    except Exception as e:
        logger.error(f"读取配置文件出错: {e}")
        return {}


def save_config(data):
    """保存配置文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info("配置已保存")
    except PermissionError:
        logger.error(f"没有权限写入配置文件 {CONFIG_FILE}")
        raise
    except Exception as e:
        logger.error(f"保存配置文件出错: {e}")
        raise


def is_authenticated():
    """检查是否已认证"""
    return session.get('authenticated', False)


# ================= 🔐 认证相关路由 =================

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """登录页面"""
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == WEB_PASSWORD:
            session['authenticated'] = True
            logger.info("Web 面板登录成功")
            return redirect(url_for('index'))
        else:
            logger.warning("Web 面板登录失败：密码错误")
            return render_template('login.html', error='密码错误，请重试')
    return render_template('login.html', error=None)


@app.route('/logout')
def logout_page():
    """登出"""
    session.pop('authenticated', None)
    return redirect(url_for('login_page'))


# ================= 🏠 主要路由 =================

@app.route('/')
def index():
    """主页"""
    if not is_authenticated():
        return redirect(url_for('login_page'))
    config = load_config()
    return render_template('index.html', config=config)


@app.route('/api/save', methods=['POST'])
def api_save():
    """保存配置 API"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "无效的 JSON 数据"}), 400
        save_config(data)
        return jsonify({"status": "success", "message": "配置已保存！"})
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        return jsonify({"status": "error", "message": f"保存失败: {str(e)}"}), 500


@app.route('/api/start', methods=['POST'])
def api_start():
    """启动机器人 API"""
    global bot_process, bot_log_file_handle, bot_started_at
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401

    if bot_is_running():
        # 同一个 Telegram Token / 同一个 Chrome 端口同时跑两个实例会互相抢指令（HTTP 409），
        # 这正是一些"Telegram 时好时坏"的来源，所以这里必须拦住
        return jsonify({"status": "error", "message": "机器人已经在运行中！（请先点停止）"})

    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(">>> [系统] 正在启动抢票指挥官...\n")

        # 打开日志文件句柄，保存引用以便后续关闭
        bot_log_file_handle = open(LOG_FILE, 'a', encoding='utf-8')

        # 强制设置子进程的环境变量为 utf-8，双重保险
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # Telegram 代理：面板里开启时才注入，只影响机器人进程；KTMB 依旧直连
        proxy_warning = ""
        try:
            proxy_on, proxy_url = proxy_setup.get_proxy_setting()
        except Exception as e:
            proxy_on, proxy_url = False, ""
            logger.warning(f"读取代理配置失败: {e}")
        if proxy_on and proxy_url:
            env["KTMB_TG_PROXY"] = proxy_url
            host, port = _local_proxy_hostport(proxy_url)
            if host and port and not proxy_setup.port_listening(host, port):
                logger.info(f"代理端口 {host}:{port} 还没监听，尝试拉起 WARP 代理模式 ...")
                ok, msg = proxy_setup.enable_warp_proxy(port, auto_install=False, wait_seconds=15)
                if ok:
                    logger.info(msg)
                else:
                    proxy_warning = "Telegram 代理没起来：" + msg
                    logger.warning(proxy_warning)
            logger.info(f"机器人将使用 Telegram 代理: {proxy_url}")
        else:
            env.pop("KTMB_TG_PROXY", None)
        # 默认优先使用项目内的 browsers 目录，避免依赖全局 Playwright 缓存
        local_browsers = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browsers")
        if "PLAYWRIGHT_BROWSERS_PATH" not in env:
            env["PLAYWRIGHT_BROWSERS_PATH"] = local_browsers if os.path.isdir(local_browsers) else "0"

        # 先确保 Chromium 真的在下载目录里（不在就自动下）——Windows 上最容易踩的坑
        if not ensure_playwright_browser(env):
            return jsonify({"status": "error",
                            "message": "Chromium 缺失且自动下载失败。请在项目目录执行: "
                                       "venv\\Scripts\\python.exe -m playwright install chromium"}), 500

        # 直接启动 bot（Chromium headless 模式不需要显示器）
        # 不用 xvfb-run 包裹，确保 SIGTERM 能正确传递到 Python 进程
        cmd = [sys.executable, "-u", "ktmb_auto.py"]
        logger.info("启动机器人")

        bot_process = subprocess.Popen(
            cmd,
            stdout=bot_log_file_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            env=env
        )
        bot_started_at = time.time()
        _write_bot_pid(bot_process.pid)
        logger.info(f"机器人已启动 (PID {bot_process.pid})")
        return jsonify({"status": "success", "message": "机器人已启动！", "warning": proxy_warning})
    except FileNotFoundError:
        logger.error("找不到 Python 解释器或 ktmb_auto.py")
        return jsonify({"status": "error", "message": "找不到 Python 或脚本文件"}), 500
    except Exception as e:
        logger.error(f"启动机器人失败: {e}")
        return jsonify({"status": "error", "message": f"启动失败: {str(e)}"}), 500


def stop_bot(timeout=60):
    """安全停止机器人

    Windows 上 Popen.terminate() 是硬杀（TerminateProcess），机器人没机会登出 KTMB，
    会触发 30 分钟冷却。所以这里先写 logout 命令文件让 bot 自己安全登出，超时才强杀。
    面板重启过（拿不到 Popen 句柄）时用 PID 文件继续兜底。
    """
    global bot_process, bot_log_file_handle, bot_started_at
    _reap_bot()

    proc = bot_process if (bot_process is not None and bot_process.poll() is None) else None
    pid = _read_bot_pid()
    knows_pid = bool(pid and _pid_alive(pid) and _pid_is_our_bot(pid))
    if proc is None and not knows_pid:
        if not _bot_heartbeat_fresh(30):
            _clear_bot_pid()
            bot_process = None
            return False
        logger.warning("没有 PID 句柄，但机器人仍在发布画面：用指令文件请它登出")
        pid = None

    try:
        send_command('logout')
        logger.info("已通知机器人安全登出 KTMB...")
    except Exception as e:
        logger.warning(f"发送登出指令失败: {e}")

    if proc is not None and os.name != 'nt':
        try:
            proc.terminate()   # Linux: SIGTERM，bot 会先安全登出
        except Exception:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None:
            if proc.poll() is not None:
                break
        elif pid:
            if not _pid_alive(pid):
                break
        elif not _bot_heartbeat_fresh(15):
            break
        time.sleep(0.5)

    if proc is not None:
        alive = proc.poll() is None
    elif pid:
        alive = _pid_alive(pid)
    else:
        alive = _bot_heartbeat_fresh(15)

    if alive:
        logger.warning("机器人未响应登出指令，强制结束")
        try:
            if proc is not None:
                proc.kill()
                proc.wait(timeout=5)
            else:
                _kill_pid(pid)
        except Exception:
            pass
    else:
        logger.info("机器人已安全登出并退出")

    bot_process = None
    bot_started_at = None
    _clear_bot_pid()
    if bot_log_file_handle:
        try:
            bot_log_file_handle.close()
        except Exception:
            pass
        bot_log_file_handle = None
    return True


@app.route('/api/stop', methods=['POST'])
def api_stop():
    """停止机器人 API"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401

    if stop_bot():
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write("\n>>> [系统] 机器人已停止。\n")
        logger.info("机器人已停止")
        return jsonify({"status": "success", "message": "机器人已停止！"})
    return jsonify({"status": "error", "message": "机器人未运行"})


@app.route('/api/status', methods=['GET'])
def api_status():
    """获取机器人状态 API"""
    if not is_authenticated():
        return jsonify({"running": False, "error": "未认证"}), 401

    running = bot_is_running()
    state = screenshot_state()
    return jsonify({
        "running": running,
        "pid": _read_bot_pid(),
        "started_at": bot_started_at,
        "screenshot_age": state.get("age"),
        "phase": state.get("phase", ""),
        "page_url": state.get("url", ""),
        "telegram": state.get("telegram", ""),
    })


@app.route('/api/logs', methods=['GET'])
def api_logs():
    """获取日志 API

    注意 from_line 的语义：客户端传"我已经有多少行了"，服务端只回增量，
    并且总是把 from_line 设成当前总行数。
    （旧实现在"没有新行"时会把最后 N 行又发一遍，客户端就重复追加 → 越刷越长）
    """
    if not is_authenticated():
        return jsonify({"logs": "未认证，请先登录"}), 401

    from_line = max(0, request.args.get('from_line', 0, type=int))

    try:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
    except FileNotFoundError:
        return jsonify({"logs": "", "total_lines": 0, "from_line": 0, "reset": True})
    except Exception as e:
        logger.error(f"读取日志失败: {e}")
        return jsonify({"logs": f"读取日志失败: {str(e)}", "total_lines": 0, "from_line": 0}), 500

    total_lines = len(all_lines)
    reset = False
    if from_line > total_lines:
        # 机器人重启过 -> 日志被截断，让前端清空重画
        start, reset = 0, True
    elif from_line > 0:
        start = from_line
    else:
        # 首次加载：只给最后 N 行
        start = max(0, total_lines - MAX_LOG_LINES)

    return jsonify({
        "logs": "".join(all_lines[start:]),
        "total_lines": total_lines,
        "from_line": total_lines,
        "reset": reset,
    })


# ================= 🎮 远程控制 =================

@app.route('/remote')
def remote_page():
    """远程控制页面"""
    if not is_authenticated():
        return redirect(url_for('login_page'))
    return render_template('remote.html')


@app.route('/api/remote/screenshot')
def api_remote_screenshot():
    """获取当前浏览器画面

    v1.3.2：机器人现在会持续把画面发布到文件（默认每 5 秒一帧），这里直接把最新一帧
    返回即可。以前是"面板写请求 -> 等机器人截 -> 3 秒超时"，机器人一忙就永远超时，
    /remote 页面就只剩"连接中..."。
    """
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401

    mark_viewer()                    # 告诉机器人"有人在看"，它才会持续发布画面
    if not os.path.exists(SCREENSHOT_PATH):
        request_screenshot()
        for _ in range(30):          # 等机器人产出第一帧，最多 6 秒
            if os.path.exists(SCREENSHOT_PATH):
                break
            time.sleep(0.2)

    if not os.path.exists(SCREENSHOT_PATH):
        running = bot_is_running()
        return jsonify({
            "status": "error",
            "bot_running": running,
            "message": "机器人没有在运行，先到主页点「启动抢票」" if not running
                       else "机器人还没产出画面（刚启动或正卡在某个页面）",
        }), 503

    try:
        with open(SCREENSHOT_PATH, 'rb') as f:
            data = f.read()
    except OSError:
        return jsonify({"status": "error", "message": "读取截图失败"}), 503

    resp = Response(data, mimetype='image/png')
    state = screenshot_state()
    resp.headers['Cache-Control'] = 'no-store, max-age=0'
    resp.headers['X-Screenshot-Age'] = f"{state.get('age') if state.get('age') is not None else -1}"
    resp.headers['X-Bot-Running'] = '1' if bot_is_running() else '0'
    return resp


@app.route('/api/remote/state')
def api_remote_state():
    """远程控制页面的状态（画面是否新鲜 / 机器人在不在跑 / 当前阶段）"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401
    mark_viewer()
    state = screenshot_state()
    state['bot_running'] = bot_is_running()
    state['stale_after'] = remote_control.SCREENSHOT_STALE_AFTER
    return jsonify(state)


@app.route('/api/remote/request', methods=['POST'])
def api_remote_request_shot():
    """强制机器人立刻截一帧（比等下一次自动发布快）"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401
    ok = request_screenshot()
    return jsonify({"status": "success" if ok else "error"})


@app.route('/api/remote/<action>', methods=['POST'])
def api_remote_action(action):
    """执行远程控制命令，并把机器人的真实执行结果回给页面"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    data.pop('action', None)
    data.pop('id', None)

    request_id = send_command(action, **data)
    if not request_id:
        return jsonify({"status": "error", "message": "指令写入失败（检查系统临时目录权限）"}), 500

    if action in ('logout', 'stop', 'shutdown'):
        return jsonify({"status": "success", "message": "已请求机器人停止并安全登出 KTMB"})

    for _ in range(40):              # 最多等 8 秒
        time.sleep(0.2)
        result = read_result(request_id)
        if result:
            return jsonify({"status": result.get('status', 'error'),
                            "message": result.get('message', '')})

    return jsonify({
        "status": "error",
        "message": "机器人没有响应：可能没在运行，或正忙着某个页面（可以去主页看日志）",
    }), 504


@app.route('/api/proxy/status')
def api_proxy_status():
    """Telegram 代理状态（不联网，很快）"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401
    enabled, url = proxy_setup.get_proxy_setting()
    warp = proxy_setup.warp_status()
    host, port = _local_proxy_hostport(url) if url else (None, None)
    return jsonify({"status": "success",
                    "proxy_enabled": enabled, "proxy_url": url,
                    "proxy_listening": bool(host and port and proxy_setup.port_listening(host, port)),
                    "warp_installed": warp["installed"], "warp_cli": warp["cli"],
                    "warp_port_listening": warp["proxy_listening"],
                    "platform": sys.platform})


@app.route('/api/proxy/probe')
def api_proxy_probe():
    """不依赖 token 的连通性探测（DNS / IPv4 / IPv6）"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401
    st = proxy_setup.probe_telegram(timeout=4)
    st["telegram_reachable"] = st.get("reachable", False)
    st["status"] = "success"
    st["explain"] = proxy_setup.explain_probe(st)
    return jsonify(st)


@app.route('/api/proxy/test', methods=['POST'])
def api_proxy_test():
    """真的用这个代理请求一次 Telegram（有 token 就做 getMe）"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        url = proxy_setup.get_proxy_setting()[1]
    cfg = load_config()
    token = ((cfg.get("notification", {}) or {}).get("telegram_token") or "").strip()
    if url:
        ok, msg = proxy_setup.test_proxy(url, token=token or None)
    else:
        ok, msg = proxy_setup._test_no_proxy(token=token or None)
    return jsonify({"status": "success" if ok else "error", "message": msg, "ok": ok, "via": url or "直连"})


@app.route('/api/proxy/enable-warp', methods=['POST'])
def api_proxy_enable_warp():
    """一键：装/开 Cloudflare WARP 代理模式，并把地址写进配置"""
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401
    data = request.get_json(silent=True) or {}
    port = int(data.get("port") or proxy_setup.DEFAULT_WARP_PORT)
    ok, msg = proxy_setup.enable_warp_proxy(port, auto_install=True)
    if not ok:
        return jsonify({"status": "error", "message": msg}), 500
    url = "socks5h://127.0.0.1:%d" % port
    cfg = load_config()
    token = ((cfg.get("notification", {}) or {}).get("telegram_token") or "").strip()
    test_ok, test_msg = proxy_setup.test_proxy(url, token=token or None)
    proxy_setup.set_proxy_setting(url, True)
    logger.info(f"WARP 代理已配置: {url} ({test_msg})")
    return jsonify({"status": "success", "message": msg, "url": url,
                    "test_ok": test_ok, "test_message": test_msg})


@app.route('/api/proxy/disable', methods=['POST'])
def api_proxy_disable():
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401
    proxy_setup.set_proxy_setting("", False)
    logger.info("Telegram 代理已关闭")
    return jsonify({"status": "success", "message": "Telegram 代理已关闭"})


# ================= 🚀 启动入口 =================

def _on_shutdown_signal(signum, frame):
    """收到 SIGTERM/SIGINT：先让机器人安全登出，再退出"""
    try:
        logger.info(f"[系统] 收到退出信号 ({signum})，正在停止机器人...")
    except Exception:
        pass
    try:
        stop_bot()
    except Exception as e:
        try:
            logger.warning(f"[系统] 停止机器人失败: {e}")
        except Exception:
            pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, _on_shutdown_signal)
    signal.signal(signal.SIGINT, _on_shutdown_signal)

    # 支持通过环境变量控制 host、port 和 debug
    host = os.environ.get('KTMB_WEB_HOST', '127.0.0.1')
    port = int(os.environ.get('KTMB_WEB_PORT', '5000'))
    debug = os.environ.get('KTMB_WEB_DEBUG', 'false').lower() == 'true'

    logger.info(f"Web 管理面板启动中... http://{host}:{port}")
    if WEB_PASSWORD == 'admin123':
        logger.warning("正在使用默认密码 admin123，建议通过 KTMB_WEB_PASSWORD 环境变量修改！")

    app.run(host=host, port=port, debug=debug)
