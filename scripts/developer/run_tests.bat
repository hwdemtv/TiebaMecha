@echo off
chcp 65001 >nul
echo.
echo ========================================
echo   TiebaMecha Web UI 启动测试
echo ========================================
echo.

setlocal

REM 设置环境变量
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo 1. 测试原始脚本 (可能失败)
echo   命令: python start_web_fix.py
echo.
pause
python start_web_fix.py
if errorlevel 1 (
    echo.
    echo ❌ 原始脚本失败 (预期中)
    pause
)

echo.
echo ========================================
echo 2. 测试简单修复脚本
echo   命令: python start_web_simple.py
echo.
pause
python start_web_simple.py

echo.
echo ========================================
echo 3. 测试猴子补丁修复脚本
echo   命令: python start_web_monkeypatch.py
echo.
pause
python start_web_monkeypatch.py

echo.
echo ========================================
echo 4. 测试新启动脚本
echo   命令: python start_web.py
echo.
pause
python start_web.py

echo.
echo ========================================
echo 5. 测试 uvicorn 修复
echo   命令: python test_uvicorn_fix.py
echo.
pause
python test_uvicorn_fix.py

echo.
echo ========================================
echo 所有测试完成！
echo.
pause