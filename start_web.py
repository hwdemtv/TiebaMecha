import sys
import os
import logging

# ┌─────────────────────────────────────────────────────────────────────┐
# │  修复 uvicorn KeyError: 'warn'                                      │
# │  根本原因：flet 将 log_level='warn' 传给 uvicorn，而 uvicorn 的     │
# │  LOG_LEVELS 字典只有 'warning' 键，不存在 'warn'。                  │
# │  解法：在 uvicorn 配置对象绑定前打猴子补丁，将 'warn' 替换为         │
# │  'warning'。此代码必须在 import flet 之前执行。                     │
# └─────────────────────────────────────────────────────────────────────┘
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
# [修复] 中文工作目录下 .pth 文件 GBK 解码崩溃：改用 PYTHONPATH 替代
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _src_dir not in os.environ.get("PYTHONPATH", ""):
    os.environ["PYTHONPATH"] = _src_dir + os.pathsep + os.environ.get("PYTHONPATH", "")

# 提前注入 Flet Web 上传所需的密钥
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))
import secrets as _secrets
_flet_key = os.getenv("TIEBA_MECHA_SECRET_KEY") or os.getenv("FLET_SECRET_KEY")
if not _flet_key:
    _flet_key = _secrets.token_hex(32)
    print("[WARN] 未设置 TIEBA_MECHA_SECRET_KEY，已生成随机密钥（重启后失效，建议配置 .env）")
os.environ["FLET_SECRET_KEY"] = _flet_key

import uvicorn.config as _uvc
_orig_configure_logging = _uvc.Config.configure_logging
def _patched_configure_logging(self):
    """修复：将 uvicorn 接收到的 'warn' 级别名称替换为合法的 'warning'"""
    if getattr(self, "log_level", None) == "warn":
        self.log_level = "warning"
    return _orig_configure_logging(self)
_uvc.Config.configure_logging = _patched_configure_logging

# ┌─────────────────────────────────────────────────────────────────────┐
# │  修复 Flet 0.23.2 pubsub_hub "dictionary changed size during       │
# │  iteration" RuntimeError                                            │
# │  根本原因：PubSubHub.unsubscribe_all() 中直接迭代                    │
# │  self.__subscriber_topics[session_id].keys()，而迭代中的             │
# │  self.__unsubscribe_topic() 会删除同一个字典的键，导致 RuntimeError。│
# │  解法：用 list() 复制键再迭代。此补丁必须在 import flet 之后执行。   │
# └─────────────────────────────────────────────────────────────────────┘
import flet_core.pubsub.pubsub_hub as _psh
_orig_unsubscribe_all = _psh.PubSubHub.unsubscribe_all
def _patched_unsubscribe_all(self, session_id: str):
    """修复：先复制键列表再迭代，避免迭代中字典被修改"""
    import logging as _log
    _log.getLogger(__name__).debug(f"pubsub.unsubscribe_all({session_id})")
    with self._PubSubHub__lock:
        self._PubSubHub__unsubscribe(session_id)
        if session_id in self._PubSubHub__subscriber_topics:
            for topic in list(self._PubSubHub__subscriber_topics[session_id].keys()):
                self._PubSubHub__unsubscribe_topic(session_id, topic)
_psh.PubSubHub.unsubscribe_all = _patched_unsubscribe_all

# 智能路径检测：兼容开发版(src/)与便携版(root)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if os.path.exists(SRC_DIR):
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)
else:
    # 便携版环境下，直接将根目录加入路径
    if ROOT_DIR not in sys.path:
        sys.path.insert(0, ROOT_DIR)

import flet as ft
from dotenv import load_dotenv
from tieba_mecha.web.app import TiebaMechaApp
from tieba_mecha.db.crud import get_db

# 加载环境变量
env_path = os.path.join(ROOT_DIR, ".env")
load_dotenv(env_path)


async def main(page: ft.Page):
    """Flet 应用主函数"""
    app = TiebaMechaApp(page)
    db = await get_db()
    await app.initialize(db)

def run_app(port: int = 9006):
    """启动 Flet 应用"""
    # 确保上传目录存在，使用绝对路径避免工作目录问题
    upload_dir = os.path.abspath("uploads")
    os.makedirs(upload_dir, exist_ok=True)

    # 获取用于 Web 上传加密的密钥 (优先从环境变量读取，已在模块加载时设置)
    secret_key = os.getenv("FLET_SECRET_KEY") or os.getenv("TIEBA_MECHA_SECRET_KEY")
    if not secret_key:
        import secrets as _secrets2
        secret_key = _secrets2.token_hex(32)
        print("[WARN] 未设置 TIEBA_MECHA_SECRET_KEY，已生成随机密钥（重启后失效）")

    # 显式注入 Flet 要求的环境变量，这是解决 Web 上传报错最可靠的方法
    os.environ["FLET_SECRET_KEY"] = secret_key
    os.environ["FLET_UPLOAD_DIR"] = upload_dir

    # 构建应用参数
    app_kwargs = {
        "port": port,
        "view": ft.AppView.WEB_BROWSER,
        "upload_dir": upload_dir,
    }

    if hasattr(ft, 'run'):
        try:
            # 新版本: ft.run() 第一个参数是 target (位置参数)
            ft.run(main, **app_kwargs)
        except TypeError:
            # 旧版本: 所有参数都必须是关键字参数
            ft.run(target=main, **app_kwargs)
    else:
        # 更旧版本: 使用 ft.app()
        ft.app(target=main, **app_kwargs)


if __name__ == "__main__":
    # 检查是否有命令行参数指定端口
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
            print(f"使用指定端口: {port}")
        except ValueError:
            print(f"无效的端口号: {sys.argv[1]}，使用默认端口 9006")
            port = 9006
    else:
        port = 9006
    
    print("启动 TiebaMecha Web UI...")
    print(f"访问地址: http://localhost:{port}")
    print("按 Ctrl+C 停止应用")
    
    try:
        run_app(port=port)
    except KeyboardInterrupt:
        print("\n应用已停止")
    except Exception as e:
        print(f"启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("按任意键继续...")