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
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import json
import os
import subprocess
import sys
import secrets
import logging

# ================= 📝 日志系统 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('KTMB_Web')

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

CONFIG_FILE = "config.json"
LOG_FILE = "bot.log"

# Web 面板访问密码（可通过环境变量设置，默认为 admin123）
WEB_PASSWORD = os.environ.get('KTMB_WEB_PASSWORD', 'admin123')

# 日志最大返回行数
MAX_LOG_LINES = int(os.environ.get('KTMB_MAX_LOG_LINES', '200'))

bot_process = None
bot_log_file_handle = None  # 保存日志文件句柄引用

if not os.path.exists(LOG_FILE):
    open(LOG_FILE, 'w', encoding='utf-8').close()


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
    global bot_process, bot_log_file_handle
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401

    if bot_process and bot_process.poll() is None:
        return jsonify({"status": "error", "message": "机器人已经在运行中！"})

    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(">>> [系统] 正在启动抢票指挥官...\n")

        # 打开日志文件句柄，保存引用以便后续关闭
        bot_log_file_handle = open(LOG_FILE, 'a', encoding='utf-8')

        # 强制设置子进程的环境变量为 utf-8，双重保险
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        bot_process = subprocess.Popen(
            [sys.executable, "-u", "ktmb_auto.py"],
            stdout=bot_log_file_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            env=env
        )
        logger.info("机器人已启动")
        return jsonify({"status": "success", "message": "机器人已启动！"})
    except FileNotFoundError:
        logger.error("找不到 Python 解释器或 ktmb_auto.py")
        return jsonify({"status": "error", "message": "找不到 Python 或脚本文件"}), 500
    except Exception as e:
        logger.error(f"启动机器人失败: {e}")
        return jsonify({"status": "error", "message": f"启动失败: {str(e)}"}), 500


@app.route('/api/stop', methods=['POST'])
def api_stop():
    """停止机器人 API"""
    global bot_process, bot_log_file_handle
    if not is_authenticated():
        return jsonify({"status": "error", "message": "未认证"}), 401

    if bot_process and bot_process.poll() is None:
        bot_process.terminate()
        # 等待进程退出，超时后强制杀死
        try:
            bot_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bot_process.kill()
            logger.warning("机器人进程未响应，已强制杀死")

        bot_process = None

        # 关闭日志文件句柄
        if bot_log_file_handle:
            try:
                bot_log_file_handle.close()
            except Exception:
                pass
            bot_log_file_handle = None

        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write("\n>>> [系统] 机器人已被强制停止！\n")
        logger.info("机器人已停止")
        return jsonify({"status": "success", "message": "机器人已停止！"})

    return jsonify({"status": "error", "message": "机器人未运行"})


@app.route('/api/status', methods=['GET'])
def api_status():
    """获取机器人状态 API"""
    if not is_authenticated():
        return jsonify({"running": False, "error": "未认证"}), 401

    global bot_process
    is_running = bot_process is not None and bot_process.poll() is None
    return jsonify({"running": is_running})


@app.route('/api/logs', methods=['GET'])
def api_logs():
    """获取日志 API，支持偏移量"""
    if not is_authenticated():
        return jsonify({"logs": "未认证，请先登录"}), 401

    # 支持 from_line 参数，返回指定行之后的日志
    from_line = request.args.get('from_line', 0, type=int)

    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            total_lines = len(all_lines)

            if from_line > 0 and from_line < total_lines:
                # 只返回新增的行
                new_lines = all_lines[from_line:]
                return jsonify({
                    "logs": "".join(new_lines),
                    "total_lines": total_lines,
                    "from_line": from_line
                })
            else:
                # 返回最后 N 行
                return jsonify({
                    "logs": "".join(all_lines[-MAX_LOG_LINES:]),
                    "total_lines": total_lines,
                    "from_line": max(0, total_lines - MAX_LOG_LINES)
                })
    except FileNotFoundError:
        return jsonify({"logs": "日志文件不存在...", "total_lines": 0, "from_line": 0})
    except Exception as e:
        logger.error(f"读取日志失败: {e}")
        return jsonify({"logs": f"读取日志失败: {str(e)}", "total_lines": 0, "from_line": 0}), 500


# ================= 🚀 启动入口 =================

if __name__ == '__main__':
    # 支持通过环境变量控制 host、port 和 debug
    host = os.environ.get('KTMB_WEB_HOST', '127.0.0.1')
    port = int(os.environ.get('KTMB_WEB_PORT', '5000'))
    debug = os.environ.get('KTMB_WEB_DEBUG', 'false').lower() == 'true'

    logger.info(f"Web 管理面板启动中... http://{host}:{port}")
    logger.info(f"默认密码可通过环境变量 KTMB_WEB_PASSWORD 设置")

    app.run(host=host, port=port, debug=debug)
