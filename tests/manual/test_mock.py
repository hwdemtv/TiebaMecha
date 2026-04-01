"""Mock test for TiebaMecha core logic"""
import asyncio
import os
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from tieba_mecha.db.crud import get_db
from tieba_mecha.core import account, sign

async def test_all():
    print("=" * 60)
    print("TiebaMecha Mock 测试")
    print("=" * 60)

    # 1. 数据库初始化
    print("\n[1] 初始化数据库...")
    db = await get_db()
    await db.init_db()
    print("    ✓ 数据库初始化成功")

    # 2. 模拟环境变量
    print("\n[2] 设置模拟环境变量...")
    os.environ["TIEBA_MECHA_SALT"] = "test_salt"
    os.environ["TIEBA_MECHA_SECRET_KEY"] = "test_secret"
    print("    ✓ 环境变量设置成功")

    # 3. 添加模拟账号
    print("\n[3] 添加模拟账号...")
    test_bduss = "mock_bduss_12345"
    test_name = "Mock助手"
    
    acc_info = await account.add_account(db, test_name, test_bduss)
    print(f"    ✓ 账号添加成功: {acc_info.name} (ID: {acc_info.id})")

    # 4. 验证加密一致性
    print("\n[4] 验证加密一致性...")
    creds = await account.get_account_credentials(db, acc_info.id)
    if creds and creds[0] == test_bduss:
        print("    ✓ 加密/解密结果匹配")
    else:
        print(f"    ✗ 加密/解密结果不匹配: {creds[0] if creds else 'None'} != {test_bduss}")

    # 5. 测试原子切换活跃账号
    print("\n[5] 测试原子切换活跃账号...")
    # 再加一个账号
    await account.add_account(db, "Mock账户2", "bduss2")
    accounts = await db.get_accounts()
    print(f"    当前账号总数: {len(accounts)}")
    
    target_id = accounts[-1].id
    await account.switch_account(db, target_id)
    active = await db.get_active_account()
    if active and active.id == target_id:
        print(f"    ✓ 活跃账号切换成功: {active.name}")
    else:
        print("    ✗ 活跃账号切换失败")

    # 6. 测试设置存储 (Settings Migration)
    print("\n[6] 测试设置存储 (Settings Migration)...")
    test_key = "schedule"
    test_val = '{"sign_time": "09:30", "enabled": true}'
    await db.set_setting(test_key, test_val)
    retrieved = await db.get_setting(test_key)
    if retrieved == test_val:
        print("    ✓ 设置保存与读取成功")
    else:
        print(f"    ✗ 设置读取不匹配: {retrieved}")

    # 7. 测试贴吧去重逻辑
    print("\n[7] 测试贴吧去重逻辑...")
    await db.add_forum(fid=123, fname="测试贴吧", account_id=acc_info.id)
    # 再次添加相同的
    await db.add_forum(fid=123, fname="测试贴吧", account_id=acc_info.id)
    forums = await db.get_forums(acc_info.id)
    if len(forums) == 1:
        print("    ✓ 贴吧去重逻辑验证通过 (数量仍为 1)")
    else:
        print(f"    ✗ 贴吧去重逻辑失效 (数量为 {len(forums)})")

    # 关闭
    await db.close()
    
    print("\n" + "=" * 60)
    print("Mock 测试完成!")
    print("=" * 60)

if __name__ == "__main__":
    # 使用独立的临时数据库进行测试
    test_db_path = Path("data/test_mock.db")
    if test_db_path.exists():
        test_db_path.unlink()
        
    asyncio.run(test_all())
