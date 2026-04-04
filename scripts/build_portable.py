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
    portable_dir = dist_dir / "TiebaMecha"

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

    # 创建启动脚本
    print(f"[6/6] 创建启动脚本...")

    # Windows 启动脚本
    start_bat = portable_dir / "启动Web界面.bat"
    start_bat.write_text(f"""@echo off
cd /d "%~dp0"
call _python\\Scripts\\activate.bat
python -m tieba_mecha.web.app
pause
""", encoding="utf-8")

    # 首次运行配置脚本
    setup_bat = portable_dir / "首次运行配置.bat"
    setup_bat.write_text(f"""@echo off
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
    shutil.make_archive(str(zip_path), "zip", dist_dir, "TiebaMecha")

    print(f"\n[DONE] Build completed!")
    print(f"   Output: {zip_path}.zip")
    print(f"   Size: {(zip_path.with_suffix('.zip')).stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    build_portable()
