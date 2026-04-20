"""测试存活统计数据"""
import asyncio
from tieba_mecha.db.crud import Database, DEFAULT_DB_PATH

async def test():
    print(f"数据库路径: {DEFAULT_DB_PATH}")
    print(f"数据库文件是否存在: {DEFAULT_DB_PATH.exists()}")
    
    db = Database()
    await db.init_db()
    
    from sqlalchemy import text
    from tieba_mecha.db.models import MaterialPool
    
    async with db.async_session() as session:
        # 统计总记录数
        result = await session.execute(text("SELECT COUNT(*) FROM material_pool"))
        print(f"material_pool 总记录数: {result.scalar()}")
        
        # 查看表结构
        result2 = await session.execute(text("PRAGMA table_info(material_pool)"))
        print("\n表结构:")
        for row in result2.all():
            print(f"  {row}")
    
    await db.close()

asyncio.run(test())
