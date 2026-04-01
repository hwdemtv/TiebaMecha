@echo off
chcp 65001
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -E -c "import sys; sys.path.insert(0, 'src'); from tieba_mecha.scheduler import main; import asyncio; asyncio.run(main())"
pause
