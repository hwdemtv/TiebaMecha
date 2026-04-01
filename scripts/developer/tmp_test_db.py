import asyncio
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.append(str(Path(__file__).parent.parent / "src"))

async def test_db():
    from tieba_mecha.db.crud import Database
    from tieba_mecha.db.models import Account
    
    db_path = Path(__file__).parent / "test_mecha.db"
    if db_path.exists(): db_path.unlink()
    
    db = Database(db_path)
    await db.init_db()
    print("Database initialized.")
    
    # 验证字段是否存在
    async with db.async_session() as session:
        from sqlalchemy import select
        acc = Account(name="test", bduss="xxx", user_id=1)
        session.add(acc)
        await session.commit()
        
        result = await session.execute(select(Account).where(Account.name == "test"))
        acc_db = result.scalar_one()
        print(f"Account status: {acc_db.status}") # 默认应该是 unknown
        print(f"Account last_verified: {acc_db.last_verified}")
        
    await db.close()
    if db_path.exists(): db_path.unlink()

if __name__ == "__main__":
    asyncio.run(test_db())
