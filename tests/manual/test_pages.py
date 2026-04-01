import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock

# 模拟 flet
sys.modules['flet'] = MagicMock()
import flet as ft

# 将 src 添加到路径
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from tieba_mecha.web.app import TiebaMechaApp

def test():
    print("=== 开始页面构建单元测试 ===")
    page = MagicMock(spec=ft.Page)
    app = TiebaMechaApp(page)
    app.db = MagicMock()
    app.db.get_forums = AsyncMock(return_value=[])
    app.db.get_crawl_history = AsyncMock(return_value=[])
    
    pages = ["dashboard", "accounts", "sign", "posts", "crawl", "settings"]
    
    for p in pages:
        print(f"正在构建页面: {p}...")
        try:
            if p == "dashboard":
                app._build_dashboard()
            elif p == "accounts":
                app._build_accounts_page()
            elif p == "sign":
                app._build_sign_page()
            elif p == "posts":
                app._build_posts_page()
            elif p == "crawl":
                app._build_crawl_page()
            elif p == "settings":
                app._build_settings_page()
            print(f"  ✓ {p} 构建成功")
        except Exception as e:
            print(f"  ✗ {p} 构建失败: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    test()
