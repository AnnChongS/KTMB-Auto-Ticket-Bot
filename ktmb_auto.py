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
from datetime import datetime
from playwright.sync_api import Playwright, sync_playwright, expect

# ================= 📝 日志系统初始化 =================
def setup_logging():
    """配置日志系统，同时输出到文件和控制台"""
    log_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    # 文件处理器
    file_handler = logging.FileHandler('bot.log', encoding='utf-8', mode='a')
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG)

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)

    # 根日志器
    logger = logging.getLogger('KTMBBot')
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

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

PREFER_FORWARD = CFG.get("preferences", {}).get("prefer_forward", True)
PREFER_WINDOW = CFG.get("preferences", {}).get("prefer_window", True)
PREFER_NORMAL_SEAT = CFG.get("preferences", {}).get("prefer_normal_seat", True)
ACCEPT_TABLE_SEAT = CFG.get("preferences", {}).get("accept_table_seat", True)

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

        if data.get("ok") and len(data.get("result", [])) > 0:
            last_update = data["result"][-1]
            new_offset = last_update["update_id"] + 1

            if "message" in last_update and "text" in last_update["message"]:
                text = last_update["message"]["text"].strip().lower()
                parts = text.split()
                command = parts[0].split('@')[0]
                target_id = parts[1] if len(parts) > 1 else "all"
                if len(parts) == 1 and text[-1].isdigit():
                    target_id = text[-1]
                    command = text[:-1]

                if command == "/logout" and (target_id == "all" or target_id == BOT_ID):
                    SHOULD_LOGOUT_AND_EXIT = True
                    logger.info(f"[TG] 收到 logout 指令，标记退出")

                return command, target_id, new_offset
            return None, None, new_offset
    except requests.exceptions.RequestException as e:
        logger.debug(f"[TG] 检查指令网络错误: {e}")
    except (KeyError, IndexError, ValueError) as e:
        logger.debug(f"[TG] 解析指令响应失败: {e}")
    return None, None, offset


def handle_popup(page):
    """处理页面弹窗"""
    time.sleep(0.5)
    try:
        if page.locator("#popupModal").is_visible():
            if page.locator("#popupModalCloseButton").is_visible():
                page.locator("#popupModalCloseButton").click()
            elif page.locator("#popupModalOkButton").is_visible():
                page.locator("#popupModalOkButton").click()
            elif page.locator("#popupModalCancelButton").is_visible():
                page.locator("#popupModalCancelButton").click()
            page.evaluate("document.getElementById('popupModal').style.display = 'none';")
            page.evaluate("if(document.querySelector('.modal-backdrop')) document.querySelector('.modal-backdrop').remove();")
            logger.debug("[页面] 已关闭弹窗")
        elif page.locator(".modal-content:visible").count() > 0:
            page.locator("button.close, button:has-text('Close')").click()
            logger.debug("[页面] 已关闭模态框")
    except Exception as e:
        logger.debug(f"[页面] 处理弹窗时出现异常（可忽略）: {e}")


def safe_logout(page):
    """安全登出 KTMB 账号"""
    logger.info("[系统] 收到退出指令，正在执行安全登出(Logout)...")
    try:
        if not page.locator("#navbarFileDropdown").first.is_visible():
            page.goto("https://online.ktmb.com.my/", timeout=15000)

        if page.locator("#navbarFileDropdown").first.is_visible():
            page.locator("#navbarFileDropdown").first.click(force=True)
            time.sleep(1)
            logout_btn = page.locator("a.dropdown-item[href='/Account/Logout']")
            if logout_btn.count() > 0:
                logout_btn.first.click(force=True)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                logger.info("[系统] 登出成功！")
                send_notification("✅ 已精准安全退出 KTMB 账号，告别 30 分钟冷却。程序即将关闭。")
            else:
                page.goto("https://online.ktmb.com.my/Account/Logout", timeout=10000)
                send_notification("✅ 已通过后门网址强行登出。程序安全关闭。")
        else:
            logger.info("[系统] 当前未登录，无需登出。")
            send_notification("✅ 当前未登录，程序安全关闭。")
    except Exception as e:
        logger.error(f"[系统] 安全登出失败: {e}")
        send_notification(f"⚠️ 尝试安全退出失败: {e}")


