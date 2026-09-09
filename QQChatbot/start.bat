@echo off
REM ============================================================
REM QQ AI Chatbot - Windows Launcher
REM Usage: start.bat [install^|bot^|qq^|all^|stop]
REM ============================================================

setlocal enabledelayedexpansion
REM Always switch to this script's own directory first
cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
set "BOT_DIR=%SCRIPT_DIR%bot"
set "LAGRANGE_DIR=%SCRIPT_DIR%lagrange"
set "NAPCAT_DIR=%SCRIPT_DIR%napcat"
set "LOG_DIR=%SCRIPT_DIR%logs"
REM Prefer the known system Python310, fall back to PATH python
set "PYEXE=python"
if exist "C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe" set "PYEXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if "%~1"=="" goto all

if /i "%~1"=="install" goto install
if /i "%~1"=="bot" goto bot
if /i "%~1"=="qq" goto qq
if /i "%~1"=="napcat" goto napcat
if /i "%~1"=="watchdog" goto watchdog
if /i "%~1"=="all" goto all
if /i "%~1"=="stop" goto stop

echo Usage: start.bat [install^|bot^|qq^|napcat^|watchdog^|all^|stop]
goto :eof

:install
echo [INFO] Installing Python dependencies...
cd /d "%BOT_DIR%"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if not exist "%BOT_DIR%\.env" (
    copy "%BOT_DIR%\.env.example" "%BOT_DIR%\.env" >nul
    echo [WARN] .env created. Please edit it and fill in your DeepSeek API Key.
)

if not exist "%LAGRANGE_DIR%\Lagrange.OneBot.exe" (
    echo [WARN] Lagrange.OneBot.exe not found in lagrange folder.
    echo Download win-x64 build from https://github.com/LagrangeDev/Lagrange.Core/releases
)

echo [INFO] Install finished.
goto :eof

:bot
echo [INFO] Checking dependencies...
python -c "import nonebot, fastapi, websockets, aiosqlite, yaml" 2>nul
if errorlevel 1 (
    echo [ERROR] Dependencies missing for this Python:
    python -c "import sys; print('  Using:', sys.executable)"
    echo [FIX]   Run: run.bat install
    echo.
    pause
    goto :eof
)
echo [INFO] Starting NoneBot...
cd /d "%BOT_DIR%"
start "QQ-Bot" cmd /k ""%PYEXE%" bot.py"
echo [INFO] Bot started in a new window.
goto :eof

:watchdog
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',18081);$c.Close();exit 0}catch{exit 1}"
if not errorlevel 1 (
  echo [INFO] Watchdog already running on 18081.
  goto wd_ensure
)
echo [INFO] Starting watchdog (auto-restart backend, control port 18081)...
cd /d "%SCRIPT_DIR%"
start "QQBot-Watchdog" /min cmd /k ""%PYEXE%" watchdog.py"
powershell -NoProfile -Command "Start-Sleep -Seconds 2"
:wd_ensure
REM Always ask watchdog to ensure the backend; POST /start also resets desired_running=true,
REM fixing the case where a previous "stop" left it false and double-click never started backend.
echo [INFO] Requesting watchdog to start/ensure the backend...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%ensure_backend.ps1"
goto :eof

:qq
if not exist "%LAGRANGE_DIR%\Lagrange.OneBot.exe" (
    echo [ERROR] Lagrange.OneBot.exe not found in lagrange folder.
    goto :eof
)
echo [INFO] Starting Lagrange.OneBot (QQ side)...
cd /d "%LAGRANGE_DIR%"
start "Lagrange-QQ" cmd /k "Lagrange.OneBot.exe"
echo [INFO] Lagrange window opened. Scan the QR code inside it with mobile QQ.
goto :eof

:napcat
if not exist "%NAPCAT_DIR%\launcher-win10.bat" (
    echo [ERROR] NapCat launcher not found in napcat folder.
    goto :eof
)
echo [INFO] Starting NapCat (QQ protocol side)...
echo [INFO] A UAC prompt may appear - click Yes to allow admin.
cd /d "%NAPCAT_DIR%"
start "NapCat-QQ" cmd /k "launcher-win10.bat %2"
echo [INFO] NapCat window opened. Scan the QR code in it (or open the WebUI URL printed).
goto :eof

:all
call :watchdog
echo [INFO] Waiting for backend health port 18080...
set /a _w=0
:waitloop
timeout /t 2 /nobreak >nul
set /a _w+=2
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',18080);$c.Close();exit 0}catch{exit 1}"
if not errorlevel 1 goto waited
if %_w% LSS 40 goto waitloop
:waited
echo [INFO] Backend is up after ~%_w%s
call :napcat
echo [INFO] All done. Web console: http://127.0.0.1:18080
goto :eof

:stop
echo [INFO] Stopping...
REM Stop watchdog FIRST so it won't immediately restart the backend
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*watchdog.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
taskkill /f /fi "WINDOWTITLE eq QQBot-Watchdog*" 2>nul
REM Kill only the python process running bot.py (match command line; window-title match misses the child python)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
taskkill /f /fi "WINDOWTITLE eq QQ-Bot*" 2>nul
taskkill /f /im Lagrange.OneBot.exe 2>nul
REM NapCat injects QQ.exe launched with --enable-logging; close only those (keeps normal QQ)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='QQ.exe'\" | Where-Object { $_.CommandLine -like '*enable-logging*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
taskkill /f /fi "WINDOWTITLE eq NapCat-QQ*" 2>nul
REM NapCat bootstrapper process
taskkill /f /im NapCatWinBootMain.exe 2>nul
echo [INFO] Stopped.
goto :eof
