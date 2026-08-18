**[English](README.md)** | 中文

# 🚄 KTMB 自动抢票机器人 v1.2

马来西亚 KTMB 火车票自动抢票系统，支持 **Windows** 和 **Linux** 双平台运行。

通过 Playwright 浏览器自动化模拟真人操作，实现全自动监控余票、智能选座、自动填单、极速付款，并支持 Telegram 远程控制。

---

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 🔄 **智能捡漏** | 无票时自动循环刷新，支持自定义间隔（默认 180 秒） |
| 📅 **多任务监控** | config.json 中配置多个搜索任务，逐个监控 |
| 💺 **智能选座** | 遍历车厢，优先普通座 → 大桌座；旧式火车支持靠窗/朝前偏好 |
| 💳 **多种付款** | KTM Wallet（全自动扣款）、DuitNow（生成二维码）、TnG、Manual |
| 🔔 **Telegram 通知** | 抢票成功、心跳存活、崩溃报错 → 自动推送文字+截图（含重试机制） |
| 📱 **远程控制** | 通过 Telegram Bot 发送指令操控机器人 |
| 🖥️ **Web 管理面板** | Flask 后端 + 密码认证，浏览器打开 `localhost:5000` 配置/启停/看日志 |
| 🛡️ **安全登出** | `/logout` 远程安全退出账号，避免 30 分钟冷却 |
| 🤖 **开机防误触** | 启动时自动清空历史 Telegram 指令，防止旧 `/logout` 误执行 |
| 🔐 **Web 面板认证** | 密码保护，防止未授权访问 |
| 📝 **专业日志** | 结构化日志系统，同时输出到文件和控制台 |
| 🔄 **通知重试** | Telegram 通知自动重试，指数退避策略 |

---

## 🚀 快速开始（3步搞定）

### 1. 安装 Python

下载安装 Python 3.8+：https://www.python.org/downloads/

> ⚠️ 安装时务必勾选 **"Add Python to PATH"**

### 2. 运行启动脚本

**Windows：** 双击 `start_bot.bat`

**Linux：** 终端运行 `bash start_linux.sh`

脚本会自动完成一切：创建虚拟环境、安装依赖、安装浏览器、启动服务。

### 3. 打开管理面板配置

浏览器打开 **http://localhost:5000**

> 🔐 **默认密码：** `admin123`
> 可通过环境变量修改：`export KTMB_WEB_PASSWORD=你的密码`

在 Web 界面填写：
- KTMB 账号密码
- 监控路线和日期
- Telegram Bot Token（可选，用于通知和远程控制）
- 付款方式

点击 **保存** → **启动**，坐等出票 🎉

---

## ⚙️ 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `KTMB_WEB_PASSWORD` | `admin123` | Web 面板登录密码 |
| `KTMB_WEB_HOST` | `127.0.0.1` | Web 面板监听地址 |
| `KTMB_WEB_PORT` | `5000` | Web 面板监听端口 |
| `KTMB_WEB_DEBUG` | `false` | 是否启用 Flask 调试模式 |
| `KTMB_MAX_LOG_LINES` | `200` | API 返回的最大日志行数 |
| `FLASK_SECRET_KEY` | (自动生成) | Flask 会话加密密钥 |

---

## 📱 Telegram 远程指令

| 指令 | 说明 |
|------|------|
| `/snap` | 截取当前屏幕画面发送到手机 |
| `/snap1` | 指定 Bot ID=1 截屏（多机器人时使用） |
| `/duitnow` | 生成 DuitNow 付款二维码 |
| `/tng` | 生成 Touch 'n Go 付款二维码 |
| `/wallet` | 使用 KTM Wallet 自动扣款 |
| `/manual` | 切换为人工付款模式 |
| `/logout` | 安全登出 KTMB 账号并关闭程序 |
| `/logout1` | 指定 Bot ID=1 安全登出 |

> 支持 `/snap all`（广播）和 `/snap1`（定向）两种格式

---

## 📂 项目结构

