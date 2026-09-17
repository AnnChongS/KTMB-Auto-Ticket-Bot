# -*- coding: utf-8 -*-
#
# KTMB Auto Ticket Bot
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
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import time
import requests
import json
import os
import logging
import signal
import threading
from datetime import datetime
from playwright.sync_api import Playwright, sync_playwright
from remote_control import save_screenshot, get_command, execute_remote_command

# ================= 📝 日志系统初始化 =================
def setup_logging():
    """配置日志系统，避免重复输出"""
    log_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    # 文件处理器（超过 5MB 自动轮转，避免无限增长）
    try:
        if os.path.exists('bot.log') and os.path.getsize('bot.log') > 5 * 1024 * 1024:
            if os.path.exists('bot.log.1'):
                os.remove('bot.log.1')
            os.rename('bot.log', 'bot.log.1')
    except Exception:
        pass

    file_handler = logging.FileHandler('bot.log', encoding='utf-8', mode='a')
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG)

    # 根日志器
    logger = logging.getLogger('KTMBBot')
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # 仅在直接运行时添加控制台输出（避免子进程时 stdout 重定向到 bot.log 导致重复）
    if sys.stdout and hasattr(sys.stdout, 'fileno'):
        try:
            # 检测 stdout 是否指向终端（而非文件）
            if os.isatty(sys.stdout.fileno()):
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setFormatter(log_formatter)
                console_handler.setLevel(logging.INFO)
                logger.addHandler(console_handler)
        except Exception:
            pass

    return logger

logger = setup_logging()

# ================= 🔧 配置加载模块 =================
CONFIG_FILE = "config.json"

