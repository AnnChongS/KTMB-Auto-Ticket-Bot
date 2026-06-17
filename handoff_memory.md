# KTMB Auto Ticket Bot — 技术交接备忘录

> 最后更新: 2026-06-07 | 仓库: https://github.com/AnnChongS/ktmb-auto-ticket-bot | 当前版本: v1.1

---

## 一、项目架构

```
ktmb-auto-ticket-bot/
├── ktmb_auto.py          # 核心抢票引擎 (560行, Playwright)
├── app.py                # Flask Web 管理面板 (114行, :5000)
├── templates/index.html  # Jinja2 + Bootstrap5 + JS i18n (498行)
├── config.example.json   # 配置模板 (用户复制为 config.json)
├── start_bot.bat         # Windows 一键启动
├── start_linux.sh        # Linux 一键启动 (xvfb-run)
├── static/favicon*.ico
├── .gitignore            # 排除 config.json / venv / __pycache__
├── LICENSE               # AGPL-3.0
├── README.md             # 英文
└── README_zh.md          # 中文
```

**技术栈**: Python 3.8+ / Playwright (Chromium) / Flask / Bootstrap 5 / Telegram Bot API

**运行模式**:
- **Windows**: `start_bot.bat` → 自动创建 venv → 装依赖 → 启动 Chrome `--remote-debugging-port=9222` → 启动 Flask → 浏览器打开 `:5000`
- **Linux**: `start_linux.sh` → 自动装 xvfb + venv → `xvfb-run ... python3 app.py` → 虚拟屏幕 1920x1080

**核心流程**: 登录 KTMB → 搜索车次(循环) → 选座 → 填乘客 → 等待支付指令(TG) → 完成

---

## 二、关键设计决策

### 2.1 浏览器连接方式
- **Windows**: `connect_over_cdp("http://localhost:9222")` — 连接用户手动启动的 Chrome，保留登录态
- **Linux**: `launch_persistent_context(headless=False)` — 自己启动 Chromium，配合 Xvfb 虚拟屏幕

### 2.2 安全退出机制 (`safe_logout`)
- KTMB 网站登出后有 **30 分钟冷却期**（同一账号无法立即重新登录）
- `/logout` 指令 → `safe_logout()` → 点击 navbar 下拉菜单 → 点击 Logout 链接
- 如果菜单不可见，fallback 到直接访问 `/Account/Logout`
- 全局标志 `SHOULD_LOGOUT_AND_EXIT` 控制主循环退出

### 2.3 开机防误触 (`flush_telegram_updates`)
- 启动时调用 `getUpdates` 获取最后一条 update_id + 1 作为 offset
- 这样旧的 `/logout` 指令不会被重新执行
- **Windows 旧版没有这个功能**，开机可能立即执行旧指令导致自杀

### 2.4 Telegram 指令解析
- 格式: `/snap` (广播) 或 `/snap1` (定向到 bot_id=1) 或 `/snap all`
- 解析逻辑: 分割空格 → 第一段取 `@` 前作为 command → 第二段作为 target_id
- 如果只有一个 token 且最后一位是数字，拆分最后一位作为 target_id

### 2.5 支付流程
- `Command` 模式: 抢到票后进入待命，等 TG 指令 `/duitnow` `/tng` `/wallet` `/manual`
- 支付网关页面是新弹出的 tab，用 `context.expect_page()` 捕获
- DuitNow/TnG 需要截图发给用户扫码，页面高度拉伸到 2500px + scrollBy(0,400) 防裁切

---

## 三、踩过的坑

### 3.1 Git 仓库敏感信息
- **config.json 包含真实密码和 token** → 已加入 `.gitignore`
- **HTML 模板 placeholder 泄露 token 前缀**: `placeholder="8201821536:AAH..."` → 已改为 `"123456789:ABCdef..."`
- **Git 历史无法彻底清洗**（除非 force push + BFG），但当前 HEAD 无泄露

### 3.2 Release 版本管理
- GitHub 按 **创建时间** 排序 latest release，不是版本号
- 后创建的 v1.0 会覆盖 v1.1 成为 latest → 需要手动 PATCH `make_latest: true`
- 删除 release 不会自动删除 tag，需要分别删除

