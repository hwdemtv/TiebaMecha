#!/usr/bin/env python3
"""
测试 uvicorn 日志级别修复
"""
import os
import sys

# 设置环境变量
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
os.environ["LOG_LEVEL"] = "warning"  # 关键修复：使用 'warning' 而不是 'warn'

# 测试 uvicorn 配置
try:
    import uvicorn
    import uvicorn.config
    
    print("=== 测试 uvicorn 日志级别 ===")
    
    # 测试不同的日志级别
    test_levels = ["critical", "error", "warning", "info", "debug", "trace"]
    
    for level in test_levels:
        try:
            # 创建配置对象测试
            config = uvicorn.Config(app=None, host="127.0.0.1", port=8000, log_level=level)
            print(f"✓ 日志级别 '{level}' 有效")
        except KeyError as e:
            print(f"✗ 日志级别 '{level}' 无效: {e}")
    
    print("\n=== 测试 flet 兼容性 ===")
    
    # 测试 flet 的日志级别处理
    import flet as ft
    
    # 创建一个简单的测试应用
    async def test_main(page: ft.Page):
        page.title = "测试应用"
        page.add(ft.Text("Hello from Flet!"))
    
    print("尝试启动测试应用（将在5秒后自动停止）...")
    
    # 在后台线程中启动应用
    import threading
    import time
    
    def run_test_app():
        try:
            ft.app(
                target=test_main,
                port=9007,
                view=ft.AppView.WEB_BROWSER,
                log_level="warning"  # 使用正确的日志级别
            )
        except Exception as e:
            print(f"启动失败: {e}")
    
    # 启动线程运行应用
    thread = threading.Thread(target=run_test_app, daemon=True)
    thread.start()
    
    # 等待5秒后停止
    time.sleep(5)
    print("\n测试完成！")
    
except ImportError as e:
    print(f"导入模块失败: {e}")
    print("请确保已安装所需依赖:")
    print("  pip install flet uvicorn")
except Exception as e:
    print(f"测试过程中出错: {e}")
    import traceback
    traceback.print_exc()

input("\n按 Enter 键退出...")