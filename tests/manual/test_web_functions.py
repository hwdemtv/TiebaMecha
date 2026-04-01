"""Test Web UI functionality directly"""
import asyncio
import sys
sys.path.insert(0, 'src')

from tieba_mecha.db.crud import Database, get_db
from tieba_mecha.core import account, sign, post, crawl

async def test_web_functions():
    print("=" * 60)
    print("Web UI 功能模拟测试")
    print("=" * 60)

    # 初始化数据库
    print("\n[初始化数据库]")
    db = await get_db()
    print("✓ 数据库初始化成功")

    # 测试账号验证
    print("\n[测试账号验证]")
    BDUSS = "5xd0txUmFHRkNmcHFqNXpVSFVJRWNxRTN5NWZUZm5nREtZTW4ySjFQbXdVdkZwSVFBQUFBJCQAAAAAAQAAAAEAAABOsJ-faHdkZW10djMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALDFyWmwxclpZk"
    STOKEN = "65dd288200e7a2fd0c1ea746562cdcd34f95d1e2d16ece1a37ad3c4c3908b7a3"

    print("验证账号中...")
    valid, user_id, user_name = await account.verify_account(BDUSS, STOKEN)
    if valid:
        print(f"✓ 验证成功: {user_name} (ID: {user_id})")
    else:
        print("✗ 验证失败")
        return

    # 测试添加账号
    print("\n[测试添加账号]")
    acc = await account.add_account(db, "测试账号", BDUSS, STOKEN)
    await db.update_account(acc.id, user_id=user_id, user_name=user_name)
    print(f"✓ 添加账号成功: ID={acc.id}")

    # 测试获取账号列表
    print("\n[测试获取账号列表]")
    accounts = await account.list_accounts(db)
    for a in accounts:
        print(f"  - {a.name} (active={a.is_active})")

    # 测试同步贴吧
    print("\n[测试同步贴吧]")
    added = await sign.sync_forums_to_db(db)
    print(f"✓ 同步完成，新增 {added} 个贴吧")

    # 测试获取签到统计
    print("\n[测试签到统计]")
    stats = await sign.get_sign_stats(db)
    print(f"  总数: {stats['total']}, 已签: {stats['signed']}, 未签: {stats['unsigned']}")

    # 测试获取贴吧列表
    print("\n[测试获取贴吧列表]")
    forums = await db.get_forums()
    for f in forums[:5]:
        print(f"  - {f.fname} (连续签到: {f.sign_count}天)")
    if len(forums) > 5:
        print(f"  ... 还有 {len(forums)-5} 个")

    # 测试签到单个贴吧
    print("\n[测试签到单个贴吧]")
    if forums:
        result = await sign.sign_forum(db, forums[0].fname)
        print(f"  {forums[0].fname}: {'成功' if result.success else result.message}")

    # 测试获取帖子
    print("\n[测试获取帖子]")
    threads = await post.get_threads(db, "天堂鸡汤", pn=1, rn=3)
    print(f"✓ 获取 {len(threads)} 条帖子")
    for t in threads[:3]:
        print(f"  - {t.title[:30]}...")

    # 关闭数据库
    await db.close()

    print("\n" + "=" * 60)
    print("所有测试完成!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_web_functions())
