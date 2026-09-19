English | **[中文](README_zh.md)**

# 🚄 KTMB Auto Ticket Bot v1.3.3

An automated ticket booking system for Malaysia's KTMB train service, supporting both **Windows** and **Linux** platforms.

Powered by Playwright browser automation to simulate real user operations — fully automated ticket monitoring, smart seat selection, form filling, instant payment, and Telegram remote control.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔄 **Smart Ticket Hunt** | Auto-refresh loop when no tickets available, with customizable interval (default 180s) |
| 📅 **Multi-Task Monitoring** | Configure multiple search tasks in config.json, monitored sequentially |
| 💺 **Smart Seat Selection** | Scans all carriages, prefers normal seats → table seats; old trains support window/forward preferences |
| 💳 **Multiple Payment Methods** | KTM Wallet (auto-deduct), DuitNow (QR code), TnG, Manual |
| 🔔 **Telegram Notifications** | Booking success, heartbeat status, crash alerts → auto push with text + screenshots |
| 📱 **Remote Control** | Control the bot via Telegram Bot commands |
| 🖥️ **Web Management Panel** | Flask backend with password authentication, open `localhost:5000` in browser to configure/start/stop/view logs |
| 🛡️ **Safe Logout** | `/logout` remotely logs out safely, avoiding the 30-minute cooldown |
| 🤖 **Anti-Misfire on Boot** | Automatically clears old Telegram commands on startup to prevent stale `/logout` execution |
| 🔐 **Web Panel Authentication** | Password-protected web panel to prevent unauthorized access |
| 📝 **Structured Logging** | Professional logging system with file output and console display |
| 🔄 **Notification Retry** | Telegram notifications with automatic retry and exponential backoff |
| 🎮 **Web Remote Control** | `/remote` live screen with mouse click / keyboard / CSS selector control - works even when Telegram is blocked |


---

## 🆕 v1.3.3: faster grabbing + Telegram never blocks

| Symptom | Root cause (measured in your log) | Fix |
|---------|----------------------------------|-----|
| **Search / seat selection slow as a turtle** | (1) reading a missing #fixedPax element wasted the 20s default timeout; (2) the booking result waited 15s for bookingData and then 8s more for the button; (3) the result table was scanned row by row, one browser round trip per row (10+ rows = 20s+) | (1) the passenger count is read inside the same JS scan; (2) poll every 0.25s and continue the moment booking succeeds (saves 20s+); (3) all rows are scanned in a single JS call |
| **Telegram hiccups froze the ticket flow** (log: a 51s stall at startup, 13s before clicking the passenger button) | notifications and getUpdates ran on the main thread, so network timeouts (15s each, with retries) were charged to the ticket flow | Telegram I/O moved to **background threads**: separate text/photo queues, a sender that retries until delivered (items kept up to 10 minutes), a long-polling receiver; the main thread only touches local queues - **it never waits for the network** |
| Are messages lost while Telegram is down? | - | No: queued messages go out as soon as the network recovers; text has priority over photos; a best-effort flush runs on exit |
| **The QR payment link did not open** | the URL sent was the bare viewqr.php from after clicking PAY (no order parameters) | Now the **gateway payment URL from BEFORE clicking PAY** is sent first (that is the valid one), then PAY is clicked, then both the QR page URL and the QR image URL are sent; the screenshot caption carries the link too |
| Having /remote open slowed things down | a screenshot every 5s, a few hundred ms each on Windows | **No screenshots at all while nobody is watching**; with the page open it publishes on the configured interval (phase/state still update) |

---

## 🆕 What v1.3.2 fixes (vs v1.3)

