@echo off
chcp 65001
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set LOG_LEVEL=warning
python start_web.py
pause
