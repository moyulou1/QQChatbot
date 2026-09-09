@echo off
REM Unambiguous launcher (start is a built-in command name).
REM Usage: run.bat [install^|bot^|all^|stop] ; no arg = all
cd /d "%~dp0"
call "%~dp0start.bat" %*
