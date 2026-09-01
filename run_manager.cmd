@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
set "PYTHONPATH=%ROOT%\python"
set "PYTHONUNBUFFERED=1"
set "MT5_MANAGER_DAEMON=1"
cd /d "%ROOT%"
python -u -m mt5_hook manage --interval 20 --no-sleep 2>> "%ROOT%\logs\manager.err.log"
