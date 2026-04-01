"""Test account verification"""
import asyncio
import sys
sys.path.insert(0, 'src')

import aiotieba

async def test_login():
    print("测试账号登录...")

    # 你的凭证
    BDUSS = "5xd0txUmFHRkNmcHFqNXpVSFVJRWNxRTN5NWZUZm5nREtZTW4ySjFQbXdVdkZwSVFBQUFBJCQAAAAAAQAAAAEAAABOsJ-faHdkZW10djMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALDFyWmwxclpZk"
    STOKEN = "65dd288200e7a2fd0c1ea746562cdcd34f95d1e2d16ece1a37ad3c4c3908b7a3"

    print(f"BDUSS 长度: {len(BDUSS)}")
    print(f"STOKEN 长度: {len(STOKEN)}")

    try:
        async with aiotieba.Client(BDUSS=BDUSS, STOKEN=STOKEN) as client:
            print("\n尝试获取用户信息...")
            user = await client.get_self_info()
            if user:
                print(f"✓ 登录成功!")
                print(f"  用户ID: {user.user_id}")
                print(f"  用户名: {user.user_name}")
                print(f"  昵称: {user.nick_name}")
            else:
                print("✗ 用户信息为空")
    except Exception as e:
        print(f"✗ 登录失败: {type(e).__name__}: {e}")

    # 尝试无需登录的操作
    print("\n测试无需登录的操作...")
    try:
        async with aiotieba.Client() as client:
            threads = await client.get_threads("天堂鸡汤", pn=1, rn=3)
            print(f"✓ 获取帖子成功: {len(threads)} 条")
            if threads:
                print(f"  示例: {threads[0].title[:30]}...")
    except Exception as e:
        print(f"✗ 获取帖子失败: {e}")

if __name__ == "__main__":
    asyncio.run(test_login())
