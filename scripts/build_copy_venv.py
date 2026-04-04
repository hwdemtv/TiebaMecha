"""
复制虚拟环境打包脚本
直接复制当前虚拟环境，确保版本完全一致
"""

import shutil
import sys
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).parent.parent


def build_copy_venv():
    project_root = get_project_root()
    dist_dir = project_root / "dist"
    portable_dir = dist_dir / "TiebaMecha_Portable"

    # 清理
    print("[1/4] 清理旧构建...")
    if portable_dir.exists():
        shutil.rmtree(portable_dir)
    dist_dir.mkdir(exist_ok=True)

    # 复制源代码
    print("[2/4] 复制源代码...")
    portable_dir.mkdir(parents=True)
    shutil.copytree(project_root / "src" / "tieba_mecha", portable_dir / "tieba_mecha")

    # 复制当前虚拟环境
    print("[3/4] 复制虚拟环境（这可能需要几分钟）...")
    venv_src = project_root / ".venv"
    venv_dst = portable_dir / "_python"
    shutil.copytree(venv_src, venv_dst)

    # 复制配置文件
    for item in [".env.example", "README.md"]:
        src = project_root / item
        if src.exists():
            shutil.copy2(src, portable_dir / item)

    # 创建启动脚本
    print("[4/4] 创建启动脚本...")
    launcher_py = portable_dir / "launcher.py"
    launcher_py.write_text('''
import os
import sys
import logging

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
logging.addLevelName(logging.WARNING, "WARNING")
sys.path.insert(0, os.getcwd())

import flet as ft

def _patch_uvicorn_log_level():
    try:
        import uvicorn.config as _uvc
        _orig = _uvc.Config.configure_logging
        def _patched(self):
            if getattr(self, "log_level", None) == "warn":
                self.log_level = "warning"
            return _orig(self)
        _uvc.Config.configure_logging = _patched
    except: pass

_patch_uvicorn_log_level()

from tieba_mecha.web.app import TiebaMechaApp
from tieba_mecha.db.crud import get_db

async def main(page: ft.Page):
    app = TiebaMechaApp(page)
    db = await get_db()
    await app.initialize(db)

if __name__ == "__main__":
    port = 9006
    print(f"访问地址: http://localhost:{port}")
    _patch_uvicorn_log_level()
    try:
        ft.run(main, port=port, view=ft.AppView.WEB_BROWSER)
    except TypeError:
        ft.run(target=main, port=port, view=ft.AppView.WEB_BROWSER)
''', encoding="utf-8")

    start_bat = portable_dir / "启动.bat"
    start_bat.write_text('''@echo off
chcp 65001 >nul
cd /d "%~dp0"
call _python\\Scripts\\activate.bat
python launcher.py
pause
''', encoding="utf-8")

    # 打包 ZIP
    print("\n[打包] 创建 ZIP...")
    zip_path = dist_dir / "TiebaMecha-Portable"
    shutil.make_archive(str(zip_path), "zip", dist_dir, portable_dir.name)

    total_size = sum(f.stat().st_size for f in portable_dir.rglob("*") if f.is_file())
    print(f"\n[DONE] 打包完成: {zip_path}.zip")
    print(f"大小: {total_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    build_copy_venv()