| Symptom | Root cause | Fix |
|---------|-----------|-----|
| **Windows: "cannot reach Telegram" every now and then** | A fresh TCP/TLS connection per request, no retry, no way to tell "no network / bad token / another instance stealing getUpdates"; a half-broken IPv6 stack hangs on the AAAA record | All requests now use a **pooled session** (keep-alive), connection failures are retried and automatically **fall back to IPv4**; HTTP 401/409/400 print an explicit reason; new proxy switch `telegram_proxy` / `KTMB_TG_PROXY` |
| **Telegram commands flaky (HTTP 409)** | Two bot instances running at once (the Start button could be pressed again after a panel restart) | The panel now tracks the bot PID and refuses to start a second instance; 409 is reported clearly |
| **Element not found -> silent freeze, no error, no retry** | Waits never checked external state; failures were just a one-line warning | New `tick()` heartbeat makes **every long wait interruptible**; `click_first` retries and logs the **current URL / title / visible buttons** plus a screenshot; a watchdog reports the scene after 2 minutes of no heartbeat |
| **`/remote` stuck on "connecting", no picture** | Panel wrote a request, waited 3s for the bot to screenshot; a busy bot always timed out and the page kept showing a stale frame | The bot now **publishes a frame every 5s** (`screenshot_interval`); the panel serves the newest frame; the page shows frame age / bot state / phase and real command results |
| **Remote clicks land in the wrong place** | Full-page screenshot coordinates do not match the browser viewport | Screenshots are viewport-sized now, so clicks map 1:1 |
| **`/manual` exited the program and logged out** | Manual hand-over was treated as "task done" | `/manual` now enters **manual mode**: the browser stays open, the bot stops touching the page; `/cancel` resumes, `/logout` exits |
| Stop took minutes while waiting at the payment gateway | Up to 120s of uninterruptible waiting | Waiting is now sliced and always answers stop; missing buttons report the current page |
| Seat selection reported success although nothing was booked | No verification | Missing booking data *and* missing passenger button is now a failure with a scene report |
| Overlays blocked every later click | The fallback only hid modals with inline styles (Bootstrap 5 modals survived) | It now hides every **visible** `.modal` (seat modal preserved) |
| Blank page / 502 counted as "logged in" | `is_logged_in` only checked for a login link | Now also checks domain, readyState and page content |
| Panel restart lost the session / could not stop the old bot | Random secret key; only its own child process was tracked | Secret key persisted to `.flask_secret`; PID file + heartbeat double detection, so a restarted panel can still stop the bot safely |
| Viewport stuck at 1920x2500 after screenshots | `enlarge_and_shot` never restored it | Viewport restored after the shot |

> Still recommended: never run two instances on the same KTMB account, and always stop with the panel button or Telegram `/logout`.

---

## 🎮 Web remote control (/remote)

Click **Remote** in the top-right corner of the panel, or open `http://127.0.0.1:5000/remote`:

- Left side: **live view** of the bot's browser (auto-refresh every 3s, the bot publishes a frame every 5s by default)
- **Click anywhere on the picture** to fill the coordinates, then press "Click here" to click that exact spot (viewport coordinates)
- Supports click / type text / Enter / navigate / scroll / refresh / **CSS selector click**
- Every command shows the **real result returned by the bot** (success or the failure reason)
- The status box shows how old the frame is, whether the bot is running, and the current phase

> Handy when Telegram is blocked, or when you want to take over the payment manually.

---

## 🚀 Quick Start (3 Steps)

### 1. Install Python

Download and install Python 3.8+: https://www.python.org/downloads/

> ⚠️ Make sure to check **"Add Python to PATH"** during installation

### 2. Run the Startup Script

**Windows:** Double-click `start_bot.bat`

**Linux:** Run `bash start_linux.sh` in terminal

The script handles everything automatically: creates virtual environment, installs dependencies, installs browser, and starts the service.

### 3. Open the Management Panel

Open **http://localhost:5000** in your browser

> 🔐 **Default password:** `admin123`
> You can change it via environment variable: `export KTMB_WEB_PASSWORD=your_password`

Fill in the web interface:
- KTMB account credentials
- Routes and dates to monitor
- Telegram Bot Token (optional, for notifications and remote control)
- Payment method

