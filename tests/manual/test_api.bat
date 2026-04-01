@echo off
chcp 65001
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -E -c "import sys; sys.path.insert(0, 'src'); exec(open('test_flet_api.py', encoding='utf-8').read())"
pause