def ensure_on_homepage(page):
    """确保在主页"""
    current_url = page.url
    target_url = "https://online.ktmb.com.my/"
    if current_url == target_url or current_url == target_url + "Home/Index":
        handle_popup(page)
        return
    logger.info("[导航] 跳转回主页...")
    try:
        page.goto(target_url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        handle_popup(page)
    except Exception as e:
        logger.warning(f"[导航] 跳转主页失败: {e}")


def login(page):
    """登录 KTMB 账号"""
    page.set_viewport_size({"width": 1920, "height": 1080})
    try:
        if "Login" not in page.url:
            page.goto("https://online.ktmb.com.my/Account/Login", timeout=30000)
    except Exception as e:
        logger.warning(f"[登录] 导航到登录页失败: {e}")

    start_time = time.time()
    while time.time() - start_time < 15:
        handle_popup(page)
        if page.locator("a[href*='/Account/Login']").is_visible():
            logger.info("[系统] 执行登录...")
            try:
                page.locator("a[href*='/Account/Login']").click()
                page.get_by_role("textbox", name="Email").fill(KTMB_EMAIL)
                page.get_by_role("textbox", name="Password").fill(KTMB_PASSWORD)
                page.get_by_role("button", name="Login").click()
                page.wait_for_url("https://online.ktmb.com.my/", timeout=15000)
                logger.info("[系统] 登录成功")
                return
            except Exception as e:
                logger.warning(f"[登录] 登录异常，重试中: {e}")
                handle_popup(page)
                time.sleep(2)

        if page.locator("#navbarFileDropdown").first.is_visible():
            return
        time.sleep(1)

    logger.error("[系统] 登录超时（15秒）")
    try:
        page.context.clear_cookies()
        logger.info("[系统] 已清除 cookies")
    except Exception as e:
        logger.warning(f"[系统] 清除 cookies 失败: {e}")


def perform_search(page, config):
    """执行车票搜索"""
    date_str = f"{config['year']}-{config['month']}-{config['day']}"
    logger.info(f"[搜索] 计划: {date_str} {config['time']}")

    time.sleep(1)
    page.locator("#select2-FromStationId-container").click(timeout=15000)
    page.get_by_role("option", name=config['from']).click()
    page.locator("#select2-ToStationId-container").click()
    page.get_by_role("option", name=config['to']).click()

    try:
        depart_input = page.get_by_role("textbox", name="Depart")
        depart_input.evaluate("node => { node.scrollIntoView(); node.click(); }")
        page.wait_for_selector(".lightpick__select-years", state="visible", timeout=3000)
    except Exception as e:
        logger.warning(f"[搜索] 日历首次弹出失败，清理弹窗后重试: {e}")
        handle_popup(page)
        time.sleep(0.5)
        try:
            depart_input = page.get_by_role("textbox", name="Depart")
            depart_input.click(force=True)
            page.wait_for_selector(".lightpick__select-years", state="visible", timeout=5000)
        except Exception as e:
            logger.error(f"[搜索] 网页卡顿，日历无法打开: {e}")
            try:
                error_shot = page.screenshot(type='png', full_page=True)
                send_telegram_photo("⚠️ <b>日历弹出失败！</b>", error_shot)
            except Exception as screenshot_err:
                logger.warning(f"[搜索] 截图失败: {screenshot_err}")
            return False

    max_retries = 12
    for _ in range(max_retries):
        cy = int(page.locator(".lightpick__select-years").input_value())
        cm = int(page.locator(".lightpick__select-months").input_value()) + 1
        if cy > config['year'] or (cy == config['year'] and cm >= config['month']):
            break
        page.locator(".lightpick__next-action").click()
        time.sleep(0.3)

    day_str = str(config['day'])
    page.locator(f".lightpick__day:not(.is-next-month):not(.is-previous-month):text-is('{day_str}')").click()
    page.get_by_role("button", name="SEARCH").click()

    try:
        page.wait_for_selector(".btn-seat-layout", timeout=8000)
    except Exception:
        logger.info("[搜索] 无车次")
        return False

    handle_popup(page)
    target_btn = page.locator(f"tr:has-text('{config['time']}') .btn-seat-layout")

    if target_btn.count() > 0:
        logger.info("[搜索] 发现目标车次！")
        if page.locator("#popupModal").is_visible():
            handle_popup(page)
        try:
            target_btn.first.click(timeout=5000)
        except Exception:
            handle_popup(page)
            target_btn.first.click(force=True)
        return True
    else:
        logger.info("[搜索] 车次未找到")
        return False


def select_seat(page, is_old_train):
    """智能选座"""
    logger.info("[操作] 正在选座...")
    try:
        page.wait_for_selector("#seatSelect", state="visible", timeout=15000)
    except Exception:
        logger.warning("[选座] 座位选择界面未出现")
        return False

    coach_buttons = page.locator(".coache-btn")
    for i in range(coach_buttons.count()):
        btn = coach_buttons.nth(i)
        if "active" not in btn.get_attribute("class"):
            btn.click()
            time.sleep(0.5)

        seat = None
        if is_old_train:
            base_sel = "img.selectable-icon:visible:not([src*='Selected']):not([src*='Occupied'])[data-seat-service-type='Standard']"
            dir_sel = ":is([src*='Foward'], [src*='Forward'])" if PREFER_FORWARD else "[src*='Backward']"
            win_sel = "[src*='Win']" if PREFER_WINDOW else ":not([src*='Win'])"

            seat = page.locator(f"{base_sel}{dir_sel}{win_sel}").first
            if not seat or seat.count() == 0:
                seat = page.locator(f"{base_sel}{dir_sel}").first
            if not seat or seat.count() == 0 and PREFER_WINDOW:
                seat = page.locator(f"{base_sel}{win_sel}").first
            if not seat or seat.count() == 0:
                seat = page.locator(f"{base_sel}").first
        else:
            if PREFER_NORMAL_SEAT:
                seat = page.locator("img.selectable-icon[src*='StanFor']:visible").first
            if not seat and ACCEPT_TABLE_SEAT:
                seat = page.locator("img.selectable-icon[src*='StanClus']:visible").first

        if seat and seat.is_visible():
            try:
                seat.click()
                time.sleep(0.5)
                confirm = page.locator("#confirmSeatBtn")
                if "disabled-btn" not in confirm.get_attribute("class"):
                    confirm.click()
                    logger.info("[选座] 选座成功！")
                    return True
                else:
                    confirm.click()
                    return True
            except Exception as e:
                logger.debug(f"[选座] 点击确认失败: {e}")

    try:
        page.locator("#seatSelect .close").click()
    except Exception:
        pass
    logger.warning("[选座] 所有车厢均无可用座位")
    return False


def extract_ticket_info(page):
    """提取票务信息"""
    try:
        page.wait_for_selector(".station_detail", timeout=5000)
        train_info_el = page.locator(".station_detail .c444.mt5.fw100.f17").first
        train_info_text = train_info_el.inner_text().strip() if train_info_el.is_visible() else "未知"
        seat_info_el = page.locator(".col-lg-4 p.f12.mt10").first
        seat_info_text = seat_info_el.inner_text().strip() if seat_info_el.is_visible() else "未知"
        price_el = page.locator("tr.f14 td.text-right b").first
        price_text = price_el.inner_text().strip() if price_el.is_visible() else "未知"
        return f"🎫 票务确认\n🚆 {train_info_text}\n💺 {seat_info_text}\n💰 {price_text}"
    except Exception as e:
        logger.warning(f"[票务] 详情抓取失败: {e}")
        return "详情抓取失败"


def execute_payment_command(page, context, command, target_id):
    """执行支付指令"""
    if target_id != "all" and target_id != BOT_ID:
        return False
    logger.info(f"[指令] 收到支付指令: {command}")

    if command == "/manual":
        send_notification("🛑 已切换人工模式。")
        return True

    try:
        if command == "/duitnow":
            send_notification("💳 正在生成 DuitNow...")
            page.locator("#btnGoPaymentDuitNow").click()
            proceed_btn = page.locator("input[value*='Click HERE'], input[type='submit']").first
            proceed_btn.wait_for(state="visible", timeout=15000)
            with context.expect_page() as new_page_info:
                proceed_btn.click()
            gateway_page = new_page_info.value
            gateway_page.wait_for_load_state("domcontentloaded")
            time.sleep(8)
            gateway_page.set_viewport_size({"width": 1920, "height": 2500})
            time.sleep(1)
            try:
                gateway_page.evaluate("window.scrollBy(0, 400)")
            except Exception:
                pass
            screenshot = gateway_page.screenshot(type='png', full_page=True)
            send_telegram_photo("⚡️ DuitNow 请扫码", screenshot)
            send_notification(f"🔗 链接:\n{gateway_page.url}")
            return False

        elif command == "/tng":
            send_notification("💳 正在生成 TnG...")
            page.locator("#btnGoPaymentTnG").click()
            proceed_btn = page.locator("input[value*='Click HERE'], input[type='submit']").first
            proceed_btn.wait_for(state="visible", timeout=15000)
            with context.expect_page() as new_page_info:
                proceed_btn.click()
            gateway_page = new_page_info.value
            try:
                gateway_page.wait_for_selector("#submitButton", state="visible", timeout=20000)
                gateway_page.locator("#submitButton").click()
            except Exception:
                pass
            time.sleep(5)
            gateway_page.set_viewport_size({"width": 1920, "height": 2500})
            time.sleep(1)
            try:
                gateway_page.evaluate("window.scrollBy(0, 400)")
            except Exception:
                pass
            screenshot = gateway_page.screenshot(type='png', full_page=True)
            send_telegram_photo("⚡️ TnG 请扫码", screenshot)
            return False

        elif command == "/wallet":
            send_notification("💳 尝试 KTM Wallet...")
            page.locator("#btnKtmbEWallet").click()
            time.sleep(3)
            if page.locator("#popupModal:has-text('insufficient')").is_visible():
                send_notification("⚠️ 余额不足！请换方式。")
                if page.locator("#popupModalCloseButton").is_visible():
                    page.locator("#popupModalCloseButton").click()
                elif page.locator("#popupModalOkButton").is_visible():
                    page.locator("#popupModalOkButton").click()
                elif page.locator("#popupModalCancelButton").is_visible():
                    page.locator("#popupModalCancelButton").click()
                try:
                    page.locator("#popupModal").wait_for(state="hidden", timeout=3000)
                except Exception:
                    pass
                return False
            else:
                send_notification("✅ Wallet 扣款已发送。")
                return True

    except Exception as e:
        logger.error(f"[支付] 指令执行异常: {e}")
        send_notification(f"⚠️ 指令异常: {e}")
    return False


def wait_for_payment_command(page, context):
    """等待 Telegram 支付指令"""
    logger.info("[系统] 进入待命模式，等待支付指令...")
    ticket_info = extract_ticket_info(page)
    msg = (
        f"🚨 <b>{BOT_NAME} 抢票成功！</b> 🚨\n\n{ticket_info}\n\n"
        f"👇 <b>请发送指令：</b>\n/duitnow{BOT_ID} | /tng{BOT_ID} | /wallet{BOT_ID} | /manual{BOT_ID}"
    )
    send_notification(msg)

    _, _, last_offset = check_telegram_command(offset=None)
    for _ in range(400):
        time.sleep(3)
        command, target_id, new_offset = check_telegram_command(offset=last_offset)
        if command:
            last_offset = new_offset
            if execute_payment_command(page, context, command, target_id):
                logger.info("[系统] 流程结束。")
                return True
            else:
                if target_id == "all" or target_id == BOT_ID:
                    send_notification(f"🤖 仍在待命...")
        try:
            if not page.url.startswith("https"):
                break
        except Exception:
            break
    return True


def process_automated_payment(page, context, method):
    """处理自动支付流程"""
    if method == "Manual":
        send_notification("🛑 请手动付款！")
        time.sleep(1800)
        return True
    if method == "KTM Wallet":
        return execute_payment_command(page, context, "/wallet", "all")
    if method == "DuitNow":
        execute_payment_command(page, context, "/duitnow", "all")
        time.sleep(1800)
        return True
    logger.warning(f"[支付] 未知支付方式: {method}")
    return False


def handle_passenger_and_payment(page, context, is_old_train):
    """填写乘客信息并处理支付"""
    try:
        page.locator(".btn-passenger").click()
        page.wait_for_selector(".IsSelf", state="visible", timeout=30000)
        page.locator(".IsSelf").check()
        time.sleep(1.5)
        page.locator("select.TicketTypeId").select_option(value="Adult")
        page.locator("#btnConfirmPayment").click()
        if not is_old_train:
            try:
                page.locator("#btnUpdateInsuranceNo").click()
                page.locator("#popupModalOkButton").click()
            except Exception:
                pass
            try:
                page.locator("#btnProceedToPayment").click()
                page.locator("#confirmationConfirmButton").click()
            except Exception:
                pass
        page.wait_for_url(lambda u: "/Book" in u, timeout=30000)
        if PAYMENT_METHOD == "Command":
            return wait_for_payment_command(page, context)
        else:
            return process_automated_payment(page, context, PAYMENT_METHOD)
    except Exception as e:
        logger.error(f"[异常] 填表流程出错: {e}")
        return False


def run(playwright: Playwright) -> None:
    """主运行函数"""
    global SHOULD_LOGOUT_AND_EXIT

    # 开机立刻清空历史 Telegram 指令，防止一开机就执行以前的 /logout 导致自杀
    tg_offset = flush_telegram_updates()

    logger.info(f"[连接] 正在连接 Chrome (Port {CHROME_DEBUG_PORT})...")
    try:
        browser = playwright.chromium.connect_over_cdp(f"http://localhost:{CHROME_DEBUG_PORT}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
    except Exception as e:
        logger.error(f"[连接] 浏览器连接失败: {e}")
        logger.error(f"[连接] 请确保 Chrome 已以调试模式启动，端口: {CHROME_DEBUG_PORT}")
        return

    send_notification("🤖 机器人启动 | 模式: " + PAYMENT_METHOD)
    logger.info(f"[系统] 机器人已启动 | 模式: {PAYMENT_METHOD} | 任务数: {len(SEARCH_CONFIGS)}")

    try:
        total_loop = 0
        while True:
            if SHOULD_LOGOUT_AND_EXIT:
                safe_logout(page)
                break

            total_loop += 1
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
                    ensure_on_homepage(page)
                    login(page)
                    is_old_train = config.get("is_old_train", False)

                    if perform_search(page, config):
                        if select_seat(page, is_old_train):
                            if handle_passenger_and_payment(page, context, is_old_train):
                                logger.info("[完成] 任务成功退出。")
                                return
                            else:
                                logger.warning("[中断] 付款中断...")
                        else:
                            logger.warning("[失败] 座位被抢空")
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

            logger.info(f"[休息] 倒计时 {REFRESH_INTERVAL} 秒...")
            for i in range(REFRESH_INTERVAL, 0, -1):
                if SHOULD_LOGOUT_AND_EXIT:
                    break

                if i % 10 == 0 or i <= 5:
                    sys.stdout.write(f"    剩余 {i} 秒...\r")
                    sys.stdout.flush()

                if i % 3 == 0:
                    command, target_id, new_offset = check_telegram_command(offset=tg_offset)
                    if command:
                        tg_offset = new_offset
                        if command == "/snap" and (target_id == "all" or target_id == BOT_ID):
                            logger.info("[指令] 收到 /snap，正在截图汇报...")
                            try:
                                screenshot = page.screenshot(type='png', full_page=True)
                                send_telegram_photo("📸 <b>长官，这是现在的监控画面</b>", screenshot)
                            except Exception as e:
                                logger.warning(f"[截图] /snap 截图失败: {e}")

                time.sleep(1)
            logger.info("")

    except KeyboardInterrupt:
        logger.info("[系统] 收到 Ctrl+C，准备退出...")
    finally:
        try:
            browser.close()
            logger.info("[系统] 浏览器连接已关闭")
        except Exception:
            pass


if __name__ == '__main__':
    with sync_playwright() as playwright:
        run(playwright)
