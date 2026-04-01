"""Test all TiebaMecha functionality"""
import asyncio
import sys
sys.path.insert(0, 'src')

from tieba_mecha.db.crud import Database, get_db
from tieba_mecha.core import account, sign, post, crawl

async def test_all():
    print("=" * 60)
    print("TiebaMecha 功能测试")
    print("=" * 60)

    # 初始化数据库
    print("\n[1] 初始化数据库...")
    db = await get_db()
    print("    ✓ 数据库初始化成功")

    # 测试账号管理
    print("\n[2] 测试账号管理...")
    accounts = await account.list_accounts(db)
    print(f"    当前账号数量: {len(accounts)}")

    if accounts:
        active = await db.get_active_account()
        if active:
            print(f"    当前活跃账号: {active.name}")

            # 测试获取凭证
            creds = await account.get_account_credentials(db)
            if creds:
                print(f"    ✓ 获取凭证成功")
            else:
                print(f"    ✗ 获取凭证失败")
        else:
            print("    ! 没有活跃账号")
    else:
        print("    ! 没有添加账号，跳过账号相关测试")
        print("    提示: 请通过 Web UI 添加账号后再测试")
        return

    # 测试签到功能
    print("\n[3] 测试签到功能...")
    try:
        forums = await db.get_forums()
        print(f"    已关注贴吧数量: {len(forums)}")

        stats = await sign.get_sign_stats(db)
        print(f"    签到统计: 总数={stats['total']}, 已签={stats['signed']}, 未签={stats['unsigned']}")

        if forums:
            print("    ✓ 签到功能正常")
        else:
            print("    ! 没有关注的贴吧，请先同步")
    except Exception as e:
        print(f"    ✗ 签到测试失败: {e}")

    # 测试帖子管理
    print("\n[4] 测试帖子管理...")
    try:
        # 测试获取帖子
        threads = await post.get_threads(db, "天堂鸡汤", pn=1, rn=5)
        print(f"    获取帖子: {len(threads)} 条")
        if threads:
            print(f"    示例: {threads[0].title[:30]}...")
        print("    ✓ 帖子管理功能正常")
    except Exception as e:
        print(f"    ✗ 帖子测试失败: {e}")

    # 测试数据爬取
    print("\n[5] 测试数据爬取...")
    try:
        history = await crawl.get_crawl_history(db, limit=5)
        print(f"    爬取历史记录: {len(history)} 条")
        print("    ✓ 爬取功能正常")
    except Exception as e:
        print(f"    ✗ 爬取测试失败: {e}")

    # 关闭数据库
    await db.close()

    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_all())
