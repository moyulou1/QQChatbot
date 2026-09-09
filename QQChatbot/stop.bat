@echo off
cd /d "%~dp0"
call "%~dp0start.bat" stop
echo.
echo ============================================
echo  Bot backend + NapCat + injected QQ stopped.
echo  To start again: double-click  run.bat  (all)
echo ============================================
pause
