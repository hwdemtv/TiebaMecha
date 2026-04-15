import asyncio
import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

from tieba_mecha.db.crud import get_db
from tieba_mecha.core.client_factory import create_client
import aiotieba

async def diagnose(bduss, stoken):
    print(f"--- 贴吧账号深度属性排查 ---")
    db = await get_db()
    
    try:
        async with await create_client(db, bduss, stoken) as client:
            user_info = await client.get_self_info()
            if not user_info:
                print("[-] 无法获取用户信息")
                return
            
            print(f"[+] 用户: {user_info.nick_name} (UID: {user_info.user_id})")
            
            # 重点排查字段
            target_fields = ['is_blocked', 'is_ban', 'is_mask', 'user_status', 'is_login']
            for f in target_fields:
                val = getattr(user_info, f, "NotFound")
                print(f"    - {f}: {val}")
            
            print("\n[Step 2] 模拟核心权限动作：获取 TBS 令牌...")
            # TBS 是所有发帖/点赞动作的基础，封禁账号通常无法获取有效的 TBS
            try:
                # 在 MechaClient 中，tbs 应该随 client 加载
                # aiotieba 的 Account 对象有 tbs 属性
                tbs = await client.get_tbs()
                print(f"[+] TBS 获取成功: {tbs}")
            except Exception as e:
                print(f"[-] TBS 获取失败: {e}")

            print("\n[Step 3] 模拟发帖前置动作：获取贴吧基础信息 (get_forum)...")
            try:
                # 尝试访问一个贴吧的信息
                forum = await client.get_forum("2012")
                if forum:
                    print(f"[+] 成功访问 2012 吧 (FID: {forum.fid})")
                    # 检查在这个吧的个人权限
                    print(f"    - 吧内等级: {getattr(forum, 'level', 'N/A')}")
                    # 注意：有些全吧封禁在单个贴吧内可能显示 level，但无法发帖
                else:
                    print(f"[-] 无法获取贴吧信息")
            except Exception as e:
                print(f"[-] 获取贴吧信息异常: {e}")

    except Exception as e:
        print(f"[-] 异常: {e}")

if __name__ == "__main__":
    BDUSS = "1DRlVsbFZ1MUlYRTZ4c1dUZ2xSVk5TUzFPYnJufmpGcUw4ZTZTUUctQVRkeHBwSUFBQUFBJCQAAAAAAQAAAAEAAAB4dyqeaHdkZW10djAyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABPq8mgT6vJoTy"
    STOKEN = "871fc408e941893e6189ad48cc0e0494e77df3e787bc94a994a004b0ca6ed637"
    asyncio.run(diagnose(BDUSS, STOKEN))
