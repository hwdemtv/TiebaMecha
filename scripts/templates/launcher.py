"""
TiebaMecha Portable Launcher
该脚本用于在绿色便携版环境下初始化路径并启动 Web UI。
"""

import os
import sys
import logging
from pathlib import Path

# 1. 强制编码环境 (UTF-8 是一切稳定的基础)
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

# 2. 纠正根目录与系统路径
BASE_DIR = Path(__file__).parent.absolute()
# 加载环境变量 (优先从文件夹根目录中的 .env 读取)
from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

# 环境变量检查拦截 (防止因缺少密钥导致崩溃)
SALT = os.getenv("TIEBA_MECHA_SALT")
SECRET = os.getenv("TIEBA_MECHA_SECRET_KEY")

if not SALT or not SECRET:
    print("\n" + "!" * 60)
    print(" [警告] 系统安全凭证未配置！")
    print(" TiebaMecha 需要 TIEBA_MECHA_SALT 和 TIEBA_MECHA_SECRET_KEY 来加密账号凭据。")
    print("\n 💡 [解决办法]:")
    print(" 请先关闭本窗口，运行目录下的：")
    print("    >>> [首次运行(生成密钥).bat] <<<")
    print("\n 之后重新启动本程序将自动进入 Web 界面。")
    print("!" * 60 + "\n")
    input("按回车键退出程序...")
    sys.exit(1)

# 将 site-packages 额外加入路径 (双重保险)
RUNTIME_SP = BASE_DIR / "_runtime" / "site-packages"
if RUNTIME_SP.exists():
    sys.path.insert(0, str(RUNTIME_SP))

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))

# 3. 恢复标准日志级别名称 (兼容 aiotieba / flet 冲突)
logging.addLevelName(logging.WARNING, "WARNING")

# 4. Uvicorn & Flet 路径补丁
try:
    import uvicorn.config as _uvc
    _orig = _uvc.Config.configure_logging
    def _patched(self):
        if getattr(self, "log_level", None) == "warn":
            self.log_level = "warning"
        return _orig(self)
    _uvc.Config.configure_logging = _patched
except:
    pass

import flet as ft
from tieba_mecha.web.app import TiebaMechaApp
from tieba_mecha.db.crud import get_db

async def main(page: ft.Page):
    """应用主入口"""
    app = TiebaMechaApp(page)
    db = await get_db()
    await app.initialize(db)

if __name__ == "__main__":
    port = 9006
    print("=" * 50)
    print("   TiebaMecha Cyber-Mecha v1.1.0 [Portable]")
    print(f"   访问地址: http://localhost:{port}")
    print("=" * 50)
    
    try:
        # 兼容最新版 Flet API
        if hasattr(ft, 'run'):
            try:
                ft.run(main, port=port, view=ft.AppView.WEB_BROWSER)
            except TypeError:
                ft.run(target=main, port=port, view=ft.AppView.WEB_BROWSER)
        else:
            ft.app(target=main, port=port, view=ft.AppView.WEB_BROWSER)
    except KeyboardInterrupt:
        print("\n[系统] 用户请求关闭，程序安全退出。")
    except Exception as e:
        print(f"\n[错误] 程序运行崩溃: {e}")
        import traceback
        traceback.print_exc()
        input("\n按回车键退出...")