def load_config():
    """加载配置文件，支持 UTF-8 BOM 编码"""
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"找不到配置文件 {CONFIG_FILE}")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"配置文件 JSON 格式错误: {e}")
        sys.exit(1)
    except PermissionError:
        logger.error(f"没有权限读取配置文件 {CONFIG_FILE}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"读取配置文件出错: {e}")
        sys.exit(1)

CFG = load_config()

KTMB_EMAIL = CFG.get("account", {}).get("email", "")
KTMB_PASSWORD = CFG.get("account", {}).get("password", "")

BOT_ID = CFG.get("bot_settings", {}).get("bot_id", "1")
BOT_NAME = f"Commander-{BOT_ID}"
CHROME_DEBUG_PORT = CFG.get("bot_settings", {}).get("chrome_port", 9222)
HEARTBEAT_INTERVAL = CFG.get("bot_settings", {}).get("heartbeat_interval", 100)
REFRESH_INTERVAL = CFG.get("bot_settings", {}).get("refresh_interval", 180)

TELEGRAM_BOT_TOKEN = CFG.get("notification", {}).get("telegram_token", "")
TELEGRAM_CHAT_ID = CFG.get("notification", {}).get("telegram_chat_id", "")
HEARTBEAT_SCREENSHOT = CFG.get("notification", {}).get("heartbeat_screenshot", False)

SEARCH_CONFIGS = CFG.get("search_tasks", [])
PAYMENT_METHOD = CFG.get("payment_method", "Command")

PREF = CFG.get("preferences", {}) or {}
PREFER_NORMAL_SEAT = PREF.get("prefer_normal_seat", True)   # 优先普通座
PREFER_AISLE = PREF.get("prefer_aisle", True)               # 第一优先: 靠过道
PREFER_WINDOW = PREF.get("prefer_window", True)             # 第二优先: 靠窗
PREFER_FORWARD = PREF.get("prefer_forward", True)           # 旧火车优先正向
ACCEPT_TABLE_SEAT = PREF.get("accept_table_seat", True)     # 普通座没了才要大桌位
ACCEPT_BERTH = PREF.get("accept_berth", False)              # 旧火车卧铺
ACCEPT_FIRST_CLASS = PREF.get("accept_first_class", False)  # 头等/商务舱(默认关闭)
PREFER_FIRST_CLASS = PREF.get("prefer_first_class", False)  # 头等舱优先(排到最前)
ACCEPT_OKU = PREF.get("accept_oku", False)                  # OKU 无障碍座(默认关闭)
# 额外屏蔽关键词(匹配图标URL与座位类型, 大小写不敏感), 留空即可
EXCLUDE_SEAT_KEYWORDS = [k.lower() for k in PREF.get("exclude_seat_keywords", [])]

# 位置/类型判定关键词
_KW_CLUSTER = ("clus", "table", "grp", "group")
_KW_BERTH = ("berth", "sleeper", "bunk", "bed")
_KW_FORWARD = ("for", "fwd")
_KW_BACKWARD = ("back", "bwd", "bw")
_KW_AISLE = ("aisle", "corridor", "walkway")
_KW_WINDOW = ("win", "window")
_KW_FIRSTCLASS = ("first", "1st", "biz", "business", "premium", "executive", "platinum")

def validate_config():
    """启动时做基础配置校验，尽量在开跑前发现问题"""
    problems = []
    if not KTMB_EMAIL or not KTMB_PASSWORD:
        problems.append("账号邮箱或密码为空")
    if not SEARCH_CONFIGS:
        problems.append("search_tasks 为空，没有可监控的计划")
    today = datetime.now().date()
    for idx, cfg in enumerate(SEARCH_CONFIGS, 1):
        try:
            d = datetime(int(cfg['year']), int(cfg['month']), int(cfg['day'])).date()
            if d < today:
                problems.append(f"计划 {idx} 的日期 {d} 已过期")
        except Exception:
            problems.append(f"计划 {idx} 的日期无效: {cfg.get('year')}-{cfg.get('month')}-{cfg.get('day')}")
        for key in ("from", "to", "time"):
            if not cfg.get(key):
                problems.append(f"计划 {idx} 缺少 {key}")
    for p in problems:
        logger.error(f"[配置] {p}")
    return problems


SHOULD_LOGOUT_AND_EXIT = False

# 登录成功后缓存的 cookies（供 HTTP 安全登出使用）
_cached_cookies = {}

KTMB_BASE_URL = "https://online.ktmb.com.my"
KTMB_LOGOUT_URL = f"{KTMB_BASE_URL}/Account/Logout"

# 会话备份文件：进程被强杀时，下一次启动可据此主动登出，避免 30 分钟冷却
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ktmb_session.json")

# Telegram 通知重试配置
TG_MAX_RETRIES = 3
TG_RETRY_DELAY = 2  # 秒

# ==========================================================


def send_notification(message):
    """发送 Telegram 文字通知，带重试机制"""
    current_time = datetime.now().strftime('%H:%M:%S')
    formatted_msg = f"<b>[{BOT_NAME}] {current_time}</b>\n{message}"
    logger.info(f"[TG通知] {message[:80]}...")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": formatted_msg, "parse_mode": "HTML"}

    for attempt in range(TG_MAX_RETRIES):
        try:
            resp = requests.post(url, data=data, timeout=10)
            if resp.status_code == 200:
                return
            logger.warning(f"[TG通知] 发送失败 (HTTP {resp.status_code})，重试 {attempt + 1}/{TG_MAX_RETRIES}")
        except requests.exceptions.Timeout:
            logger.warning(f"[TG通知] 请求超时，重试 {attempt + 1}/{TG_MAX_RETRIES}")
        except requests.exceptions.ConnectionError:
            logger.warning(f"[TG通知] 连接失败，重试 {attempt + 1}/{TG_MAX_RETRIES}")
        except Exception as e:
            logger.warning(f"[TG通知] 发送异常: {e}，重试 {attempt + 1}/{TG_MAX_RETRIES}")

        if attempt < TG_MAX_RETRIES - 1:
            time.sleep(TG_RETRY_DELAY * (attempt + 1))  # 指数退避

    logger.error("[TG通知] 所有重试均失败，通知未送达")


def send_telegram_photo(caption, image_bytes):
    """发送 Telegram 截图通知，带重试机制"""
    current_time = datetime.now().strftime('%H:%M:%S')
    formatted_caption = f"<b>[{BOT_NAME}] {current_time}</b>\n{caption}"
    logger.info("[TG通知] 正在发送屏幕截图...")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {'photo': image_bytes}
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": formatted_caption, "parse_mode": "HTML"}

    for attempt in range(TG_MAX_RETRIES):
        try:
            resp = requests.post(url, data=data, files=files, timeout=30)
            if resp.status_code == 200:
                return
            logger.warning(f"[TG截图] 发送失败 (HTTP {resp.status_code})，重试 {attempt + 1}/{TG_MAX_RETRIES}")
        except requests.exceptions.Timeout:
            logger.warning(f"[TG截图] 请求超时，重试 {attempt + 1}/{TG_MAX_RETRIES}")
        except requests.exceptions.ConnectionError:
            logger.warning(f"[TG截图] 连接失败，重试 {attempt + 1}/{TG_MAX_RETRIES}")
        except Exception as e:
            logger.warning(f"[TG截图] 发送异常: {e}，重试 {attempt + 1}/{TG_MAX_RETRIES}")

        if attempt < TG_MAX_RETRIES - 1:
            time.sleep(TG_RETRY_DELAY * (attempt + 1))

    logger.error("[TG截图] 所有重试均失败，截图未送达")


def flush_telegram_updates():
    """清空之前的历史指令，防止一开机就执行以前的 /logout 导致自杀"""
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok") and len(data.get("result", [])) > 0:
            return data["result"][-1]["update_id"] + 1
    except requests.exceptions.RequestException as e:
        logger.warning(f"[TG] 清空历史指令失败: {e}")
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(f"[TG] 解析历史指令响应失败: {e}")
    return None


TELEGRAM_COMMANDS = {
    "/snap", "/logout", "/duitnow", "/tng", "/wallet", "/manual", "/cancel", "/abort",
    "/status", "/plans", "/seats", "/page", "/help",
}

# 机器人运行状态（供 /status 等指令查询）
BOT_STATE = {
    "running": False,
    "started_at": None,
    "round": 0,
    "phase": "启动中",
    "plan": "",
    "last_result": "",
    "next_refresh_in": 0,
}


def format_plans():
    if not SEARCH_CONFIGS:
        return "（没有配置监控计划）"
    out = []
    for i, cfg in enumerate(SEARCH_CONFIGS, 1):
        mode = str(cfg.get("train_mode", "") or "").lower()
        if mode not in ("auto", "old", "new"):
            mode = "old" if cfg.get("is_old_train") else "auto"
        mode_txt = {"auto": "自动识别", "old": "旧火车", "new": "新火车"}.get(mode, "自动识别")
        try:
            date_txt = f"{int(cfg.get('year'))}-{int(cfg.get('month')):02d}-{int(cfg.get('day')):02d}"
        except Exception:
            date_txt = "日期无效"
        out.append(f"{i}. <b>{cfg.get('from', '?')}</b> → <b>{cfg.get('to', '?')}</b>\n"
                   f"     {date_txt} {cfg.get('time', '?')} · {mode_txt}")
    return "\n".join(out)


def _onoff(v):
    return "✅ 开" if v else "⬜ 关"


def format_seat_prefs():
    return ("💺 <b>取票偏好（优先级从上到下）</b>\n"
            f"1. 靠过道: {_onoff(PREFER_AISLE)}\n"
            f"2. 靠窗: {_onoff(PREFER_WINDOW)}\n"
            f"3. 大桌位: {_onoff(ACCEPT_TABLE_SEAT)}\n"
            f"4. 卧铺: {_onoff(ACCEPT_BERTH)}\n"
            f"5. 头等/商务舱: {_onoff(ACCEPT_FIRST_CLASS)}\n"
            f"   头等舱优先(排最前): {_onoff(PREFER_FIRST_CLASS)}\n"
            f"6. OKU 无障碍座: {_onoff(ACCEPT_OKU)}\n"
            f"7. 普通座优先: {_onoff(PREFER_NORMAL_SEAT)}\n"
            f"旧火车正向优先: {_onoff(PREFER_FORWARD)}\n"
            f"屏蔽关键词: {', '.join(EXCLUDE_SEAT_KEYWORDS) if EXCLUDE_SEAT_KEYWORDS else '无'}")


def format_status(page=None):
    st = BOT_STATE
    uptime = "-"
    if st.get("started_at"):
        mins = int((datetime.now() - st["started_at"]).total_seconds() // 60)
        uptime = f"{mins // 60}小时{mins % 60}分" if mins >= 60 else f"{mins}分钟"
    lines = [
        f"📟 <b>{BOT_NAME} 运行状态</b>",
        f"状态: {'🟢 运行中' if st.get('running') else '🔴 已停止'} · 已运行 {uptime}",
        f"轮数: 第 {st.get('round', 0)} 轮 · 阶段: {st.get('phase', '-')}",
    ]
    if st.get("plan"):
        lines.append(f"当前计划: {st['plan']}")
    if st.get("last_result"):
        lines.append(f"上次结果: {st['last_result']}")
    if st.get("next_refresh_in"):
        lines.append(f"下次刷新: {st['next_refresh_in']} 秒后")
    lines.append(f"KTMB 登录: {'已登录' if _cached_cookies else '未登录'}")
    lines.append(f"支付方式: {PAYMENT_METHOD}")
    if page is not None:
        try:
            lines.append(f"当前页面: {page.url}")
        except Exception:
            pass
    lines.append("")
    lines.append("🎫 <b>监控计划</b>")
    lines.append(format_plans())
    return "\n".join(lines)


def format_help():
    return ("🤖 <b>可用指令</b>\n"
            "/status — 运行状态 + 监控计划\n"
            "/plans — 只列监控计划\n"
            "/seats — 当前取票偏好\n"
            "/page — 当前浏览器页面\n"
            "/snap — 截一张实时画面\n"
            "/help — 显示这条帮助\n"
            "/logout — 安全登出并结束程序\n\n"
            "<b>抢到票待命时：</b>\n"
            "/duitnow · /tng · /wallet · /manual\n"
            "/cancel — 取消待命\n\n"
            "指令后加 Bot ID 可定向，例如 /status1")


def process_telegram_command(page, command, target_id):
    """处理通用 Telegram 指令，返回 True 表示已处理"""
    if target_id != "all" and target_id != BOT_ID:
        return False
    if command == "/help":
        send_notification(format_help())
        return True
    if command == "/status":
        send_notification(format_status(page))
        return True
    if command == "/plans":
        send_notification(f"🎫 <b>监控计划</b>\n{format_plans()}")
        return True
    if command == "/seats":
        send_notification(format_seat_prefs())
        return True
    if command == "/page":
        try:
            url = page.url
        except Exception:
            url = "未知"
        send_notification(f"🌐 当前页面:\n{url}")
        return True
    if command == "/snap":
        send_snap(page)
        return True
    return False


def parse_telegram_command(text):
    """解析 Telegram 指令，支持 /cmd、/cmd1、/cmd 1、/cmd@bot 1 等写法

    返回 (command, target_id) 或 (None, None)
    """
    if not text:
        return None, None
    parts = text.strip().lower().split()
    if not parts:
        return None, None
    command = parts[0].split("@")[0]
    target_id = parts[1] if len(parts) > 1 else "all"
    if len(parts) == 1:
        cmd, tail = command[:-1], command[-1]
        if cmd in TELEGRAM_COMMANDS and tail.isdigit():
            command, target_id = cmd, tail
    if command not in TELEGRAM_COMMANDS:
        return None, None
    return command, target_id


def check_telegram_command(offset=None):
    """检查 Telegram 是否有新指令"""
    global SHOULD_LOGOUT_AND_EXIT
    if not TELEGRAM_BOT_TOKEN:
        return None, None, offset

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {"timeout": 0, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset

        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("result", []) if data.get("ok") else []
        if not results:
            return None, None, offset

        new_offset = results[-1]["update_id"] + 1

        # 依次处理所有积压消息，返回第一个有效指令
        for update in results:
            message = update.get("message") or {}
            command, target_id = parse_telegram_command(message.get("text", ""))
            if not command:
                continue
            if command == "/logout" and (target_id == "all" or target_id == BOT_ID):
                SHOULD_LOGOUT_AND_EXIT = True
                logger.info("[TG] 收到 logout 指令，标记退出")
            return command, target_id, new_offset

        return None, None, new_offset
    except requests.exceptions.RequestException as e:
        logger.debug(f"[TG] 检查指令网络错误: {e}")
    except (KeyError, IndexError, ValueError) as e:
        logger.debug(f"[TG] 解析指令响应失败: {e}")
    return None, None, offset


_NOTIFIED_POPUPS = set()
_THROTTLE_TS = {}


def throttle_ok(key, seconds):
    """同一个提示 key 在 seconds 秒内只允许触发一次，避免刷屏"""
    now = time.time()
    if now - _THROTTLE_TS.get(key, 0) < seconds:
        return False
    _THROTTLE_TS[key] = now
    return True
MAINTENANCE_KEYWORDS = (
    "maintenance", "penyelenggaraan", "down for", "under maintenance", "temporarily unavailable",
    "will be unavailable", "scheduled downtime", "system upgrade", "sedang diselenggara",
)


def notify_system_popup(text):
    """网站公告/维护弹窗 -> 记录日志并通知一次(去重)"""
    if not text:
        return
    low = text.lower()
    if not any(k in low for k in MAINTENANCE_KEYWORDS):
        return
    key = text[:120]
    logger.warning(f"[页面] 检测到网站公告/维护提示: {text[:150]}")
    if key in _NOTIFIED_POPUPS:
        return
    _NOTIFIED_POPUPS.add(key)
    send_notification(f"📢 <b>KTMB 网站公告</b>\n{text[:400]}")


def handle_popup(page, capture=False):
    """关闭页面上可能遮挡操作的各类弹窗

    覆盖: #popupModal / #validationSummaryModal / 广告弹窗 / 维护公告 / 普通 bootstrap modal
    返回捕获到的提示文本(可选)
    """
    captured = None
    try:
        # 1) 标准提示弹窗
        modal = page.locator("#popupModal")
        if modal.count() and modal.first.is_visible():
            try:
                captured = page.locator("#popupModalBody").first.inner_text().strip()[:300]
            except Exception:
                captured = None
            for sel in ("#popupModalCloseButton", "#popupModalOkButton", "#popupModalCancelButton"):
                btn = page.locator(sel)
                if btn.count() and btn.first.is_visible():
                    try:
                        btn.first.click(timeout=2000)
                        break
                    except Exception:
                        pass

        # 2) 校验错误弹窗（登录失败、座位不足等都会走这里）
        vmodal = page.locator("#validationSummaryModal")
        if vmodal.count() and vmodal.first.is_visible():
            try:
                captured = page.locator("#validationSummaryModalBody").first.inner_text().strip()[:300]
            except Exception:
                pass
            btn = page.locator("#validationSummaryModal button[data-dismiss='modal']")
            if btn.count():
                try:
                    btn.first.click(timeout=2000)
                except Exception:
                    pass

        # 3) 广告弹窗
        ad = page.locator("#advertisement-modal")
        if ad.count() and ad.first.is_visible():
            for sel in ("#CloseButtonAdvertisement", "#advertisement-modal .close", "#advertisement-modal button"):
                b = page.locator(sel)
                if b.count() and b.first.is_visible():
                    try:
                        b.first.click(timeout=2000)
                        break
                    except Exception:
                        pass

        # 4) 兜底：隐藏所有 bootstrap modal 与遮罩
        page.evaluate("""() => {
            document.querySelectorAll('.modal').forEach(m => {
                if (m.id === 'seatSelect') return;  // 选座弹窗必须保留
                if (m.style.display && m.style.display !== 'none') {
                    m.style.display = 'none';
                    m.classList.remove('show');
                }
            });
            document.querySelectorAll('.modal-backdrop').forEach(e => e.remove());
            document.querySelectorAll('.fade.show').forEach(e => {
                if (e.id !== 'seatSelect' && !e.closest('#seatSelect')) e.classList.remove('show');
            });
            document.body.classList.remove('modal-open');
        }""")
    except Exception as e:
        logger.debug(f"[页面] 处理弹窗时出现异常（可忽略）: {e}")

    if captured:
        logger.debug(f"[页面] 弹窗内容: {captured}")
        notify_system_popup(captured)
    return captured if capture else None


def safe_logout(page=None, notify=True):
    """安全登出 KTMB 账号

    优先用缓存 cookies 走 HTTP 登出（即使浏览器已关闭也能生效），
    失败时再退回 Playwright 点击/跳转登出。
    """
    logger.info("[系统] 正在执行安全登出(Logout)...")
    ok = False
    cookies = dict(_cached_cookies)

    if cookies:
        for attempt in range(1, 3):
            try:
                resp = requests.get(KTMB_LOGOUT_URL, cookies=cookies, timeout=10, allow_redirects=True)
                logger.info(f"[系统] HTTP 登出完成 (HTTP {resp.status_code})")
                ok = True
                break
            except Exception as e:
                logger.warning(f"[系统] HTTP 登出失败 ({attempt}/2): {e}")
                time.sleep(1)
    else:
        logger.warning("[系统] 没有缓存 cookies，只能尝试浏览器登出")

    if not ok and page is not None:
        try:
            page.goto(KTMB_LOGOUT_URL, timeout=10000, wait_until="domcontentloaded")
            time.sleep(1.5)
            ok = True
            logger.info("[系统] 浏览器登出完成")
        except Exception as e:
            logger.warning(f"[系统] Playwright 登出失败: {e}")

    if ok:
        logger.info("[系统] ✅ 已安全登出")
        if notify:
            send_notification("✅ 已安全退出 KTMB 账号。")
    else:
        logger.error("[系统] ⚠️ 未能确认登出，账号可能被锁定约 30 分钟")
        if notify:
            send_notification("⚠️ 未能确认安全登出，若无法重新登录请等待约 30 分钟。")

    _cached_cookies.clear()
    clear_session_file()
    if page is not None:
        try:
            page.context.clear_cookies()
        except Exception:
            pass
    return ok


def ensure_on_homepage(page):
    """确保在主页"""
    if SHOULD_LOGOUT_AND_EXIT:
        return
    current_url = page.url
    target_url = "https://online.ktmb.com.my/"
    if current_url == target_url or current_url == target_url + "Home/Index":
        handle_popup(page)
        return
    logger.info("[导航] 跳转回主页...")
    try:
        page.goto(target_url, timeout=8000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        handle_popup(page)
    except Exception as e:
        logger.warning(f"[导航] 跳转主页失败: {e}")


def save_session_file():
    try:
        with open(SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(_cached_cookies, f)
    except Exception as e:
        logger.debug(f"[系统] 保存会话文件失败: {e}")


def clear_session_file():
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception:
        pass


def cleanup_stale_session():
    """启动时清理上一次未正常登出的残留会话，避免 'Not allow multiple login'"""
    if not os.path.exists(SESSION_FILE):
        return
    try:
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
    except Exception:
        clear_session_file()
        return
    if not cookies:
        clear_session_file()
        return
    try:
        resp = requests.get(KTMB_LOGOUT_URL, cookies=cookies, timeout=10, allow_redirects=True)
        logger.info(f"[系统] 已清理上次残留会话 (HTTP {resp.status_code})")
    except Exception as e:
        logger.warning(f"[系统] 清理残留会话失败: {e}")
    clear_session_file()


def cache_cookies(page):
    """缓存当前浏览器会话的 cookies（供 HTTP 安全登出使用）"""
    global _cached_cookies
    try:
        cookies = {c['name']: c['value'] for c in page.context.cookies()}
        if cookies:
            _cached_cookies = cookies
            save_session_file()
        logger.debug(f"[系统] 已缓存 {len(_cached_cookies)} 个 cookies")
        return True
    except Exception as e:
        logger.debug(f"[登录] 缓存 cookies 失败: {e}")
        return False


def is_logged_in(page):
    """通过导航栏判断是否已登录（登录链接消失或出现登出链接即视为已登录）"""
    try:
        if page.locator("a[href*='/Account/Logout']").count():
            return True
        link = page.locator("a[href*='/Account/Login']")
        if link.count() == 0:
            return True
        return not link.first.is_visible()
    except Exception:
        return False


def close_validation_modal(page):
    """关闭校验错误弹窗，返回错误文本"""
    try:
        box = page.locator("#validationSummaryModalBody .validation-summary-errors")
        if box.count() and box.first.is_visible():
            text = box.first.inner_text().strip()
            btn = page.locator("#validationSummaryModal button[data-dismiss='modal']")
            if btn.count():
                try:
                    btn.first.click()
                except Exception:
                    pass
            page.evaluate("""() => {
                document.querySelectorAll('#validationSummaryModal').forEach(m => {
                    m.style.display = 'none'; m.classList.remove('show');
                });
                document.querySelectorAll('.modal-backdrop').forEach(e => e.remove());
                document.body.classList.remove('modal-open');
            }""")
            return text
    except Exception as e:
        logger.debug(f"[登录] 处理校验弹窗失败: {e}")
    return None


def login(page, max_attempts=3):
    """登录 KTMB 账号

    返回: True(成功) / False(失败) / "MULTI_LOGIN"(账号已在别处登录)
    """
    if SHOULD_LOGOUT_AND_EXIT:
        return False
    page.set_viewport_size({"width": 1920, "height": 1080})

    for attempt in range(1, max_attempts + 1):
        if SHOULD_LOGOUT_AND_EXIT:
            return False

        # 已经是登录状态
        if is_logged_in(page):
            cache_cookies(page)
            return True

        try:
            link = page.locator("a[href*='/Account/Login']")
            if link.count() and link.first.is_visible():
                link.first.click()
                page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception as e:
            logger.warning(f"[登录] 打开登录页失败: {e}")

        handle_popup(page)

        try:
            email_box = page.get_by_role("textbox", name="Email")
            if email_box.count() == 0:
                if is_logged_in(page):
                    cache_cookies(page)
                    return True
                time.sleep(1)
                continue
            logger.info(f"[系统] 执行登录... (第 {attempt}/{max_attempts} 次)")
            email_box.first.fill(KTMB_EMAIL)
            page.get_by_role("textbox", name="Password").first.fill(KTMB_PASSWORD)
            page.get_by_role("button", name="Login").first.click()
        except Exception as e:
            if SHOULD_LOGOUT_AND_EXIT:
                return False
            logger.warning(f"[登录] 提交表单异常，重试中: {e}")
            handle_popup(page)
            time.sleep(2)
            continue

        # 等待跳转 / 错误弹窗
        deadline = time.time() + 20
        while time.time() < deadline and not SHOULD_LOGOUT_AND_EXIT:
            handle_popup(page)
            err = close_validation_modal(page)
            if err:
                logger.error(f"[登录] 被 KTMB 拒绝: {err}")
                if "multiple login" in err.lower():
                    send_notification(
                        "⚠️ <b>KTMB 拒绝登录</b>\n账号已在别处登录（Not allow multiple login）。\n"
                        "旧会话未登出会锁定约 30 分钟，机器人将自动等待后重试。"
                    )
                    return "MULTI_LOGIN"
                if "incorrect" in err.lower() or "invalid" in err.lower() or "password" in err.lower():
                    send_notification(f"❌ <b>登录失败</b>：{err}")
                    return False
                break
            if is_logged_in(page) and "Login" not in page.url:
                logger.info("[系统] 登录成功")
                cache_cookies(page)
                return True
            time.sleep(1)

        if is_logged_in(page) and "Login" not in page.url:
            logger.info("[系统] 登录成功")
            cache_cookies(page)
            return True

        if attempt < max_attempts:
            logger.warning(f"[登录] 第 {attempt} 次尝试未成功，{3 * attempt} 秒后重试")
            time.sleep(3 * attempt)

    logger.error(f"[系统] 登录失败（已重试 {max_attempts} 次）")
    send_notification("❌ 登录失败，请检查账号密码或 KTMB 网站状态。")
    return False


def pick_select2_option(page, container_selector, option_name):
    """选择 select2 下拉框选项（优先无障碍名称，失败时按文本模糊匹配）"""
    page.locator(container_selector).click(timeout=8000)
    time.sleep(0.5)
    try:
        page.get_by_role("option", name=option_name).first.click(timeout=5000)
        return True
    except Exception:
        pass
    results_selector = container_selector.replace("-container", "-results")
    keyword = option_name.split()[0]
    li = page.locator(f"{results_selector} li").filter(has_text=keyword)
    if li.count() == 0:
        raise RuntimeError(f"找不到站点选项: {option_name}")
    li.last.click()
    return True


def open_date_picker(page):
    """打开出发日期日历"""
    for attempt in (1, 2):
        try:
            handle_popup(page)
            page.locator("#OnwardDate").click(force=True, timeout=5000)
            page.wait_for_selector(".lightpick__select-years", state="visible", timeout=5000)
            return True
        except Exception as e:
            logger.warning(f"[搜索] 日历弹出失败({attempt}/2): {e}")
            time.sleep(0.5)
    return False


def choose_date(page, year, month, day):
    """在日历中选择目标日期"""
    for _ in range(36):
        try:
            cy = int(page.locator(".lightpick__select-years").input_value())
            cm = int(page.locator(".lightpick__select-months").input_value()) + 1
        except Exception:
            break
        if (cy, cm) >= (year, month):
            break
        page.locator(".lightpick__next-action").click()
        time.sleep(0.25)

    day_loc = page.locator(
        f".lightpick__day:not(.is-next-month):not(.is-previous-month):not(.is-disabled):text-is('{day}')"
    )
    if day_loc.count() == 0:
        logger.error(f"[搜索] 日历中找不到日期 {year}-{month}-{day}（可能已过期或未开放）")
        return False
    day_loc.first.click()
    time.sleep(0.4)
    value = page.locator("#OnwardDate").input_value()
    logger.debug(f"[搜索] 已选日期: {value}")
    return bool(value)


def perform_search(page, config):
    """执行车票搜索"""
    try:
        year, month, day = int(config['year']), int(config['month']), int(config['day'])
        target_date = datetime(year, month, day).date()
    except Exception as e:
        logger.error(f"[搜索] 任务日期无效: {config} ({e})")
        return False

    if target_date < datetime.now().date():
        logger.error(f"[搜索] 目标日期 {target_date} 已过去，请在 Web 面板更新乘车计划")
        send_notification(f"⏰ 监控日期已过期: {target_date}，请在面板更新乘车计划。")
        return False

    date_str = f"{year}-{month:02d}-{day:02d}"
    logger.info(f"[搜索] 计划: {date_str} {config['time']} | {config['from']} → {config['to']}")

    try:
        handle_popup(page)
        pick_select2_option(page, "#select2-FromStationId-container", config['from'])
        pick_select2_option(page, "#select2-ToStationId-container", config['to'])

        if not open_date_picker(page):
            send_snap(page, "⚠️ <b>日历弹出失败！</b>")
            return False
        if not choose_date(page, year, month, day):
            return False

        page.locator("#btnSubmit").click()
    except Exception as e:
        logger.error(f"[搜索] 填表/提交失败: {e}")
        send_snap(page, f"⚠️ <b>搜索失败</b>\n{str(e)[:200]}")
        return False

    try:
        page.wait_for_selector(".btn-seat-layout", timeout=15000)
    except Exception:
        handle_popup(page)
        logger.info("[搜索] 没有可用车次")
        return False

    handle_popup(page)

    rows = page.locator(".depart-trips > tr")
    if rows.count() == 0:
        logger.info("[搜索] 结果为空")
        return False

    target_index = None
    for i in range(rows.count()):
        row = rows.nth(i)
        try:
            if config['time'] in row.inner_text():
                target_index = i
                break
        except Exception:
            continue

    if target_index is None:
        logger.info(f"[搜索] 未找到 {config['time']} 的车次")
        return False

    row = rows.nth(target_index)
    btn = row.locator(".btn-seat-layout").first
    btn_class = btn.get_attribute("class") or ""
    logger.info(f"[搜索] 发现目标车次！(状态: {'已订/禁用' if 'disabled' in btn_class else '可订'}) :: "
                f"{row.inner_text().replace(chr(10), ' | ')[:150]}")

    try:
        btn.click(timeout=5000)
    except Exception:
        handle_popup(page)
        btn.click(force=True)
    return True


SEAT_SCAN_JS = r"""() => {
    const seats = [];
    document.querySelectorAll('#seatSelect img.selectable-icon').forEach(img => {
        if (img.getAttribute('data-selected') === 'true') return;
        seats.push({
            coachId: img.getAttribute('data-coache-id') || img.getAttribute('data-CoacheId') || '',
            coach: img.getAttribute('data-coach-label') || '',
            no: img.getAttribute('data-seat-no') || '',
            price: parseFloat(img.getAttribute('data-seat-price') || '0') || 0,
            srv: img.getAttribute('data-seat-service-type') || '',
            src: (img.getAttribute('src') || '').toLowerCase(),
            hidden: !!img.closest('.hide')
        });
    });
    const coaches = [...document.querySelectorAll('#seatSelect .coache-btn')].map(b => ({
        id: b.getAttribute('data-CoacheId') || '',
        label: b.getAttribute('data-coach-label') || '',
        avail: b.getAttribute('data-coach-seat-available') || '?'
    }));
    return { seats, coaches };
}"""


def classify_seat(seat):
    """根据图标URL + 座位类型判断座位特征"""
    src = seat.get('src', '') or ''
    srv = (seat.get('srv', '') or '').lower()
    text = src + ' ' + srv
    return {
        'oku': 'oku' in text,
        'cluster': any(k in src for k in _KW_CLUSTER),
        'berth': any(k in text for k in _KW_BERTH),
        'window': any(k in src for k in _KW_WINDOW),
        'aisle': any(k in src for k in _KW_AISLE),
        'first_class': any(k in text for k in _KW_FIRSTCLASS),
        'forward': any(k in src for k in _KW_FORWARD),
        'backward': any(k in src for k in _KW_BACKWARD),
        'src': src,
        'srv': seat.get('srv', '') or '',
    }


def seat_exclusion_reason(seat):
    """返回被排除的原因，未排除返回 None"""
    info = classify_seat(seat)
    text = info['src'] + ' ' + info['srv'].lower()
    for kw in EXCLUDE_SEAT_KEYWORDS:
        if kw and kw in text:
            return f'黑名单({kw})'
    if info['oku'] and not ACCEPT_OKU:
        return 'OKU'
    if info['first_class'] and not ACCEPT_FIRST_CLASS:
        return '头等/商务'
    if info['cluster'] and not ACCEPT_TABLE_SEAT:
        return '大桌位'
    if info['berth'] and not ACCEPT_BERTH:
        return '卧铺'
    return None


TIER_NAMES = {0: '普通座', 1: '大桌位', 2: '卧铺', 3: '头等/商务舱', 4: 'OKU 无障碍座'}


def seat_priority_tier(seat):
    """0=普通座 1=大桌位 2=卧铺 3=头等舱 4=OKU（数字越大优先级越低）"""
    info = classify_seat(seat)
    if info['oku']:
        return 4
    if info['first_class']:
        return 3
    if info['berth']:
        return 2
    if info['cluster']:
        return 1
    return 0


def seat_sort_key(seat, prefer_aisle, prefer_window):
    """排序键：过道 > 靠窗 > 其它；正向 > 反向；同档便宜优先"""
    info = classify_seat(seat)
    if prefer_aisle and info['aisle']:
        pos = 0
    elif prefer_window and info['window']:
        pos = 1
    else:
        pos = 2
    direction = 1 if (PREFER_FORWARD and info['backward']) else 0
    return (pos, direction, seat.get('price', 0) or 0, seat.get('coach', ''), seat.get('no', ''))


def seat_label(seat):
    info = classify_seat(seat)
    parts = []
    if info['aisle']:
        parts.append('过道')
    elif info['window']:
        parts.append('靠窗')
    if info['cluster']:
        parts.append('大桌位')
    if info['berth']:
        parts.append('卧铺')
    if info['forward']:
        parts.append('正向')
    elif info['backward']:
        parts.append('反向')
    if info['srv']:
        parts.append(info['srv'])
    return ' '.join(parts) or '普通座'


def select_seat(page, train_mode="auto"):
    """智能选座

    规则: 永不选 OKU / 黑名单座位; 普通座优先, 座位位置 过道 > 靠窗 > 其它;
    普通座完全没有时才考虑大桌位, 最后才考虑卧铺。
    """
    logger.info("[操作] 正在选座...")
    BOT_STATE["phase"] = "选座中"
    try:
        page.wait_for_selector("#seatSelect.show", state="visible", timeout=10000)
    except Exception:
        logger.warning("[选座] 座位选择界面未出现")
        return False
    time.sleep(0.5)
    handle_popup(page)

    try:
        pax = max(1, int(page.locator("#fixedPax").first.input_value()))
    except Exception:
        pax = 1

    data = page.evaluate(SEAT_SCAN_JS)
    seats = data.get('seats', [])
    coaches = data.get('coaches', [])
    logger.info(f"[选座] 车厢情况: {[(c['label'], c['avail']) for c in coaches]}")

    if not seats:
        logger.warning(f"[选座] 该车次没有可选座位（共 {len(coaches)} 节车厢）")
        if throttle_ok("no_seat", 900):
            send_snap(page, "⚠️ 该车次已无空位（10 分钟内只提醒一次）")
        close_seat_modal(page)
        return False

    # 车型自适应判断(仅用于日志与偏好)
    has_cluster = any(classify_seat(x)['cluster'] for x in seats)
    has_backward = any(classify_seat(x)['backward'] for x in seats)
    if train_mode == "auto":
        detected = "新火车(ETS)" if has_cluster else ("旧火车(Intercity)" if has_backward else "未知车型")
    else:
        detected = "旧火车(Intercity)" if train_mode == "old" else "新火车(ETS)"
    logger.info(f"[选座] 车型判定: {detected} | 可选座位 {len(seats)} 个 | 含大桌位={has_cluster} 含反向座={has_backward}")

    # 分类统计（按类型分档，数字越小越优先）
    excluded = {}
    tiers = {0: [], 1: [], 2: [], 3: [], 4: []}
    for st in seats:
        reason = seat_exclusion_reason(st)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        tiers[seat_priority_tier(st)].append(st)
    if excluded:
        logger.info(f"[选座] 已排除: {excluded}")
    seen_types = sorted({(st.get('srv') or '?') for st in seats})
    logger.debug(f"[选座] 座位类型(data-seat-service-type): {seen_types}")
    summary = ' | '.join(f"{TIER_NAMES[t]} {len(v)}" for t, v in tiers.items() if v)
    logger.info(f"[选座] 候选: {summary or '无'}")

    pool, pool_name = None, None
    order = (3, 0, 1, 2, 4) if PREFER_FIRST_CLASS else (0, 1, 2, 3, 4)
    for tier in order:
        if tiers[tier]:
            pool, pool_name = tiers[tier], TIER_NAMES[tier]
            break
    if pool is None:
        logger.warning("[选座] 没有任何可接受的座位（全部被偏好/黑名单排除）")
        if throttle_ok("no_pref_seat", 900):
            send_snap(page, "⚠️ 没有符合偏好的空位（15 分钟内只提醒一次）")
        close_seat_modal(page)
        return False
    if pool_name != '普通座':
        logger.info(f"[选座] 按偏好采用 [{pool_name}]")

    pool.sort(key=lambda x: seat_sort_key(x, PREFER_AISLE, PREFER_WINDOW))
    chosen = []
    for st in pool:
        if len(chosen) >= pax:
            break
        if any(c['coachId'] == st['coachId'] and c['no'] == st['no'] for c in chosen):
            continue
        # 多人时优先同一车厢
        if chosen and len(chosen) < pax and st['coachId'] != chosen[0]['coachId']:
            continue
        chosen.append(st)
    if len(chosen) < pax:
        for st in pool:
            if len(chosen) >= pax:
                break
            if any(c['coachId'] == st['coachId'] and c['no'] == st['no'] for c in chosen):
                continue
            chosen.append(st)

    logger.info(f"[选座] 采用 [{pool_name}] 优先: " +
                ", ".join(f"{c['coach']}/{c['no']}({seat_label(c)},MYR{c.get('price', 0):.2f})" for c in chosen))

    for st in chosen:
        try:
            coach_btn = page.locator(f"#seatSelect .coache-btn[data-CoacheId='{st['coachId']}']").first
            if coach_btn.count():
                cls = coach_btn.get_attribute("class") or ""
                if "active" not in cls:
                    coach_btn.click()
                    time.sleep(0.2)
            page.locator(
                f"#seatSelect img.selectable-icon[data-seat-no='{st['no']}'][data-coache-id='{st['coachId']}']"
            ).first.click()
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"[选座] 点击座位失败 {st['coach']}-{st['no']}: {e}")

    confirm = page.locator("#confirmSeatBtn").first
    cls = ""
    try:
        cls = confirm.get_attribute("class") or ""
    except Exception:
        pass
    if "disabled-btn" in cls:
        logger.warning(f"[选座] 确认按钮未启用（已选 {len(chosen)}/{pax} 个座位）")
        send_snap(page, "⚠️ 选座确认按钮未启用")
        return False

    try:
        confirm.click()
    except Exception as e:
        logger.warning(f"[选座] 点击确认按钮失败: {e}")
        return False

    try:
        page.wait_for_function(
            "() => { const b = document.getElementById('bookingData'); return b && b.value && b.value.length > 0; }",
            timeout=15000,
        )
    except Exception:
        logger.warning("[选座] 等待订座结果超时")
    handle_popup(page)

    try:
        page.wait_for_selector(".btn-passenger", state="visible", timeout=6000)
    except Exception:
        logger.warning("[选座] 订座后未出现乘客信息按钮（可能订座未生效）")
    logger.info("[选座] 选座成功！")
    return True


def close_seat_modal(page):
    try:
        page.evaluate("""() => {
            const m = document.getElementById('seatSelect');
            if (m) { m.style.display = 'none'; m.classList.remove('show'); }
            document.querySelectorAll('.modal-backdrop').forEach(e => e.remove());
            document.body.classList.remove('modal-open');
        }""")
    except Exception:
        pass


def safe_text(page, selectors, default="未知"):
    """按顺序尝试多个选择器，返回第一个非空文本"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count():
                text = loc.inner_text().strip()
                if text:
                    return text
        except Exception:
            continue
    return default


def extract_ticket_info(page):
    """提取票务信息（多重回退，避免页面小改版就报错）"""
    try:
        page.wait_for_selector(".station_detail", timeout=5000)
    except Exception:
        pass
    train = safe_text(page, [
        ".station_detail .c444.mt5.fw100.f17",
        ".station_detail .f17",
        ".station_detail",
    ])
    seat = safe_text(page, [
        ".col-lg-4 p.f12.mt10",
        ".col-lg-4 p.f12",
        ".confirmation-section .f12",
    ])
    price = safe_text(page, [
        "tr.f14 td.text-right b",
        ".confirmation-section .text-right b",
        ".text-right b",
    ])
    return f"🎫 票务确认\n🚆 {train}\n💺 {seat}\n💰 {price}"


def first_new_page(context, before_ids):
    """返回 context 中不在 before_ids 里的第一个新页面"""
    try:
        pages = list(context.pages)
    except Exception:
        return None
    for pg in pages:
        if id(pg) not in before_ids:
            return pg
    return None


def last_new_page(context, before_ids):
    """返回 context 中最新创建的、不在 before_ids 里的页面"""
    newest = None
    try:
        pages = list(context.pages)
    except Exception:
        return None
    for pg in pages:
        if id(pg) not in before_ids:
            newest = pg
    return newest


GATEWAY_PAY_SELECTORS = (
    "#pay-now-nomultipayment",
    ".pay-button",
    "#submitButton",
    "button[type='submit']",
    "input[type='submit']",
)


def acquire_gateway_page(context, page, before_ids, timeout=300):
    """等待真正的支付网关页面出现

    KTMB 使用 window.open 打开网关；在无头/慢速环境下，新标签页可能几十秒后才
    被 Playwright 感知，因此这里同时轮询「新标签页」和「任意页面出现网关付款按钮」。
    """
    combined = ", ".join(GATEWAY_PAY_SELECTORS)
    deadline = time.time() + timeout
    last_report = time.time()
    while time.time() < deadline:
        for pg in list(context.pages):
            try:
                if pg.locator(combined).count():
                    logger.info("[支付] 已找到支付网关页面（含付款按钮）")
                    return pg
            except Exception:
                continue

        popup = first_new_page(context, before_ids)
        if popup is not None:
            logger.info("[支付] 已检测到支付网关标签页")
            try:
                popup.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            return popup

        if time.time() - last_report >= 30:
            logger.info(f"[支付] 仍在等待支付网关页面... ({int(time.time() - (deadline - timeout))}s)")
            last_report = time.time()
        time.sleep(1)

    logger.warning("[支付] 等待支付网关页面超时")
    return page


def go_to_payment_gateway(page, context, button_selectors, label, timeout=25):
    """点击支付方式 -> 点击过渡页的 "Click HERE" 按钮，返回当前页面"""
    clicked = False
    for sel in button_selectors:
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click()
                logger.info(f"[支付] 已点击 {label} 按钮 ({sel})")
                clicked = True
                break
        except Exception as e:
            logger.debug(f"[支付] 点击 {sel} 失败: {e}")
    if not clicked:
        send_snap(page, f"⚠️ 找不到 {label} 按钮，当前页面：")
        send_notification(f"📍 URL: {page.url}")
        return None

    proceed = None
    for sel in ("#PaymentGateway", "input[value*='Click HERE']", "input[value*='Click Here']",
                "input[value*='HERE']", "input[type='submit']", "button[type='submit']"):
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout * 1000)
            proceed = loc
            logger.info(f"[支付] 找到网关跳转按钮 ({sel})")
            break
        except Exception:
            continue

    if proceed is not None:
        try:
            proceed.click(timeout=10000)
            logger.info("[支付] 已点击网关跳转按钮")
        except Exception as e:
            logger.warning(f"[支付] 点击网关跳转按钮失败: {str(e)[:120]}")
    else:
        logger.warning("[支付] 未出现网关跳转按钮，检查当前页面状态")
    return page


def click_gateway_pay(context, gateway_page, before_ids=None, max_attempts=3, first_timeout=120000):
    """在支付网关页面点击 PAY，直到页面进入下一步（二维码/跳转）"""
    if before_ids is None:
        before_ids = set()
    combined = ", ".join(GATEWAY_PAY_SELECTORS)

    for attempt in range(1, max_attempts + 1):
        for sel in ("#fpxTncCheckbox", "input[name='fpx_tnc_ack']"):
            try:
                cb = gateway_page.locator(sel)
                if cb.count() and cb.first.is_visible() and not cb.first.is_checked():
                    cb.first.check()
                    logger.info("[支付] 已勾选网关条款")
            except Exception:
                pass

        timeout = first_timeout if attempt == 1 else 20000
        try:
            btn = gateway_page.locator(combined).first
            btn.wait_for(state="visible", timeout=timeout)
            btn.click(timeout=10000)
            logger.info("[支付] 已点击网关付款按钮")
        except Exception as e:
            logger.warning(f"[支付] 未找到/无法点击网关付款按钮: {str(e)[:100]}")
            break

        try:
            gateway_page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        time.sleep(6)

        still_pay_button = False
        try:
            loc = gateway_page.locator(combined).first
            if loc.count() and loc.is_visible():
                still_pay_button = True
        except Exception:
            pass
        if not still_pay_button:
            logger.info("[支付] 网关页面已进入下一步")
            break
        logger.info(f"[支付] 网关页面仍显示付款按钮，重试 ({attempt}/{max_attempts})")
        time.sleep(3)

    new_page = last_new_page(context, before_ids) if before_ids else None
    if new_page is not None and new_page is not gateway_page:
        logger.info("[支付] 网关进入新页面")
        return new_page
    return gateway_page


def enlarge_and_shot(page, caption):
    try:
        page.set_viewport_size({"width": 1920, "height": 2500})
    except Exception:
        pass
    time.sleep(1)
    try:
        page.evaluate("window.scrollBy(0, 400)")
    except Exception:
        pass
    try:
        screenshot = page.screenshot(type='png', full_page=True)
        send_telegram_photo(caption, screenshot)
        return True
    except Exception as e:
        logger.warning(f"[支付] 截图失败: {e}")
        send_notification(f"{caption}\n(截图失败: {e})\n📍 URL: {page.url}")
        return False


def execute_payment_command(page, context, command, target_id):
    """执行支付指令"""
    if target_id != "all" and target_id != BOT_ID:
        return False
    logger.info(f"[指令] 收到支付指令: {command}")

    if command == "/manual":
        send_notification("🛑 已切换人工模式。")
        return True

    try:
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.6)
        except Exception:
            pass

        try:
            before_ids = {id(p) for p in context.pages}
        except Exception:
            before_ids = set()

        if command == "/duitnow":
            send_notification("💳 正在生成 DuitNow...")
            gateway_page = go_to_payment_gateway(
                page, context, ["#btnGoPaymentDuitNow", "div:has-text('DuitNow QR')", "button:has-text('DuitNow')"], "DuitNow")
            if gateway_page is None:
                return False
            gateway_page = acquire_gateway_page(context, page, before_ids)
            gateway_page = click_gateway_pay(context, gateway_page, before_ids)
            latest = last_new_page(context, before_ids)
            if latest is not None and latest is not gateway_page:
                gateway_page = latest
            enlarge_and_shot(gateway_page, "⚡️ DuitNow 请扫码")
            send_notification(f"🔗 链接:\n{gateway_page.url}")
            return False

        if command == "/tng":
            send_notification("💳 正在生成 TnG...")
            gateway_page = go_to_payment_gateway(
                page, context, ["#btnGoPaymentTnG", "div:has-text('Touch')", "button:has-text('TnG')"], "TnG")
            if gateway_page is None:
                return False
            gateway_page = acquire_gateway_page(context, page, before_ids)
            gateway_page = click_gateway_pay(context, gateway_page, before_ids)
            latest = last_new_page(context, before_ids)
            if latest is not None and latest is not gateway_page:
                gateway_page = latest
            enlarge_and_shot(gateway_page, "⚡️ TnG 请扫码")
            return False

        if command == "/wallet":
            send_notification("💳 尝试 KTM Wallet...")
            if not click_first(page, ["#btnKtmbEWallet", "button:has-text('Wallet')"], timeout=8000, label="KTM Wallet"):
                return False
            time.sleep(3)
            popup_msg = handle_popup(page, capture=True)
            text = (popup_msg or "")
            try:
                body = page.locator("#popupModalBody").inner_text()
                text += " " + body
            except Exception:
                pass
            if "insufficient" in text.lower() or "balance" in text.lower():
                send_notification("⚠️ 余额不足！请换方式。")
                return False
            if text.strip():
                send_notification(f"ℹ️ Wallet 返回: {text.strip()[:150]}")
            send_notification("✅ Wallet 扣款已发送。")
            return True

        logger.warning(f"[支付] 未知指令: {command}")
        return False

    except Exception as e:
        logger.error(f"[支付] 指令执行异常: {e}", exc_info=True)
        send_snap(page, f"⚠️ 指令异常: {str(e)[:150]}")
    return False


def interruptible_sleep(seconds):
    """可被退出信号打断的等待"""
    for _ in range(int(seconds)):
        if SHOULD_LOGOUT_AND_EXIT:
            return False
        time.sleep(1)
    return not SHOULD_LOGOUT_AND_EXIT


def handle_remote_control(page):
    """处理一次远程控制命令"""
    try:
        save_screenshot(page)
        remote_cmd = get_command()
        if remote_cmd:
            logger.info(f"[远程控制] 收到命令: {remote_cmd['action']}")
            result = execute_remote_command(page, remote_cmd)
            logger.info(f"[远程控制] 执行结果: {result}")
    except Exception as e:
        logger.warning(f"[远程控制] 处理失败: {e}")


def send_snap(page, caption="📸 <b>长官，这是现在的监控画面</b>"):
    try:
        screenshot = page.screenshot(type='png', full_page=True)
        send_telegram_photo(caption, screenshot)
    except Exception as e:
        logger.warning(f"[截图] 截图失败: {e}")
        send_notification(f"⚠️ 截图失败: {e}")


def wait_for_payment_command(page, context):
    """等待 Telegram 支付指令（待命模式，最多 20 分钟）"""
    logger.info("[系统] 进入待命模式，等待支付指令...")
    ticket_info = extract_ticket_info(page)
    msg = (
        f"🚨 <b>{BOT_NAME} 抢票成功！</b> 🚨\n\n{ticket_info}\n\n"
        f"👇 <b>请发送指令：</b>\n/duitnow{BOT_ID} | /tng{BOT_ID} | /wallet{BOT_ID} | /manual{BOT_ID}"
    )
    send_notification(msg)

    _, _, last_offset = check_telegram_command(offset=None)
    deadline = time.time() + 20 * 60
    while time.time() < deadline:
        if SHOULD_LOGOUT_AND_EXIT:
            return False
        handle_remote_control(page)

        command, target_id, new_offset = check_telegram_command(offset=last_offset)
        if command:
            last_offset = new_offset
            if process_telegram_command(page, command, target_id):
                pass
            elif command == "/logout" and (target_id == "all" or target_id == BOT_ID):
                return False
            elif command in ("/cancel", "/abort") and (target_id == "all" or target_id == BOT_ID):
                logger.info("[指令] 收到取消指令")
                return False
            elif execute_payment_command(page, context, command, target_id):
                logger.info("[系统] 流程结束。")
                return True
            elif target_id == "all" or target_id == BOT_ID:
                send_notification("🤖 仍在待命...")
        try:
            if not page.url.startswith("https"):
                break
        except Exception:
            break
        if not interruptible_sleep(3):
            break
    return True


def process_automated_payment(page, context, method):
    """处理自动支付流程"""
    if method == "Manual":
        send_notification("🛑 请手动付款！")
        interruptible_sleep(1800)
        return True
    if method == "KTM Wallet":
        return execute_payment_command(page, context, "/wallet", "all")
    if method == "DuitNow":
        execute_payment_command(page, context, "/duitnow", "all")
        interruptible_sleep(1800)
        return True
    logger.warning(f"[支付] 未知支付方式: {method}")
    return False


def click_first(page, selectors, timeout=5000, required=True, label=""):
    """依次尝试多个选择器，点击第一个可见元素（先快速扫描，再统一等待）"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=3000)
                logger.debug(f"[点击] {label or sel} -> {sel}")
                return True
        except Exception:
            continue

    if timeout > 0:
        combined = ", ".join(selectors)
        try:
            loc = page.locator(combined).first
            loc.wait_for(state="visible", timeout=timeout)
            loc.click(timeout=5000)
            logger.debug(f"[点击] {label or combined} -> {combined}")
            return True
        except Exception:
            pass

    if required:
        logger.warning(f"[点击] 找不到可点击元素: {label or selectors}")
    return False


def cancel_booking(page):
    """取消当前未完成的订座，释放座位"""
    try:
        btn = page.locator(".btn-reset").first
        if btn.count() and btn.is_visible():
            btn.click()
            time.sleep(2)
            handle_popup(page)
            logger.info("[订座] 已取消未完成的订座")
            return True
    except Exception as e:
        logger.warning(f"[订座] 取消失败: {e}")
    return False


PAYMENT_BUTTON_SELECTORS = (
    "#btnKtmbEWallet",
    "#btnGoPaymentDuitNow",
    "#btnGoPaymentTnG",
    "#btnGoPaymentBoost",
    "#btnGoPaymentCard",
)


def payment_page_ready(page):
    """判断是否真正到达付款方式选择页"""
    for sel in PAYMENT_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return sel
        except Exception:
            continue
    return None


def advance_to_payment_page(page, max_steps=8):
    """从乘客页一路点到付款方式选择页

    流程: 确认乘客 -> (Takaful 弹窗) -> PROCEED TO PAYMENT -> (餐食确认弹窗) -> 付款页
    """
    for step in range(max_steps):
        ready = payment_page_ready(page)
        if ready:
            logger.info(f"[填表] 已到达付款页 (检测到 {ready})")
            return True

        # Takaful 确认弹窗：OK = 不购买保险
        try:
            if page.locator("#popupModal").is_visible():
                msg = ""
                try:
                    msg = page.locator("#popupModalBody").inner_text()
                except Exception:
                    pass
                logger.info(f"[填表] 弹窗: {msg.strip()[:80]}")
                if "takaful" in msg.lower():
                    if click_first(page, ["#popupModalOkButton"], timeout=4000, required=False, label="不购买保险"):
                        time.sleep(1)
                        continue
                if click_first(page, ["#popupModalOkButton"], timeout=3000, required=False, label="确认弹窗"):
                    time.sleep(1)
                    continue
        except Exception as e:
            logger.debug(f"[填表] 处理弹窗异常: {e}")

        # 餐食/条款确认弹窗
        if click_first(page, ["#confirmationConfirmButton"], timeout=2000, required=False, label="确认继续付款"):
            time.sleep(1)
            continue

        # 放弃保险按钮
        if click_first(page, ["#btnUpdateInsuranceNo"], timeout=2000, required=False, label="放弃保险"):
            time.sleep(1)
            continue

        # 继续付款按钮
        if click_first(page, ["#btnProceedToPayment"], timeout=2000, required=False, label="继续付款"):
            time.sleep(1)
            continue

        time.sleep(1)

    return bool(payment_page_ready(page))


def handle_passenger_and_payment(page, context, train_mode="auto"):
    """填写乘客信息并处理支付（完全按页面元素自适应，新旧火车同一套流程）"""
    try:
        # 1) 进入乘客信息页
        if not click_first(page, [".btn-passenger"], timeout=10000, label="乘客信息按钮"):
            logger.error("[填表] 找不到 .btn-passenger 按钮")
            send_snap(page, "⚠️ 找不到乘客信息按钮，当前页面：")
            return False
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        try:
            page.wait_for_selector("#btnConfirmPayment", state="visible", timeout=15000)
        except Exception:
            logger.warning("[填表] 乘客页确认按钮未出现")
        time.sleep(1)
        handle_popup(page)

        # 2) 勾选本人 + 票种
        for sel in (".IsSelf", "#Passengers_0__IsSelf", "input[name*='IsSelf']"):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.check(timeout=8000)
                    logger.debug(f"[填表] 已勾选乘客 ({sel})")
                    break
            except Exception as e:
                logger.debug(f"[填表] 勾选 {sel} 失败: {e}")
        time.sleep(1)

        try:
            ttype = page.locator("select.TicketTypeId, select[id*='TicketTypeId']").first
            if ttype.count():
                try:
                    ttype.select_option(value="Adult")
                except Exception:
                    ttype.select_option(label="Adult")
                logger.debug("[填表] 票种已选择 Adult")
        except Exception as e:
            logger.warning(f"[填表] 选择票种失败: {e}")

        # 3) 确认乘客
        if not click_first(page, ["#btnConfirmPayment"], timeout=10000, label="确认乘客"):
            send_snap(page, "⚠️ 找不到确认乘客按钮")
            return False
        time.sleep(2)

        # 4) 一路推进到付款页（新旧车型流程不同，统一用状态驱动）
        if not advance_to_payment_page(page):
            logger.error("[填表] 未能到达付款方式选择页")
            send_snap(page, "⚠️ <b>未能到达付款页面</b>")
            cancel_booking(page)
            return False

        logger.info(f"[填表] 当前页面: {page.url}")
        if PAYMENT_METHOD == "Command":
            return wait_for_payment_command(page, context)
        return process_automated_payment(page, context, PAYMENT_METHOD)
    except Exception as e:
        logger.error(f"[异常] 填表流程出错: {e}", exc_info=True)
        send_snap(page, f"⚠️ <b>填表流程出错</b>\n{str(e)[:200]}")
        cancel_booking(page)
        return False


def run(playwright: Playwright) -> None:
    """主运行函数"""
    global SHOULD_LOGOUT_AND_EXIT

    # 开机立刻清空历史 Telegram 指令，防止一开机就执行以前的 /logout 导致自杀
    tg_offset = flush_telegram_updates()

    browser = None
    context = None
    page = None
    launched_own_browser = False

    # 方式1: 尝试连接已有的 Chrome 调试进程
    logger.info(f"[连接] 尝试连接 Chrome (Port {CHROME_DEBUG_PORT})...")
    try:
        browser = playwright.chromium.connect_over_cdp(f"http://localhost:{CHROME_DEBUG_PORT}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        logger.info("[连接] 已连接到现有 Chrome 实例")
    except Exception as e:
        logger.warning(f"[连接] 无法连接现有 Chrome: {e}")
        logger.info("[连接] 正在自动启动 Chromium 浏览器...")

        # 方式2: 自动启动新的 Chromium 实例
        try:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--window-size=1920,1080'
                ]
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True
            )
            page = context.new_page()
            launched_own_browser = True
            logger.info("[连接] Chromium 已自动启动")
        except Exception as e2:
            logger.error(f"[连接] 自动启动 Chromium 失败: {e2}")
            send_notification(f"❌ 浏览器启动失败: {e2}")
            return

    try:
        page.set_default_timeout(30000)
        page.set_default_navigation_timeout(60000)
    except Exception:
        pass

    def _on_dialog(dialog):
        try:
            logger.warning(f"[页面] 浏览器弹窗({dialog.type}): {dialog.message[:150]}")
        except Exception:
            pass
        try:
            dialog.dismiss()
        except Exception:
            pass

    try:
        page.on("dialog", _on_dialog)
    except Exception:
        pass

    BOT_STATE.update({"running": True, "started_at": datetime.now(), "phase": "启动完成"})
    send_notification("🤖 机器人启动 | 模式: " + PAYMENT_METHOD + "\n发送 /status 查看监控计划")
    logger.info(f"[系统] 机器人已启动 | 模式: {PAYMENT_METHOD} | 任务数: {len(SEARCH_CONFIGS)}")

    cleanup_stale_session()

    config_problems = validate_config()
    if config_problems:
        send_notification("⚠️ 配置有问题：\n- " + "\n- ".join(config_problems))

    # 注册信号处理器：只置标志位（并开一个后台线程做 HTTP 登出），
    # 真正的浏览器/HTTP 登出在主循环退出后的 finally 中完成。
    def _on_stop_signal(signum, frame):
        global SHOULD_LOGOUT_AND_EXIT
        if SHOULD_LOGOUT_AND_EXIT:
            return
        SHOULD_LOGOUT_AND_EXIT = True
        try:
            logger.info(f"[系统] 收到退出信号 ({signum})，准备安全登出...")
        except Exception:
            pass

        def _emergency_http_logout():
            cookies = dict(_cached_cookies)
            if not cookies:
                return
            for _ in range(2):
                try:
                    requests.get(KTMB_LOGOUT_URL, cookies=cookies, timeout=8, allow_redirects=True)
                    return
                except Exception:
                    time.sleep(1)

        threading.Thread(target=_emergency_http_logout, daemon=True).start()

    signal.signal(signal.SIGTERM, _on_stop_signal)
    signal.signal(signal.SIGINT, _on_stop_signal)
    logger.info("[系统] 已注册安全退出信号处理器")

    try:
        total_loop = 0
        while True:
            if SHOULD_LOGOUT_AND_EXIT:
                break

            # 远程控制: 保存截图 & 处理命令
            try:
                save_screenshot(page)
                remote_cmd = get_command()
                if remote_cmd:
                    logger.info(f"[远程控制] 收到命令: {remote_cmd['action']}")
                    result = execute_remote_command(page, remote_cmd)
                    logger.info(f"[远程控制] 执行结果: {result}")
            except Exception as e:
                logger.warning(f"[远程控制] 处理失败: {e}")

            total_loop += 1
            BOT_STATE["round"] = total_loop
            BOT_STATE["next_refresh_in"] = 0
            logger.info(f"[进度] 第 {total_loop} 轮大循环 ({datetime.now().strftime('%H:%M:%S')})")

            if total_loop % HEARTBEAT_INTERVAL == 0:
                tasks_info = "".join([f"- {cfg['month']}月{cfg['day']}日 {cfg['time']}\n" for cfg in SEARCH_CONFIGS])
                msg = f"💓 存活确认\n轮数: {total_loop}\n任务:\n{tasks_info}"

                if HEARTBEAT_SCREENSHOT:
                    try:
                        screenshot = page.screenshot(type='png')
                        send_telegram_photo(msg, screenshot)
                    except Exception as e:
                        logger.warning(f"[心跳] 截图失败: {e}")
                        send_notification(msg + f"\n(⚠️ 截图失败: {e})")
                else:
                    send_notification(msg)

            for config in SEARCH_CONFIGS:
                if SHOULD_LOGOUT_AND_EXIT:
                    break

                try:
                    if SHOULD_LOGOUT_AND_EXIT:
                        break
                    BOT_STATE["plan"] = (f"{config.get('from', '?')} → {config.get('to', '?')} "
                                         f"{config.get('year')}-{config.get('month')}-{config.get('day')} {config.get('time')}")
                    BOT_STATE["phase"] = "登录中"
                    ensure_on_homepage(page)
                    if SHOULD_LOGOUT_AND_EXIT:
                        break
                    login_result = login(page)
                    if SHOULD_LOGOUT_AND_EXIT:
                        break
                    if login_result == "MULTI_LOGIN":
                        logger.warning("[系统] 账号被锁定（多处登录），等待 5 分钟后重试...")
                        for _ in range(300):
                            if SHOULD_LOGOUT_AND_EXIT:
                                break
                            time.sleep(1)
                        break
                    if not login_result:
                        logger.error("[系统] 登录失败，本轮跳过该任务")
                        break
                    train_mode = str(config.get("train_mode", "") or "").strip().lower()
                    if train_mode not in ("auto", "old", "new"):
                        # 兼容旧配置字段 is_old_train
                        train_mode = "old" if config.get("is_old_train") else "auto"

                    if SHOULD_LOGOUT_AND_EXIT:
                        break
                    BOT_STATE["phase"] = "搜索车次"
                    if perform_search(page, config):
                        BOT_STATE["last_result"] = f"发现目标车次 {config.get('time')}"
                        BOT_STATE["phase"] = "选座中"
                        if select_seat(page, train_mode):
                            BOT_STATE["phase"] = "填写乘客/付款"
                            if handle_passenger_and_payment(page, context, train_mode):
                                logger.info("[完成] 任务成功退出。")
                                return
                            else:
                                logger.warning("[中断] 付款中断...")
                                BOT_STATE["last_result"] = "付款流程中断"
                        else:
                            BOT_STATE["last_result"] = "座位被抢空 / 无符合偏好的座位"
                            logger.warning("[失败] 座位被抢空")
                    else:
                        BOT_STATE["last_result"] = f"未找到 {config.get('time')} 的车次"
                except Exception as e:
                    logger.error(f"[致命异常] 流程崩溃: {e}", exc_info=True)
                    try:
                        error_shot = page.screenshot(type='png', full_page=True)
                        send_telegram_photo(
                            f"⚠️ <b>报告！遇到卡死报错</b>\n错误详情: <code>{str(e)[:150]}...</code>",
                            error_shot
                        )
                    except Exception as screenshot_err:
                        logger.warning(f"[异常] 错误截图也失败了: {screenshot_err}")

            if SHOULD_LOGOUT_AND_EXIT:
                break

            BOT_STATE["phase"] = "休息中"
            logger.info(f"[休息] 倒计时 {REFRESH_INTERVAL} 秒...")
            for i in range(REFRESH_INTERVAL, 0, -1):
                if SHOULD_LOGOUT_AND_EXIT:
                    break

                # 远程控制: 每秒都检查
                try:
                    save_screenshot(page)
                    remote_cmd = get_command()
                    if remote_cmd:
                        logger.info(f"[远程控制] 收到命令: {remote_cmd['action']}")
                        result = execute_remote_command(page, remote_cmd)
                        logger.info(f"[远程控制] 执行结果: {result}")
                except Exception as e:
                    logger.warning(f"[远程控制] 处理失败: {e}")

                if i % 10 == 0 or i <= 5:
                    sys.stdout.write(f"    剩余 {i} 秒...\r")
                    sys.stdout.flush()

                BOT_STATE["next_refresh_in"] = i
                if i % 3 == 0:
                    command, target_id, new_offset = check_telegram_command(offset=tg_offset)
                    if command:
                        tg_offset = new_offset
                        logger.info(f"[指令] 收到 {command} ({target_id})")
                        process_telegram_command(page, command, target_id)

                time.sleep(1)
            logger.info("")

    except KeyboardInterrupt:
        logger.info("[系统] 收到 Ctrl+C，准备退出...")
        SHOULD_LOGOUT_AND_EXIT = True
    finally:
        def _raw_log(msg):
            """直接写文件，不依赖 logging 框架，确保退出阶段可追溯"""
            try:
                with open('bot.log', 'a', encoding='utf-8') as f:
                    f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
            except Exception:
                pass

        BOT_STATE["running"] = False
        BOT_STATE["phase"] = "已退出"
        _raw_log("[系统] 检测到退出，开始清理...")
        try:
            import remote_control
            remote_control.cleanup()
        except Exception:
            pass

        # 安全登出：优先使用缓存 cookies 走 HTTP（浏览器已关闭也有效）
        try:
            safe_logout(page)
        except Exception as e:
            _raw_log(f"[系统] 安全登出异常: {e}")

        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None and launched_own_browser:
                browser.close()
        except Exception:
            pass

        _raw_log("[系统] 清理完成，退出进程")
        try:
            sys.stdout.flush()
        except Exception:
            pass


if __name__ == '__main__':
    with sync_playwright() as playwright:
        run(playwright)