### 3.3 Windows vs Linux 代码差异
- 原始 Windows 版 (`ktmb_auto.py`) 缺少: `safe_logout`, `flush_telegram_updates`, `SHOULD_LOGOUT_AND_EXIT`, 崩溃截图, login 超时重试, perform_search 日历容错
- 已合并为统一版本，Windows 和 Linux 共用同一份 `ktmb_auto.py`
- 区别仅在启动脚本 (`start_bot.bat` vs `start_linux.sh`)

### 3.4 Flask 子进程启动
- `app.py` 通过 `subprocess.Popen` 启动 `ktmb_auto.py`
- Windows 需要设置 `env["PYTHONIOENCODING"] = "utf-8"` 防止中文乱码
- log 文件需要用 `encoding='utf-8'` 打开

### 3.5 Playwright 选座逻辑
- 旧火车 (Intercity): 座位图标用 `data-seat-service-type="Standard"` + src 属性区分方向/靠窗
- 新火车 (ETS): 用 `src*='StanFor'`(普通座) 和 `src*='StanClus'`(大桌座)
- 选座后 `#confirmSeatBtn` 可能有 `disabled-btn` class，仍需点击

### 3.6 Web 面板 i18n
- 使用 `data-i18n` 属性 + JS 字典翻译，不依赖外部 i18n 库
- 动态生成的 task block 在 `renderTasks()` 中用 `t('key')` 函数翻译
- 状态轮询中的文字也需要根据语言切换（用 `data-status` 属性跟踪）
- localStorage key: `ktmb_lang`

---

## 四、已知问题 & 排错思路

### 4.1 登录失败 / 超时
- **症状**: login() 超时，cookie 过期
- **排查**: 检查 `config.json` 的 email/password，检查 KTMB 网站是否改版
- **修复**: login() 已有 15 秒超时 + popup 清理 + `clear_cookies()` fallback

### 4.2 日历弹窗打不开
- **症状**: perform_search() 中 `.lightpick__select-years` 超时
- **排查**: 可能是弹窗(modal)遮挡了日期输入框
- **修复**: 已有 `handle_popup()` 重试 + `force=True` 点击 + 失败截图发 TG

### 4.3 选座失败
- **症状**: `#seatSelect` 不可见，或所有座位都是 Occupied
- **排查**: 截图检查座位图，确认 `is_old_train` 配置是否正确
- **注意**: 新旧火车的 CSS 选择器完全不同

### 4.4 支付网关白屏
- **症状**: DuitNow/TnG 支付页面空白
- **原因**: 银行反机器人检测
- **缓解**: 使用 `connect_over_cdp` 接管真实 Chrome（Windows），设置真实 User-Agent

### 4.5 Telegram 通知不发
- **排查**: 检查 token 和 chat_id 是否正确，chat_id 可能是负数（群组）
- **注意**: `config.json` 中 telegram_token 被 mask 时，读取后是 `""` + `_set: true`，不是 `prefix***`

### 4.6 Linux 虚拟屏幕崩溃
- **症状**: Xvfb 进程挂了
- **排查**: `ps aux | grep Xvfb`，检查 `/tmp/.X99-lock`
- **修复**: `pkill -f Xvfb` 然后重新 `xvfb-run`

---

## 五、GitHub 仓库状态

| 标签 | 说明 |
|------|------|
| v1.0 | 初始发布 |
| v1.1 | 中英文双语支持 (latest) |

**GitHub Actions**: 无（手动发布 release + 上传 zip）

**Release 流程**:
1. 代码改动 → `git commit` → `git push`
2. 用 Python + GitHub API 创建/更新 release
3. 打包 zip (排除 venv/__pycache__/config.json) → 上传为 release asset

---

## 六、后续可优化方向

1. **KTMB 网站改版适配** — CSS 选择器硬编码，改版后需更新 `perform_search()` 和 `select_seat()`
2. **多账号支持** — 当前 config.json 只支持单账号
3. **支付方式扩展** — 当前只实现 DuitNow/TnG/Wallet 三种
4. **错误恢复** — 当前崩溃后进程退出，可以加 watchdog 自动重启
5. **Docker 部署** — 打包成 Docker image，一键部署到任意 VPS
6. **Web 面板认证** — 当前 `:5000` 无密码保护，公网暴露有风险
7. **config.json 加密** — 密码明文存储，可以用 keyring 或加密
