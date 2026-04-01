@echo off
chcp 65001
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -E test_minimal_app.py
pause
