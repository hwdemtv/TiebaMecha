import asyncio
import sys
import os

# 将 src 添加到路径
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
sys.path.insert(0, os.path.join(os.getcwd()))

from tieba_mecha.db.crud import get_db
from tieba_mecha.core.proxy import get_best_proxy_config
from tieba_mecha.core.auto_rule import apply_rules_to_threads
from tieba_mecha.core.plugin_loader import get_plugin_manager

class MockThread:
    def __init__(self, title, tid):
        self.title = title
        self.tid = tid

async def verify():
    print("=== 开始进阶功能验证 ===")
    try:
        db = await get_db()
        
        # 1. 验证代理
        print("\n[1] 验证代理池...")
        await db.add_proxy("1.2.3.4", 8080, protocol="http")
        proxy_config = await get_best_proxy_config(db)
        if proxy_config and "1.2.3.4:8080" in str(proxy_config.url):
            print(f"  ✓ 代理添加与获取成功: {proxy_config.url}")
        else:
            print("  ✗ 代理验证失败")

        # 2. 验证自动化规则
        print("\n[2] 验证自动化规则...")
        await db.add_auto_rule("test_ba", "keyword", "广告", "notify")
        
        mock_threads = [MockThread("这是一个广告帖子", 123), MockThread("正常帖子", 456)]
        # 这里我们只是运行一下，观察输出是否包含 notify
        print("  预期输出应包含 [AutoRule] 发现匹配帖子(监控)...")
        # 由于 apply_rules_to_threads 会尝试创建 client，可能会因为没有账号信息而失败，所以我们 mock 掉 creds
        from unittest.mock import patch
        with patch("tieba_mecha.core.auto_rule.get_account_credentials", return_value=("mock_bduss", "mock_stoken")):
            with patch("tieba_mecha.core.auto_rule.create_client") as mc:
                # 模拟一个 client 对象
                from unittest.mock import AsyncMock
                mc.return_value.__aenter__.return_value = AsyncMock()
                await apply_rules_to_threads(db, "test_ba", mock_threads)
        print("  ✓ 规则匹配逻辑调用完成")

        # 3. 验证插件系统
        print("\n[3] 验证插件中心...")
        manager = get_plugin_manager()
        # 强制设置 plugins 目录
        manager.plugins_dir = os.path.join(os.getcwd(), "plugins")
        loaded = manager.load_plugins()
        if "hello_mecha" in loaded:
            print(f"  ✓ 插件加载成功: {loaded}")
            result = await manager.run_plugin("hello_mecha", db=db)
            print(f"  ✓ 插件运行结果: {result}")
        else:
            print(f"  ✗ 插件加载失败，当前插件列表: {list(manager.plugins.keys())}")

        await db.close()
        print("\n=== 验证完成 ===")
    except Exception as e:
        print(f"\n[Error] 验证过程中发生异常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(verify())
