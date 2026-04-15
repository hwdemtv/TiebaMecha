import asyncio
import sys
import os

# 将 src 目录加入路径
sys.path.append(os.path.join(os.getcwd(), "src"))

from tieba_mecha.db.crud import get_db
from tieba_mecha.core.client_factory import create_client
import aiotieba

async def diagnose(bduss, stoken):
    print(f"--- 贴吧账号深度诊断 (aiotieba v{aiotieba.__version__}) ---")
    db = await get_db()
    
    try:
        async with await create_client(db, bduss, stoken) as client:
            print("\n[Step 1] 获取个人基本信息 (get_self_info)...")
            user_info = await client.get_self_info()
            if not user_info:
                print("[-] 结果: 无法获取用户信息")
                return
            
            print(f"[+] 结果: 成功! UID: {user_info.user_id}, 昵称: {user_info.nick_name}")
            # 打印所有可用属性，看看封禁标记在哪里
            print(f"[DEBUG] UserInfo 属性列表: {dir(user_info)}")
            
            # 尝试查找 ban 相关字段
            ban_fields = [f for f in dir(user_info) if 'ban' in f.lower() or 'mask' in f.lower()]
            print(f"[DEBUG] 疑似封禁相关字段: {ban_fields}")
            for f in ban_fields:
                try: print(f"    - {f}: {getattr(user_info, f)}")
                except: pass

            print("\n[Step 2] 模拟业务请求：获取关注贴吧列表 (get_follow_forums)...")
            try:
                # 这是一个常用的业务接口，封禁账号通常在这里会返回错误
                result = await client.get_follow_forums(user_info.user_id, pn=1, rn=1)
                if result and hasattr(result, 'err') and result.err:
                    print(f"[-] 结果: 接口返回错误! Code: {getattr(result.err, 'code', 'N/A')}, Message: {getattr(result.err, 'msg', 'N/A')}")
                else:
                    print(f"[+] 结果: 成功获取列表，账号状态似乎正常。")
            except Exception as e:
                print(f"[-] 异常: {e}")

    except Exception as e:
        print(f"[-] 初始化/执行异常: {e}")

if __name__ == "__main__":
    BDUSS = "1DRlVsbFZ1MUlYRTZ4c1dUZ2xSVk5TUzFPYnJufmpGcUw4ZTZTUUctQVRkeHBwSUFBQUFBJCQAAAAAAQAAAAEAAAB4dyqeaHdkZW10djAyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABPq8mgT6vJoTy"
    STOKEN = "871fc408e941893e6189ad48cc0e0494e77df3e787bc94a994a004b0ca6ed637"
    asyncio.run(diagnose(BDUSS, STOKEN))
