@echo off
REM OS-aware launcher. Python auto-detects Windows vs macOS for paths and install.
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYTHONPATH=%ROOT%\python;%PYTHONPATH%"
set "PYTHONUNBUFFERED=1"
where python >nul 2>&1
if errorlevel 1 (
  echo Python not found on PATH. Install Python 3.9+ from python.org and retry.
  exit /b 1
)
python -m mt5_hook %*