Click **Save** → **Start**, then wait for tickets 🎉

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KTMB_WEB_PASSWORD` | `admin123` | Web panel login password |
| `KTMB_WEB_HOST` | `127.0.0.1` | Web panel listen address |
| `KTMB_WEB_PORT` | `5000` | Web panel listen port |
| `KTMB_WEB_DEBUG` | `false` | Enable Flask debug mode |
| `KTMB_MAX_LOG_LINES` | `200` | Maximum log lines returned by API |
| `FLASK_SECRET_KEY` | auto-generated, saved to `.flask_secret` | Flask session key (sessions survive panel restarts) |
| `KTMB_HEADLESS` | `1` on Linux, `0` from the Windows launcher | Run headless (`0` = show the browser window) |
| `KTMB_CHROME_PROFILE` | `chrome_profile/` in the project | Chromium user data directory |
| `KTMB_CHROME_PORT` | `chrome_port` from config | Chrome debug port (take over via chrome://inspect) |
| `KTMB_TG_PROXY` | (follow system proxy) | Proxy for Telegram, e.g. `http://127.0.0.1:7890`; `off` = force direct connection |
| `KTMB_TG_IPV4` | `0` | `1` = use IPv4 only from the start (for half-broken IPv6 networks) |

---

## 📱 Telegram Remote Commands

| Command | Description |
|---------|-------------|
| `/status` | Full status: phase, round, uptime, current plan, last result, next refresh, plan list |
| `/plans` | List every monitored plan (from -> to - date time - train mode) |
| `/seats` | Current seat preference ladder and toggles |
| `/page` | URL of the page the browser is on |
| `/snap` | Capture the current screen and send it to your phone |
| `/help` | Show command help |
| `/duitnow` | Generate the DuitNow payment QR (Fiuu gateway, clicks through to the QR page) |
| `/tng` | Generate the Touch 'n Go payment QR |
| `/wallet` | Pay with KTM Wallet |
| `/manual` | Switch to manual payment |
| `/cancel` | Cancel the payment standby |
| `/logout` | Safely log out of KTMB and shut down |

> Every command supports `/cmd all` (broadcast) and `/cmd1` (target Bot ID=1), e.g. `/status1`, `/snap all`

### When Telegram cannot be reached

1. Read `bot.log`: the reason is printed (`ConnectionError` / `SSL error` / `HTTP 401` bad token / `HTTP 409` another instance is polling)
2. Bad token -> paste a new one in the panel; `409` -> make sure only one bot is running
3. Behind a proxy -> set `notification.telegram_proxy` in `config.json`, or `KTMB_TG_PROXY=http://127.0.0.1:7890`
4. If the proxy makes it worse -> `KTMB_TG_PROXY=off` forces a direct connection
5. The bot keeps retrying by itself once the network is back - no restart needed

### notification settings

| Field | Description |
|-------|-------------|
| `telegram_token` | Token from BotFather |
| `telegram_chat_id` | Your chat id (send the bot a message first) |
| `heartbeat_screenshot` | Attach a screenshot to the heartbeat (true/false) |
| `telegram_proxy` | Optional. Proxy for Telegram, e.g. `http://127.0.0.1:7890`; `off` = force direct |

---

## 📂 Project Structure

```
├── ktmb_auto.py          # Core ticket booking engine
├── app.py                # Flask web management panel
├── config.json           # Configuration file (user-created)
├── config.example.json   # Configuration template
├── requirements.txt      # Python dependencies with version pinning
├── start_bot.bat         # Windows one-click startup script
├── start_linux.sh        # Linux one-click startup script
├── templates/
│   ├── index.html        # Web management interface
│   └── login.html        # Web panel login page
├── static/
│   └── favicon.ico       # Icon
└── bot.log               # Bot runtime log (auto-generated)
```

---

## 🔧 Configuration Guide

### search_tasks

| Field | Description |
|-------|-------------|
| `from` / `to` | Departure / Arrival station (must match KTMB website display names) |
| `year` / `month` / `day` | Target travel date (integer values) |
| `time` | Target train time, format `HH:MM` |
| `is_old_train` | Whether it's an old-style train (affects seat selection logic) |

### preferences

| Field | Description |
|-------|-------------|
| `prefer_forward` | Prefer forward-facing seats (old trains) |
| `prefer_window` | Prefer window seats (old trains) |
| `prefer_normal_seat` | Prefer normal seats (new trains) |
| `accept_table_seat` | Accept table seats when no normal seats available |

### payment_method

| Value | Description |
|-------|-------------|
| `"Command"` | Wait for Telegram command to choose payment method (recommended) |
| `"KTM Wallet"` | Auto-use KTM Wallet for deduction |
| `"DuitNow"` | Auto-generate DuitNow QR code |
| `"Manual"` | Notify user for manual payment |

