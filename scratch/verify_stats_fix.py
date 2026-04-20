import asyncio
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from tieba_mecha.db.crud import Database
from tieba_mecha.db.models import MaterialPool, TargetPool, BatchPostLog

async def verify():
    db = Database()
    async with db.async_session() as session:
        # 1. 测试击穿数回填
        # 先创建一个模拟的成功日志
        log = BatchPostLog(
            task_id="test_task",
            fname="测试吧",
            status="success",
            title="测试标题",
            tid=12345
        )
        session.add(log)
        await session.commit()
        
        print("--- 正在执行回填 ---")
        updated = await db.backfill_success_count()
        print(f"回填结果: {updated} 条记录")
        
        # 2. 测试被删数统计
        # 创建模拟的被删物料
        m1 = MaterialPool(
            title="被删1",
            content="内容",
            status="success",
            survival_status="dead",
            death_reason="deleted_by_mod",
            posted_fname="测试吧",
            posted_tid=111
        )
        m2 = MaterialPool(
            title="被删2",
            content="内容",
            status="success",
            survival_status="dead",
            death_reason="deleted_by_system",
            posted_fname="测试吧",
            posted_tid=222
        )
        session.add_all([m1, m2])
        await session.commit()
        
        print("--- 正在获取统计 ---")
        stats = await db.get_forum_matrix_stats()
        
        for item in stats:
            if item['fname'] == "测试吧":
                print(f"贴吧: {item['fname']}")
                print(f"击穿数 (success_count): {item['success_count']}")
                print(f"被删数 (deleted_count): {item['deleted_count']}")
                
                # 断言
                if item['success_count'] >= 1 and item['deleted_count'] == 2:
                    print("✅ 验证通过！")
                else:
                    print("❌ 验证失败！")
        
        # 清理模拟数据 (可选)
        # await session.delete(log)
        # await session.delete(m1)
        # await session.delete(m2)
        # await session.commit()

if __name__ == "__main__":
    asyncio.run(verify())
