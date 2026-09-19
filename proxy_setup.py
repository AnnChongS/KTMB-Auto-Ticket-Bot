# -*- coding: utf-8 -*-
#
# KTMB Auto Ticket Bot - Telegram 代理助手
# Copyright (C) 2025 AnnChongS
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
"""Telegram 代理助手（v1.3.4）

有些网络（ISP / 公司 / 校园网）会把 api.telegram.org 的 IP 直接黑洞掉：
DNS 正常、ping 不通、TCP 443 超时 —— 这时候抢票本身没问题，只是收不到
Telegram 通知和指令。这个模块负责：

  1. 探测：不依赖 token，直接测 DNS + IPv4/IPv6 的 TCP+TLS 能不能通
  2. 一键：装 Cloudflare WARP（官方 MSI）并开「代理模式」(SOCKS5)，只给 TG 走
  3. 测试：拿配置里的代理真的请求一次 api.telegram.org（有 token 就做 getMe）
  4. 落盘：把代理地址写进 config.json 的 notification.telegram_proxy

命令行用法（start_bot.bat 会调用 offer）：
    python proxy_setup.py offer          # 连不上时才弹菜单询问
    python proxy_setup.py probe          # 只探测
    python proxy_setup.py status         # 打印当前代理状态(JSON)
    python proxy_setup.py test [url]     # 测试代理（默认用 config.json 里的）
    python proxy_setup.py enable-warp    # 开 WARP 代理模式并写配置
    python proxy_setup.py disable        # 关掉代理设置
"""
from __future__ import print_function

import json
import os
import re
import platform
import socket
import ssl
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")

TG_HOST = "api.telegram.org"
TG_PORT = 443
WARP_MSI_URL = "https://downloads.cloudflareclient.com/v1/download/windows/ga"
DEFAULT_WARP_PORT = 40000
DEFAULT_API_BASE = "https://api.telegram.org"          # warp-cli proxy port 默认值
WARP_CLI_CANDIDATES = (
    r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe",
    r"C:\Program Files (x86)\Cloudflare\Cloudflare WARP\warp-cli.exe",
)


# ============================ 基础工具 ============================

def is_windows():
    return os.name == "nt" or platform.system() == "Windows"


