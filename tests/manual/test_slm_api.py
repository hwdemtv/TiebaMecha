import asyncio
import os
import sys

# 确保能引入 src 下的模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tieba_mecha.core.link_manager import SmartLinkConnector

async def test_api_integration():
    api_key = os.environ.get("SLM_API_KEY", "")
    if not api_key:
        print("❌ 请先设置环境变量 SLM_API_KEY")
        return

    class MockDB:
        """模拟数据库，用于单元测试注入配置"""
        async def get_setting(self, key, default=""):
            if key == "slm_api_url":
                return "https://s.hubinwei.top"
            if key == "slm_api_key":
                # 从环境变量注入，勿硬编码
                return api_key
            return default

    db = MockDB()
    connector = SmartLinkConnector(db)
    
    print("🚀 [1/2] 正在测试 API 连通性与握手...")
    success, msg = await connector.test_connection()
    
    if success:
        print(f"✅ 握手成功! 返回信息: {msg}\n")
    else:
        print(f"❌ 握手失败! 错误原因: {msg}\n")
        return

    print("🚀 [2/2] 正在拉取用户的活动短链和 SEO 标题...")
    links = await connector.get_active_shortlinks()
    print(f"✅ 成功从公网同步了 {len(links)} 个短链节点!\n")
    
    # 打印前 3 条作为样本
    print("--- 短链资产样本 (Top 3) ---")
    for i, link in enumerate(links[:3]):
        short_code = link.get('shortCode')
        original = link.get('originalUrl', '')
        seo_title = link.get('seoTitle', '未设置')
        
        # 截断过长的 URL
        if len(original) > 40:
            original = original[:37] + "..."
            
        print(f"[{i+1}] 短码: {short_code}")
        print(f"    原始链接: {original}")
        print(f"    SEO标题: {seo_title}")
        print("-" * 30)
        
    await connector.close()

if __name__ == "__main__":
    asyncio.run(test_api_integration())
