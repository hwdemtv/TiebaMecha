import asyncio
import sys
import os

# 将 src 添加到路径
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from tieba_mecha.core.ai_optimizer import AIOptimizer
from unittest.mock import AsyncMock, patch

async def verify():
    print("=== 开始 AI SEO 优化功能验证 ===")
    
    # 模拟数据库
    db = AsyncMock()
    db.get_setting.side_effect = lambda k, d: {
        "ai_api_key": "test_key",
        "ai_base_url": "https://api.test.com",
        "ai_model": "test-model"
    }.get(k, d)

    optimizer = AIOptimizer(db)
    
    # 验证配置读取
    config = await optimizer._get_config()
    print(f"[1] 配置读取验证: {config}")
    assert config["api_key"] == "test_key"

    # 验证优化请求 (Mock 掉网络)
    print("\n[2] 模拟优请求...")
    mock_resp_json = {
        "choices": [{
            "message": {
                "content": '{"title": "【SEO 优化】机甲风帖", "content": "这是优化后的机甲风内容。"}'
            }
        }]
    }

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = mock_resp_json
        mock_post.return_value.__aenter__.return_value = mock_resp

        success, title, content, err = await optimizer.optimize_post("原标题", "原内容")
        
        if success:
            print(f"  ✓ 优化成功")
            print(f"  优化标题: {title}")
            print(f"  优化内容: {content}")
        else:
            print(f"  ✗ 优化失败: {err}")

    print("\n=== 功能验证完成 ===")

if __name__ == "__main__":
    asyncio.run(verify())
