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

import uvicorn.config as _uvc
_orig_configure_logging = _uvc.Config.configure_logging
def _patched_configure_logging(self):
    """修复：将 uvicorn 接收到的 'warn' 级别名称替换为合法的 'warning'"""
    if getattr(self, "log_level", None) == "warn":
        self.log_level = "warning"
    return _orig_configure_logging(self)
_uvc.Config.configure_logging = _patched_configure_logging

# 添加 src 到路径
sys.path.insert(0, "src")

import flet as ft
from tieba_mecha.web.app import TiebaMechaApp, get_db


async def main(page: ft.Page):
    """Flet 应用主函数"""
    app = TiebaMechaApp(page)
    db = await get_db()
    await app.initialize(db)

def run_app(port: int = 9006):
    """启动 Flet 应用，已在 app.py 中通过 logging.addLevelName 修复 uvicorn 日志级别问题"""
    ft.app(
        target=main,
        port=port,
        view=ft.AppView.WEB_BROWSER,
    )


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