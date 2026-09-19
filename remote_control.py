"""
远程控制模块 - 允许通过 Web 界面控制 KTMB 浏览器

进程间通信方式（纯文件 IPC，Windows / Linux 通用）:

    bot  --截图/状态-->  <tmp>/ktmb_remote_screenshot.png + .json
    bot  <--命令-------  <tmp>/ktmb_remote_command.json
    bot  --执行结果-->   <tmp>/ktmb_remote_result.json   (带 request id)

设计要点（v1.3.2 修复）:
1. 截图由机器人"持续发布"（默认每 5 秒一帧），Web 面板随时能拿到最新画面，
   不再依赖"请求-等待-超时 3 秒"的握手，之前 /remote 一直转圈就是死在这里。
2. 截图先写临时文件再 os.replace 原子替换，避免面板读到写了一半的 PNG。
3. 每条命令带 request id，机器人执行完把结果写回，面板能显示真实成败原因。
4. 截图默认只截"可视区域"，这样面板上点 (x, y) 与浏览器 viewport 坐标 1:1 对应。

坐标说明: 面板显示的是 1920x1080 viewport 的截图，点击时前端会按图片实际显示
尺寸反推出原始像素坐标，因此这里直接用 page.mouse.click(x, y) 即可。
"""
import json
import os
import tempfile
import time
import uuid

_TMP = tempfile.gettempdir()
SCREENSHOT_PATH = os.path.join(_TMP, "ktmb_remote_screenshot.png")
SCREENSHOT_META_PATH = os.path.join(_TMP, "ktmb_remote_screenshot.json")
COMMAND_PATH = os.path.join(_TMP, "ktmb_remote_command.json")
RESULT_PATH = os.path.join(_TMP, "ktmb_remote_result.json")
SCREENSHOT_REQUEST_PATH = os.path.join(_TMP, "ktmb_screenshot_requested")

# 截图超过这个秒数就认为机器人已经不在发布画面了
SCREENSHOT_STALE_AFTER = 90


def cleanup():
    """Bot 退出时清理临时文件"""
    for path in (SCREENSHOT_REQUEST_PATH, COMMAND_PATH, RESULT_PATH,
                 SCREENSHOT_META_PATH, SCREENSHOT_PATH):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


# ============================ 截图 ============================

def request_screenshot():
    """Flask 调用: 请求机器人立刻截一张（会插队到下一次 tick）"""
    try:
        with open(SCREENSHOT_REQUEST_PATH, 'w') as f:
            f.write(str(time.time()))
        return True
    except Exception:
        return False


def screenshot_requested():
    """Bot 调用: 面板是否要求立刻截图"""
    return os.path.exists(SCREENSHOT_REQUEST_PATH)


def _replace_with_retry(src, dst, attempts=5):
    """Windows 上如果面板正好打开着目标文件，os.replace 会抛 PermissionError，重试几次"""
    last = None
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return True
        except Exception as e:
            last = e
            time.sleep(0.15 * (i + 1))
    try:
        with open(src, 'rb') as fi, open(dst, 'wb') as fo:
            fo.write(fi.read())
        os.remove(src)
        return True
    except Exception:
        logger_print(f"[远程控制] 写入截图失败: {last}")
        return False


def _clear_screenshot_request():
    try:
        os.remove(SCREENSHOT_REQUEST_PATH)
    except Exception:
        pass


def publish_screenshot(page, force=False, interval=5.0, full_page=False, meta_extra=None):
    """Bot 调用: 把当前画面发布给 Web 面板

    返回 True 表示真的截了一张。interval 秒内不会重复截（force=True 除外），
    单次截图异常也不会影响主流程。
    """
    try:
        requested = screenshot_requested()
        if not force and not requested:
            if interval and screenshot_age() is not None and screenshot_age() < interval:
                return False
        _clear_screenshot_request()

        tmp_png = SCREENSHOT_PATH + ".tmp"
        page.screenshot(path=tmp_png, full_page=full_page, type='png')
        _replace_with_retry(tmp_png, SCREENSHOT_PATH)

        meta = {"ts": time.time()}
        try:
            meta["url"] = page.url
        except Exception:
            meta["url"] = ""
        if isinstance(meta_extra, dict):
            meta.update(meta_extra)
        _write_json(SCREENSHOT_META_PATH, meta)
        return True
    except Exception as e:
        try:
            logger_print(f"[远程控制] 截图失败: {e}")
        except Exception:
            pass
        return False


