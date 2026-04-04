"""批量发帖核心逻辑：反风控 + AI 变体 + 三种账号调度策略 + 多贴吧支持"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, AsyncGenerator

from aiotieba.exception import TiebaServerError

from ..db.crud import Database
from .account import get_account_credentials
from .client_factory import create_client
from .ai_optimizer import AIOptimizer
from .obfuscator import Obfuscator
from .logger import log_info, log_warn, log_error
from .auth import get_auth_manager, AuthStatus


class RateLimiter:
    """基于滑动时间窗的动态令牌限流器"""
    def __init__(self, rpm: int = 15):
        self.rpm = rpm
        self.timestamps = []
        
    async def wait_if_needed(self):
        now = time.time()
        # 淘汰一分钟之前的记录
        self.timestamps = [t for t in self.timestamps if now - t < 60]
        
        if len(self.timestamps) >= self.rpm:
            wait_time = 60 - (now - self.timestamps[0]) + 1
            await log_warn(f"触发内部速率安全墙 (>{self.rpm}帖/分)，流控休眠 {wait_time:.1f} 秒...")
            await asyncio.sleep(wait_time)
            # 唤醒后刷新时间记录避免堆叠
            now = time.time()
            self.timestamps = [t for t in self.timestamps if now - t < 60]
            
        self.timestamps.append(now)


class BionicDelay:
    """拟人化随机延迟驱动器 (基于高斯分布与生物钟权重)"""
    @staticmethod
    def get_delay(min_sec: float, max_sec: float) -> float:
        # 1. 基础高斯采样
        mean = (min_sec + max_sec) / 2
        sigma = (max_sec - min_sec) / 6
        delay = random.gauss(mean, sigma)
        
        # 2. 昼夜生理权重注入 (凌晨1-7点反应较慢)
        hour = datetime.now().hour
        if 1 <= hour <= 7:
            delay *= random.uniform(1.5, 2.2)
            
        # 3. 边界裁剪
        return max(min_sec * 0.8, min(delay, max_sec * 2.5))

    @staticmethod
    async def sleep(min_sec: float, max_sec: float):
        delay = BionicDelay.get_delay(min_sec, max_sec)
        await log_info(f"拟人化随机休眠: {delay}s...")
        await asyncio.sleep(delay)


@dataclass
class BatchPostTask:
    """批量发帖任务配置"""
    id: str
    # 目标贴吧（支持多个）
    fname: str                              # 兼容旧字段
    fnames: List[str] = field(default_factory=list)  # 多贴吧列表（优先）
    titles: List[str] = field(default_factory=list)
    contents: List[str] = field(default_factory=list)
    accounts: List[int] = field(default_factory=list)
    # 发帖策略: round_robin（轮询）/ random（随机）/ weighted（加权）
    strategy: str = "round_robin"
    # 文案组合模式: random (随机) / strict (精准匹配)
    pairing_mode: str = "random"
    # 账号临时权重覆盖（key=account_id, value=权重1–10）
    # 不为空时，覆盖数据库中的全局 post_weight
    weight_override: dict = field(default_factory=dict)
    delay_min: float = 60.0
    delay_max: float = 300.0
    use_ai: bool = False
    status: str = "pending"
    progress: int = 0
    total: int = 0
    start_time: Optional[datetime] = None

    def get_fnames(self) -> List[str]:
        """获取目标贴吧列表（优先使用 fnames，回落 fname）"""
        if self.fnames:
            return self.fnames
        if self.fname:
            return [self.fname]
        return []


class BatchPostManager:
    """管理大规模异步发帖任务，支持三种账号调度策略"""

    def __init__(self, db: Database):
        self.db = db
        self._active_tasks = {}

    def _weighted_choice(self, accounts_with_weights: list[tuple]) -> int:
        """
        加权随机抽样。

        Args:
            accounts_with_weights: [(account_id, weight), ...]

        Returns:
            被选中的 account_id
        """
        ids = [a[0] for a in accounts_with_weights]
        weights = [a[1] for a in accounts_with_weights]
        return random.choices(ids, weights=weights, k=1)[0]

    async def _build_weighted_accounts(self, task: BatchPostTask) -> list[tuple]:
        """
        构建账号+权重列表。临时覆盖优先于数据库全局权重。

        Returns:
            [(account_id, weight), ...]
            
        Note:
            预构建权重列表,避免在发帖循环中重复查询数据库(N+1问题)
        """
        # 一次性获取所有账号,避免N+1查询
        all_accounts = await self.db.get_accounts()
        account_map = {acc.id: acc for acc in all_accounts}
        
        result = []
        for acc_id in task.accounts:
            # 优先使用临时覆盖权重
            if acc_id in task.weight_override:
                weight = max(1, min(10, task.weight_override[acc_id]))
            else:
                # 从预加载的账号映射中读取权重
                acc_obj = account_map.get(acc_id)
                weight = acc_obj.post_weight if acc_obj else 5
            result.append((acc_id, weight))
        return result

    async def _pick_account(self, task: BatchPostTask, step: int, weights: list[tuple]) -> int:
        """
        根据任务策略选取本次发帖使用的账号 ID。

        Args:
            task: 任务对象
            step: 当前步骤序号（从 0 开始）
            weights: [(account_id, weight), ...]

        Returns:
            account_id
        """
        if task.strategy == "round_robin":
            return task.accounts[step % len(task.accounts)]
        elif task.strategy == "random":
            return random.choice(task.accounts)
        elif task.strategy == "weighted":
            return self._weighted_choice(weights)
        else:
            return task.accounts[step % len(task.accounts)]

    async def _pick_optimal_account_for_target(self, task: BatchPostTask, target_fname: str, step: int, weights: list[tuple]) -> int:
        """
        靶场智能撮合核心：优先寻找本号已关注且 is_post_target=True 的原生号
        这极大提高了防抽几率（本土作战）。
        """
        from sqlalchemy import select
        from ..db.models import Forum
        
        async with self.db.async_session() as session:
            stmt = select(Forum.account_id).where(
                Forum.fname == target_fname,
                Forum.is_post_target == True,
                Forum.account_id.in_(task.accounts)
            )
            result = await session.execute(stmt)
            native_accounts = result.scalars().all()
            
            if native_accounts:
                # 在这些拥有本土优势的账号中随机挑选一个
                return random.choice(native_accounts)
                
        # 如果没有原生号储备，回落大盘调度策略（空降打法）
        return await self._pick_account(task, step, weights)

    async def execute_task(self, task: BatchPostTask) -> AsyncGenerator[dict, None]:
        """
        执行批量发帖任务。支持多贴吧、三种策略、临时权重覆盖。

        Yields:
            dict: {status, tid/msg, progress, total, fname, account_id}
        """
        task.progress = 0

        # --- 授权门控与配额限制 ---
        am = get_auth_manager()
        is_pro = (am.status == AuthStatus.PRO)
        
        if not is_pro:
            # 尝试刷新一次授权状态
            await am.check_local_status()
            is_pro = (am.status == AuthStatus.PRO)
            
        if not is_pro:
            # Free 版配额限制
            if len(task.accounts) > 1:
                task.accounts = task.accounts[:1]
                await log_warn("Free 版仅支持单账号发帖，已自动截断账号列表")
            
            if task.total > 3:
                task.total = 3
                await log_warn("Free 版单次任务上限为 3 帖，请升级 Pro 解锁无限火力")
                
            if task.use_ai:
                task.use_ai = False
                await log_warn("AI 变体功能仅对 Pro 用户开放，已自动降级为原始文案")
        # ------------------------

        fnames = task.get_fnames()
        if not fnames:
            yield {"status": "failed", "msg": "未指定目标贴吧"}
            return

        await log_info(
            f"启动批量发帖任务: {fnames} | 策略: {task.strategy} | 目标数: {task.total}"
        )

        # 获取所有可用账号
        if not task.accounts:
            active_acc = await self.db.get_active_account()
            if not active_acc:
                yield {"status": "failed", "msg": "未找到可用账号"}
                return
            task.accounts = [active_acc.id]

        # 预构建权重列表（避免每次循环都查数据库）
        weighted_accounts = await self._build_weighted_accounts(task)
        
        # 预加载所有账号信息,避免N+1查询
        all_accounts = await self.db.get_accounts()
        account_map = {acc.id: acc for acc in all_accounts}

        # 从全局数据库拉取所有 Pending 物料
        pending_materials = await self.db.get_materials(status="pending")
        if not pending_materials:
            yield {"status": "failed", "msg": "物料池为空或没有待发(pending)物料，请先录入或重置状态"}
            return

        # 调整总执行次数不超过物料上限
        actual_total = min(task.total, len(pending_materials))
        task.total = actual_total

        limiter = RateLimiter(rpm=15)

        for i in range(actual_total):
            # 经过令牌桶限流器 (RPM=15)
            await limiter.wait_if_needed()

            # 轮选目标贴吧（优先取得，以便给账号撮合使用）
            target_fname = fnames[i % len(fnames)]

            # 选取账号：智能撮合，本土原生号优先
            account_id = await self._pick_optimal_account_for_target(task, target_fname, i, weighted_accounts)

            # 检查代理状态：代理失效则跳过该账号
            acc = account_map.get(account_id)
            if acc and acc.status == "suspended_proxy":
                await log_warn(f"账号 [{account_id}] 代理已挂起，跳过本次发帖")
                yield {"status": "skipped", "msg": f"账号 {account_id} 代理挂起，已跳过", "progress": task.progress, "total": task.total}
                continue

            creds = await get_account_credentials(self.db, account_id)
            if not creds:
                await log_warn(f"账号 [{account_id}] 凭证获取失败，跳过")
                continue

            _, bduss, stoken, proxy_id, cuid, ua = creds

            # 使用数据库物料实体
            current_material = pending_materials[i]
            title = current_material.title
            content = current_material.content

            # AI 动态变体增强 (增加强制 10 秒超时避免单个挂起全队)
            if task.use_ai:
                try:
                    optimizer = AIOptimizer(self.db)
                    success, opt_title, opt_content, err = await asyncio.wait_for(
                        optimizer.optimize_post(title, content),
                        timeout=12.0
                    )
                    if success:
                        title, content = opt_title, opt_content
                        await log_info(f"AI 变体生成成功: {title[:20]}...")
                    else:
                        await log_warn(f"AI 改写拒稿，回落原始文案: {err}")
                except asyncio.TimeoutError:
                    await log_warn("AI 服务调用超时 (超过12s)，回落原始文案")
                except Exception as ex:
                    await log_warn(f"AI 服务异常: {str(ex)}")

            try:
                async with await create_client(self.db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
                    import httpx
                    
                    # 确保获取上下文 TBS
                    await client.get_self_info()
                    if not getattr(client.account, 'tbs', None):
                        yield {"status": "error", "msg": "获取账号发帖凭证(TBS)失败", "fname": target_fname, "progress": task.progress, "total": task.total}
                        continue

                    forum = await client.get_forum(target_fname)
                    
                    headers = {
                        "Cookie": f"BDUSS={bduss}; STOKEN={stoken}",
                        "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                        "Referer": f"https://tieba.baidu.com/f?kw={target_fname}",
                        "Origin": "https://tieba.baidu.com",
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    }
                    
                    proxy_model = await self.db.get_proxy(proxy_id) if proxy_id else None
                    proxy_url = None
                    if proxy_model:
                        from .account import decrypt_value
                        scheme = proxy_model.protocol
                        h = proxy_model.host
                        p = proxy_model.port
                        user = decrypt_value(proxy_model.username) if proxy_model.username else ""
                        pwd = decrypt_value(proxy_model.password) if proxy_model.password else ""
                        if user and pwd:
                            proxy_url = f"{scheme}://{user}:{pwd}@{h}:{p}"
                        else:
                            proxy_url = f"{scheme}://{h}:{p}"
                            
                    # 【防抽引擎】零宽防风控挂载 (确保连改写后的 AI 文本也能被防抽)
                    safe_title = Obfuscator.inject_zero_width_chars(title, density=0.2)
                    safe_content = Obfuscator.humanize_spacing(Obfuscator.inject_zero_width_chars(content, density=0.3))
                        
                    data = {
                        "ie": "utf-8",
                        "kw": target_fname,
                        "fid": forum.fid,
                        "tbs": client.account.tbs,
                        "title": safe_title,
                        "content": safe_content,
                        "anonymous": 0
                    }
                    
                    tid = 0
                    success = False
                    err_msg = ""
                    
                    async with httpx.AsyncClient(proxy=proxy_url) as http_client:
                        # 启动预热仿生浏览器动作
                        try:
                            await http_client.get(f"https://tieba.baidu.com/f?kw={target_fname}", headers=headers, timeout=10.0)
                            await asyncio.sleep(1.2)
                        except Exception:
                            pass
                            
                        # 进行核心发射
                        res = await http_client.post(
                            "https://tieba.baidu.com/f/commit/thread/add",
                            headers=headers,
                            data=data,
                            timeout=15.0
                        )
                        res_json = res.json()
                        if res_json.get("err_code") == 0:
                            tid = res_json.get("data", {}).get("tid", 0)
                            success = True
                        else:
                            err_msg = str(res_json.get('error') or res_json)

                    if success:
                        task.progress += 1
                        await self.db.update_material_status(
                            current_material.id, 
                            "success", 
                            posted_fname=target_fname, 
                            posted_tid=tid
                        )
                        await self.db.update_target_pool_status(target_fname, is_success=True)
                        await log_info(
                            f"[{task.strategy}] 账号 {account_id} → {target_fname} 发帖成功 ({task.progress}/{task.total})"
                        )
                        # 拟人化随机休眠逻辑
                        if i < actual_total - 1:
                            await BionicDelay.sleep(task.delay_min, task.delay_max)
                        yield {
                            "status": "success", "tid": tid, "fname": target_fname,
                            "account_id": account_id, "progress": task.progress, "total": task.total,
                        }
                    else:
                        await self.db.update_material_status(current_material.id, "failed", err_msg)
                        await self.db.update_target_pool_status(target_fname, is_success=False, error_reason=err_msg)
                        yield {"status": "error", "msg": f"发帖拦截: {err_msg}", "fname": target_fname, "progress": task.progress, "total": task.total}
            except TiebaServerError as e:
                await self.db.update_material_status(current_material.id, "failed", f"平台拒绝: {e.msg}")
                await asyncio.sleep(30.0)
            except Exception as e:
                await self.db.update_material_status(current_material.id, "failed", f"执行异常: {str(e)}")
                await asyncio.sleep(60.0)

        task.status = "completed"
        await log_info(f"批量发帖任务完成: {fnames} | 成功: {task.progress}/{task.total}")

    async def reply_to_thread(self, account_id: int, fname: str, tid: int, content: str) -> bool:
        """基础回帖/自顶实现"""
        from .account import get_account_credentials
        from .client_factory import create_client
        from .obfuscator import Obfuscator
        
        creds = await get_account_credentials(self.db, account_id)
        if not creds: return False

        _, bduss, stoken, proxy_id, cuid, ua = creds
        async with await create_client(self.db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
            await client.get_self_info()
            if not getattr(client.account, 'tbs', None): return False
            
            safe_content = Obfuscator.inject_zero_width_chars(content, density=0.1)
            try:
                await client.add_post(fname, tid, safe_content)
                return True
            except Exception as e:
                await log_error(f"自顶回帖失败 [TID:{tid}]: {str(e)}")
                return False


class AutoBumpManager:
    """自动回帖(自顶)调度管理器"""
    def __init__(self, db):
        self.db = db
        self.post_manager = BatchPostManager(db)

    async def process_all_candidates(self):
        """扫描并处理所有待自顶的物料"""
        from datetime import datetime, timedelta
        
        # 获取开启了自动回帖、发帖成功、且距离上次回帖超过 45 分钟的物料
        async with self.db.async_session() as session:
            from ..db.models import MaterialPool
            from sqlalchemy import select, and_
            
            # 宽限期：45 分钟回一次
            threshold_time = datetime.now() - timedelta(minutes=45)
            
            stmt = select(MaterialPool).where(
                and_(
                    MaterialPool.status == "success",
                    MaterialPool.is_auto_bump == True,
                    MaterialPool.posted_tid != None,
                    MaterialPool.posted_tid != 0,
                    (MaterialPool.last_bumped_at == None) | (MaterialPool.last_bumped_at < threshold_time)
                )
            )
            result = await session.execute(stmt)
            candidates = result.scalars().all()
            
            if not candidates:
                return

            await log_info(f"发现 {len(candidates)} 个物料满足自动回帖条件，开始执行...")
            
            # 选取一个活跃账号进行回帖 (简单策略：使用当前全局活跃号)
            active_acc = await self.db.get_active_account()
            if not active_acc:
                await log_warn("自动回帖失败：未设置全局活跃账号")
                return

            for material in candidates:
                # 构造回帖内容：可以从 AI 生成或者简单的占位符，这里简单使用标题变体
                bump_content = f"自顶一下：{material.title[:15]}..." 
                if material.ai_status == "rewritten":
                    bump_content = f"分享：{material.title}"
                    
                success = await self.post_manager.reply_to_thread(
                    active_acc.id, 
                    material.posted_fname, 
                    material.posted_tid, 
                    bump_content
                )
                
                if success:
                    await self.db.update_material_bump(material.id)
                    await log_info(f"物料 [{material.id}] 自顶成功 (TID:{material.posted_tid})")
                    # 避免回帖太快，引入拟人化短休眠
                    await asyncio.sleep(random.uniform(5, 15))
                else:
                    await log_warn(f"物料 [{material.id}] 自顶失败")
