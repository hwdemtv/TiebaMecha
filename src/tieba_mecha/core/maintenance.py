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

# 公域池：用于新号无关注列表时的冷启动破冰探索
DEFAULT_PUBLIC_FORUMS = ["贴吧", "王者荣耀", "原神", "电脑玩家", "显卡", "Steam", "数码", "电影", "弱智"]

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
                forums_list = forums_res.objs
                
                # [Optimization] 如果服务器端返回为空，回退使用本地数据库同步过的列表
                if not forums_list:
                    local_forums = await self.db.get_forums(acc_id)
                    if local_forums:
                        await log_info(f"[BioWarming] {account_name} 服务器关注列表为空，已自愈: 切换至本地数据库中的 {len(local_forums)} 个贴吧。")
                        forums_list = local_forums
                
                # 识别账号发育状态：<= 3 个关注吧视为"新号/冷启动(Cold State)"
                # 新号需要更加谨慎，浏览时间更长，互动(点赞)几乎为0
                is_cold_state = len(forums_list) <= 3
                is_public_exploration = False

                if not forums_list:
                    await log_info(f"[BioWarming] {account_name} 无任何关注数据，启动【冷启动探索模式】...")
                    target_forum_name = random.choice(DEFAULT_PUBLIC_FORUMS)
                    is_public_exploration = True
                else:
                    # 随机选一个吧进行浏览
                    target_forum = random.choice(forums_list)
                    target_forum_name = target_forum.fname

                await log_info(f"[BioWarming] {account_name} 模拟进入 [{target_forum_name}] 进行深层浏览...")
                
                # 获取主题帖列表 (模拟翻页看帖)
                threads = await client.get_threads(target_forum_name, pn=1)
                
                # 拟真停顿：模拟阅读标题
                if is_cold_state:
                    await self._human_sleep(8, 18)
                else:
                    await self._human_sleep(5, 12)
                
                if threads.objs:
                    # 随机深入一个帖子
                    target_thread = random.choice(threads.objs[:10])
                    await log_info(f"[BioWarming] {account_name} 正在阅读帖子: {target_thread.title[:20]}...")
                    
                    # 获取帖子内容
                    await client.get_posts(target_thread.tid, pn=1)
                    
                    # 拟真停顿：模拟深度阅读
                    if is_cold_state:
                        await self._human_sleep(15, 35) # 新号看更久，装作为普通路人
                    else:
                        await self._human_sleep(10, 25)

                    # 步骤 B: 随机互动 (点赞) 与 破冰机制
                    
                    # 对于新号，大幅降低点赞概率（甚至接近0），优先做破冰关注
                    agree_prob = 0.05 if is_cold_state else 0.6
                    if random.random() < agree_prob:
                        await log_info(f"[BioWarming] {account_name} 觉得不错，随手点了一个赞。")
                        try:
                            await client.agree(target_thread.tid)
                        except Exception as e:
                            await log_warn(f"[BioWarming] 点赞异常: {str(e)}")
                        await self._human_sleep(2, 5)
                    
                    # 破冰式关注 (仅在新号且处于公域探索模式时触发)
                    if is_cold_state and is_public_exploration:
                        if random.random() < 0.25:  # 25% 的概率关注该公域吧
                            await log_info(f"[BioWarming] {account_name} 【破冰】尝试关注探索吧: {target_forum_name}")
                            try:
                                await client.follow_forum(target_forum_name)
                            except Exception as e:
                                await log_warn(f"[BioWarming] 破冰关注异常: {str(e)}")
                            await self._human_sleep(3, 8)

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
