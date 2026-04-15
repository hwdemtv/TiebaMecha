import asyncio
import sys
import os
from aiotieba import Client

async def diagnose(bduss, stoken):
    print("--- 贴吧账号封禁深度诊断程序 ---")
    print(f"BDUSS: {bduss[:10]}...{bduss[-10:]}")
    
    async with Client(bduss, stoken) as client:
        print("\n[Step 1] 获取个人基本信息 (get_self_info)...")
        try:
            user_info = await client.get_self_info()
            if not user_info:
                print("[-] 结果: 无法获取用户信息")
            else:
                print(f"[+] 结果: 成功! UID: {user_info.user_id}, 昵称: {user_info.nick_name}")
                print(f"    - is_mask: {getattr(user_info, 'is_mask', 'N/A')}")
                print(f"    - is_ban: {getattr(user_info, 'is_ban', 'N/A')}")
                print(f"    - is_admin: {getattr(user_info, 'is_admin', 'N/A')}")
        except Exception as e:
            print(f"[-] 异常: {e}")

        print("\n[Step 2] 模拟实战操作：尝试获取‘2012’吧签到信息...")
        try:
            # 获取签到信息需要有效的账号权限，封禁账号通常会在这里报错
            sign_info = await client.get_f_sign_info("2012")
            if sign_info:
                print(f"[+] 结果: 成功! 当前等级: {sign_info.level}")
            else:
                print("[-] 结果: 获取失败，可能已被封禁。")
        except Exception as e:
            print(f"[-] 异常: {e} (这通常意味着账号已被拦截)")

        print("\n[Step 3] 尝试获取 TBS (发帖必备令牌)...")
        try:
            tbs = await client.get_tbs()
            print(f"[+] 结果: 成功! TBS: {tbs}")
        except Exception as e:
            print(f"[-] 异常: {e}")

    print("\n诊断完成。")

if __name__ == "__main__":
    # 使用用户提供的凭证
    BDUSS = "1DRlVsbFZ1MUlYRTZ4c1dUZ2xSVk5TUzFPYnJufmpGcUw4ZTZTUUctQVRkeHBwSUFBQUFBJCQAAAAAAQAAAAEAAAB4dyqeaHdkZW10djAyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABPq8mgT6vJoTy"
    STOKEN = "871fc408e941893e6189ad48cc0e0494e77df3e787bc94a994a004b0ca6ed637"
    
    asyncio.run(diagnose(BDUSS, STOKEN))
