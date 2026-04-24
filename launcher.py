import sys
import os
import socket
import logging
import asyncio

# ┌─────────────────────────────────────────────────────────────────────┐
# │  修复 uvicorn KeyError: 'warn'                                      │
# └─────────────────────────────────────────────────────────────────────┘
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

# 提前注入 Flet Web 上传及双模运行所需的密钥
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))
SECRET_KEY = os.getenv("TIEBA_MECHA_SECRET_KEY") or os.getenv("FLET_SECRET_KEY") or "cyber_mecha_dual_launcher_999"
os.environ["FLET_SECRET_KEY"] = SECRET_KEY

import uvicorn.config as _uvc
_orig_configure_logging = _uvc.Config.configure_logging
def _patched_configure_logging(self):
    if getattr(self, "log_level", None) == "warn":
        self.log_level = "warning"
    return _orig_configure_logging(self)
_uvc.Config.configure_logging = _patched_configure_logging

# ┌─────────────────────────────────────────────────────────────────────┐
# │  修复 Flet 0.23.2 pubsub_hub "dictionary changed size during       │
# │  iteration" RuntimeError                                            │
# └─────────────────────────────────────────────────────────────────────┘
import flet_core.pubsub.pubsub_hub as _psh
_orig_unsubscribe_all = _psh.PubSubHub.unsubscribe_all
def _patched_unsubscribe_all(self, session_id: str):
    import logging as _log
    _log.getLogger(__name__).debug(f"pubsub.unsubscribe_all({session_id})")
    with self._PubSubHub__lock:
        self._PubSubHub__unsubscribe(session_id)
        if session_id in self._PubSubHub__subscriber_topics:
            for topic in list(self._PubSubHub__subscriber_topics[session_id].keys()):
                self._PubSubHub__unsubscribe_topic(session_id, topic)
_psh.PubSubHub.unsubscribe_all = _patched_unsubscribe_all

# 智能路径检测：兼容开发版(src/)与便携版(root)
SRC_DIR = os.path.join(ROOT_DIR, "src")
if os.path.exists(SRC_DIR):
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)
else:
    # 便携版环境下，直接将根目录加入路径
    if ROOT_DIR not in sys.path:
        sys.path.insert(0, ROOT_DIR)

import flet as ft
from tieba_mecha.web.app import TiebaMechaApp
from tieba_mecha.db.crud import get_db

def get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

async def main(page: ft.Page):
    """Flet 应用主函数"""
    app = TiebaMechaApp(page)
    db = await get_db()
    await app.initialize(db)

def run_app(port: int = 9006):
    """启动“桌面窗口+Web 模式”双模应用"""
    upload_dir = os.path.abspath("uploads")
    os.makedirs(upload_dir, exist_ok=True)

    local_ip = get_local_ip()
    print("\n" + "="*50)
    print(" 贴吧机甲 (TiebaMecha) 双模启动器已就绪")
    print("="*50)
    print(f" [+] 本地控制：已弹出独立桌面窗口")
    print(f" [+] 网页/远程：http://localhost:{port}")
    print(f" [+] 局域网访问：http://{local_ip}:{port}")
    print("="*50)
    print(" [提示] 桌面窗口模式下载入文件无需等待上传，速度极快。")
    print(" [注意] 请勿关闭此黑窗口，否则程序将彻底退出。\n")

    # 同时开启窗口模式和网页服务
    # host="0.0.0.0" 允许手机/局域网访问
    ft.app(
        target=main,
        port=port,
        host="0.0.0.0",
        view=ft.AppView.FLET_APP,  # 开启桌面窗口
        upload_dir=upload_dir,
        assets_dir="assets"
    )

if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            port = 9006
    else:
        port = 9006
    
    try:
        run_app(port=port)
    except KeyboardInterrupt:
        print("\n应用已通过 Ctrl+C 停止")
    except Exception as e:
        print(f"启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("按任意键退出...")