### bot_settings

| Field | Description |
|-------|-------------|
| `bot_id` | Bot ID to distinguish instances when running multiple bots |
| `chrome_port` | Chrome debug port (default 9222) |
| `heartbeat_interval` | Send heartbeat notification every N cycles |
| `refresh_interval` | Seconds to wait when no tickets available (default 180) |
| `screenshot_interval` | How often to publish a frame for /remote (default 5s). Nothing is captured while nobody watches; 0 = only capture while the /remote page is open |

---

## 🔄 Ticket Flow

```
Start → Clear old TG commands → Login to KTMB → Search for trains
  ↓ No tickets
Wait N seconds → Search again (loop)
  ↓ Tickets found
Select seat → Fill passenger info → Confirm order
  ↓
[Command mode] Wait for TG command to choose payment method
[Auto mode] Execute payment directly
  ↓
Payment complete → Notify user → Program exits
```

---

## 🛡️ About the 30-minute cooldown (important)

KTMB only allows **one active session per account**. If the bot process is killed without
logging out, the next login returns `Not allow multiple login` and the account stays locked
until the old session times out (about 30 minutes).

This version protects against that in several ways:

1. Cached cookies are written to `.ktmb_session.json` right after login (already git-ignored)
2. On SIGTERM/SIGINT a background thread performs an HTTP logout immediately, then the main
   loop performs a full logout before exiting
3. On startup any leftover session from a previous crash is logged out first
4. `Not allow multiple login` is detected and the bot waits 5 minutes instead of retrying in a loop
5. `/api/stop` waits up to 45 seconds for a graceful logout before force-killing the process

> Always stop the bot with the Web panel **Stop** button or Telegram `/logout`, never `kill -9`.

---

## 🧭 Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `Not allow multiple login` | Account already logged in elsewhere; wait ~30 min or log out the old session |
| "target date has passed" | Update the date in `search_tasks` via the web panel |
| "no selectable seat" | The train is really full; the bot keeps refreshing |
| "could not reach the payment page" | Payment DOM changed; check the log screenshot and update selectors |
| Log file too large | Rotated automatically to `bot.log.1` above 5MB |
| **Windows: `Executable doesn't exist at ...\browsers\chromium_headless_shell-XXXX\...`** | **Fixed in v1.3.1.** The old `start_bot.bat` set `PLAYWRIGHT_BROWSERS_PATH` *after* `playwright install`, so Chromium was downloaded into the default cache (`%LOCALAPPDATA%\ms-playwright`) while the bot looked inside `.\browsers` - exactly this error. The variable is now set before installing, and the browser is installed + verified on every launch. If it still fails, delete the `browsers\` folder and run the launcher again. |
| `cannot connect to existing Chrome: ECONNREFUSED ::1:9222` | Harmless: the bot starts its own Chromium. It only means "no external Chrome to attach to". |
| **Does a dead Telegram slow the ticket flow?** | No. Since v1.3.3 notifications and commands use background threads: the ticket flow never waits for Telegram, and queued messages keep retrying until delivered (kept up to 10 minutes). |
| **Telegram never connects although the network is fine** | Since v1.3.2: pooled connections, automatic retries, automatic IPv4 fallback, and a precise reason in the log. Behind a proxy set `KTMB_TG_PROXY=http://127.0.0.1:7890`; if the proxy breaks it, set `KTMB_TG_PROXY=off`. A wrong token gives `HTTP 401`, two instances give `HTTP 409`. |
| **`/remote` stays on "connecting" with no picture** | The bot publishes frames continuously now: check whether the "phase" is moving; the page tells you whether the bot is not running or has not produced a frame yet. If the frame age never changes, look for the `[看门狗]` / `[定位失败]` lines in `bot.log`. |
| **Remote clicks do nothing / hit the wrong spot** | Screenshots are viewport-sized now, so you click exactly what you see. Blocking overlays are reported through the command result. |
| **`/manual` closed the browser and exited** | Fixed in v1.3.2: `/manual` keeps the browser open in manual mode, `/cancel` resumes monitoring. |
| `[看门狗] no heartbeat for N seconds` in the log | The bot is stuck inside a browser call. Send the surrounding 50 log lines; the panel Stop button still works and logs out safely. |
| Stopping the bot takes very long | Since v1.3.1 the bot polls for a stop request even during login retries / searches / payment standby, so it logs out and exits within 1-3s (no hard kill, no 30-minute cooldown). |

