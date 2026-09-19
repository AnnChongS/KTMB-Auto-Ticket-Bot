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
| `/status` | 运行状态总览：阶段、轮数、已运行时长、当前计划、上次结果、下次刷新、监控计划清单 |
| `/plans` | 只列出所有监控计划（地点 → 地点 · 日期 时间 · 车型） |
| `/seats` | 当前取票偏好（优先级 + 各项开关） |
| `/page` | 当前浏览器所在页面 URL |
| `/snap` | 截取当前屏幕画面发送到手机 |
| `/help` | 显示指令帮助 |
| `/duitnow` | 生成 DuitNow 付款二维码（Fiuu 网关，自动点到二维码页） |
| `/tng` | 生成 Touch 'n Go 付款二维码 |
| `/wallet` | 使用 KTM Wallet 自动扣款 |
| `/manual` | 切换为人工付款模式 |
| `/cancel` | 取消付款待命 |
| `/logout` | 安全登出 KTMB 账号并关闭程序 |

> 所有指令都支持 `/cmd all`（广播）和 `/cmd1`（定向 Bot ID=1），例如 `/status1`、`/snap all`

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

## 🛡️ 关于 30 分钟冷却（重要）

KTMB 同一账号**不允许同时登录**。如果程序被强制杀死（SIGKILL / 直接关窗口）而没有登出，
下一次登录会收到 `Not allow multiple login`，账号会被锁定到旧会话超时（约 30 分钟）。

本版本已针对这一点做了多层保护：

1. **登录后立即缓存 cookies** 到 `.ktmb_session.json`（已加入 `.gitignore`）
2. **收到停止信号（SIGTERM/SIGINT）时**：先起后台线程用 cookies 走 HTTP 登出，主循环退出后再做一次完整登出
3. **启动时自动清理残留会话**：如果上次没正常登出，启动时会先尝试用缓存 cookies 登出，避免“多处登录”
4. **识别 `Not allow multiple login`**：不再反复重试登录，而是等待 5 分钟后再试，并发送 Telegram 提醒
5. **正常停止 Web 面板**：`/api/stop` 会给机器人最多 45 秒完成登出，再强制结束

> 请始终通过 Web 面板的 **停止** 按钮或 Telegram `/logout` 退出，不要直接 `kill -9`。

---

## 🧭 故障排查

| 现象 | 原因 / 处理 |
|------|-------------|
| `Not allow multiple login` | 账号已在别处登录，等待约 30 分钟或先登出旧会话 |
| 提示「监控日期已过期」 | `search_tasks` 里的日期已过去，请在面板改成未来日期 |
| 提示「该车次没有可选座位」 | 该车次确实无票，等待下一轮刷新 |
| 提示「未能到达付款页面」 | 支付流程元素变化，日志里会有截图，可据此更新选择器 |
| 日志文件过大 | 超过 5MB 会自动轮转为 `bot.log.1` |
| **Windows：`Executable doesn't exist at ...\browsers\chromium_headless_shell-XXXX\...`** | **v1.3.1 已修复**。旧 `start_bot.bat` 是在 `playwright install` **之后**才设置 `PLAYWRIGHT_BROWSERS_PATH`，所以 Chromium 被下载进了默认缓存（`%LOCALAPPDATA%\ms-playwright`），而机器人却在 `.\browsers` 里找 → 就是这个报错。现在变量在安装之前就设好，并且每次启动都会安装 + 校验浏览器。仍然报错的话：删掉 `browsers\` 文件夹再双击一次。 |
| `无法连接现有 Chrome: ECONNREFUSED ::1:9222` | 正常现象：机器人会自己启动 Chromium，这行只是提示"没有现成的 Chrome 可以接管"，不影响运行。 |
| 点「停止」后等很久 | v1.3.1 起，机器人在登录重试 / 搜索 / 付款待命等长时间等待中也会每秒检查停止指令，通常 1-3 秒内安全登出退出（不会被硬杀，避免 30 分钟冷却）。 |

---

## 🧩 关键页面元素（2026-09 实测）

| 步骤 | 元素 |
|------|------|
| 首页站点 | `#select2-FromStationId-container` / `#select2-ToStationId-container`，选项文本为 `站名 + 车型` |
| 出发日期 | `#OnwardDate` + lightpick 日历（`.lightpick__select-year(s)/__month(s)/__day/__next-action`） |
| 搜索 | `#btnSubmit` |
| 搜索结果 | `.depart-trips > tr`，每行 `.btn-seat-layout`；行内有 `data-HourMinute` |
| 选座弹窗 | `#seatSelect.show`、`.coache-btn[data-CoacheId]`、`.coache-layout`、`img.selectable-icon[data-seat-no][data-coache-id][data-seat-service-type][data-seat-price]`、`#confirmSeatBtn` |
| 座位图标 | `/Image/GetSeatTypeImageFromTrain?id=StanForWinWC / StanForAisleWC / StanBackWinWC / StanClusForWinWC / StdBwWinOKUFlexi ...`（`Win`=靠窗、`For/Back`=朝向、`Clus`=大桌、`Table`=桌位） |
| 订座结果 | 隐藏域 `#bookingData`，成功后出现 `.btn-passenger`，取消用 `.btn-reset` |
| 乘客页 | `/Book`：`.IsSelf`、`select.TicketTypeId`(value=`Adult`)、`#btnConfirmPayment` |
| 保险 | `#btnUpdateInsuranceNo`(NO THANKS) → 弹窗 `#popupModalOkButton` |
| 继续付款 | `#btnProceedToPayment` → 餐食确认 `#confirmationConfirmButton` |
| 付款方式页 | `#btnKtmbEWallet` / `#btnGoPaymentDuitNow` / `#btnGoPaymentTnG`（都是 `div`） |
| 网关过渡页 | `#PaymentGateway`（value: `Click HERE to proceed to payment gateway`，初始 `display:none`） |
| 网关(Fiuu) | `#pay-now-nomultipayment` / `.pay-button` → 之后是 `viewqr.php` 二维码页 |

