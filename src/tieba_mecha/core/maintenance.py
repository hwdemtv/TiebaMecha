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

        # [Fix 7] account_id 必须显式传入，避免歧义
        if account_id is None:
            await log_warn("[BioWarming] run_maint_cycle 需要显式传入 account_id，跳过")
            return False

        # 1. 获取账号凭证
        creds = await get_account_credentials(self.db, account_id)
        if not creds:
            await log_warn(f"[BioWarming] 账号 {account_id} 凭证获取失败，跳过维护")
            return False

        acc_id, bduss, stoken, proxy_id, cuid, ua = creds
        # 优化显示：获取实际账号名称而非仅显示 ID
        acc = await self.db.get_account_by_id(acc_id)
        account_name = acc.user_name or acc.name if acc else f"ID:{acc_id}"

        # [Fix 9] 代理健康检查：绑定代理已失效时跳过，避免浪费时间在必然失败的操作上
        if proxy_id:
            proxy = await self.db.get_proxy(proxy_id)
            if not proxy or not proxy.is_active:
                await log_warn(f"[BioWarming] 账号 {account_name} 绑定代理已失效，跳过维护")
                return False

        try:
            async with await create_client(self.db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
                # 步骤 A: 模拟兴趣探测 (获取关注列表并随机进吧)
                await log_info(f"[BioWarming] 启动维护协议: {account_name}...")

                # 获取自我信息以获取 ID
                self_info = await client.get_self_info()
                if not self_info:
                    # [Fix 2] 添加日志
                    await log_error(f"[BioWarming] {account_name} 获取用户信息失败，跳过本次维护")
                    return False

                # [Fix 1] 改为 pn=1 获取第一页，避免随机翻页拿到空数据
                forums_res = await client.get_follow_forums(self_info.user_id, pn=1)
                forums_raw = forums_res.objs if forums_res and not getattr(forums_res, 'err', None) else []

                # [Fix 4] 统一为 fname 字符串列表，消除类型不一致
                forum_names: list[str] = [f.fname for f in forums_raw if f.fname]

                # [Optimization] 如果服务器端返回为空，回退使用本地数据库同步过的列表
                if not forum_names:
                    local_forums = await self.db.get_forums(acc_id)
                    if local_forums:
                        await log_info(f"[BioWarming] {account_name} 服务器关注列表为空，已自愈: 切换至本地数据库中的 {len(local_forums)} 个贴吧。")
                        forum_names = [f.fname for f in local_forums if f.fname]

                # [Fix 5] 冷启动阈值从 <=3 调整为 <=10，更准确识别新号
                is_cold_state = len(forum_names) <= 10
                is_public_exploration = False

                if not forum_names:
                    await log_info(f"[BioWarming] {account_name} 无任何关注数据，启动【冷启动探索模式】...")
                    target_forum_name = random.choice(DEFAULT_PUBLIC_FORUMS)
                    is_public_exploration = True
                else:
                    # 随机选一个吧进行浏览
                    target_forum_name = random.choice(forum_names)

                await log_info(f"[BioWarming] {account_name} 模拟进入 [{target_forum_name}] 进行深层浏览...")

                # 获取主题帖列表 (模拟翻页看帖)
                threads = await client.get_threads(target_forum_name, pn=1)

                # 拟真停顿：模拟阅读标题
                if is_cold_state:
                    await self._human_sleep(8, 18)
                else:
                    await self._human_sleep(5, 12)

                # [防检测] 模拟搜索行为（15% 概率）
                if random.random() < 0.15:
                    search_keywords = ["Python", "游戏", "手机", "电脑", "音乐", "电影", "学习", "工作"]
                    keyword = random.choice(search_keywords)
                    await log_info(f"[BioWarming] {account_name} 模拟搜索: {keyword}")
                    try:
                        await client.search_threads(keyword)
                        await self._human_sleep(5, 15)
                    except Exception:
                        pass

                if threads and threads.objs:
                    # [Fix 6] 浏览 2-3 个帖子而非仅 1 个，增加行为多样性
                    browse_count = random.randint(2, 3)
                    browsed_threads = random.sample(threads.objs[:10], min(browse_count, len(threads.objs[:10])))

                    # [Fix 3] 点赞限制：单次维护最多点赞 1 次，避免高频检测
                    liked = False
                    for target_thread in browsed_threads:
                        await log_info(f"[BioWarming] {account_name} 正在阅读帖子: {target_thread.title[:20]}...")

                        # 获取帖子内容
                        await client.get_posts(target_thread.tid, pn=1)

                        # 拟真停顿：模拟深度阅读
                        if is_cold_state:
                            await self._human_sleep(15, 35)
                        else:
                            await self._human_sleep(10, 25)

                        # 步骤 B: 随机互动 (点赞) 与 破冰机制
                        # [Fix 3] 仅在未点赞时尝试，单轮最多 1 次点赞
                        if not liked:
                            base_prob = 0.20 if is_cold_state else 0.40
                            agree_prob = base_prob + random.uniform(-0.1, 0.1)
                            if random.random() < agree_prob:
                                await log_info(f"[BioWarming] {account_name} 觉得不错，随手点了一个赞。")
                                try:
                                    await client.agree(target_thread.tid)
                                    liked = True
                                except Exception as e:
                                    await log_warn(f"[BioWarming] 点赞异常: {str(e)}")
                                await self._human_sleep(2, 5)
                            else:
                                await log_info(f"[BioWarming] {account_name} 看了看，没点。")

                        # 帖子间延迟
                        await self._human_sleep(3, 8)

                    # [防检测] 模拟翻页浏览（30% 概率，翻 2-3 页）
                    if random.random() < 0.30:
                        for page in range(2, random.randint(3, 4)):
                            await self._human_sleep(3, 8)
                            try:
                                await client.get_threads(target_forum_name, pn=page)
                            except Exception:
                                break

                    # 破冰式关注 (仅在新号且处于公域探索模式时触发)
                    if is_cold_state and is_public_exploration:
                        if random.random() < 0.25:
                            await log_info(f"[BioWarming] {account_name} 【破冰】尝试关注探索吧: {target_forum_name}")
                            try:
                                await self._human_sleep(3, 10)
                                await client.follow_forum(target_forum_name)
                            except Exception as e:
                                await log_warn(f"[BioWarming] 破冰关注异常: {str(e)}")
                            await self._human_sleep(3, 8)

                # 记录成功维护
                await self.db.update_maint_status(acc_id)

                await log_info(f"[BioWarming] {account_name} 维护周期结束 | 权重负载均衡中...")
                return True

        except Exception as e:
            # [Fix 2] 区分异常类型，提供排查线索
            await log_error(f"[BioWarming] {account_name} 维护过程异常: {type(e).__name__}: {str(e)}")
            return False

    async def _human_sleep(self, min_s: float, max_s: float):
        """对数正态分布停顿 — 大量短停顿 + 偶尔长停顿，模拟真人行为节奏"""
        import math
        mean = math.log((min_s + max_s) / 2)
        sigma = 0.5
        sleep_time = random.lognormvariate(mean, sigma)
        sleep_time = max(min_s, min(max_s, sleep_time))
        await asyncio.sleep(sleep_time)


async def do_warming(db: Database, account_id: int):
    """便捷调用接口"""
    manager = MaintManager(db)
    return await manager.run_maint_cycle(account_id)