---

## 🎫 Seat selection rules (rewritten in v1.3)

Seat picking is driven purely by page data (`data-*` attributes + seat icon URLs) and
**auto-detects old vs new trains** - no hard-coded selectors. Old trains have no Takaful
insurance while new ones do, so the flow now simply clicks whatever button exists on the page.

| Priority | Seat | Note |
|----------|------|------|
| 01 | Aisle | highest priority (toggle in the panel) |
| 02 | Window | second choice when no aisle seat |
| 03 | Table / cluster | only when every normal seat is gone |
| 04 | Berth | old trains, last resort (off by default) |
| 05 | First / Business class | **off by default**, used only when higher tiers have no seat |
| 06 | OKU accessible seat | **off by default**, reserved for passengers with disabilities |

- Old trains additionally sort **forward > backward** (`for`/`fwd` vs `back`/`bwd`/`bw`)
- First class and OKU are optional toggles - turn 05 on when you actually want first class,
  then enable "First class first" to make it the top priority
- Extra blocked keywords are editable in the panel (comma separated, empty by default)
- "No seats" / "only unwanted seats" alerts are throttled to once every 15 minutes
- Train type detection: cluster seats => new (ETS); backward seats => old (Intercity)

---

## 🖥️ New admin panel (v1.3)

Railway dispatch-console aesthetic: split-flap status board, paper-ticket task cards, seat
priority ladder, dot-matrix log console.

- Five tabs: Trips / Seats / Account / Alerts / System
- Seat priority ladder 01 aisle -> 02 window -> 03 table -> 04 berth with live toggles
- Save-time validation: future date, HH:MM time, stations required
- Login and remote-control pages restyled to match

> Restart `app.py` after editing templates (Flask caches them).

---

## 🚀 Launcher scripts (rewritten in v1.3)

**Linux**

```
./start_linux.sh          # start (venv, deps, browser, free-port check)
./start_linux.sh stop     # stop (bot logs out of KTMB first)
./start_linux.sh status
./start_linux.sh restart
```

**Windows**: double-click `start_bot.bat`, press Ctrl+C to stop (graceful logout first).

- On Linux a busy port automatically rolls over to the next free one
- Scripts point `PLAYWRIGHT_BROWSERS_PATH` at the local `browsers/` folder
- `app.py` now handles SIGTERM/SIGINT: stopping the panel also stops the bot gracefully

---

## 🔒 Security Notes


- **Web Panel Password**: Change the default password `admin123` via environment variable `KTMB_WEB_PASSWORD`
- **Network Access**: Web panel defaults to `127.0.0.1` (localhost only). Set `KTMB_WEB_HOST=0.0.0.0` to allow remote access (not recommended without additional security)
- **Config File**: Contains sensitive credentials. Ensure `config.json` is in `.gitignore` and has restricted file permissions
- **Telegram Token**: Store your bot token securely; do not commit it to version control

---

## ⚠️ Disclaimer

- This project is intended for **technical exchange and personal learning** only. Do not use it for any illegal activities.
- All consequences of using this tool, including **account bans, booking failures, and financial losses**, are the sole responsibility of the user.
- This project is **not affiliated with KTMB (Keretapi Tanah Melayu Berhad)** or any official organization.
- The author is not responsible for any direct or indirect damages arising from the use of this project.

---

## 📝 License

This project is licensed under the **GNU AGPLv3 License** - see the [LICENSE](LICENSE) file for details.

**⚠️ Warning:**

Under the AGPL-3.0 license, **any modifications, derivatives, or network services based on this project must release their complete source code under the same AGPL-3.0 license.**

**🚫 The following uses are prohibited:**

- **Scalping / Ticket Reselling** — Using this project for any form of ticket scalping, reselling, or illegal trading is strictly forbidden
- **Commercial Profit** — Using this project for paid ticket booking services or any commercial operations is strictly forbidden
- **Closed-Source Modifications** — Any modified versions must be fully open-sourced to users

Violators will be subject to legal action at the author's discretion.
