@echo off
rem ============================================================
rem  KTMB Ticket Commander - Windows launcher
rem  1. Double-click this file
rem  2. Open http://127.0.0.1:5000 in your browser
rem  3. Press Ctrl+C in this window to stop (the bot logs out first)
rem ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul
title KTMB Ticket Commander
cd /d "%~dp0"

set "VENV_PY=%CD%\venv\Scripts\python.exe"
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\browsers"
if "%KTMB_WEB_PORT%"=="" set "KTMB_WEB_PORT=5000"

echo ===================================================
echo   KTMB Ticket Commander - launcher
echo ===================================================
echo.

echo [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.9+ and tick "Add Python to PATH".
    pause
    exit /b 1
)

echo [2/6] Checking virtual environment...
if not exist "%VENV_PY%" (
    echo       creating venv...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] failed to create venv
        pause
        exit /b 1
    )
)

echo [3/6] Installing dependencies...
"%VENV_PY%" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)

echo [4/6] Checking Playwright browser...
dir /b /ad "%PLAYWRIGHT_BROWSERS_PATH%\chromium-*" >nul 2>&1
if errorlevel 1 (
    echo       downloading Chromium (first run only)...
    "%VENV_PY%" -m playwright install chromium
    if errorlevel 1 (
        echo [ERROR] browser download failed
        pause
        exit /b 1
    )
)

echo [5/6] Checking port %KTMB_WEB_PORT%...
netstat -ano | findstr /r /c:":%KTMB_WEB_PORT% .*LISTENING" >nul
if not errorlevel 1 (
    echo [WARN] port %KTMB_WEB_PORT% is already in use.
    echo        Set another one first, e.g.  set KTMB_WEB_PORT=5001
    echo.
)

echo [6/6] Starting web panel - keep this window open...
echo.
echo   Panel : http://127.0.0.1:%KTMB_WEB_PORT%
echo   Pass  : admin123   (change it with KTMB_WEB_PASSWORD)
echo.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:%KTMB_WEB_PORT%"
"%VENV_PY%" app.py

echo.
echo Service stopped.
pause
