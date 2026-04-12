"""批量发帖核心逻辑：反风控 + AI 变体 + 三种账号调度策略 + 多贴吧支持"""

import asyncio
import random
import time
import urllib.parse
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
    """基于滑动时间窗的动态令牌限流器 (支持并发安全)"""
    def __init__(self, rpm: int = 15):
        self.rpm = rpm
        self.timestamps = []
        self._lock = asyncio.Lock()
        
    async def wait_if_needed(self):
        async with self._lock:
            now = time.time()
            # 淘汰一分钟之前的记录
            self.timestamps = [t for t in self.timestamps if now - t < 60]
            
            if len(self.timestamps) >= self.rpm:
                # 基于最早时间戳计算休眠时长
                wait_time = 60 - (now - self.timestamps[0]) + 1
                await log_warn(f"触发内部速率安全墙 (>{self.rpm}帖/分)，流控休眠 {wait_time:.1f} 秒...")
                await asyncio.sleep(wait_time)
                # 唤醒后刷新时间记录以修正窗口
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

    @staticmethod
    def get_tactical_advice(err_msg: str) -> dict:
        """战术情报分析：将生硬的报错转化为实战建议"""
        advice_map = {
            "用户没有权限": {
                "reason": "等级不足、被封禁或未关注该吧。",
                "action": "1. 运行【全域签到】提升等级(需≥4级)；\n2. 开启【安全原初打法】优先用关注老号；\n3. 检查账号是否被该吧吧务列入黑名单。"
            },
            "由于吧务设置": {
                "reason": "触发了该贴吧吧务自定义的小号/关键字拦截规则。",
                "action": "1. 启用 AI 强力改写混淆文案特征；\n2. 使用更高等级(>7级)的账号出战；\n3. 检查文案中是否含有直链。"
            },
            "内容中含有": {
                "reason": "触发了百度平台级敏感词库拦截。",
                "action": "1. 开启 AI 深度改写；\n2. 增加零宽字符密度；\n3. 尝试将敏感关键词用拼音或谐音替换。"
            },
            "验证码": {
                "reason": "发帖频率过高或账号处于风控高度侦察态。",
                "action": "1. 显著增加发帖延迟(建议>600s)；\n2. 前往【安全本营】进行一次拟人化养号维护；\n3. 更换代理 IP。"
            },
            "贴吧升级中": {
                "reason": "目标贴吧由于后台维护暂时关闭发帖功能。",
                "action": "1. 暂时跳过该点位；\n2. 1-2小时后再试。"
            }
        }
        
        for key, info in advice_map.items():
            if key in err_msg:
                return info
        return {
            "reason": "未知干扰，可能是由于网络不稳定或百度返回了非标准代码。",
            "action": "1. 尝试对该账号进行手工登录验证；\n2. 检查代理节点是否依然存活。"
        }

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
        # 强制在任务启动前刷新一次本地状态，避免异步加载延迟导致的权限误判 (修复 AI 开启无效问题)
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

        # [核平级编码修复] 强制确保该异步任务线程的输出编码为 UTF-8
        import sys
        if sys.platform == "win32":
            try:
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
                if hasattr(sys.stderr, "reconfigure"):
                    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass

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

            # [Step 1: AI 动态变体增强] (增加强制 15 秒超时避免单个挂起全队)
            try:
                if task.use_ai:
                    optimizer = AIOptimizer(self.db)
                    success_ai, opt_title, opt_content, err_ai = await asyncio.wait_for(
                        optimizer.optimize_post(title, content),
                        timeout=15.0
                    )
                    if success_ai:
                        title, content = opt_title, opt_content
                        # 将生成的变体同步回数据库，让用户在 UI 界面可见
                        await self.db.update_material_ai(current_material.id, title, content)
                        short_title = title[:15] + "..." if len(title) > 15 else title
                        await log_info(f"[Step 1] AI 变体生成成功: [{short_title}]")
                    else:
                        await log_warn(f"[Step 1] AI 改写拒稿，回落原始文案: {err_ai}")
            except Exception as ex:
                await log_warn(f"[Step 1] AI 服务异常或超时: {str(ex)}")

            # 核心执行链
            try:
                async with await create_client(self.db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
                    import httpx
                    
                    # 确保获取上下文 TBS
                    try:
                        await client.get_self_info()
                    except Exception: pass
                    
                    if not getattr(client.account, 'tbs', None):
                        yield {"status": "error", "msg": "获取账号发帖凭证(TBS)失败", "fname": target_fname, "progress": task.progress, "total": task.total}
                        continue

                    forum = await client.get_forum(target_fname)
                    
                    # 代理配置
                    proxy_model = await self.db.get_proxy(proxy_id) if proxy_id else None
                    proxy_url = None
                    if proxy_model:
                        from .account import decrypt_value
                        scheme = proxy_model.protocol
                        h = proxy_model.host
                        p = proxy_model.port
                        user = decrypt_value(proxy_model.username) if proxy_model.username else ""
                        pwd = decrypt_value(proxy_model.password) if proxy_model.password else ""
                        proxy_url = f"{scheme}://{user}:{pwd}@{h}:{p}" if user and pwd else f"{scheme}://{h}:{p}"

                    # --- [Step 2: 文案安全清洗、强制编码转换及混淆] ---
                    try:
                        # [关键修复] 强制 UTF-8 清洗，并剔除可能导致 Win32 环境崩溃的非法字符
                        title = title.encode('utf-8', 'replace').decode('utf-8')
                        content = content.encode('utf-8', 'replace').decode('utf-8')
                        
                        # 混淆逻辑
                        obfuscated_content = Obfuscator.inject_zero_width_chars(content, density=0.2)
                        safe_content = Obfuscator.humanize_spacing(obfuscated_content)
                    except Exception as e:
                        err_msg = f"[Step 2] 文本预处理异常: {str(e)}"
                        await self.db.update_material_status(current_material.id, "failed", err_msg, posted_fname=target_fname, posted_account_id=account_id)
                        yield {"status": "error", "msg": err_msg, "fname": target_fname, "progress": task.progress, "total": task.total}
                        continue

                    # --- [Step 3: 网络协议发射] ---
                    tid = 0
                    success = False
                    err_msg = ""
                    try:
                        # [关键修复] 对 URL 路径中的中文进行 Percent-encoding 转义
                        # 核心原因：Windows 系统某些环境下请求头不允许非 ASCII 字符，必须转义为 % 形式
                        quoted_fname = urllib.parse.quote(target_fname)
                        headers = {
                            "Cookie": f"BDUSS={bduss}; STOKEN={stoken}",
                            "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                            "Referer": f"https://tieba.baidu.com/f?kw={quoted_fname}",
                            "Origin": "https://tieba.baidu.com",
                            "X-Requested-With": "XMLHttpRequest",
                            "Accept": "application/json, text/javascript, */*; q=0.01",
                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        }
                        data = {
                            "ie": "utf-8",
                            "kw": target_fname,
                            "fid": getattr(forum, 'fid', 0),
                            "tbs": getattr(client.account, 'tbs', ''),
                            "title": title,
                            "content": safe_content,
                            "anonymous": 0
                        }
                        
                        async with httpx.AsyncClient(proxy=proxy_url) as http_client:
                            # 仿生预热流量 (转义后的 URL)
                            try:
                                await http_client.get(f"https://tieba.baidu.com/f?kw={quoted_fname}", headers=headers, timeout=10.0)
                                await asyncio.sleep(1.2)
                            except: pass
                                
                            # [终极加固] 手动对 Body 进行 UTF-8 编码，防止系统 ASCII 环境干扰导致崩溃
                            body_content = urllib.parse.urlencode(data).encode('utf-8')
                            
                            res = await http_client.post(
                                "https://tieba.baidu.com/f/commit/thread/add",
                                headers=headers,
                                content=body_content,
                                timeout=20.0
                            )
                            res_json = res.json()
                            err_code = res_json.get("err_code", 0)
                            
                            if err_code == 0:
                                tid = res_json.get("data", {}).get("tid", 0)
                                success = True
                            else:
                                err_msg = str(res_json.get('error') or res_json)
                                # 识别封禁逻辑
                                if err_code == 3250004:
                                    await log_error(f"检测到吧务封禁！账号 {account_id} 已在 {target_fname} 自动熔断。")
                                    await self.db.mark_forum_banned(account_id, target_fname, reason="Step 3 发射检测到吧务封禁")
                                    try: await client.unfollow_forum(target_fname)
                                    except: pass
                                elif err_code == 340001:
                                    await log_warn(f"{target_fname} 正在升级中，已跳过该点位。")
                                    continue
                    except Exception as e:
                        # [环境诊断] 如果进入此块，str(e) 将明确告知是否仍为 encoding 错误
                        err_msg = f"[Step 3] 通讯过程异常 (账号:{account_id} @ {target_fname}): {str(e)}"
                        await log_error(err_msg)
                        await self.db.update_material_status(current_material.id, "failed", err_msg, posted_fname=target_fname, posted_account_id=account_id)
                        yield {"status": "error", "msg": err_msg, "fname": target_fname, "progress": task.progress, "total": task.total}
                        continue

                    if success:
                        task.progress += 1
                        await self.db.update_material_status(
                            current_material.id, 
                            "success", 
                            posted_fname=target_fname, 
                            posted_tid=tid,
                            posted_account_id=account_id
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
                        await self.db.update_material_status(
                            current_material.id, 
                            "failed", 
                            last_error=err_msg,
                            posted_fname=target_fname,
                            posted_account_id=account_id
                        )
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
                err_str = str(e)
                await log_error(f"自顶回帖失败 [TID:{tid}]: {err_str}")
                
                # 识别吧务封禁
                if "3250004" in err_str:
                    # 标记位熔断：不再删除记录，而是设为封禁并停止发帖许可
                    await self.db.mark_forum_banned(account_id, fname, reason="回帖触发吧务封禁 (3250004)")
                    try:
                        await client.unfollow_forum(fname)
                    except Exception as ue:
                        logger.warning(f"回帖熔断取消关注失败: {ue}")
                        
                    # 寻找关联物料并关闭自动回帖，防止持续背刺
                    async with self.db.async_session() as session:
                        from ..db.models import MaterialPool
                        from sqlalchemy import update
                        await session.execute(
                            update(MaterialPool).where(MaterialPool.posted_tid == tid).values(is_auto_bump=False)
                        )
                        await session.commit()
                    await log_warn(f"账号 {account_id} 在 {fname} 遭遇封禁，已转入标记熔断并紧急关闭 TID:{tid} 的自动回帖。")
                
                return False

    async def unfollow_forums_bulk(self, fnames: list[str], progress_callback=None):
        """批量取消关注并清理数据库记录"""
        from .account import get_account_credentials
        
        # 1. 识别受影响的账号
        account_ids = await self.db.get_account_ids_following_forums(fnames)
        
        # 如果没有账号关注这些吧，直接清理数据库
        if not account_ids:
            await self.db.delete_forum_memberships_globally(fnames)
            await self.db.delete_target_pool_by_fnames(fnames)
            return True

        total_actions = len(account_ids) * len(fnames)
        current_action = 0
        
        for acc_id in account_ids:
            creds = await get_account_credentials(self.db, acc_id)
            if not creds: continue
            
            _, bduss, stoken, proxy_id, cuid, ua = creds
            try:
                # 这里的 create_client 已经包含 proxy 支持
                async with await create_client(
                    self.db, 
                    bduss=bduss, 
                    stoken=stoken,
                    proxy_id=proxy_id,
                    cuid=cuid,
                    ua=ua
                ) as client:
                    for fname in fnames:
                        try:
                            await client.unfollow_forum(fname)
                            await log_info(f"账号 {acc_id} 已成功取消关注 [{fname}]")
                        except Exception as e:
                            await log_error(f"账号 {acc_id} 取消关注 [{fname}] 失败: {str(e)}")
                        
                        current_action += 1
                        if progress_callback:
                            await progress_callback(current_action, total_actions)
                        
                        # 批量操作期间的小休眠，防止触发高频拦截
                        await asyncio.sleep(random.uniform(0.5, 1.5))
            except Exception as e:
                await log_error(f"创建客户端执行取关任务失败(ID:{acc_id}): {e}")

        # 2. 清理数据库记录
        del_membership_count = await self.db.delete_forum_memberships_globally(fnames)
        del_target_count = await self.db.delete_target_pool_by_fnames(fnames)
        
        await log_info(f"全局阵地清理完成：移除了 {del_membership_count} 条关注记录，移除了 {del_target_count} 个靶场目标。")
        return True


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
            
            # 宽限期：45 分钟回一次，且总自顶次数不超过 50 次 (安全门控)
            threshold_time = datetime.now() - timedelta(minutes=45)
            
            stmt = select(MaterialPool).where(
                and_(
                    MaterialPool.status == "success",
                    MaterialPool.is_auto_bump == True,
                    MaterialPool.posted_tid != None,
                    MaterialPool.posted_tid != 0,
                    MaterialPool.bump_count < 50, # 强制安全限额
                    (MaterialPool.last_bumped_at == None) | (MaterialPool.last_bumped_at < threshold_time)
                )
            )
            result = await session.execute(stmt)
            candidates = result.scalars().all()
            
            if not candidates:
                return

            await log_info(f"发现 {len(candidates)} 个物料满足自动回帖条件 (次数 < 50)，开始执行...")
            
            # 运行时 Skip-List：防止在同一次扫描中反复背刺已被封的账号
            banned_pairs = set() # (account_id, fname)
            
            # 获取当前全局活跃号作为后备
            default_acc = await self.db.get_active_account()

            for material in candidates:
                # 【原号出战策略】优先选用发帖时的原号进行回帖 (Section 4.2)
                target_account_id = material.posted_account_id or (default_acc.id if default_acc else None)
                if not target_account_id:
                    continue
                
                # 检查动态黑名单
                if (target_account_id, material.posted_fname) in banned_pairs:
                    continue

                # --- 拟人化随机文案引擎 ---
                BUMP_TEMPLATES = [
                    "赞一个，资源已取！", "支持楼主，感谢分享", "马住备用，技术贴支持", 
                    "前排围观，顺便帮顶", "太实用了，楼主大气", "已收藏，感谢大佬",
                    "这个必须顶上去", "好东西，mark一下", "分享即美德，赞！",
                    "路过帮顶，支持原创", "感谢分享，整理辛苦了", "百度一下，支持此贴"
                ]
                RANDOM_EMOJIS = ["(๑•̀ㅂ• middle dot)و✧", "(￣▽￣)ノ", "[赞]", "✨", "🚀", "🔥", "👍", "🙏", "🍺"]
                
                # 基于物料标题和随机词库构造
                base_text = random.choice(BUMP_TEMPLATES)
                if random.random() < 0.4: # 40% 概率携带标题关键词
                    keyword = material.title[:10]
                    base_text = f"关于【{keyword}】：{base_text}"
                
                bump_content = f"{base_text} {random.choice(RANDOM_EMOJIS)}"
                
                if material.ai_status == "rewritten":
                    # AI 改写过的物料使用略微不同的风格
                    bump_content = f"分享好物：{material.title} {random.choice(RANDOM_EMOJIS)}"
                    
                success = await self.post_manager.reply_to_thread(
                    target_account_id, 
                    material.posted_fname, 
                    material.posted_tid, 
                    bump_content
                )
                
                if success:
                    await self.db.update_material_bump(material.id)
                    await log_info(f"物料 [{material.id}] 自顶成功 (账号:{target_account_id} | TID:{material.posted_tid})")
                    await asyncio.sleep(random.uniform(5, 15))
                else:
                    # 如果失败是因为封禁，记入本次运行的 Skip-List
                    banned_pairs.add((target_account_id, material.posted_fname))
                    await log_warn(f"物料 [{material.id}] 自顶失败")

