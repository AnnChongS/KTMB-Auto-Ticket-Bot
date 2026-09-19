"""
远程控制模块 - 允许通过Web界面控制KTMB浏览器
使用文件IPC: bot写截图到文件, Flask读取; Flask写命令到文件, bot读取执行
"""
import json
import os
import tempfile
import time

_TMP = tempfile.gettempdir()
SCREENSHOT_PATH = os.path.join(_TMP, "ktmb_remote_screenshot.png")
COMMAND_PATH = os.path.join(_TMP, "ktmb_remote_command.json")
SCREENSHOT_REQUEST_PATH = os.path.join(_TMP, "ktmb_screenshot_requested")


def cleanup():
    """Bot 退出时清理临时文件"""
    for path in (SCREENSHOT_REQUEST_PATH, COMMAND_PATH):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

def save_screenshot(page):
    """Bot调用: 按需保存截图（只在被请求时）"""
    if not os.path.exists(SCREENSHOT_REQUEST_PATH):
        return False
    try:
        os.remove(SCREENSHOT_REQUEST_PATH)
        time.sleep(0.5)
        page.screenshot(path=SCREENSHOT_PATH, full_page=True)
        return True
    except Exception as e:
        print(f"[远程控制] 截图失败: {e}")
        return False

def get_command():
    """Bot调用: 获取待执行的命令"""
    if not os.path.exists(COMMAND_PATH):
        return None
    try:
        with open(COMMAND_PATH, 'r', encoding='utf-8') as f:
            cmd = json.load(f)
        os.remove(COMMAND_PATH)
        return cmd
    except Exception:
        return None

def peek_command():
    """不删除地读取命令文件

    用于机器人在长时间等待（登录重试、搜索、付款待命）时也能马上发现"停止"指令。
    普通命令不会被消费掉，仍然由 get_command() 正常取走执行。
    """
    if not os.path.exists(COMMAND_PATH):
        return None
    try:
        with open(COMMAND_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def execute_remote_command(page, cmd):
    """Bot调用: 执行远程命令"""
    action = cmd.get('action')
    try:
        if action == 'click':
            x, y = cmd['x'], cmd['y']
            page.mouse.click(x, y)
            time.sleep(0.5)
            return {'status': 'success', 'message': f'点击 ({x}, {y})'}
        
        elif action == 'type':
            text = cmd['text']
            page.keyboard.type(text)
            return {'status': 'success', 'message': f'输入: {text}'}
        
        elif action == 'key':
            key = cmd.get('key', 'Enter')
            page.keyboard.press(key)
            return {'status': 'success', 'message': f'按键: {key}'}
        
        elif action == 'navigate':
            url = cmd['url']
            page.goto(url, wait_until='domcontentloaded', timeout=15000)
            return {'status': 'success', 'message': f'跳转: {url}'}
        
        elif action == 'scroll':
            direction = cmd.get('direction', 'down')
            amount = 500 if direction == 'down' else -500
            page.evaluate(f"window.scrollBy(0, {amount})")
            return {'status': 'success', 'message': f'滚动 {direction}'}
        
        elif action == 'refresh':
            page.reload(wait_until='domcontentloaded', timeout=15000)
            return {'status': 'success', 'message': '页面已刷新'}
        
        elif action == 'selector_click':
            selector = cmd['selector']
            page.locator(selector).click(timeout=5000)
            return {'status': 'success', 'message': f'点击: {selector}'}
        
        elif action == 'selector_wait':
            selector = cmd['selector']
            timeout = cmd.get('timeout', 10000)
            page.locator(selector).wait_for(state='visible', timeout=timeout)
            return {'status': 'success', 'message': f'元素已出现: {selector}'}
        
        else:
            return {'status': 'error', 'message': f'未知操作: {action}'}
    
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def send_command(action, **kwargs):
    """Flask调用: 发送命令给bot"""
    cmd = {'action': action, **kwargs}
    try:
        with open(COMMAND_PATH, 'w', encoding='utf-8') as f:
            json.dump(cmd, f)
        return True
    except Exception:
        return False

def request_screenshot():
    """Flask调用: 请求bot截图"""
    try:
        with open(SCREENSHOT_REQUEST_PATH, 'w') as f:
            f.write('1')
        return True
    except Exception:
        return False
