@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo [TiebaMecha] 正在启动纯 Web 网页端控制器...
_runtime\python.exe start_web.py
if %ERRORLEVEL% neq 0 pause
endlocal
