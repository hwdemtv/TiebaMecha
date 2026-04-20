"""快速诊断脚本：检查 target_pool 和 batch_post_logs 的击穿数数据"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["TIEBA_MECHA_SALT"] = "a" * 64
os.environ["TIEBA_MECHA_SECRET_KEY"] = "b" * 64


async def main():
    from tieba_mecha.db.crud import Database
    from tieba_mecha.db.models import TargetPool, BatchPostLog
    from sqlalchemy import select, func, text

    db = Database()
    await db.init_db()

    async with db.async_session() as session:
        # 1. 检查 batch_post_logs 中有多少 success 记录
        result = await session.execute(
            select(BatchPostLog.fname, func.count(BatchPostLog.id).label("cnt"))
            .where(BatchPostLog.status == "success")
            .group_by(BatchPostLog.fname)
        )
        log_stats = {row.fname: row.cnt for row in result.all()}
        print(f"\n=== BatchPostLog 中 success 记录 ===")
        if log_stats:
            for fname, cnt in sorted(log_stats.items(), key=lambda x: -x[1]):
                print(f"  {fname}: {cnt} 条")
        else:
            print("  (无数据)")

        # 2. 检查 target_pool 中的 success_count
        result = await session.execute(select(TargetPool))
        pools = result.scalars().all()
        print(f"\n=== TargetPool 击穿数 ===")
        if pools:
            for p in pools:
                match = "✅" if p.success_count > 0 else "❌"
                log_cnt = log_stats.get(p.fname, 0)
                mismatch = " ⚠️ 与日志不一致!" if log_cnt > 0 and p.success_count == 0 else ""
                print(f"  {match} {p.fname}: success_count={p.success_count}, fail_count={p.fail_count}, is_active={p.is_active} (日志success={log_cnt}){mismatch}")
        else:
            print("  (target_pool 为空)")

        # 3. 检查 batch_post_logs 总量
        result = await session.execute(
            select(BatchPostLog.status, func.count(BatchPostLog.id))
            .group_by(BatchPostLog.status)
        )
        print(f"\n=== BatchPostLog 状态统计 ===")
        for status, cnt in result.all():
            print(f"  {status}: {cnt} 条")

        # 4. 尝试执行 backfill 并查看效果
        print(f"\n=== 执行 backfill_success_count ===")
        updated = await db.backfill_success_count()
        print(f"  更新了 {updated} 条记录")

        # 5. 再次检查
        result = await session.execute(select(TargetPool))
        pools = result.scalars().all()
        print(f"\n=== Backfill 后 TargetPool 击穿数 ===")
        for p in pools:
            match = "✅" if p.success_count > 0 else "❌"
            print(f"  {match} {p.fname}: success_count={p.success_count}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
