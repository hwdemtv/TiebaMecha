import asyncio
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from tieba_mecha.db.crud import Database
from tieba_mecha.db.models import TargetPool

async def verify():
    db = Database()
    fname = "实时测试吧"
    
    # 1. 确保 TargetPool 中有该吧记录
    await db.upsert_target_pools([fname], "测试组")
    
    # 获取初始状态
    async with db.async_session() as session:
        from sqlalchemy import select
        res = await session.execute(select(TargetPool).where(TargetPool.fname == fname))
        pool = res.scalar()
        initial_count = pool.success_count or 0
        print(f"初始击穿数: {initial_count}")

    # 2. 模拟成功发帖统计更新
    print("--- 触发一次成功发帖统计更新 ---")
    await db.update_target_pool_status(fname, is_success=True)
    
    # 检查更新后状态
    async with db.async_session() as session:
        res = await session.execute(select(TargetPool).where(TargetPool.fname == fname))
        pool = res.scalar()
        new_count = pool.success_count or 0
        print(f"更新后击穿数: {new_count}")
        
    if new_count == initial_count + 1:
        print("[SUCCESS] 实时累加验证通过！")
    else:
        print(f"[FAILED] 验证失败！期望 {initial_count + 1}，实际 {new_count}")

    # 3. 模拟失败发帖
    print("--- 触发一次失败发帖 ---")
    await db.update_target_pool_status(fname, is_success=False, error_reason="模拟失败")
    
    async with db.async_session() as session:
        res = await session.execute(select(TargetPool).where(TargetPool.fname == fname))
        pool = res.scalar()
        print(f"失败后击穿数 (不应变化): {pool.success_count}")
        print(f"失败次数 (fail_count): {pool.fail_count}")
        
    if pool.success_count == new_count and pool.fail_count >= 1:
        print("[SUCCESS] 失败逻辑处理验证通过！")
    else:
        print("[FAILED] 失败逻辑验证失败！")

if __name__ == "__main__":
    asyncio.run(verify())