```
├── ktmb_auto.py          # 核心抢票引擎
├── app.py                # Flask Web 管理面板
├── config.json           # 配置文件（需自行创建）
├── config.example.json   # 配置模板
├── requirements.txt      # Python 依赖（含版本锁定）
├── start_bot.bat         # Windows 一键启动脚本
├── start_linux.sh        # Linux 一键启动脚本
├── templates/
│   ├── index.html        # Web 管理界面
│   └── login.html        # Web 面板登录页
├── static/
│   └── favicon.ico       # 图标
└── bot.log               # 机器人运行日志（自动生成）
```

---

## 🔧 配置说明

### search_tasks（搜索任务）

| 字段 | 说明 |
|------|------|
| `from` / `to` | 出发站 / 到达站（必须与 KTMB 网站显示一致） |
| `year` / `month` / `day` | 目标日期（整数类型） |
| `time` | 目标车次时间，格式 `HH:MM` |
| `is_old_train` | 是否旧式火车（影响选座逻辑） |

### preferences（选座偏好）

| 字段 | 说明 |
|------|------|
| `prefer_forward` | 优先朝前座位（旧火车） |
| `prefer_window` | 优先靠窗座位（旧火车） |
| `prefer_normal_seat` | 优先普通座位（新火车） |
| `accept_table_seat` | 无普通座时接受大桌座 |

### payment_method（付款方式）

| 值 | 说明 |
|----|------|
| `"Command"` | 等待 Telegram 指令选择付款方式（推荐） |
| `"KTM Wallet"` | 自动使用 KTM Wallet 扣款 |
| `"DuitNow"` | 自动生成 DuitNow 二维码 |
| `"Manual"` | 通知人工手动付款 |

### bot_settings（机器人设置）

| 字段 | 说明 |
|------|------|
| `bot_id` | 机器人 ID，多开时区分不同实例 |
| `chrome_port` | Chrome 调试端口（默认 9222） |
| `heartbeat_interval` | 每 N 轮发送一次存活通知 |
| `refresh_interval` | 无票时等待秒数（默认 180） |

---

## 🔄 抢票流程

```
启动 → 清空历史TG指令 → 登录KTMB → 搜索车次
  ↓ 无票
等待 N 秒 → 重新搜索（循环）
  ↓ 有票
选座 → 填写乘客信息 → 确认订单
  ↓
[Command模式] 等待TG指令选择付款方式
[自动模式] 直接执行付款
  ↓
付款完成 → 通知用户 → 程序退出
```

---

## 🔒 安全说明

- **Web 面板密码**：请通过环境变量 `KTMB_WEB_PASSWORD` 修改默认密码 `admin123`
- **网络访问**：Web 面板默认仅监听 `127.0.0.1`（本地访问），设置 `KTMB_WEB_HOST=0.0.0.0` 可允许远程访问（不建议，需额外安全措施）
- **配置文件**：包含敏感凭据，请确保 `config.json` 已加入 `.gitignore` 并设置适当的文件权限
- **Telegram Token**：请妥善保管 Bot Token，不要提交到版本控制系统

---

## ⚠️ 免责声明

- 本项目仅供**技术交流与个人学习**使用，请勿用于任何违法行为
- 使用本工具造成的**账号封禁、购票失败、资金损失**等一切后果由使用者自行承担
- 本项目**不隶属于 KTMB (Keretapi Tanah Melayu Berhad)** 或任何官方机构
- 作者不对因使用本项目产生的任何直接或间接损失负责

---

## 📝 License

This project is licensed under the **GNU AGPLv3 License** - see the [LICENSE](LICENSE) file for details.

**⚠️ 注意 (Warning):**

由于采用了 AGPL-3.0 协议，**任何对本项目源代码的修改、衍生或基于本项目的网络服务，都必须以相同的 AGPL-3.0 协议公开全部源代码。**

**🚫 禁止以下用途：**

- **代抢黑产** — 禁止将本项目用于任何形式的代抢、黄牛倒票等违法违规活动
- **商业牟利** — 禁止将本项目用于任何收费代抢、商业运营等盈利行为
- **闭源修改** — 任何修改后的版本必须向用户公开完整源代码

如有违反，作者保留追究法律责任的权利。