def _run(cmd, timeout=60, capture=True):
    """跑一条命令，返回 (returncode, stdout+stderr)"""
    try:
        p = subprocess.run(cmd, capture_output=capture, text=True,
                           timeout=timeout, shell=isinstance(cmd, str),
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 127, "找不到命令: %s" % (cmd if isinstance(cmd, str) else cmd[0])
    except subprocess.TimeoutExpired:
        return 124, "命令超时: %s" % (cmd if isinstance(cmd, str) else cmd[0])
    except Exception as e:
        return 1, "%s: %s" % (type(e).__name__, e)


# ============================ 1. 探测 Telegram ============================

def _tcp_tls_probe(family, host=TG_HOST, timeout=6):
    """指定协议族做 TCP + TLS 握手，返回 (ok, 说明)"""
    fam_name = "IPv4" if family == socket.AF_INET else "IPv6"
    try:
        infos = socket.getaddrinfo(host, TG_PORT, family, socket.SOCK_STREAM)
    except socket.gaierror as e:
        return False, "%s 没有可用地址（%s）" % (fam_name, e)
    except Exception as e:
        return False, "%s 解析失败（%s）" % (fam_name, e)
    if not infos:
        return False, "%s 没有可用地址" % fam_name

    ip = infos[0][4][0]
    if ip in ("0.0.0.0", "127.0.0.1", "::1"):
        return False, "%s 被 DNS 过滤了（解析到 %s）" % (fam_name, ip)
    try:
        sock = socket.create_connection((ip, TG_PORT), timeout=timeout)
    except Exception as e:
        return False, "%s 连接 %s:443 失败（%s）" % (fam_name, ip, type(e).__name__)
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            ss.settimeout(timeout)
            ss.send(b"HEAD / HTTP/1.1\r\nHost: " + host.encode() + b"\r\nConnection: close\r\n\r\n")
            data = ss.recv(32)
        return (b"HTTP/" in data), "%s %s 已连通" % (fam_name, ip)
    except Exception as e:
        return False, "%s %s TLS 握手失败（%s）" % (fam_name, ip, type(e).__name__)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def probe_telegram(timeout=6, host=TG_HOST):
    """不需要 token：DNS / IPv4 / IPv6 三项探测"""
    out = {"host": host, "reachable": False, "detail": "", "ipv4": False, "ipv6": False,
           "dns_ok": False, "dns_ips": []}
    try:
        infos = socket.getaddrinfo(host, TG_PORT, 0, socket.SOCK_STREAM)
        out["dns_ips"] = sorted({i[4][0] for i in infos})
        out["dns_ok"] = bool(out["dns_ips"])
    except Exception as e:
        out["detail"] = "DNS 解析失败：%s" % e
        return out

    ok4, why4 = _tcp_tls_probe(socket.AF_INET, host, timeout)
    ok6, why6 = _tcp_tls_probe(socket.AF_INET6, host, timeout)
    out["ipv4"], out["ipv6"] = ok4, ok6

    if ok4 or ok6:
        out["reachable"] = True
        out["detail"] = why4 if ok4 else why6
    elif not out["dns_ips"]:
        out["detail"] = "DNS 解析不到地址，请检查 DNS 设置"
    else:
        out["detail"] = "%s；%s" % (why4, why6)
    return out


def explain_probe(st):
    lines = ["Telegram 探测结果："]
    lines.append("  DNS      : %s" % ("正常 " + ", ".join(st.get("dns_ips") or []) if st.get("dns_ok") else "失败"))
    lines.append("  IPv4     : %s" % ("可连" if st.get("ipv4") else "不通"))
    lines.append("  IPv6     : %s" % ("可连" if st.get("ipv6") else "不通"))
    lines.append("  结论     : %s" % ("直连正常" if st.get("reachable") else st.get("detail")))
    return "\n".join(lines)


# ============================ 2. WARP ============================

def find_warp_cli():
    for p in WARP_CLI_CANDIDATES:
        if os.path.exists(p):
            return p
    found = shutil_which("warp-cli") or shutil_which("warp-cli.exe")
    return found


def shutil_which(name):
    try:
        import shutil
        return shutil.which(name)
    except Exception:
        return None


def warp_cli(*args, timeout=60):
    """调用 warp-cli，自动带上 --accept-tos"""
    cli = find_warp_cli()
    if not cli:
        return 127, "没有找到 warp-cli（Cloudflare WARP 未安装）"
    return _run([cli, "--accept-tos"] + list(args), timeout=timeout)


def _parse_json_loose(text):
    """warp-cli 的 JSON 输出有时是 pretty print，宽松解析一下"""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search("{" + ".*" + "}", text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def warp_status():
    """返回 WARP 状态摘要（连没连上 + 本地代理端口在不在听）"""
    cli = find_warp_cli()
    st = {"installed": bool(cli), "cli": cli or "", "connected": None,
          "mode": "", "port": DEFAULT_WARP_PORT, "proxy_listening": False, "raw": ""}
    if cli:
        code, out = warp_cli("-j", "status", timeout=20)
        st["raw"] = (out or "")[:300]
        data = _parse_json_loose(out)
        if isinstance(data, dict):
            status = str(data.get("status") or data.get("state") or "").lower()
            st["connected"] = (("connected" in status and "disconnected" not in status)
                                or data.get("connected") is True)
            st["mode"] = str(data.get("mode") or "")
        else:
            low = (out or "").lower()
            if "connected" in low or "disconnected" in low:
                st["connected"] = ("disconnected" not in low)
    st["proxy_listening"] = port_listening("127.0.0.1", st["port"])
    return st


def port_listening(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def wait_port(host="127.0.0.1", port=DEFAULT_WARP_PORT, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if port_listening(host, port):
            return True
        time.sleep(1)
    return False


def is_admin():
    if not is_windows():
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def download_warp_installer():
    """从 Cloudflare 官方地址下载 MSI，返回本地路径"""
    import tempfile
    dst = os.path.join(tempfile.gettempdir(), "Cloudflare_WARP.msi")
    print("[WARP] 正在从官方地址下载安装包（可能要一两分钟）...")
    print("       %s" % WARP_MSI_URL)
    try:
        import urllib.request
        req = urllib.request.Request(WARP_MSI_URL, headers={"User-Agent": "KTMB-Bot-ProxySetup"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dst, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = int(done * 100 / total)
                    sys.stdout.write("\r       %d%%  (%.1f/%.1f MB)" % (pct, done / 1048576.0, total / 1048576.0))
                    sys.stdout.flush()
        print("")
    except Exception as e:
        print("[WARP] 下载失败：%s" % e)
        return None
    print("[WARP] 下载完成：%s（%.1f MB）" % (dst, os.path.getsize(dst) / 1048576.0))
    return dst


def install_warp():
    """静默安装 WARP（需要管理员；不是管理员会自动弹 UAC 提权）"""
    if not is_windows():
        print("[WARP] 自动安装目前只支持 Windows。其它系统请看 README 的代理章节。")
        return False
    msi = download_warp_installer()
    if not msi:
        return False
    print("[WARP] 正在静默安装（可能弹出 UAC，请点“是”）...")
    if is_admin():
        code, out = _run(["msiexec", "/i", msi, "/qn", "/norestart"], timeout=1800)
    else:
        ps = ("Start-Process msiexec -ArgumentList '/i \"%s\" /qn /norestart' "
              "-Verb RunAs -Wait" % msi)
        code, out = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                         timeout=1800)
    if find_warp_cli():
        print("[WARP] 安装完成")
        return True
    print("[WARP] 安装似乎没有成功（返回码 %s）" % code)
    if out:
        print("       " + out.strip().splitlines()[-1][:200])
    return False


def _warp_registered():
    """已经注册过就别再注册：registration new 会替换已有注册（Teams 用户会掉出组织）"""
    for args in (("-j", "registration", "show"), ("registration", "show")):
        code, out = warp_cli(*args, timeout=30)
        text = (out or "").strip()
        if code == 0 and text:
            low = text.lower()
            if "no registration" in low or "not registered" in low:
                return False
            return True
    return False


def enable_warp_proxy(port=DEFAULT_WARP_PORT, auto_install=False, wait_seconds=40, force=False):
    """开 WARP 代理模式（本地 SOCKS5），返回 (ok, 说明)

    - 已经装了 WARP：绝不重新下载/安装（只有找不到 warp-cli 时才安装）
    - 代理端口已经在监听：什么都不改，直接复用
    - 已经注册过：跳过 registration new（避免 Teams / Zero Trust 用户掉出组织）
    """
    if not find_warp_cli():
        if not (auto_install and is_windows()):
            return False, "没有找到 warp-cli，请先安装 Cloudflare WARP"
        if not install_warp():
            return False, "WARP 安装失败"

    if port_listening("127.0.0.1", port) and not force:
        return True, "WARP 代理端口 %d 已经在跑，直接复用（没有改动任何设置）" % port

    if not _warp_registered():
        print("[WARP] 首次使用，注册设备 ...")
        code, out = warp_cli("registration", "new", timeout=120)
        if code != 0:
            return False, "warp-cli registration new 失败：%s" % (out or "")[:200]
    else:
        print("[WARP] 检测到已有注册，跳过注册（不会动你原来的账号/组织）")

    print("[WARP] 切换为代理模式 (SOCKS5) ...")
    code, out = warp_cli("mode", "proxy", timeout=60)
    if code != 0:
        return False, "warp-cli mode proxy 失败：%s" % (out or "")[:200]
    code, out = warp_cli("proxy", "port", str(port), timeout=60)
    if code != 0:
        print("[WARP] 设置端口失败（用默认端口继续）：%s" % (out or "")[:120])
    print("[WARP] 连接 ...")
    warp_cli("connect", timeout=120)
    if wait_port("127.0.0.1", port, seconds=wait_seconds):
        return True, "WARP 代理模式已就绪：socks5h://127.0.0.1:%d（已把 WARP 切换为代理模式）" % port
    return False, "WARP 已连接，但本地代理端口 %d 还没起来（稍后重试，或看 warp-cli status）" % port


# ============================ 3. 测试代理 ============================

def test_api_base(base, token=None, timeout=12):
    """测自建反代（Cloudflare Worker 等）：直接 GET {base}/bot<token>/getMe"""
    base = (base or "").strip().rstrip("/")
    if not base:
        return False, "没有填写反代地址"
    url = base + "/"
    if token:
        url = "%s/bot%s/getMe" % (base, token)
    try:
        import requests
    except Exception:
        return False, "本机没有 requests 库"
    try:
        r = requests.get(url, timeout=timeout)
    except Exception as e:
        return False, "反代连不上 -> %s: %s" % (type(e).__name__, str(e)[:150])
    if r.status_code == 200:
        if token:
            try:
                d = r.json()
                if d.get("ok"):
                    u = d.get("result", {})
                    return True, "反代可用 ✅ 机器人：@%s（%s）" % (u.get("username"), u.get("first_name"))
                return False, "反代通了，但 Telegram 返回错误：%s" % str(d.get("description"))[:120]
            except Exception:
                pass
        return True, "反代可用 ✅（HTTP 200）"
    if r.status_code in (301, 302, 401, 404, 409):
        return True, "反代可用 ✅（HTTP %d，说明路是通的）" % r.status_code
    return False, "反代返回 HTTP %d" % r.status_code


def test_proxy(proxy_url, token=None, timeout=12):
    """用给定代理真的请求一次 api.telegram.org，返回 (ok, 说明)"""
    if not proxy_url:
        return False, "没有填写代理地址"
    url = "https://%s/" % TG_HOST
    if token:
        url = "https://%s/bot%s/getMe" % (TG_HOST, token)
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        import requests
    except Exception:
        return False, "本机没有 requests 库"
    try:
        r = requests.get(url, proxies=proxies, timeout=timeout)
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, str(e)[:160])
        if "SOCKS" in msg or "Missing dependencies" in msg:
            msg += "（SOCKS5 代理需要 PySocks：pip install PySocks）"
        return False, "通过代理连接失败 -> %s" % msg
    if r.status_code == 200:
        if token:
            try:
                d = r.json()
                if d.get("ok"):
                    u = d.get("result", {})
                    return True, "代理可用 ✅ 机器人：@%s（%s）" % (u.get("username"), u.get("first_name"))
                return False, "代理通了，但 Telegram 返回错误：%s" % str(d.get("description"))[:120]
            except Exception:
                pass
        return True, "代理可用 ✅（HTTP %d）" % r.status_code
    if r.status_code in (301, 302, 401, 404, 409):
        return True, "代理可用 ✅（HTTP %d，说明网络已通）" % r.status_code
    return False, "代理返回 HTTP %d" % r.status_code


def _test_no_proxy(token=None, timeout=8):
    """不用代理直接测（走本机网络）"""
    url = "https://%s/" % TG_HOST
    if token:
        url = "https://%s/bot%s/getMe" % (TG_HOST, token)
    try:
        import requests
    except Exception:
        return False, "本机没有 requests 库"
    try:
        r = requests.get(url, timeout=timeout)
        return True, "直连正常（HTTP %d）" % r.status_code
    except Exception as e:
        return False, "直连失败：%s: %s" % (type(e).__name__, str(e)[:140])


# ============================ 4. 配置读写 ============================

def read_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def write_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


def get_proxy_setting(cfg=None):
    """返回 (启用?, 代理地址)"""
    cfg = cfg if cfg is not None else read_config()
    n = cfg.get("notification", {}) or {}
    url = (n.get("telegram_proxy") or "").strip()
    enabled = n.get("telegram_proxy_enabled", True)   # 兼容老配置：有地址就算开
    if not url:
        enabled = False
    return bool(enabled), url


def get_api_base(cfg=None):
    """自建反代地址（空 = 用官方 api.telegram.org）"""
    env = (os.environ.get("KTMB_TG_API_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    cfg = cfg if cfg is not None else read_config()
    n = cfg.get("notification", {}) or {}
    return ((n.get("telegram_api_base") or "").strip()).rstrip("/")


def set_api_base(base):
    cfg = read_config()
    n = cfg.setdefault("notification", {})
    n["telegram_api_base"] = (base or "").strip().rstrip("/")
    write_config(cfg)
    return n["telegram_api_base"]


def set_proxy_setting(url, enabled=True):
    cfg = read_config()
    n = cfg.setdefault("notification", {})
    n["telegram_proxy"] = url or ""
    n["telegram_proxy_enabled"] = bool(enabled and url)
    write_config(cfg)
    return n["telegram_proxy_enabled"]


# ============================ 5. 命令行 ============================

def _menu_offer():
    """start_bot.bat 调用的交互菜单：只在真的连不上 Telegram 时才出现"""
    if os.environ.get("KTMB_NO_PROXY_PROMPT"):
        return 0
    st = probe_telegram()
    print("")
    if st["reachable"]:
        print("[TG 检测] Telegram 直连正常（%s），跳过代理设置" % st["detail"])
        return 0
    base_now = get_api_base()
    if base_now:
        ok_b, msg_b = test_api_base(base_now)
        if ok_b:
            print("[TG 检测] 直连不通（%s），但自建反代可用：%s" % (st["detail"], msg_b))
            return 0
        print("[TG 检测] 反代 %s 目前不可用：%s" % (base_now, msg_b))

    print("=" * 62)
    print("  ⚠️  检测到连不上 Telegram（api.telegram.org）")
    print("      %s" % st["detail"])
    print("      影响：抢票、远程控制都正常，只是收不到 TG 通知和指令")
    print("=" * 62)
    print("  [1] 安装/启用 Cloudflare WARP，只让 Telegram 走它（推荐，需管理员）")
    print("  [2] 我自己有代理（Clash / VPS 的 HTTP/SOCKS5 地址）")
    print("  [3] 我有自建反代域名（Cloudflare Worker 等，不用装任何软件）")
    print("  [4] 跳过（以后可以在网页面板的「通知」页里设置）")
    try:
        choice = (input("  请选择 [1/2/3/4]，直接回车 = 4 ：") or "4").strip()
    except Exception:
        return 0
    print("")

    if choice == "1":
        if find_warp_cli() and not port_listening("127.0.0.1", DEFAULT_WARP_PORT):
            print("  注意：你的 WARP 已经安装。这一步会把它切换成「代理模式」——")
            print("        只有 Telegram 走代理；如果你平时用 WARP 全局上网，全局接管会停掉。")
            try:
                go = (input("  继续吗？[Y/n]：") or "y").strip().lower()
            except Exception:
                go = "y"
            if go.startswith("n"):
                print("  [取消] 没有改动你的 WARP 设置。")
                return 0
        ok, msg = enable_warp_proxy(auto_install=True)
        print("[WARP] %s" % msg)
        if ok:
            url = "socks5h://127.0.0.1:%d" % DEFAULT_WARP_PORT
            ok2, msg2 = test_proxy(url)
            print("[测试] %s" % msg2)
            if ok2 or ok2 is True:
                set_proxy_setting(url, True)
                print("")
                print("  ✅ 已写入配置：notification.telegram_proxy = %s" % url)
                print("     想关掉：网页面板「通知」页取消勾选「启用 Telegram 代理」")
                return 0
            print("[WARP] 代理端口起来了但测试没通过，暂时不写入配置（可选择 [2] 手填别的代理）")
        print("[提示] 也可以先用 [2] 填你自己的代理地址")
        return 0

    if choice == "2":
        try:
            url = (input("  代理地址（例：http://127.0.0.1:7890 或 socks5h://127.0.0.1:40000）：") or "").strip()
        except Exception:
            return 0
        if not url:
            print("[跳过] 没有填写")
            return 0
        ok, msg = test_proxy(url)
        print("[测试] %s" % msg)
        if ok:
            set_proxy_setting(url, True)
            print("  ✅ 已写入配置：notification.telegram_proxy = %s" % url)
        else:
            try:
                save = (input("  测试没通过，仍然保存吗？[y/N]：") or "n").strip().lower()
            except Exception:
                save = "n"
            if save.startswith("y"):
                set_proxy_setting(url, True)
                print("  已保存（抢票不受影响，收不到通知时再回来调整）")
        return 0

    if choice == "3":
        print("  例：https://tg.你的域名.com/密钥  （程序会自动拼上 /bot<token>/方法名）")
        try:
            base = (input("  反代地址：") or "").strip()
        except Exception:
            return 0
        if not base:
            print("[跳过] 没有填写")
            return 0
        ok_b, msg_b = test_api_base(base)
        print("[测试] %s" % msg_b)
        if ok_b:
            set_api_base(base)
            print("  ✅ 已写入配置：notification.telegram_api_base = %s" % base)
        else:
            try:
                save = (input("  测试没通过，仍然保存吗？[y/N]：") or "n").strip().lower()
            except Exception:
                save = "n"
            if save.startswith("y"):
                set_api_base(base)
                print("  已保存（抢票不受影响）")
        return 0

    print("  [跳过] 已跳过，抢票功能不受影响。")
    return 0


def cmd_status():
    enabled, url = get_proxy_setting()
    st = probe_telegram()
    w = warp_status()
    out = {
        "telegram_reachable": st["reachable"],
        "telegram_detail": st["detail"],
        "dns_ok": st["dns_ok"], "ipv4_ok": st["ipv4"], "ipv6_ok": st["ipv6"],
        "proxy_enabled": enabled, "proxy_url": url,
        "warp_installed": w["installed"], "warp_cli": w["cli"],
        "warp_port_listening": w["proxy_listening"],
        "platform": platform.system(),
    }
    base_cfg = get_api_base()
    out["api_base"] = base_cfg or DEFAULT_API_BASE
    if base_cfg:
        ok_b, msg_b = test_api_base(base_cfg)
        out["api_base_test_ok"], out["api_base_test_msg"] = ok_b, msg_b
    elif url:
        ok, msg = test_proxy(url)
        out["proxy_test_ok"], out["proxy_test_msg"] = ok, msg
    print(json.dumps(out, ensure_ascii=False))
    return 0


def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "status").lower()
    if cmd == "offer":
        return _menu_offer()
    if cmd == "probe":
        print(explain_probe(probe_telegram()))
        return 0
    if cmd == "status":
        return cmd_status()
    if cmd == "test":
        url = argv[2] if len(argv) > 2 else get_proxy_setting()[1]
        st = probe_telegram()
        if not st["reachable"] and not url:
            print("[提示] 直连不通：%s" % st["detail"])
        if url:
            print("[测试] 通过代理 %s" % url)
            ok, msg = test_proxy(url)
        else:
            print("[测试] 不使用代理（直连）")
            ok, msg = _test_no_proxy()
        print(("✅ " if ok else "❌ ") + msg)
        return 0 if ok else 1
    if cmd in ("enable-warp", "enable"):
        port = int(argv[2]) if len(argv) > 2 else DEFAULT_WARP_PORT
        ok, msg = enable_warp_proxy(port, auto_install=True)
        print(("[WARP] ✅ " if ok else "[WARP] ❌ ") + msg)
        if ok:
            url = "socks5h://127.0.0.1:%d" % port
            tok_ok, tok_msg = test_proxy(url)
            print("[测试] " + tok_msg)
            set_proxy_setting(url, True)
            print("[配置] 已写入 config.json：%s" % url)
        return 0 if ok else 1
    if cmd == "set-base":
        base = argv[2] if len(argv) > 2 else ""
        if not base:
            print("用法: proxy_setup.py set-base https://tg.你的域名/密钥")
            return 1
        ok_b, msg_b = test_api_base(base)
        print("[测试] %s" % msg_b)
        set_api_base(base)
        print("[配置] 已写入 config.json：notification.telegram_api_base = %s" % base)
        return 0 if ok_b else 1
    if cmd == "test-base":
        base = argv[2] if len(argv) > 2 else get_api_base()
        if not base:
            print("[提示] 没有配置反代地址（notification.telegram_api_base）")
            return 1
        ok_b, msg_b = test_api_base(base)
        print(("✅ " if ok_b else "❌ ") + msg_b)
        return 0 if ok_b else 1
    if cmd == "disable-base":
        set_api_base("")
        print("[配置] 已清空自建反代地址，改回官方 api.telegram.org")
        return 0
    if cmd == "disable":
        set_proxy_setting("", False)
        print("[配置] 已关闭 Telegram 代理")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
