@echo off
rem ============================================================
rem  KTMB Ticket Commander - Windows launcher
rem  1. Double-click this file
rem  2. A Chrome window will open by itself (that is the bot's browser - keep it open)
rem  3. Open http://127.0.0.1:5000 in your browser
rem  4. Press Ctrl+C in this window to stop (the bot logs out first)
rem
rem  v1.3.2 fixes
rem  ----------------------------------------------------------
rem  [D] The bot now keeps the web panel's /remote screen alive by
rem      publishing a screenshot every few seconds, and every wait
rem      loop answers "stop" / remote clicks within ~1 second.
rem  [E] If api.telegram.org cannot be reached, set a proxy with:
rem        set KTMB_TG_PROXY=http://127.0.0.1:7890
rem      or force direct connection (ignore system proxy) with:
rem        set KTMB_TG_PROXY=off
rem
rem  v1.3.1 fixes
rem  ----------------------------------------------------------
rem  [A] PLAYWRIGHT_BROWSERS_PATH must be set BEFORE "playwright install
rem      chromium", otherwise Chromium goes to the default cache while the
rem      bot looks in .\browsers  -> "Executable doesn't exist at ...".
rem      It is now set first, and the browser is always installed+verified.
rem  [B] The browser is now VISIBLE (headed) and listens on the debug port
rem      below, so you can actually watch it. The bot launches it itself -
rem      no need to start Chrome manually (the debug port still works for
rem      chrome://inspect if you ever want to take over).
rem  [C] A stale "logout" command file could make a freshly started bot
rem      kill itself instantly (looked like an endless loop) - it is now
rem      cleared on start.
rem ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul
title KTMB Ticket Commander
cd /d "%~dp0"

set "VENV_PY=%CD%\venv\Scripts\python.exe"
set "PW_PATH=%CD%\browsers"
rem  === MUST be set before ANY "playwright install" call ===
set "PLAYWRIGHT_BROWSERS_PATH=%PW_PATH%"
rem  === visible browser window + debugging port + profile ===
set "KTMB_HEADLESS=0"
set "KTMB_CHROME_PORT=9222"
set "KTMB_CHROME_PROFILE=%CD%\chrome_profile"
if "%KTMB_WEB_PORT%"=="" set "KTMB_WEB_PORT=5000"

echo ===================================================
echo   KTMB Ticket Commander
echo ===================================================
echo.

echo [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 goto no_python

echo [2/6] Checking virtual environment...
if not exist "%VENV_PY%" (
    echo       creating venv...
    python -m venv venv
    if errorlevel 1 goto venv_fail
)

echo [3/6] Installing / updating dependencies...
"%VENV_PY%" -m pip install -q --disable-pip-version-check --upgrade -r requirements.txt
if errorlevel 1 goto pip_fail

echo [4/6] Installing Chromium into:
echo       %PLAYWRIGHT_BROWSERS_PATH%
echo       ^(first run downloads about 150 MB, later runs are instant^)
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 goto browser_fail

echo       verifying the browser really exists...
"%VENV_PY%" -c "import os,sys;from playwright.sync_api import sync_playwright as s;pw=s().start();e=pw.chromium.executable_path;pw.stop();print('      browser: '+e);sys.exit(0 if os.path.exists(e) else 3)"
if errorlevel 1 goto browser_verify_fail

echo [5/6] Checking port %KTMB_WEB_PORT%...
netstat -ano | findstr /r /c:":%KTMB_WEB_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] port %KTMB_WEB_PORT% is already in use.
    echo        Set another one first, e.g.  set KTMB_WEB_PORT=5001
    echo.
)

echo [6/6] Starting web panel - keep this window open...
echo.
echo   Panel   : http://127.0.0.1:%KTMB_WEB_PORT%
echo   Remote  : http://127.0.0.1:%KTMB_WEB_PORT%/remote   (live screen + click/keyboard)
echo   Browser : a Chrome window will open automatically (debug port %KTMB_CHROME_PORT%)
echo   Profile : %KTMB_CHROME_PROFILE%
echo   Stop    : Ctrl+C  (the bot logs out of KTMB before exiting)
echo.
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:%KTMB_WEB_PORT%"
"%VENV_PY%" app.py

echo.
echo Service stopped.
pause
exit /b 0

:no_python
echo [ERROR] Python not found. Install Python 3.9+ and tick "Add Python to PATH".
pause
exit /b 1

:venv_fail
echo [ERROR] Failed to create the virtual environment.
pause
exit /b 1

:pip_fail
echo [ERROR] pip install failed. Check your network / proxy.
pause
exit /b 1

:browser_fail
echo [ERROR] Could not download Chromium.
echo         Try manually:
echo           set PLAYWRIGHT_BROWSERS_PATH=%PW_PATH%
echo           "%VENV_PY%" -m playwright install chromium
pause
exit /b 1

:browser_verify_fail
echo [ERROR] Chromium is still missing after installing.
echo         Delete this folder and run this launcher again:
echo           %PW_PATH%
pause
exit /b 1

