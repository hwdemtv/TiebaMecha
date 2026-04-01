@echo off
chcp 65001
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -c "import flet as ft; icons = [x for x in dir(ft.icons) if 'DASH' in x.upper() or 'ACCOUNT' in x.upper() or 'EVENT' in x.upper() or 'FORUM' in x.upper() or 'DOWN' in x.upper() or 'LIGHT' in x.upper() or 'DARK' in x.upper() or 'PLAY' in x.upper() or 'CHECK' in x.upper() or 'ADD' in x.upper() or 'DELETE' in x.upper() or 'SEARCH' in x.upper() or 'SYNC' in x.upper()]; print('\n'.join(sorted(icons)))"
pause