> 注意：付款方式页与乘客页 URL 都是 `/Book`，**不能靠 URL 判断是否到达付款页**，必须检测付款按钮元素。

---

## 🎫 选座规则（v1.3 重写）

选座完全由页面元素（`data-*` 属性 + 座位图标 URL）驱动，**自动识别新旧火车**，不再依赖硬编码选择器。
旧火车没有 Takaful 保险，新火车有 —— 流程改为「页面上有哪个按钮就点哪个」，新旧车共用一套代码。

| 优先级 | 座位 | 说明 |
|--------|------|------|
| 01 | 靠过道 | 最优先（面板可关闭） |
| 02 | 靠窗 | 没有过道位时的第二选择 |
| 03 | 大桌位 / cluster | 普通座全部售罄才会选 |
| 04 | 卧铺 | 旧火车最后选择，默认关闭 |
| 05 | 头等舱 / 商务舱 | **默认关闭**，打开后前面几档都没位才会选 |
| 06 | OKU 无障碍座 | **默认关闭**，打开后才会选（这是给身心障碍人士预留的座位） |

- 旧火车额外按 **正向 > 反向** 排序（`for`/`fwd` vs `back`/`bwd`/`bw`）
- 头等舱 / OKU 都是**可选开关**，想买头等舱时在面板打开 05；
  想**优先**买头等舱再打开「头等舱优先(排最前)」
- 额外屏蔽关键词可在面板「取票偏好」填写（英文逗号分隔，留空即可）
- 无票 / 只剩不符合偏好的座位时，同一提醒 **15 分钟只发一次**，不再 Telegram 刷屏
- 车型自动判定：出现大桌位 => 新火车(ETS)；出现反向座 => 旧火车(Intercity)

---

## 🖥️ 新版管理面板（v1.3）

界面改为「铁路调度台 / 发车时刻牌」风格：翻牌状态灯、纸质车票式任务卡、选座优先级阶梯、点阵日志台。

- 5 个标签页：乘车计划 / 取票偏好 / 账号付款 / 通知 / 系统
- 选座偏好阶梯：01 过道 → 02 靠窗 → 03 大桌位 → 04 卧铺，开关即时生效
- 保存前校验：日期必须是未来、时间必须 HH:MM、站点不能为空
- 登录页与远程控制页同步换新

> 修改模板后需要重启 `app.py`（Flask 会缓存模板）。

---

## 🚀 启动脚本（v1.3 重写）

**Linux**

```
./start_linux.sh          # 启动（自动建 venv、装依赖、下浏览器、检测端口占用）
./start_linux.sh stop     # 停止（先让机器人安全登出 KTMB）
./start_linux.sh status   # 状态
./start_linux.sh restart  # 重启
```

**Windows**：双击 `start_bot.bat`，按 Ctrl+C 停止（会先安全登出）。

- Linux 下端口被占用会**自动改用下一个可用端口**
- 脚本把 `PLAYWRIGHT_BROWSERS_PATH` 指向项目内 `browsers/`，不再依赖全局缓存
- `app.py` 现在处理 SIGTERM/SIGINT：停止 Web 面板会连带把机器人优雅停掉（先登出）

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
