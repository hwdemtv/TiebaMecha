@echo off
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

rem 跑测试的入口包装：实际逻辑在 run_tests.py（根目录定位、参数透传都在那边）。
rem 此前本文件是一段早已失效的「Web UI 启动测试」，引用了 4 个不存在的脚本
rem （2026-11-24 体检时清理）。
rem 用法：
rem   run_tests.bat                 跑常规门禁（不含 tests/manual）
rem   run_tests.bat tests/test_x.py 只跑指定文件
rem   run_tests.bat --cov           带覆盖率

rem 切到项目根目录（本脚本位于 scripts\developer\ 下）
pushd "%~dp0..\.."

if "%~1"=="" (
    python scripts\developer\run_tests.py --ignore=tests/manual %*
) else (
    python scripts\developer\run_tests.py %*
)
set EXITCODE=%ERRORLEVEL%

popd
echo.
if %EXITCODE%==0 (
    echo [OK] 测试通过
) else (
    echo [FAIL] 测试失败，退出码 %EXITCODE%
)
pause
exit /b %EXITCODE%
