import sys
import os
import asyncio

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tieba_mecha.core.ai_optimizer import AIOptimizer
from tieba_mecha.db.crud import Database

async def test_personas():
    db = Database("tieba_mecha.db")
    optimizer = AIOptimizer(db)
    
    title = "百度网盘资源分享"
    content = "这里有一份最新的电影资源，欢迎大家下载。链接：https://pan.baidu.com/s/123456"
    
    personas = ["normal", "resource_god", "casual", "newbie"]
    
    for p in personas:
        print(f"\n--- Testing Persona: {p} ---")
        success, opt_t, opt_c, err = await optimizer.optimize_post(title, content, persona=p)
        if success:
            print(f"Title: {opt_t}")
            print(f"Content: {opt_c}")
        else:
            print(f"Error: {err}")

if __name__ == "__main__":
    asyncio.run(test_personas())
