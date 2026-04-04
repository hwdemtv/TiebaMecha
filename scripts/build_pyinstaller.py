"""
PyInstaller 打包脚本
将 TiebaMecha 打包成单个可执行文件或目录
"""

import shutil
import subprocess
import sys
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).parent.parent


def build_pyinstaller():
    project_root = get_project_root()
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"

    # 清理旧的构建目录
    print("[1/5] 清理旧构建目录...")
    for d in [build_dir, dist_dir / "TiebaMecha"]:
        if d.exists():
            shutil.rmtree(d)
    dist_dir.mkdir(exist_ok=True)

    # 确保 PyInstaller 已安装
    print("[2/5] 检查依赖...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        check=True,
        capture_output=True,
    )

    # 创建入口脚本
    print("[3/5] 创建入口脚本...")
    entry_script = project_root / "build_entry.py"
    entry_script.write_text('''
import os
import sys
import logging

# 强制 UTF-8 编码
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

# 恢复标准日志级别名称
logging.addLevelName(logging.WARNING, "WARNING")

import flet as ft

# Uvicorn 日志级别补丁
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

_patch_uvicorn_log_level()

from tieba_mecha.web.app import TiebaMechaApp
from tieba_mecha.db.crud import get_db

async def main(page: ft.Page):
    """应用主函数"""
    app = TiebaMechaApp(page)
    db = await get_db()
    await app.initialize(db)

if __name__ == "__main__":
    port = 9006
    print("=" * 40)
    print("   TiebaMecha 启动中...")
    print("=" * 40)
    print(f"访问地址: http://localhost:{port}")

    _patch_uvicorn_log_level()

    try:
        if hasattr(ft, 'run'):
            try:
                ft.run(main, port=port, view=ft.AppView.WEB_BROWSER)
            except TypeError:
                ft.run(target=main, port=port, view=ft.AppView.WEB_BROWSER)
        else:
            ft.app(target=main, port=port, view=ft.AppView.WEB_BROWSER)
    except Exception as e:
        print(f"启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("按回车键退出...")
''', encoding="utf-8")

    # 构建 PyInstaller 命令
    print("[4/5] 执行 PyInstaller 打包（可能需要几分钟）...")

    # 收集 flet 的隐式依赖
    hidden_imports = [
        "--hidden-import", "flet",
        "--hidden-import", "flet.core",
        "--hidden-import", "flet_runtime",
        "--hidden-import", "uvicorn",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "aiotieba",
        "--hidden-import", "sqlalchemy.dialects.sqlite",
        "--hidden-import", "aiosqlite",
        "--hidden-import", "apscheduler",
        "--hidden-import", "apscheduler.schedulers.asyncio",
        "--hidden-import", "apscheduler.triggers.interval",
        "--hidden-import", "apscheduler.triggers.cron",
        "--hidden-import", "apscheduler.executors.pool",
        "--hidden-import", "typer",
        "--hidden-import", "typer.core",
        "--hidden-import", "cryptography",
        "--hidden-import", "cryptography.fernet",
        "--hidden-import", "aiohttp",
        "--hidden-import", "dotenv",
        "--hidden-import", "sqlite3",
        "--hidden-import", "logging.config",
        "--hidden-import", "PIL",
        "--hidden-import", "pydantic",
        "--hidden-import", "pydantic_core",
        "--hidden-import", "annotated_types",
    ]

    # 收集数据文件
    collect_all = [
        "--collect-all", "flet",
        "--collect-all", "flet_runtime",
        "--collect-all", "aiotieba",
    ]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "TiebaMecha",
        "--noconfirm",
        "--clean",
        # "--onefile",  # 注释掉以生成目录（更快，更稳定），取消注释生成单文件
        "--windowed",  # 无控制台窗口
        "--add-data", f"{project_root / 'src' / 'tieba_mecha'};tieba_mecha",
        *hidden_imports,
        *collect_all,
        str(entry_script),
    ]

    result = subprocess.run(cmd, cwd=project_root)
    if result.returncode != 0:
        print(f"PyInstaller 打包失败，退出码: {result.returncode}")
        return

    # 复制必要文件
    print("[5/5] 复制配置文件...")
    output_dir = dist_dir / "TiebaMecha"
    for item in [".env.example"]:
        src = project_root / item
        if src.exists():
            shutil.copy2(src, output_dir / item)

    # 创建 data 目录
    (output_dir / "data").mkdir(exist_ok=True)

    # 清理临时文件
    entry_script.unlink()
    spec_file = project_root / "TiebaMecha.spec"
    if spec_file.exists():
        spec_file.unlink()

    print(f"\n[DONE] 打包完成!")
    print(f"   输出目录: {output_dir}")
    print(f"   可执行文件: {output_dir / 'TiebaMecha.exe'}")

    # 计算大小
    total_size = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())
    print(f"   总大小: {total_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    build_pyinstaller()
