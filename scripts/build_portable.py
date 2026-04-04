"""
便携版打包脚本
将 TiebaMecha 打包成解压即用的 ZIP 包，内嵌 Python 运行环境
"""

import shutil
import subprocess
import sys
import venv
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).parent.parent


def build_portable():
    project_root = get_project_root()
    dist_dir = project_root / "dist"
    portable_dir = dist_dir / "TiebaMecha_v110_Final"

    # 运行兼容性检查
    print(f"[0/6] 运行兼容性检查...")
    check_script = project_root / "scripts" / "check_compatibility.py"
    if check_script.exists():
        result = subprocess.run(
            [sys.executable, str(check_script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        print(result.stdout)
        if result.returncode != 0:
            print("⚠️ 兼容性检查发现问题，但仍继续打包...")
    else:
        print("  跳过兼容性检查（脚本不存在）")

    # 清理旧的构建目录
    if portable_dir.exists():
        print(f"[1/6] 清理旧构建目录...")
        shutil.rmtree(portable_dir)
    else:
        dist_dir.mkdir(exist_ok=True)

    # 创建目录结构
    print(f"[2/6] 创建目录结构...")
    portable_dir.mkdir(parents=True, exist_ok=True)
    (portable_dir / "data").mkdir(exist_ok=True)

    # 复制源代码
    print(f"[3/6] 复制源代码...")
    shutil.copytree(project_root / "src" / "tieba_mecha", portable_dir / "tieba_mecha")

    # 复制必要文件
    for item in ["pyproject.toml", "README.md", ".env.example", "start_web.bat"]:
        src = project_root / item
        if src.exists():
            shutil.copy2(src, portable_dir / item)

    # 创建嵌入式 Python 环境
    print(f"[4/6] 创建 Python 虚拟环境...")
    venv_dir = portable_dir / "_python"
    venv.create(venv_dir, with_pip=True)

    # 获取 venv 中的 pip 路径
    if sys.platform == "win32":
        pip_exe = venv_dir / "Scripts" / "pip.exe"
        python_exe = venv_dir / "Scripts" / "python.exe"
    else:
        pip_exe = venv_dir / "bin" / "pip"
        python_exe = venv_dir / "bin" / "python"

    # 安装依赖
    print(f"[5/6] 安装依赖（这可能需要几分钟）...")
    subprocess.run(
        [str(pip_exe), "install", "-e", "."],
        cwd=portable_dir,
        check=True,
        capture_output=True
    )

    # 创建加固型启动入口 launcher.py
    print(f"[6.1/6] 创建加固型启动入口 launcher.py...")
    launcher_py = portable_dir / "launcher.py"
    launcher_py.write_text("""import os
import sys
import logging

# 强制环境编码为 UTF-8，防止 Windows 处理中文日志崩溃
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

# 恢复标准日志级别名称 (Flet 可能会覆盖为 'warn')
logging.addLevelName(logging.WARNING, "WARNING")

# 确保能加载当前目录下的 tieba_mecha 包
sys.path.insert(0, os.getcwd())

import flet as ft

# Uvicorn 日志级别补丁 (修复 Flet 传参 'warn' 导致的 KeyError)
# 必须在 ft.run() 调用之前执行
def _patch_uvicorn_log_level():
    try:
        import uvicorn.config as _uvc
        _orig_configure_logging = _uvc.Config.configure_logging
        def _patched_configure_logging(self):
            if getattr(self, "log_level", None) == "warn":
                self.log_level = "warning"
            return _orig_configure_logging(self)
        _uvc.Config.configure_logging = _patched_configure_logging
    except (ImportError, AttributeError):
        pass

# 立即执行补丁
_patch_uvicorn_log_level()

from tieba_mecha.web.app import TiebaMechaApp, get_db

async def main(page: ft.Page):
    \"\"\"应用主函数\"\"\"
    app = TiebaMechaApp(page)
    db = await get_db()
    await app.initialize(db)

if __name__ == "__main__":
    port = 9006
    print("========================================")
    print("   TiebaMecha 启动中...")
    print("========================================")
    print(f"访问地址: http://localhost:{port}")

    try:
        # 再次执行补丁确保生效
        _patch_uvicorn_log_level()

        # 兼容不同 Flet 版本
        if hasattr(ft, 'run'):
            try:
                # 尝试新版本 API (positional argument)
                ft.run(main, port=port, view=ft.AppView.WEB_BROWSER)
            except TypeError:
                # 回退到关键字参数 (旧版本)
                ft.run(target=main, port=port, view=ft.AppView.WEB_BROWSER)
        else:
            # Flet < 0.80.0 使用 ft.app()
            ft.app(target=main, port=port, view=ft.AppView.WEB_BROWSER)
    except Exception as e:
        print(f"启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("按任意键退出...")
""", encoding="utf-8")

    # 创建启动脚本
    print(f"[6.2/6] 创建启动脚本...")

    # Windows 启动脚本
    start_bat = portable_dir / "启动Web界面.bat"
    start_bat.write_text(f"""@echo off
chcp 65001 >nul
title TiebaMecha - Launching
cd /d "%~dp0"
echo 正在进入虚拟环境...
call _python\\Scripts\\activate.bat
echo 正在启动主程序，请稍候...
python launcher.py
if %ERRORLEVEL% neq 0 (
    echo 程序异常退出，请检查上述错误信息。
    pause
)
""", encoding="utf-8")

    # 首次运行配置脚本
    setup_bat = portable_dir / "首次运行配置.bat"
    setup_bat.write_text(f"""@echo off
chcp 65001 >nul
title TiebaMecha - Initial Setup
cd /d "%~dp0"
echo ========================================
echo   TiebaMecha 首次运行配置
echo ========================================
echo.
echo 请按以下步骤配置加密密钥：
echo.
echo 1. 复制 .env.example 为 .env
echo 2. 编辑 .env 文件，填入随机生成的密钥
echo    可使用以下 Python 命令生成：
echo    python -c "import secrets; print('TIEBA_MECHA_SALT=' + secrets.token_hex(32)); print('TIEBA_MECHA_SECRET_KEY=' + secrets.token_hex(32))"
echo.
echo 3. 配置完成后运行 "启动Web界面.bat"
echo.
pause
if not exist .env (
    copy .env.example .env
    echo 已创建 .env 文件，请编辑填入密钥
    notepad .env
)
""", encoding="utf-8")

    # 打包 ZIP
    print(f"\n[打包] 创建 ZIP 压缩包...")
    zip_path = dist_dir / "TiebaMecha-portable"
    # 使用正确的目录名称
    shutil.make_archive(str(zip_path), "zip", dist_dir, portable_dir.name)

    print(f"\n[DONE] Build completed!")
    print(f"   Output: {zip_path}.zip")
    print(f"   Size: {(zip_path.with_suffix('.zip')).stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    build_portable()
