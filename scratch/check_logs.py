import asyncio
import os
import sys

# 将 src 添加到路径
sys.path.append(os.path.join(os.getcwd(), "src"))

from tieba_mecha.core.logger import get_recent_logs, log_info

async def test():
    # 先加一条测试日志
    await log_info("[BioWarming] Test maintenance log")
    logs = await get_recent_logs(50)
    print("Recent logs:")
    for l in logs:
        print(f"{l['time']} [{l['level']}] {l['message']}")
    
    bw_logs = [l for l in logs if "[BioWarming]" in l['message']]
    print(f"\nBioWarming logs found: {len(bw_logs)}")

if __name__ == "__main__":
    asyncio.run(test())
