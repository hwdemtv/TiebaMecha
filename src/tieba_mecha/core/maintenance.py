"""BioWarming - Account Maintenance Engine (拟人化养号引擎)
通过模拟真人浏览、随性点赞等行为，在百度大数据模型中建立健康的活跃画像。
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from aiotieba.logging import get_logger

from ..db.crud import Database
from .account import get_account_credentials
from .client_factory import create_client
from .logger import log_info, log_warn, log_error

logger = get_logger()

class MaintManager:
    """养号维护管理器"""

    def __init__(self, db: Database):
        self.db = db

    async def run_maint_cycle(self, account_id: Optional[int] = None) -> bool:
        """运行一次养号维护循环"""
        
        # 1. 获取账号凭证
        creds = await get_account_credentials(self.db, account_id)
        if not creds:
            await log_warn(f"[BioWarming] 账号 {account_id if account_id else '当前活跃'} 凭证获取失败，跳过维护")
            return False

        acc_id, bduss, stoken, proxy_id, cuid, ua = creds
        account_name = f"ID:{acc_id}"

        try:
            async with await create_client(self.db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
                # 步骤 A: 模拟兴趣探测 (获取关注列表并随机进吧)
                await log_info(f"[BioWarming] 启动维护协议: {account_name}...")
                
                # 获取自我信息以获取 ID
                self_info = await client.get_self_info()
                if not self_info:
                    return False
                
                # 获取关注列表 (随机取一页)
                forums_res = await client.get_follow_forums(self_info.user_id, pn=random.randint(1, 3))
                if not forums_res.objs:
                    await log_warn(f"[BioWarming] {account_name} 关注列表为空，无法执行模拟浏览")
                    return False

                # 随机选一个吧进行浏览
                target_forum = random.choice(forums_res.objs)
                await log_info(f"[BioWarming] {account_name} 模拟进入 [{target_forum.fname}] 进行深层浏览...")
                
                # 获取主题帖列表 (模拟翻页看帖)
                threads = await client.get_threads(target_forum.fname, pn=1)
                
                # 拟真停顿：模拟阅读标题
                await self._human_sleep(5, 12)
                
                if threads.objs:
                    # 随机深入一个帖子
                    target_thread = random.choice(threads.objs[:10])
                    await log_info(f"[BioWarming] {account_name} 正在阅读帖子: {target_thread.title[:20]}...")
                    
                    # 获取帖子内容
                    await client.get_posts(target_thread.tid, pn=1)
                    
                    # 拟真停顿：模拟深度阅读
                    await self._human_sleep(10, 25)

                    # 步骤 B: 随机互动 (点赞)
                    if random.random() < 0.6:  # 60% 概率产生互动
                        await log_info(f"[BioWarming] {account_name} 觉得不错，随手点了一个赞。")
                        await client.agree(target_thread.tid)
                        await self._human_sleep(2, 5)

                # 记录成功维护
                await self.db.update_maint_status(acc_id)
                
                await log_info(f"[BioWarming] {account_name} 维护周期结束 | 权重负载均衡中...")
                return True

        except Exception as e:
            await log_error(f"[BioWarming] 维护过程遭遇背刺: {str(e)}")
            return False

    async def _human_sleep(self, min_s: float, max_s: float):
        """模拟真人的变频停顿"""
        sleep_time = random.uniform(min_s, max_s)
        await asyncio.sleep(sleep_time)

async def do_warming(db: Database, account_id: int):
    """便捷调用接口"""
    manager = MaintManager(db)
    return await manager.run_maint_cycle(account_id)