def save_screenshot(page, **kwargs):
    """兼容旧调用方式（v1.3 的 handle_remote_control 用这个）"""
    kwargs.pop('page', None)
    return publish_screenshot(page, force=True, **kwargs)


def screenshot_age():
    """距上一帧截图多少秒；从没有过截图时返回 None"""
    try:
        return max(0.0, time.time() - os.path.getmtime(SCREENSHOT_PATH))
    except OSError:
        return None


def screenshot_state():
    """面板调用: 当前截图状态"""
    age = screenshot_age()
    meta = _read_json(SCREENSHOT_META_PATH) or {}
    return {
        "has_screenshot": age is not None,
        "age": round(age, 2) if age is not None else None,
        "stale": (age is None) or (age > SCREENSHOT_STALE_AFTER),
        "url": meta.get("url", ""),
        "phase": meta.get("phase", ""),
        "round": meta.get("round", 0),
        "ts": meta.get("ts", 0),
        "pid": meta.get("pid"),
    }


def publish_state(phase="", round_no=0):
    """Bot 调用: 更新运行状态（附带在截图 meta 里，面板可显示当前阶段）"""
    meta = _read_json(SCREENSHOT_META_PATH) or {}
    meta["phase"] = str(phase)
    meta["round"] = round_no
    _write_json(SCREENSHOT_META_PATH, meta)


# ============================ 命令 ============================

def send_command(action, **kwargs):
    """Flask 调用: 发送命令给 bot，返回 request id（失败返回 None）"""
    cmd = {'action': action, 'id': uuid.uuid4().hex[:12], 'ts': time.time()}
    cmd.update(kwargs)
    try:
        _write_json(COMMAND_PATH, cmd)
        return cmd['id']
    except Exception:
        return None


def peek_command():
    """不删除地读取命令文件（长时间等待中也能发现"停止"指令）"""
    return _read_json(COMMAND_PATH)


def get_command():
    """Bot 调用: 取走待执行的命令"""
    cmd = _read_json(COMMAND_PATH)
    if cmd is None:
        return None
    try:
        os.remove(COMMAND_PATH)
    except Exception:
        pass
    return cmd


def send_result(request_id, status, message):
    """Bot 调用: 回写命令执行结果，供 Web 面板显示"""
    try:
        _write_json(RESULT_PATH, {
            'id': request_id, 'status': status,
            'message': str(message)[:500], 'ts': time.time(),
        })
    except Exception:
        pass


def read_result(request_id, max_age=30):
    """Flask 调用: 读取指定命令的结果（没有 / 过期返回 None）"""
    data = _read_json(RESULT_PATH)
    if not data or data.get('id') != request_id:
        return None
    if time.time() - float(data.get('ts', 0)) > max_age:
        return None
    return data


# ============================ 辅助 ============================

def logger_print(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


# ============================ 执行 ============================

def execute_remote_command(page, cmd):
    """Bot 调用: 执行远程命令，返回 {'status','message'}"""
    action = cmd.get('action')
    try:
        if action == 'click':
            x, y = int(cmd['x']), int(cmd['y'])
            page.mouse.click(x, y)
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
            if not str(url).lower().startswith(('http://', 'https://')):
                return {'status': 'error', 'message': '只允许 http/https 地址'}
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
            page.locator(selector).first.click(timeout=5000)
            return {'status': 'success', 'message': f'点击: {selector}'}

        elif action == 'selector_wait':
            selector = cmd['selector']
            timeout = int(cmd.get('timeout', 10000))
            page.locator(selector).first.wait_for(state='visible', timeout=timeout)
            return {'status': 'success', 'message': f'元素已出现: {selector}'}

        elif action == 'back':
            page.go_back(wait_until='domcontentloaded', timeout=15000)
            return {'status': 'success', 'message': '返回上一页'}

        elif action == 'info':
            return {'status': 'success', 'message': page.url}

        else:
            return {'status': 'error', 'message': f'未知操作: {action}'}

    except Exception as e:
        return {'status': 'error', 'message': f'{type(e).__name__}: {str(e)[:200]}'}
