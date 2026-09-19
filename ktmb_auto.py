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
import queue
from datetime import datetime
from playwright.sync_api import Playwright, sync_playwright
from remote_control import (publish_screenshot, publish_state, get_command, peek_command,
                            execute_remote_command, send_result,
                            cleanup as cleanup_remote_files)

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
CHROME_DEBUG_PORT = int(os.environ.get("KTMB_CHROME_PORT")
                        or CFG.get("bot_settings", {}).get("chrome_port", 9222))
# 显示浏览器窗口（Windows 默认开启，方便人看着/接管；Linux 服务器可设 KTMB_HEADLESS=1）
HEADLESS = str(os.environ.get("KTMB_HEADLESS", "1")).strip().lower() not in ("0", "false", "no", "off")
CHROME_PROFILE_DIR = os.environ.get("KTMB_CHROME_PROFILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
try:
    HEARTBEAT_INTERVAL = max(1, int(CFG.get("bot_settings", {}).get("heartbeat_interval", 100) or 100))
except Exception:
    HEARTBEAT_INTERVAL = 100
try:
    REFRESH_INTERVAL = max(5, int(CFG.get("bot_settings", {}).get("refresh_interval", 180) or 180))
except Exception:
    REFRESH_INTERVAL = 180

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


_TG_NET = {"down": False, "last_log": 0.0, "reason": ""}

# 通知发送队列 + 收发线程状态（Telegram 全部后台异步，绝不阻塞抢票）
# 文字和图片分两条队列：文字（抢到票、付款链接、报错）永远优先，图片积压也不会挡住文字
_TG_TEXT_Q = queue.Queue(maxsize=100)
_TG_PHOTO_Q = queue.Queue(maxsize=12)
_TG_GATE = {"until": 0.0, "backoff": 1.5}
_TG_STATS = {"queued": 0, "sent": 0, "dropped": 0}
TG_QUEUE_KEEP = 10 * 60          # 一条通知最多在队列里保留 10 分钟，期间一直重试
_TG_WORKERS = {"started": False, "stop": False, "lock": threading.Lock(), "event": None}

# Telegram 网络参数（可写在 config.json 的 notification 里，也可用环境变量覆盖）
#   telegram_proxy : "http://127.0.0.1:7890" 指定代理；填 "off" / "direct" 表示强制直连（忽略系统代理）
TG_PROXY = (os.environ.get("KTMB_TG_PROXY")
            or (CFG.get("notification", {}) or {}).get("telegram_proxy", "") or "").strip()
TG_FORCE_IPV4 = str(os.environ.get("KTMB_TG_IPV4", "0")).strip().lower() in ("1", "true", "yes", "on")

_tg_local = threading.local()
_TG_IPV4_DONE = {"done": False}


def _tg_reset_session():
    _tg_local.session = None


def _force_tg_ipv4():
    """很多 Windows 机器的 IPv6 是半残的：api.telegram.org 解析到 AAAA 后就一直连不上。
    网络层失败时自动切换成"只走 IPv4"再试，只做一次并且写进日志。"""
    if _TG_IPV4_DONE["done"]:
        return
    _TG_IPV4_DONE["done"] = True
    try:
        import socket
        import urllib3.util.connection as u3c
        u3c.allowed_gai_family = lambda: socket.AF_INET
        _tg_reset_session()
        logger.warning("[TG] 连接失败，已切换为强制 IPv4 重试")
    except Exception as e:
        logger.debug(f"[TG] 切换 IPv4 失败: {e}")


def _tg_session():
    """每个线程一个连接池：复用 TCP/TLS 连接，弱网下比每次新建连接稳得多"""
    sess = getattr(_tg_local, "session", None)
    if sess is not None:
        return sess
    sess = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    proxy = TG_PROXY.lower()
    if proxy in ("off", "none", "direct", "no-proxy", "-"):
        sess.trust_env = False
        sess.proxies = {}
    elif TG_PROXY:
        sess.trust_env = False
        sess.proxies = {"http": TG_PROXY, "https": TG_PROXY}
    logger.info(f"[TG] 已建立连接池会话 (代理: {TG_PROXY or '跟随系统'})")
    _tg_local.session = sess
    return sess


def _tg_call(method, url, retries=TG_MAX_RETRIES, **kwargs):
    """统一的 Telegram 请求：连接复用 + 自动重试 + 明确的失败原因

    返回 (response, error_text)；response 为 None 表示网络层彻底失败。
    """
    last = ""
    for attempt in range(1, max(1, retries) + 1):
        try:
            resp = _tg_session().request(method, url, **kwargs)
        except requests.exceptions.SSLError as e:
            last = f"SSL 证书错误: {str(e)[:120]}"
        except requests.exceptions.RequestException as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
            _force_tg_ipv4()
        else:
            if resp.status_code == 200:
                _tg_ok()
                return resp, None
            if resp.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {resp.status_code}"
            else:
                return resp, None
        if attempt < retries:
            time.sleep(TG_RETRY_DELAY * attempt)
    _tg_net_fail(None, last)
    return None, last


def _tg_error_detail(resp):
    try:
        data = resp.json()
        return str(data.get("description") or data)[:160]
    except Exception:
        try:
            return resp.text[:160]
        except Exception:
            return ""


def _tg_net_fail(where=None, err=None):
    """Telegram 连不上时的统一处理：不刷屏，每 60 秒提示一次，并说明真实原因"""
    if err is None:
        err = _TG_NET.get("reason") or "未知错误"
    _TG_NET["reason"] = str(err)
    _TG_NET["down"] = True
    now = time.time()
    if where or now - _TG_NET["last_log"] >= 60:
        first = _TG_NET["last_log"] <= 0
        _TG_NET["last_log"] = now
        if first:
            logger.warning(f"[TG] 连不上 api.telegram.org：{str(err)[:120]}")
            logger.warning("[TG] 通知与 Telegram 指令暂时不可用；网页端 /remote 仍可用；网络恢复后会自动继续")
        else:
            logger.info(f"[TG] 仍连不上 Telegram（{str(err)[:60]}），稍后自动重试")


def _tg_ok():
    if _TG_NET["down"]:
        _TG_NET["down"] = False
        logger.info("[TG] Telegram 已恢复连接")


def _tg_enqueue(kind, payload, caption=""):
    """把通知丢进后台发送队列，立即返回（绝不等待网络）"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    item = {"kind": kind, "payload": payload, "caption": caption,
            "ts": time.time(), "tries": 0}
    target = _TG_PHOTO_Q if kind == "photo" else _TG_TEXT_Q
    try:
        target.put_nowait(item)
    except queue.Full:
        if kind == "photo":
            # 截图是"锦上添花"，发不出去就丢，绝不占着内存/队列
            _TG_STATS["dropped"] += 1
            return
        try:
            target.get_nowait()           # 文字：丢最旧的，保证最新通知能发出去
            target.put_nowait(item)
        except Exception:
            return
    _TG_STATS["queued"] += 1
    start_tg_workers()


def send_notification(message):
    """发送 Telegram 文字通知（排队后台发送，不阻塞抢票）"""
    current_time = datetime.now().strftime('%H:%M:%S')
    formatted_msg = f"<b>[{BOT_NAME}] {current_time}</b>\n{message}"
    logger.info(f"[TG通知] {message[:80]}")
    _tg_enqueue("text", formatted_msg)


def send_telegram_photo(caption, image_bytes):
    """发送 Telegram 截图通知（排队后台发送，不阻塞抢票）"""
    if not image_bytes:
        return
    current_time = datetime.now().strftime('%H:%M:%S')
    formatted_caption = f"<b>[{BOT_NAME}] {current_time}</b>\n{str(caption)[:900]}"
    logger.info("[TG通知] 截图已排队")
    _tg_enqueue("photo", image_bytes, formatted_caption)


def _tg_send_once(item):
    """发送一条；返回 (是否结束, 错误原因)"""
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if item["kind"] == "photo":
        resp, err = _tg_call("POST", base + "/sendPhoto", retries=1, timeout=(5, 45),
                             data={"chat_id": TELEGRAM_CHAT_ID,
                                   "caption": item.get("caption", ""), "parse_mode": "HTML"},
                             files={"photo": ("screen.png", item["payload"], "image/png")})
    else:
        resp, err = _tg_call("POST", base + "/sendMessage", retries=1, timeout=(5, 20),
                             data={"chat_id": TELEGRAM_CHAT_ID,
                                   "text": item["payload"], "parse_mode": "HTML"})
    if resp is None:
        return False, err
    if resp.status_code == 200:
        return True, ""
    detail = _tg_error_detail(resp)
    if resp.status_code in (400, 401, 403, 404):
        logger.warning(f"[TG] 发送被拒绝（HTTP {resp.status_code}）: {detail}")
        return True, detail          # 内容/token 问题，重试没意义
    return False, f"HTTP {resp.status_code}: {detail}"


def _next_tg_item():
    """先发文字，再发图片（文字才是抢票要紧的信息）"""
    for q in (_TG_TEXT_Q, _TG_PHOTO_Q):
        try:
            return q.get_nowait()
        except queue.Empty:
            continue
    return None


def _requeue_tg_item(item):
    q = _TG_PHOTO_Q if item["kind"] == "photo" else _TG_TEXT_Q
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
            q.put_nowait(item)
        except Exception:
            _TG_STATS["dropped"] += 1


def _tg_sender_loop():
    """后台发送线程：一直重试直到送达（最多保留 10 分钟），网络不通也不影响抢票"""
    while not _TG_WORKERS["stop"]:
        item = _next_tg_item()
        if item is None:
            _TG_WORKERS["event"].wait(0.5)
            continue

        # 全局退避：网络不通时所有通知一起等，不让某一条卡住后面所有消息
        while not _TG_WORKERS["stop"] and time.time() < _TG_GATE["until"]:
            _TG_WORKERS["event"].wait(0.5)
        if _TG_WORKERS["stop"]:
            break

        item["tries"] += 1
        ok, err = _tg_send_once(item)
        if ok:
            _TG_STATS["sent"] += 1
            _TG_GATE["until"] = 0.0
            _TG_GATE["backoff"] = 1.5
            continue

        age = int(time.time() - item["ts"])
        if item["tries"] <= 2 or item["tries"] % 8 == 0:
            logger.warning(f"[TG] 发送失败（第 {item['tries']} 次，已排队 {age} 秒）: {err}")
        if age > TG_QUEUE_KEEP:
            logger.error(f"[TG] 通知排队超过 {TG_QUEUE_KEEP // 60} 分钟仍未送达，放弃："
                         f"{str(item.get('payload'))[:50]}")
            _TG_STATS["dropped"] += 1
            continue

        _requeue_tg_item(item)
        _TG_GATE["until"] = time.time() + _TG_GATE["backoff"]
        _TG_GATE["backoff"] = min(30.0, _TG_GATE["backoff"] * 2)


def _tg_receiver_loop():
    """后台接收线程：长轮询 getUpdates，把指令放进本地队列"""
    if not TELEGRAM_BOT_TOKEN:
        return
    offset = prime_telegram_offset()
    if offset:
        TG_OFFSET["value"] = offset
    bad_rounds = 0
    while not _TG_WORKERS["stop"]:
        try:
            command, target_id, new_offset = check_telegram_command(
                offset=TG_OFFSET["value"], long_poll=True)
        except Exception as e:
            logger.debug(f"[TG] 接收线程异常: {e}")
            command, target_id, new_offset = None, None, None
        if new_offset:
            TG_OFFSET["value"] = new_offset
        if command:
            bad_rounds = 0
            with _TG_QUEUE_LOCK:
                if len(_TG_QUEUE) >= _TG_QUEUE_MAX:
                    _TG_QUEUE.pop(0)
                _TG_QUEUE.append((command, target_id))
            logger.info(f"[指令] 已排队: {command} ({target_id})")
        elif _TG_NET["down"]:
            bad_rounds += 1
            _TG_WORKERS["event"].wait(min(30, 2 * bad_rounds))
        else:
            bad_rounds = 0


def start_tg_workers():
    """启动 Telegram 收发线程（幂等；没有 token 就什么都不做）"""
    if not TELEGRAM_BOT_TOKEN:
        return
    with _TG_WORKERS["lock"]:
        if _TG_WORKERS["started"]:
            return
        _TG_WORKERS["started"] = True
        _TG_WORKERS["stop"] = False
        if _TG_WORKERS["event"] is None:
            _TG_WORKERS["event"] = threading.Event()
        threading.Thread(target=_tg_sender_loop, daemon=True, name="tg-sender").start()
        threading.Thread(target=_tg_receiver_loop, daemon=True, name="tg-receiver").start()
        logger.info("[TG] 收发线程已启动：抢票主流程不再等待 Telegram")


def tg_queue_size():
    """还有多少条通知没发出去"""
    return _TG_TEXT_Q.qsize() + _TG_PHOTO_Q.qsize()


def _tg_flush(seconds=6):
    """退出前尽量把队列里的通知发出去（尽力而为）"""
    if not _TG_WORKERS["started"]:
        return
    deadline = time.time() + seconds
    while time.time() < deadline and tg_queue_size() > 0:
        time.sleep(0.3)
    left = tg_queue_size()
    if left:
        logger.warning(f"[TG] 退出时仍有 {left} 条通知未送达")


BOT_START_TS = int(time.time())


# 同一批 getUpdates 里多余的指令先存这里，逐条交给流程处理（避免被丢弃）
_TG_BATCH = []


def _is_stale_message(message):
    """启动之前发出的消息 -> 一律忽略

    你的网络对 Telegram 时通时断，很多 getUpdates 请求失败，导致旧消息一直积压在
    Telegram 服务器上没有被确认。等网络恢复时它们会被一次性推回来 —— 于是机器人
    "自己收到 /manual 然后退出"。用发送时间过滤掉这些历史消息。
    """
    ts = message.get("date")
    if not ts:
        return False
    return int(ts) < BOT_START_TS - 5


def prime_telegram_offset():
    """只取 offset、丢弃积压的旧消息；绝不执行其中任何一条"""
    if not TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    resp, err = _tg_call("GET", url, retries=2,
                         params={"timeout": 0, "allowed_updates": ["message"]}, timeout=15)
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
        results = data.get("result", []) if data.get("ok") else []
        if results:
            logger.info(f"[TG] 丢弃 {len(results)} 条积压的旧消息（避免旧指令被重放）")
            return results[-1]["update_id"] + 1
    except Exception as e:
        logger.debug(f"[TG] 解析 getUpdates 失败: {e}")
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


def check_telegram_command(offset=None, long_poll=False):
    """检查 Telegram 是否有新指令

    long_poll=True 时用长轮询（timeout=20）：有消息立刻返回，没有就挂着等。
    一批里有多个指令时会依次返回，不会像以前那样只留第一条、其它被丢掉。
    """
    global SHOULD_LOGOUT_AND_EXIT
    if not TELEGRAM_BOT_TOKEN:
        return None, None, offset

    if _TG_BATCH:
        cmd, target = _TG_BATCH.pop(0)
        return cmd, target, offset

    wait_s = 20 if long_poll else 0
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {"timeout": wait_s, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset

        resp, err = _tg_call("GET", url, retries=1 if long_poll else 2,
                             params=params, timeout=wait_s + 10)
        if resp is None:
            return None, None, offset
        if resp.status_code == 409:
            if throttle_ok("tg_409", 300):
                logger.error("[TG] HTTP 409：同一个 Bot Token 正在被另一个程序 getUpdates。"
                             "请确认只运行了一个机器人实例，否则指令会被随机抢走")
            return None, None, offset
        if resp.status_code == 401:
            if throttle_ok("tg_401", 300):
                logger.error("[TG] HTTP 401：telegram_token 无效，请在面板「通知」里重新填写")
            return None, None, offset
        if resp.status_code != 200:
            return None, None, offset

        data = resp.json()
        results = data.get("result", []) if data.get("ok") else []
        if not results:
            return None, None, offset

        new_offset = results[-1]["update_id"] + 1
        skipped = 0
        for update in results:
            message = update.get("message") or {}
            if _is_stale_message(message):
                skipped += 1
                continue
            command, target_id = parse_telegram_command(message.get("text", ""))
            if not command:
                continue
            if command == "/logout" and (target_id == "all" or target_id == BOT_ID):
                SHOULD_LOGOUT_AND_EXIT = True
                logger.info("[TG] 收到 logout 指令，标记退出")
            _TG_BATCH.append((command, target_id))
        if skipped:
            logger.info(f"[TG] 忽略 {skipped} 条启动前发来的旧消息")
        if _TG_BATCH:
            cmd, target = _TG_BATCH.pop(0)
            return cmd, target, new_offset
        return None, None, new_offset
    except requests.exceptions.RequestException as e:
        _tg_net_fail(None, f"{type(e).__name__}: {e}")
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


# ================= 🔄 心跳 / 可打断等待 / 出错定位 =================
# v1.3.2：所有耗时等待都必须经过 tick()。这样在任何阶段（登录、搜索、选座、
# 等待付款、休息）都能及时响应：停止指令、Web 面板截图、Telegram 指令。
# 之前的"找不到元素就卡死、点停止要等好几分钟"就是因为等待期间完全不看外部状态。

try:
    SCREENSHOT_INTERVAL = float((CFG.get("bot_settings", {}) or {}).get("screenshot_interval", 5) or 0)
except Exception:
    SCREENSHOT_INTERVAL = 5.0

_TG_QUEUE = []
_TG_QUEUE_MAX = 10
_TG_LAST_POLL = 0.0
_TG_QUEUE_LOCK = threading.Lock()
TG_OFFSET = {"value": None}
_LAST_TICK = {"ts": time.time(), "phase": ""}


def tick(page=None, phase=None, force_shot=False):
    """轻量心跳：检查停止指令 / 发布截图 / 收取 Telegram 指令（只入队，不在流程中间执行）"""
    try:
        _LAST_TICK["ts"] = time.time()
        if phase:
            BOT_STATE["phase"] = phase
            _LAST_TICK["phase"] = phase
    except Exception:
        pass
    if SHOULD_LOGOUT_AND_EXIT:
        return False
    if check_remote_stop_command():
        return False
    _publish_state_throttled()
    if page is not None:
        try:
            # 没人在看 /remote 时 publish_screenshot 会直接返回，不浪费抢票时间
            publish_screenshot(page, force=force_shot,
                               interval=SCREENSHOT_INTERVAL if SCREENSHOT_INTERVAL > 0 else 3.0)
        except Exception:
            pass
    return True


def pending_telegram_count():
    """本地待处理指令数量（不联网）"""
    with _TG_QUEUE_LOCK:
        return len(_TG_QUEUE)


def drop_pending_telegram(why=""):
    """丢弃本地队列里还没处理的指令（进入付款待命前用，避免执行很久以前的旧指令）"""
    with _TG_QUEUE_LOCK:
        n = len(_TG_QUEUE)
        _TG_QUEUE.clear()
    if n:
        logger.info(f"[TG] 丢弃 {n} 条待处理旧指令{(' - ' + why) if why else ''}")
    return n


def next_telegram_command():
    with _TG_QUEUE_LOCK:
        return _TG_QUEUE.pop(0) if _TG_QUEUE else (None, None)


_LAST_STATE_PUB = {"ts": 0.0, "phase": None}


def _publish_state_throttled():
    """把阶段/轮数/pid/TG状态写进 meta（不截图，很便宜，但也要限流）"""
    now = time.time()
    phase = BOT_STATE.get("phase", "")
    if phase == _LAST_STATE_PUB["phase"] and now - _LAST_STATE_PUB["ts"] < 3:
        return
    _LAST_STATE_PUB["ts"] = now
    _LAST_STATE_PUB["phase"] = phase
    try:
        publish_state(phase=phase, round_no=BOT_STATE.get("round", 0),
                      extra={"pid": os.getpid(),
                             "telegram": "down" if _TG_NET["down"] else "ok"})
    except Exception:
        pass


def sleep_s(seconds, page=None, phase=None):
    """可打断等待：响应停止指令 + 持续发布截图 + 收指令入队"""
    deadline = time.time() + max(0.0, float(seconds))
    while True:
        if not tick(page, phase=phase):
            return False
        remaining = deadline - time.time()
        if remaining <= 0:
            return True
        time.sleep(min(0.5, remaining))


_IS_VISIBLE_JS = """(sels) => {
    for (const s of sels) {
        try {
            const el = document.querySelector(s);
            if (el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return s;
        } catch (e) { /* 选择器不是标准 CSS（Playwright 专有语法）就跳过 */ }
    }
    return null;
}"""


def wait_visible(page, selector, timeout=10, label=None):
    """分片等待元素可见：等待期间依旧能响应停止/远程面板，不会整段卡死

    每次轮询只做一次 CDP 往返（旧实现每次 count + is_visible 两次往返，
    Windows 上这些往返就是"慢"的主要来源）。
    """
    deadline = time.time() + max(0.0, float(timeout))
    loc = None
    while True:
        try:
            if page.evaluate(_IS_VISIBLE_JS, [selector]):
                return True
        except Exception:
            # JS 判不了（比如选择器带 Playwright 语法）就退回 locator
            try:
                if loc is None:
                    loc = page.locator(selector).first
                if loc.count() and loc.is_visible():
                    return True
            except Exception:
                pass
        if time.time() >= deadline:
            return False
        if not tick(page):
            return False
        time.sleep(0.25)


def wait_any(page, selectors, timeout=10):
    """任意一个选择器可见就返回（一次 JS 判断全部，省掉 N 次往返）"""
    deadline = time.time() + max(0.0, float(timeout))
    selectors = list(selectors)
    while True:
        try:
            if page.evaluate(
                """(sels) => sels.some(s => {
                    const el = document.querySelector(s);
                    return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                })""", selectors):
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        if not tick(page):
            return False
        time.sleep(0.25)


def page_report(page):
    """描述"现在在哪一页、页面长什么样"——出错定位用"""
    url, title, ready, buttons = "?", "?", "?", []
    try:
        url = page.url
    except Exception:
        pass
    try:
        title = (page.title() or "")[:60]
    except Exception:
        pass
    try:
        ready = page.evaluate("document.readyState")
    except Exception:
        pass
    try:
        buttons = page.evaluate("""() => Array.from(document.querySelectorAll('button, input[type=submit], input[type=button], a.btn'))
            .filter(e => e.offsetParent !== null)
            .slice(0, 8)
            .map(e => (e.id ? '#' + e.id : '') + '|' + String(e.innerText || e.value || '').trim().slice(0, 18))""")
    except Exception:
        pass
    text = f"页面: {url} | 标题: {title} | readyState={ready}"
    if buttons:
        text += f" | 可见按钮: {buttons}"
    return text


def report_stuck(page, what, shot=True, notify=True):
    """找不到元素/流程异常时：写清"在哪、缺什么、页面上有什么"，并发一张截图（去重）"""
    detail = page_report(page)
    logger.error(f"[定位失败] {what} :: {detail}")
    if not notify or not throttle_ok(f"stuck:{str(what)[:40]}", 120):
        return detail
    if shot:
        try:
            img = page.screenshot(type='png', full_page=True, timeout=15000)
            send_telegram_photo(f"⚠️ <b>{str(what)[:120]}</b>\n<code>{detail[:300]}</code>", img)
            return detail
        except Exception as e:
            logger.debug(f"[定位失败] 截图也失败: {e}")
    send_notification(f"⚠️ {what}\n{detail[:300]}")
    return detail


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
        #    v1.3.2：旧实现只在"元素带内联 display 样式"时才隐藏，Bootstrap 5 /
        #    纯 CSS 类控制的可见弹窗根本盖不住 —— 弹窗挡住按钮就会一路"找不到元素"。
        page.evaluate("""() => {
            const isVisible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            document.querySelectorAll('.modal').forEach(m => {
                if (m.id === 'seatSelect' || m.closest('#seatSelect')) return;  // 选座弹窗必须保留
                if (isVisible(m) || m.classList.contains('show')) {
                    m.style.display = 'none';
                    m.classList.remove('show');
                }
            });
            document.querySelectorAll('.modal-backdrop').forEach(e => e.remove());
            document.querySelectorAll('body > .fade.show').forEach(e => {
                if (e.id !== 'seatSelect' && !e.closest('#seatSelect')) e.classList.remove('show');
            });
            document.body.classList.remove('modal-open');
            document.body.style.overflow = '';
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
        logger.info("[系统] 本次没有成功登录过，无需登出（跳过网络请求）")
        ok = True

    if not ok and page is not None:
        try:
            page.goto(KTMB_LOGOUT_URL, timeout=6000, wait_until="domcontentloaded")
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
    """判断是否已登录

    v1.3.2：旧实现是"看不到登录链接就算已登录"，于是页面白屏 / 502 / 还在加载时
    也会返回 True，机器人接着在坏页面上点搜索，最后只报一句"找不到元素"。
    现在要求页面确实是 KTMB 且内容正常，才允许走"没有登录链接 = 已登录"这条老规则。
    """
    try:
        url = (page.url or "").lower()
        if "online.ktmb.com.my" not in url:
            return False
        if "/account/login" in url:
            return False
        if page.locator("a[href*='/Account/Logout']").count():
            return True
        link = page.locator("a[href*='/Account/Login']")
        if link.count():
            return not link.first.is_visible()
        try:
            if page.evaluate("document.readyState") == "loading":
                return False
            if not (page.locator("body").inner_text() or "").strip():
                return False
        except Exception:
            return False
        return True
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
                logger.warning(f"[登录] 没找到登录表单 (第 {attempt}/{max_attempts} 次) :: {page_report(page)}")
                if not interruptible_sleep(1, page):
                    return False
                continue
            if check_remote_stop_command():
                return False
            logger.info(f"[系统] 执行登录... (第 {attempt}/{max_attempts} 次)")
            email_box.first.fill(KTMB_EMAIL)
            page.get_by_role("textbox", name="Password").first.fill(KTMB_PASSWORD)
            page.get_by_role("button", name="Login").first.click()
        except Exception as e:
            if SHOULD_LOGOUT_AND_EXIT:
                return False
            logger.warning(f"[登录] 提交表单异常，重试中: {e} :: {page_report(page)}")
            handle_popup(page)
            if not interruptible_sleep(2, page):
                return False
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
            if not interruptible_sleep(1, page):
                return False

        if is_logged_in(page) and "Login" not in page.url:
            logger.info("[系统] 登录成功")
            cache_cookies(page)
            return True

        if attempt < max_attempts:
            wait_s = 2 * attempt
            logger.warning(f"[登录] 第 {attempt} 次尝试未成功，{wait_s} 秒后重试")
            if not interruptible_sleep(wait_s, page):
                return False

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
        report_stuck(page, f"搜索填表失败: {str(e)[:150]}")
        return False

    if not wait_visible(page, ".btn-seat-layout", timeout=15, label="车次座位按钮"):
        # 可能是被弹窗挡住了：清一次弹窗再等 5 秒
        handle_popup(page)
        if not wait_visible(page, ".btn-seat-layout", timeout=5, label="车次座位按钮"):
            logger.info(f"[搜索] 没有可用车次 :: {page_report(page)}")
            return False

    handle_popup(page)

    rows = page.locator(".depart-trips > tr")
    if rows.count() == 0:
        logger.info("[搜索] 结果为空")
        return False

    # 一次 JS 在浏览器里把所有行扫完（旧实现逐行 inner_text，每行一次往返，实测拖 20+ 秒）
    try:
        target_index = page.evaluate(
            """(t) => {
                const rows = [...document.querySelectorAll('.depart-trips > tr')];
                for (let i = 0; i < rows.length; i++) {
                    if ((rows[i].innerText || '').includes(t)) return i;
                }
                return -1;
            }""", config['time'])
    except Exception as e:
        logger.debug(f"[搜索] 扫描车次行失败: {e}")
        target_index = -1

    if target_index is None or target_index < 0:
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
    const paxEl = document.querySelector('#fixedPax');
    const pax = paxEl ? (parseInt(paxEl.value, 10) || 1) : 1;
    return { seats, coaches, pax };
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
    if not wait_visible(page, "#seatSelect", timeout=10, label="选座弹窗"):
        report_stuck(page, "点开车次后选座弹窗没有出现")
        return False
    time.sleep(0.5)
    handle_popup(page)

    data = page.evaluate(SEAT_SCAN_JS)
    seats = data.get('seats', [])
    coaches = data.get('coaches', [])
    # 人数顺便在同一个 JS 里读掉（旧实现单独读 #fixedPax，元素不存在时要干等 20 秒默认超时）
    try:
        pax = max(1, int(data.get('pax') or 1))
    except Exception:
        pax = 1
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
        if not tick(page):
            return False
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
        report_stuck(page, f"选座确认按钮未启用（已选 {len(chosen)}/{pax} 个座位）")
        return False

    t_click = time.time()
    try:
        confirm.click()
    except Exception as e:
        logger.warning(f"[选座] 点击确认按钮失败: {e}")
        return False

    # 轮询订座结果，一成功立刻往下走
    # （旧实现先干等 bookingData 15 秒、再等按钮 8 秒 —— 实测白等 23 秒）
    booked = False
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            state = page.evaluate(
                """() => {
                    const b = document.getElementById('bookingData');
                    const btn = document.querySelector('.btn-passenger');
                    return {
                        hasData: !!(b && b.value && b.value.length > 0),
                        visible: !!(btn && (btn.offsetWidth || btn.offsetHeight || btn.getClientRects().length))
                    };
                }"""
            )
            if state.get('hasData') or state.get('visible'):
                booked = True
                break
        except Exception:
            pass
        if not tick(page):
            return False
        time.sleep(0.25)

    if booked:
        logger.info(f"[选座] 订座完成（等待 {time.time() - t_click:.1f} 秒）")
    handle_popup(page)

    if not booked and wait_visible(page, ".btn-passenger", timeout=3, label="乘客信息按钮"):
        booked = True

    if not booked:
        report_stuck(page, "选座确认后既没有拿到订座数据，也没出现乘客信息按钮")
        close_seat_modal(page)
        return False
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

    v1.3.2：等待期间照常跑心跳（原来这里会闷头等最多 5 分钟，面板点停止要等到超时才生效）。
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
        if not sleep_s(1, page):
            return page

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
        report_stuck(page, f"付款页找不到 {label} 按钮")
        return None

    proceed = None
    for sel in ("#PaymentGateway", "input[value*='Click HERE']", "input[value*='Click Here']",
                "input[value*='HERE']", "input[type='submit']", "button[type='submit']"):
        loc = page.locator(sel).first
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    if loc.count() and loc.is_visible():
                        break
                except Exception:
                    pass
                if not tick(page):
                    return page
                time.sleep(0.3)
            else:
                continue
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
        logger.warning(f"[支付] 未出现网关跳转按钮，检查当前页面状态 :: {page_report(page)}")
    return page


def click_gateway_pay(context, gateway_page, before_ids=None, max_attempts=3, first_timeout=120000):
    """在支付网关页面点击 PAY，直到页面进入下一步（二维码/跳转）

    v1.3.2：等待按钮的部分改成"分片轮询"，等待期间仍然响应停止指令，
    并且找不到按钮时会把当前页面信息写进日志（原来只是默默等 120 秒然后 break）。
    """
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

        wait_ms = first_timeout if attempt == 1 else 20000
        btn = gateway_page.locator(combined).first
        deadline = time.time() + wait_ms / 1000.0
        found = False
        while time.time() < deadline:
            try:
                if btn.count() and btn.is_visible():
                    found = True
                    break
            except Exception:
                pass
            if not tick(gateway_page):
                return gateway_page
            time.sleep(0.5)

        if not found:
            logger.warning("[支付] 未找到网关付款按钮（等待超时）")
            report_stuck(gateway_page, "支付网关页找不到付款按钮")
            break

        try:
            btn.click(timeout=10000)
            logger.info("[支付] 已点击网关付款按钮")
        except Exception as e:
            logger.warning(f"[支付] 点击网关付款按钮失败: {str(e)[:100]}")
            report_stuck(gateway_page, "支付网关付款按钮点不动")
            break

        try:
            gateway_page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        sleep_s(6, gateway_page)

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
        sleep_s(3, gateway_page)

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
    finally:
        # v1.3.2：以前截图后视口一直停在 1920x2500，后面所有操作/心跳都在超长视口里跑
        try:
            page.set_viewport_size({"width": 1920, "height": 1080})
        except Exception:
            pass


def collect_payment_links(page, *extra_urls):
    """收集能重新打开付款页的链接

    v1.3.3：实测"点完 PAY 之后"那个 viewqr.php 是裸网址，发过去打不开；
    真正有用的是**点击 PAY 之前**的网关网址（带订单参数的付款页）。
    这里把点击前后的网址、以及页面上二维码图片的网址一起收集起来。
    """
    links = []

    def add(u):
        u = (u or "").strip()
        if u.startswith("http") and u not in links:
            links.append(u)

    for u in extra_urls:
        add(u)
    try:
        add(page.url)
    except Exception:
        pass
    try:
        for u in page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('img').forEach(img => {
                const s = img.src || '';
                if (/qr/i.test(s) && !/^data:/.test(s)) out.push(s);
            });
            return out.slice(0, 3);
        }""") or []:
            add(u)
    except Exception:
        pass
    return links


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

        if command in ("/duitnow", "/tng"):
            is_tng = (command == "/tng")
            label = "TnG" if is_tng else "DuitNow"
            if is_tng:
                buttons = ["#btnGoPaymentTnG", "div:has-text('Touch')", "button:has-text('TnG')"]
            else:
                buttons = ["#btnGoPaymentDuitNow", "div:has-text('DuitNow QR')", "button:has-text('DuitNow')"]

            send_notification(f"💳 正在生成 {label}...")
            gateway_page = go_to_payment_gateway(page, context, buttons, label)
            if gateway_page is None:
                return False
            gateway_page = acquire_gateway_page(context, page, before_ids)

            # 先把"点开就能付款"的网关网址发出去
            # （点 PAY 之后的 viewqr.php 是二维码展示页，单独发过去通常打不开）
            pay_url = ""
            try:
                pay_url = gateway_page.url or ""
            except Exception:
                pass
            if pay_url.startswith("http"):
                logger.info(f"[支付] 点击付款前的网关网址: {pay_url}")
                send_notification(
                    f"🔗 <b>{label} 付款页链接</b>（先存这个，点开就是付款页）:\n{pay_url}")

            gateway_page = click_gateway_pay(context, gateway_page, before_ids)
            latest = last_new_page(context, before_ids)
            if latest is not None and latest is not gateway_page:
                gateway_page = latest

            links = collect_payment_links(gateway_page, pay_url)
            if links:
                send_notification(f"🔗 <b>{label} 付款链接</b>:\n" + "\n".join(links))
            enlarge_and_shot(gateway_page, f"⚡️ {label} 请扫码" + (f"\n\n🔗 {pay_url}" if pay_url else ""))
            return False

        if command == "/wallet":
            send_notification("💳 尝试 KTM Wallet...")
            if not click_first(page, ["#btnKtmbEWallet", "button:has-text('Wallet')"], timeout=8000, label="KTM Wallet"):
                return False
            sleep_s(3, page)
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


def check_remote_stop_command():
    """检查命令文件里是否有停止指令

    这一步很关键：Windows 上面板无法用 SIGTERM 优雅停止进程（terminate 是硬杀），
    只能靠命令文件。如果在长时间等待中不检查，面板 45 秒超时后会硬杀，
    机器人来不及登出 KTMB -> 30 分钟冷却。
    """
    global SHOULD_LOGOUT_AND_EXIT
    if SHOULD_LOGOUT_AND_EXIT:
        return True
    try:
        cmd = peek_command()
    except Exception:
        return False
    if not cmd:
        return False
    if cmd.get('action') in ('logout', 'stop', 'shutdown'):
        SHOULD_LOGOUT_AND_EXIT = True
        logger.info("[远程控制] 收到停止指令，准备安全登出")
        return True
    return False


def interruptible_sleep(seconds, page=None):
    """可被打断的等待：响应退出信号、Web 面板停止指令、远程截图请求与 TG 指令"""
    for _ in range(int(seconds)):
        if not tick(page):
            return False
        time.sleep(1)
    return not SHOULD_LOGOUT_AND_EXIT


def handle_remote_control(page):
    """处理一次远程控制命令

    注意：Web 面板"停止"时在 Windows 上无法用 SIGTERM 优雅退出（terminate 是硬杀），
    所以面板会写一个 logout 命令文件，由这里接管并触发安全登出。
    """
    global SHOULD_LOGOUT_AND_EXIT
    try:
        tick(page)
        remote_cmd = get_command()
        if not remote_cmd:
            return
        action = remote_cmd.get('action')
        if action in ('logout', 'stop', 'shutdown'):
            SHOULD_LOGOUT_AND_EXIT = True
            logger.info("[远程控制] 收到停止指令，准备安全登出")
            send_result(remote_cmd.get('id'), 'success', '收到停止指令，正在安全登出')
            return
        logger.info(f"[远程控制] 收到命令: {action}")
        result = execute_remote_command(page, remote_cmd)
        logger.info(f"[远程控制] 执行结果: {result}")
        send_result(remote_cmd.get('id'), result.get('status'), result.get('message'))
        try:
            publish_screenshot(page, force=True, interval=0)
        except Exception:
            pass
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
    """等待 Telegram 支付指令（待命模式，最多 20 分钟）

    返回: "SUCCESS"(已付款) / "MANUAL"(交人工) / False(取消、超时或退出)

    v1.3.2：
      * 每秒都在跑心跳 → 面板截图/停止/远程点击都即时生效；
      * Telegram 指令走队列，不会因为在等长轮询而漏掉；
      * /manual 不再让程序退出（以前会直接登出并关掉浏览器，人工根本没法接着付）。
    """
    logger.info("[系统] 进入待命模式，等待支付指令...")
    BOT_STATE["phase"] = "抢到票，等待付款指令"
    ticket_info = extract_ticket_info(page)
    msg = (
        f"🚨 <b>{BOT_NAME} 抢票成功！</b> 🚨\n\n{ticket_info}\n\n"
        f"👇 <b>请发送指令：</b>\n/duitnow{BOT_ID} | /tng{BOT_ID} | /wallet{BOT_ID} | /manual{BOT_ID}"
    )
    send_notification(msg)

    # 关键：进入待命前先丢弃本地还排着队的旧指令（很久以前发的 /manual 不该这时才执行）
    drop_pending_telegram("进入付款待命")

    deadline = time.time() + 20 * 60
    while time.time() < deadline:
        if SHOULD_LOGOUT_AND_EXIT:
            return False

        handle_remote_control(page)
        if SHOULD_LOGOUT_AND_EXIT:
            return False

        command, target_id = next_telegram_command()
        if not command:
            if not sleep_s(0.6, page):
                return False
            continue

        logger.info(f"[指令] 收到 {command} ({target_id})")
        if process_telegram_command(page, command, target_id):
            continue
        if command == "/logout" and target_id in ("all", BOT_ID):
            return False
        if command in ("/cancel", "/abort") and target_id in ("all", BOT_ID):
            logger.info("[指令] 收到取消指令，结束待命")
            send_notification("🛑 已取消付款待命，机器人继续监控。")
            return False
        if command == "/manual" and target_id in ("all", BOT_ID):
            execute_payment_command(page, context, "/manual", target_id)
            return "MANUAL"
        if execute_payment_command(page, context, command, target_id):
            return "SUCCESS"
        if target_id in ("all", BOT_ID):
            send_notification(f"🤖 仍在待命...（{command} 已处理）")

        try:
            if not page.url.startswith("https"):
                break
        except Exception:
            break

    if not SHOULD_LOGOUT_AND_EXIT:
        logger.warning("[系统] 20 分钟内没有收到付款指令，退出待命")
        send_notification("⏰ 20 分钟没收到付款指令，已退出待命（订单未付款）。")
    return False


def manual_hold(page):
    """人工接管模式：机器人不再碰页面，浏览器保持打开

    返回 True = 用户取消，继续下一轮监控；False = 应该退出程序（/logout 或面板停止）
    """
    BOT_STATE["phase"] = "人工接管"
    logger.info("[人工] 已交接，浏览器保持打开；发 /logout 结束，发 /cancel 恢复自动监控")
    send_notification("🧑‍✈️ <b>人工接管中</b>\n机器人不会再操作页面，浏览器保持打开。\n"
                      "/snap 看画面 · /cancel 恢复自动监控 · /logout 退出并登出")
    while True:
        if SHOULD_LOGOUT_AND_EXIT:
            return False
        handle_remote_control(page)
        if SHOULD_LOGOUT_AND_EXIT:
            return False
        command, target_id = next_telegram_command()
        if command:
            logger.info(f"[指令] 收到 {command} ({target_id})")
            if command == "/logout" and target_id in ("all", BOT_ID):
                return False
            if command in ("/cancel", "/abort") and target_id in ("all", BOT_ID):
                send_notification("🛑 已退出手动模式，机器人继续自动监控。")
                return True
            if not process_telegram_command(page, command, target_id):
                send_notification("🧑‍✈️ 人工接管中：目前只响应 /snap /status /page /cancel /logout")
        time.sleep(0.5)


def process_automated_payment(page, context, method):
    """处理自动支付流程

    返回: "SUCCESS"(已付款) / "MANUAL"(交给人工，浏览器继续开着) / False
    """
    if method == "Manual":
        send_notification("🛑 请手动付款！机器人不会再操作页面。")
        return "MANUAL"
    if method == "KTM Wallet":
        return "SUCCESS" if execute_payment_command(page, context, "/wallet", "all") else False
    if method == "DuitNow":
        execute_payment_command(page, context, "/duitnow", "all")
        send_notification("📱 DuitNow 二维码已生成，请扫码付款。")
        return "MANUAL"
    logger.warning(f"[支付] 未知支付方式: {method}")
    return False


def click_first(page, selectors, timeout=5000, required=True, label="", retries=None):
    """依次尝试多个选择器，点击第一个可见元素（先快速扫描，再统一等待）

    v1.3.2：找不到元素时会重试，并把"当前页面 / 可见按钮"写进日志（之前只丢一句
    warning 就继续往下走，表现就是"卡住又没有错误信息"）。
    """
    if isinstance(selectors, str):
        selectors = [selectors]
    if retries is None:
        retries = 2 if required else 1

    for attempt in range(1, retries + 1):
        if not tick(page):
            return False

        # 快路径：一次 JS 找出可见元素，再点它（比逐个 locator.count()/is_visible() 少一半往返）
        try:
            visible_sel = page.evaluate(_IS_VISIBLE_JS, selectors)
        except Exception:
            visible_sel = None
        if visible_sel:
            try:
                page.locator(visible_sel).first.click(timeout=3000)
                logger.debug(f"[点击] {label or visible_sel} -> {visible_sel}")
                return True
            except Exception:
                pass

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
                loc.wait_for(state="visible", timeout=min(timeout, 6000))
                loc.click(timeout=5000)
                logger.debug(f"[点击] {label or combined} -> {combined}")
                return True
            except Exception:
                pass
        if attempt < retries:
            sleep_s(1, page)

    if required:
        report_stuck(page, f"找不到可点击元素: {label or selectors}")
    else:
        logger.debug(f"[点击] 未找到（可忽略）: {label or selectors}")
    return False


def cancel_booking(page):
    """取消当前未完成的订座，释放座位"""
    try:
        btn = page.locator(".btn-reset").first
        if btn.count() and btn.is_visible():
            btn.click()
            sleep_s(2, page)
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
    每一步之间都会跑心跳，所以随时可以停止/远程接管。
    """
    for step in range(max_steps):
        if not tick(page):
            return False

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
                        sleep_s(1, page)
                        continue
                if click_first(page, ["#popupModalOkButton"], timeout=3000, required=False, label="确认弹窗"):
                    sleep_s(1, page)
                    continue
        except Exception as e:
            logger.debug(f"[填表] 处理弹窗异常: {e}")

        # 餐食/条款确认弹窗
        if click_first(page, ["#confirmationConfirmButton"], timeout=2000, required=False, label="确认继续付款"):
            sleep_s(1, page)
            continue

        # 放弃保险按钮
        if click_first(page, ["#btnUpdateInsuranceNo"], timeout=2000, required=False, label="放弃保险"):
            sleep_s(1, page)
            continue

        # 继续付款按钮
        if click_first(page, ["#btnProceedToPayment"], timeout=2000, required=False, label="继续付款"):
            sleep_s(1, page)
            continue

        sleep_s(1, page)

    if payment_page_ready(page):
        return True
    report_stuck(page, f"点了 {max_steps} 步都没能到达付款方式选择页")
    return False


def handle_passenger_and_payment(page, context, train_mode="auto"):
    """填写乘客信息并处理支付（完全按页面元素自适应，新旧火车同一套流程）

    返回: "SUCCESS"(已付款) / "MANUAL"(交人工接管) / False(中断或失败)
    """
    try:
        # 1) 进入乘客信息页
        if not click_first(page, [".btn-passenger"], timeout=10000, label="乘客信息按钮"):
            logger.error("[填表] 找不到 .btn-passenger 按钮")
            return False
        # 乘客页哪个元素先出现就用哪个（旧实现串行等 load_state 15 秒 + 元素 15 秒）
        if not wait_any(page, ["#btnConfirmPayment", "select.TicketTypeId", ".IsSelf"], timeout=15):
            report_stuck(page, "点了乘客信息按钮后乘客页没有出来")
            return False
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
        sleep_s(0.6, page)

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
            return False

        # 4) 一路推进到付款页（advance_to_payment_page 自己会等，不用先盲等 2 秒）
        if not advance_to_payment_page(page):
            logger.error("[填表] 未能到达付款方式选择页")
            cancel_booking(page)
            return False

        logger.info(f"[填表] 当前页面: {page.url}")
        BOT_STATE["phase"] = "已到付款页"
        if PAYMENT_METHOD == "Command":
            return wait_for_payment_command(page, context)
        outcome = process_automated_payment(page, context, PAYMENT_METHOD)
        return outcome if outcome in ("SUCCESS", "MANUAL") else False
    except Exception as e:
        logger.error(f"[异常] 填表流程出错: {e}", exc_info=True)
        report_stuck(page, f"填表流程出错: {str(e)[:150]}")
        cancel_booking(page)
        return False


def run(playwright: Playwright) -> None:
    """主运行函数"""
    global SHOULD_LOGOUT_AND_EXIT

    global BOT_START_TS
    BOT_START_TS = int(time.time())

    # 清掉上一次残留的"停止/截图"命令文件，否则新进程一起来就自杀（表现为一直 loop）
    try:
        cleanup_remote_files()
    except Exception:
        pass

    # Telegram 收发全部丢到后台线程：网络再烂也不会让抢票流程等一秒
    # （接收线程会先 prime 掉开机前积压的旧指令，等价于原来的 flush）
    _LAST_TICK["ts"] = time.time()
    start_tg_workers()

    browser = None
    context = None
    page = None
    launched_own_browser = False

    # 方式1: 尝试连接已有的 Chrome 调试进程
    mode = "无窗口(headless)" if HEADLESS else "可见窗口(headed)"
    browser = None
    logger.debug(f"[连接] 探测端口 {CHROME_DEBUG_PORT} 上有没有可接管的 Chrome ...")
    try:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{CHROME_DEBUG_PORT}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        logger.info(f"[连接] 已接管端口 {CHROME_DEBUG_PORT} 上的 Chrome")
    except Exception as e:
        browser = None
        logger.debug(f"[连接] 端口 {CHROME_DEBUG_PORT} 无现成 Chrome: {str(e).splitlines()[0][:80]}")

    if browser is None:
        # 预检：真的启动一次浏览器，失败就给可操作提示
        try:
            _probe = playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            _probe.close()
        except Exception as probe_err:
            detail = str(probe_err).strip().splitlines()[0][:200]
            hint = (f"浏览器无法启动: {detail}\n"
                    f"下载目录: {os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or '(默认缓存)'}\n"
                    "请在项目目录执行:\n"
                    "venv\\Scripts\\python.exe -m playwright install chromium")
            logger.error("[连接] " + hint.replace(chr(10), " | "))
            send_notification("❌ <b>浏览器无法启动</b>\n" + hint)
            return

        try:
            os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)
            args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--window-size=1920,1080',
                f'--remote-debugging-port={CHROME_DEBUG_PORT}',
                '--no-first-run',
                '--no-default-browser-check',
            ]
            if HEADLESS:
                args.append('--disable-gpu')
            else:
                args.append('--start-maximized')
            context = playwright.chromium.launch_persistent_context(
                CHROME_PROFILE_DIR,
                headless=HEADLESS,
                args=args,
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
            )
            page = context.pages[0] if context.pages else context.new_page()
            launched_own_browser = True
            logger.info(f"[连接] Chromium 已启动（{mode}）")
            logger.info(f"[连接]   调试端口: {CHROME_DEBUG_PORT}   （chrome://inspect 可接管）")
            logger.info(f"[连接]   用户数据: {CHROME_PROFILE_DIR}")
        except Exception as e2:
            logger.error(f"[连接] 启动 Chromium 失败: {e2}")
            send_notification(f"❌ 浏览器启动失败: {e2}")
            return

    try:
        # 默认超时不要设太长：元素找不到时宁可早点失败、重试、报错，
        # 也不要每个动作都干等 30 秒（用户感受就是"卡死"）
        page.set_default_timeout(20000)
        page.set_default_navigation_timeout(45000)
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

    # 看门狗：心跳长时间不动 = 卡在某个 Playwright 调用里，至少把现场写进日志
    def _watchdog():
        last_warn = 0.0
        while not SHOULD_LOGOUT_AND_EXIT:
            time.sleep(10)
            idle = time.time() - _LAST_TICK["ts"]
            if idle > 120 and time.time() - last_warn > 120:
                last_warn = time.time()
                logger.warning(f"[看门狗] 已 {int(idle)} 秒没有心跳（阶段: {BOT_STATE.get('phase')}，"
                               f"计划: {BOT_STATE.get('plan') or '-'}）；若一直没有新日志，"
                               "请在面板点停止，或把 bot.log 最后 50 行发出来定位")
    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        total_loop = 0
        while True:
            if SHOULD_LOGOUT_AND_EXIT:
                break

            # 远程控制: 截图 + 处理命令（含面板停止指令）
            handle_remote_control(page)
            if SHOULD_LOGOUT_AND_EXIT:
                break

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
                        # 期间照样跑心跳：面板仍能看到画面，也能随时停止
                        sleep_s(300, page)
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
                            outcome = handle_passenger_and_payment(page, context, train_mode)
                            if outcome == "SUCCESS":
                                logger.info("[完成] 付款完成，任务成功退出。")
                                BOT_STATE["last_result"] = "已付款，任务完成"
                                return
                            if outcome == "MANUAL":
                                BOT_STATE["last_result"] = "人工接管中"
                                if not manual_hold(page):
                                    return
                                logger.info("[人工] 已退出手动模式，继续自动监控。")
                            else:
                                logger.warning("[中断] 付款中断，继续下一轮监控...")
                                BOT_STATE["last_result"] = "付款流程中断"
                        else:
                            BOT_STATE["last_result"] = "座位被抢空 / 无符合偏好的座位"
                            logger.warning("[失败] 座位被抢空")
                    else:
                        BOT_STATE["last_result"] = f"未找到 {config.get('time')} 的车次"
                except Exception as e:
                    logger.error(f"[致命异常] 流程崩溃: {e}", exc_info=True)
                    detail = ""
                    try:
                        detail = page_report(page)
                        logger.error(f"[致命异常] 现场 :: {detail}")
                    except Exception:
                        pass
                    shot_ok = False
                    try:
                        error_shot = page.screenshot(type='png', full_page=True, timeout=15000)
                        send_telegram_photo(
                            f"⚠️ <b>报告！流程崩溃</b>\n<code>{str(e)[:150]}</code>\n{detail[:250]}",
                            error_shot
                        )
                        shot_ok = True
                    except Exception as screenshot_err:
                        logger.warning(f"[异常] 错误截图也失败了: {screenshot_err}")
                    if not shot_ok:
                        send_notification(f"⚠️ 流程崩溃: {str(e)[:150]}\n{detail[:250]}")

            if SHOULD_LOGOUT_AND_EXIT:
                break

            BOT_STATE["phase"] = "休息中"
            logger.info(f"[休息] 倒计时 {REFRESH_INTERVAL} 秒...")
            for i in range(REFRESH_INTERVAL, 0, -1):
                if SHOULD_LOGOUT_AND_EXIT:
                    break

                BOT_STATE["next_refresh_in"] = i
                # 远程控制 + 心跳：每秒都跑（面板停止指令 / 截图发布 / TG 指令入队）
                handle_remote_control(page)
                if SHOULD_LOGOUT_AND_EXIT:
                    break

                command, target_id = next_telegram_command()
                if command:
                    logger.info(f"[指令] 收到 {command} ({target_id})")
                    process_telegram_command(page, command, target_id)

                if i % 10 == 0 or i <= 5:
                    sys.stdout.write(f"    剩余 {i} 秒...\r")
                    sys.stdout.flush()

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
            _tg_flush(6)
        except Exception:
            pass
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
