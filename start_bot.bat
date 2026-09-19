@echo off
rem ============================================================
rem  KTMB Ticket Commander - Windows launcher
rem  1. Double-click this file
rem  2. A Chrome window will open (that is the bot's browser - do not close it)
rem  3. Open http://127.0.0.1:5000 in your browser
rem  4. Press Ctrl+C in this window to stop (the bot logs out first)
rem
rem  v1.3.1 fixes
rem  ----------------------------------------------------------
rem  [A] PLAYWRIGHT_BROWSERS_PATH used to be set AFTER "playwright install
rem      chromium", so Chromium went to the default cache while the bot
rem      looked in .\browsers  ->  "Executable doesn't exist at ...\browsers\...".
rem      Now the variable is set before any playwright call, and the browser
rem      is always installed + verified.
rem  [B] The browser was headless (no window), so it looked like nothing
rem      started. Now Chrome runs WITH A VISIBLE WINDOW and opens the
rem      debugging port below, so you can watch / take over any time.
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

echo [1/7] Checking Python...
python --version >nul 2>&1
if errorlevel 1 goto no_python

echo [2/7] Checking virtual environment...
if not exist "%VENV_PY%" (
    echo       creating venv...
    python -m venv venv
    if errorlevel 1 goto venv_fail
)

echo [3/7] Installing / updating dependencies...
"%VENV_PY%" -m pip install -q --disable-pip-version-check --upgrade -r requirements.txt
if errorlevel 1 goto pip_fail

echo [4/7] Installing Chromium into:
echo       %PLAYWRIGHT_BROWSERS_PATH%
echo       ^(first run downloads about 150 MB, later runs are instant^)
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 goto browser_fail

echo       verifying the browser really exists...
"%VENV_PY%" -c "import os,sys;from playwright.sync_api import sync_playwright as s;pw=s().start();e=pw.chromium.executable_path;pw.stop();print('      browser: '+e);sys.exit(0 if os.path.exists(e) else 3)"
if errorlevel 1 goto browser_verify_fail

echo [5/7] Opening Chrome with remote debugging (a browser window should appear)...
where chrome >nul 2>&1
if errorlevel 1 (
    echo       system Chrome not found - the bot will open its own Chromium window
) else (
    start "" chrome --remote-debugging-port=%KTMB_CHROME_PORT% --user-data-dir="%KTMB_CHROME_PROFILE%"
    echo       chrome --remote-debugging-port=%KTMB_CHROME_PORT% --user-data-dir="%KTMB_CHROME_PROFILE%"
)

echo [6/7] Checking port %KTMB_WEB_PORT%...
netstat -ano | findstr /r /c:":%KTMB_WEB_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] port %KTMB_WEB_PORT% is already in use.
    echo        Set another one first, e.g.  set KTMB_WEB_PORT=5001
    echo.
)

echo [7/7] Starting web panel - keep this window open...
echo.
echo   Panel    : http://127.0.0.1:%KTMB_WEB_PORT%
echo   Browser  : visible Chrome, debug port %KTMB_CHROME_PORT%
echo   Profile  : %KTMB_CHROME_PROFILE%
echo   Browsers : %PLAYWRIGHT_BROWSERS_PATH%
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

