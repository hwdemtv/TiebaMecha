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


class PerAccountRateLimiter:
    """
    每账号独立RPM限制器。
    每个账号有独立的滑动时间窗，避免全局限流导致资源竞争。
    相比全局 RateLimiter，更精细化控制每个账号的发帖频率。
    """
    def __init__(self, rpm: int = 5):
        """
        Args:
            rpm: 每个账号每分钟最大发帖数，默认5帖/分
        """
        self.rpm = rpm
        self._account_timestamps = {}  # {account_id: [timestamps]}
        self._lock = asyncio.Lock()
    
    async def wait_if_needed(self, account_id: int):
        """检查并等待该账号的限流"""
        async with self._lock:
            if account_id not in self._account_timestamps:
                self._account_timestamps[account_id] = []
            
            timestamps = self._account_timestamps[account_id]
            now = time.time()
            
            # 淘汰一分钟之前的记录
            timestamps = [t for t in timestamps if now - t < 60]
            
            if len(timestamps) >= self.rpm:
                # 基于该账号最早时间戳计算休眠时长
                wait_time = 60 - (now - timestamps[0]) + 1
                await log_warn(
                    f"账号 [{account_id}] 触发独立速率限制 ({self.rpm}帖/分)，"
                    f"等待 {wait_time:.1f}s..."
                )
                await asyncio.sleep(wait_time)
                now = time.time()
                timestamps = [t for t in timestamps if now - t < 60]
            
            timestamps.append(now)
            self._account_timestamps[account_id] = timestamps
    
    def get_status(self, account_id: int) -> dict:
        """获取指定账号的限流状态"""
        if account_id not in self._account_timestamps:
            return {"account_id": account_id, "rpm": self.rpm, "recent_posts": 0, "can_post": True}
        
        now = time.time()
        recent = [t for t in self._account_timestamps[account_id] if now - t < 60]
        return {
            "account_id": account_id,
            "rpm": self.rpm,
            "recent_posts": len(recent),
            "can_post": len(recent) < self.rpm
        }
    
    def reset_account(self, account_id: int):
        """重置指定账号的限流记录"""
        if account_id in self._account_timestamps:
            del self._account_timestamps[account_id]


class AccountForumCooldown:
    """
    账号-贴吧独立冷却跟踪器。
    确保每个账号在每个贴吧有独立的冷却时间，避免同一账号短时间跨多贴吧被检测。
    """
    def __init__(self, cooldown_seconds: float = 600):
        self.cooldown_seconds = cooldown_seconds
        self._last_post = {}  # {(account_id, fname): timestamp}
        self._lock = asyncio.Lock()
    
    def can_post(self, account_id: int, fname: str) -> bool:
        """检查账号-贴吧组合是否可以发帖（未在冷却中）"""
        key = (account_id, fname)
        last_time = self._last_post.get(key, 0)
        return time.time() - last_time >= self.cooldown_seconds
    
    def get_remaining_cooldown(self, account_id: int, fname: str) -> float:
        """获取剩余冷却时间（秒）"""
        key = (account_id, fname)
        last_time = self._last_post.get(key, 0)
        elapsed = time.time() - last_time
        return max(0, self.cooldown_seconds - elapsed)
    
    async def record_post(self, account_id: int, fname: str):
        """记录一次发帖，更新冷却时间"""
        async with self._lock:
            self._last_post[(account_id, fname)] = time.time()
    
    async def get_available_forum(self, account_id: int, fnames: List[str]) -> Optional[str]:
        """
        为指定账号找一个不在冷却中的贴吧。
        如果所有贴吧都在冷却中，返回 None。
        """
        for fname in fnames:
            if self.can_post(account_id, fname):
                return fname
        return None


class CaptchaCircuitBreaker:
    """
    验证码熔断器：检测到验证码后自动暂停该账号的发帖任务。
    验证码是百度高风险行为的强烈信号，继续发帖可能导致封号。
    """
    def __init__(self, cooldown_minutes: int = 30):
        """
        Args:
            cooldown_minutes: 触发验证码后熔断时长，默认30分钟
        """
        self.cooldown_minutes = cooldown_minutes
        self._captcha_triggers = {}  # {account_id: timestamp}
        self._lock = asyncio.Lock()
    
    async def check_and_trigger(self, account_id: int, err_msg: str, err_code: int) -> bool:
        """
        检查是否触发验证码熔断。
        
        Returns:
            True 表示触发了熔断，应暂停该账号；False 表示正常
        """
        captcha_keywords = ["验证码", "captcha", "安全验证", "操作太频繁", "频繁登录", "账号异常"]
        captcha_codes = [6, 7, 16, 18, 40, 100006, 100007]  # 常见的验证码相关错误码
        
        is_captcha = any(kw in str(err_msg) for kw in captcha_keywords) or err_code in captcha_codes
        
        if is_captcha:
            async with self._lock:
                self._captcha_triggers[account_id] = time.time()
            await log_error(
                f"🚨 验证码熔断：账号 [{account_id}] 触发验证码，暂停发帖 {self.cooldown_minutes} 分钟。"
                f"建议：手动验证账号或增加发帖间隔。"
            )
            return True
        return False
    
    def is_in_cooldown(self, account_id: int) -> bool:
        """检查账号是否处于验证码熔断中"""
        if account_id not in self._captcha_triggers:
            return False
        
        elapsed = time.time() - self._captcha_triggers[account_id]
        if elapsed >= self.cooldown_minutes * 60:
            # 熔断超时，自动解除
            del self._captcha_triggers[account_id]
            return False
        return True
    
    def get_remaining_cooldown(self, account_id: int) -> float:
        """获取验证码熔断剩余时间（秒）"""
        if account_id not in self._captcha_triggers:
            return 0
        elapsed = time.time() - self._captcha_triggers[account_id]
        remaining = self.cooldown_minutes * 60 - elapsed
        return max(0, remaining)


class ContentSimilarityDetector:
    """
    内容重复度检测器：避免批量发送高度相似的内容被识别为机器行为。
    计算标题和内容的哈希相似度，超过阈值则警告或跳过。
    """
    def __init__(self, similarity_threshold: float = 0.7):
        """
        Args:
            similarity_threshold: 相似度阈值，0-1，越高越严格
        """
        self.similarity_threshold = similarity_threshold
        self._history = []  # [(hash, timestamp)]
        self._lock = asyncio.Lock()
    
    def _normalize_text(self, text: str) -> str:
        """标准化文本：去除标点、空格、小写化"""
        import re
        text = re.sub(r'[^\w\u4e00-\u9fff]', '', text)  # 只保留字母数字和中文
        return text.lower()
    
    def _compute_similarity(self, text1: str, text2: str) -> float:
        """计算两个文本的相似度（基于字符级Jaccard）"""
        t1 = set(self._normalize_text(text1))
        t2 = set(self._normalize_text(text2))
        if not t1 or not t2:
            return 0.0
        intersection = len(t1 & t2)
        union = len(t1 | t2)
        return intersection / union if union > 0 else 0.0
    
    async def check(self, title: str, content: str) -> tuple[bool, float]:
        """
        检查内容是否与历史内容过于相似。
        
        Returns:
            (是否通过检查, 最高相似度)
        """
        combined_text = f"{title} {content}"
        
        async with self._lock:
            max_similarity = 0.0
            # 只检查最近30分钟内的历史记录
            cutoff = time.time() - 1800
            recent_history = [h for h in self._history if h[1] > cutoff]
            
            for history_hash, _ in recent_history:
                similarity = self._compute_similarity(combined_text, history_hash)
                max_similarity = max(max_similarity, similarity)
            
            return max_similarity < self.similarity_threshold, max_similarity
    
    async def record(self, title: str, content: str):
        """记录本次发帖内容"""
        combined = f"{title} {content}"
        async with self._lock:
            self._history.append((combined, time.time()))
            # 只保留最近100条记录
            if len(self._history) > 100:
                self._history = self._history[-100:]


class FailureCircuitBreaker:
    """
    连续失败熔断器：账号连续失败N次后自动暂停，防止过度尝试导致封号。
    """
    def __init__(self, max_consecutive_failures: int = 3, cooldown_minutes: int = 60):
        """
        Args:
            max_consecutive_failures: 最大连续失败次数
            cooldown_minutes: 熔断后暂停时长
        """
        self.max_consecutive_failures = max_consecutive_failures
        self.cooldown_minutes = cooldown_minutes
        self._failure_counts = {}  # {account_id: (count, last_failure_time)}
        self._lock = asyncio.Lock()
    
    async def record_failure(self, account_id: int) -> bool:
        """
        记录一次失败。
        
        Returns:
            True 表示触发了熔断；False 表示正常
        """
        async with self._lock:
            now = time.time()
            if account_id in self._failure_counts:
                count, last_time = self._failure_counts[account_id]
                # 如果上次失败超过30分钟，重置计数
                if now - last_time > 1800:
                    count = 0
                count += 1
                self._failure_counts[account_id] = (count, now)
            else:
                self._failure_counts[account_id] = (1, now)
            
            count, _ = self._failure_counts[account_id]
            if count >= self.max_consecutive_failures:
                await log_error(
                    f"🚨 连续失败熔断：账号 [{account_id}] 连续失败 {count} 次，"
                    f"暂停 {self.cooldown_minutes} 分钟。请检查账号状态或网络。"
                )
                return True
            return False
    
    async def record_success(self, account_id: int):
        """记录一次成功，重置失败计数"""
        async with self._lock:
            if account_id in self._failure_counts:
                del self._failure_counts[account_id]
    
    def is_in_cooldown(self, account_id: int) -> bool:
        """检查账号是否处于熔断中"""
        if account_id not in self._failure_counts:
            return False
        
        count, last_time = self._failure_counts[account_id]
        if count < self.max_consecutive_failures:
            return False
        
        elapsed = time.time() - last_time
        if elapsed >= self.cooldown_minutes * 60:
            # 超时自动解除，但保留计数（下次失败会继续累积）
            return False
        return True


class TimeWindowDispatcher:
    """
    时段智能分散器：根据当前时段动态调整发帖延迟，避免在低活跃时段集中发帖。
    深夜/凌晨时段自动增加延迟，模拟正常用户作息。
    """
    def __init__(self, quiet_start: int = 1, quiet_end: int = 6):
        """
        Args:
            quiet_start: 静默开始小时（0-23）
            quiet_end: 静默结束小时（0-23）
        """
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end
    
    def is_quiet_hours(self) -> bool:
        """判断是否处于静默时段"""
        current_hour = datetime.now().hour
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= current_hour < self.quiet_end
        else:  # 跨天情况如 23-5
            return current_hour >= self.quiet_start or current_hour < self.quiet_end
    
    def get_multiplier(self) -> float:
        """
        获取延迟倍率：
        - 正常时段：1.0x
        - 静默时段(1-6点)：3.0x（凌晨发帖风险极高，极端降速）
        - 早高峰(7-9点)：1.5x（用户开始活跃，适度加速）
        """
        current_hour = datetime.now().hour
        if 1 <= current_hour < 6:
            return 3.0  # 凌晨风险最高
        elif 7 <= current_hour < 10:
            return 1.5  # 早高峰，用户活跃
        return 1.0
    
    def get_adjusted_delay(self, min_delay: float, max_delay: float) -> tuple[float, float]:
        """获取调整后的延迟范围"""
        multiplier = self.get_multiplier()
        return (min_delay * multiplier, max_delay * multiplier)


class AutoWeightCalculator:
    """
    自动权重计算器：根据账号多维度指标自动计算推荐发帖权重。
    
    计算因素及权重：
    - 平均贴吧等级 (30%): 无等级=1, 1-3级=3, 4-6级=5, 7-10级=7, 10+级=10
    - 签到成功率 (25%): success/(total||1) * 10
    - 账号状态 (20%): active=10, pending=7, error=3, 其他=1
    - 代理绑定 (15%): 已绑定=10, 未绑定=5
    - 验证时效 (10%): 7天内=10, 30天内=7, 30天+=3, 从未=1
    """
    
    # 各因素权重配置
    WEIGHT_LEVEL = 0.30      # 等级权重
    WEIGHT_SIGN = 0.25       # 签到成功率权重
    WEIGHT_STATUS = 0.20    # 账号状态权重
    WEIGHT_PROXY = 0.15     # 代理绑定权重
    WEIGHT_VERIFIED = 0.10  # 验证时效权重
    
    @classmethod
    def calc_level_score(cls, avg_level: float) -> float:
        """根据平均等级计算得分 (1-10)"""
        if avg_level <= 0:
            return 1.0
        elif avg_level <= 3:
            return 3.0
        elif avg_level <= 6:
            return 5.0
        elif avg_level <= 10:
            return 7.0
        else:
            return 10.0
    
    @classmethod
    def calc_sign_score(cls, success: int, total: int) -> float:
        """根据签到成功率计算得分 (1-10)"""
        if total == 0:
            return 5.0  # 无历史数据，给予中等分数
        rate = success / max(total, 1)
        return min(10.0, rate * 10.0)
    
    @classmethod
    def calc_status_score(cls, status: str) -> float:
        """根据账号状态计算得分 (1-10)"""
        status_scores = {
            "active": 10.0,
            "pending": 7.0,
            "error": 3.0,
            "suspended_proxy": 2.0,
            "suspended": 1.0,
            "banned": 1.0,
            "expired": 1.0,
        }
        return status_scores.get(status.lower(), 1.0)
    
    @classmethod
    def calc_proxy_score(cls, has_proxy: bool) -> float:
        """根据代理绑定情况计算得分 (1-10)"""
        return 10.0 if has_proxy else 5.0
    
    @classmethod
    def calc_verified_score(cls, last_verified: datetime | None) -> float:
        """根据验证时效计算得分 (1-10)"""
        if last_verified is None:
            return 1.0  # 从未验证
        
        from datetime import timedelta
        days_since = (datetime.now() - last_verified).days
        
        if days_since <= 7:
            return 10.0
        elif days_since <= 30:
            return 7.0
        elif days_since <= 60:
            return 5.0
        else:
            return 2.0
    
    @classmethod
    def calculate(cls, account: 'Account', forums: list['Forum'] = None) -> tuple[int, dict]:
        """
        计算账号的推荐权重。
        
        Args:
            account: Account 对象
            forums: 该账号关联的 Forum 对象列表，可选
            
        Returns:
            (推荐权重 1-10, 详细得分字典)
        """
        # 1. 计算平均等级得分
        if forums:
            levels = [f.level for f in forums if f.level > 0]
            avg_level = sum(levels) / len(levels) if levels else 0.0
        else:
            avg_level = 0.0
        level_score = cls.calc_level_score(avg_level)
        
        # 2. 计算签到成功率得分
        if forums:
            total_signs = sum(f.history_total for f in forums)
            success_signs = sum(f.history_success for f in forums)
        else:
            total_signs, success_signs = 0, 0
        sign_score = cls.calc_sign_score(success_signs, total_signs)
        
        # 3. 账号状态得分
        status_score = cls.calc_status_score(account.status)
        
        # 4. 代理绑定得分
        proxy_score = cls.calc_proxy_score(account.proxy_id is not None)
        
        # 5. 验证时效得分
        verified_score = cls.calc_verified_score(account.last_verified)
        
        # 6. 加权计算总分
        total_score = (
            level_score * cls.WEIGHT_LEVEL +
            sign_score * cls.WEIGHT_SIGN +
            status_score * cls.WEIGHT_STATUS +
            proxy_score * cls.WEIGHT_PROXY +
            verified_score * cls.WEIGHT_VERIFIED
        )
        
        # 7. 映射到 1-10 范围
        final_weight = max(1, min(10, round(total_score)))
        
        # 8. 构建详细得分报告
        details = {
            "account_id": account.id,
            "account_name": account.name,
            "avg_level": round(avg_level, 1),
            "level_score": round(level_score, 1),
            "total_signs": total_signs,
            "success_rate": round(success_signs / max(total_signs, 1) * 100, 1),
            "sign_score": round(sign_score, 1),
            "status": account.status,
            "status_score": round(status_score, 1),
            "has_proxy": account.proxy_id is not None,
            "proxy_score": round(proxy_score, 1),
            "days_since_verified": (datetime.now() - account.last_verified).days if account.last_verified else None,
            "verified_score": round(verified_score, 1),
            "total_score": round(total_score, 1),
            "recommended_weight": final_weight,
        }
        
        return final_weight, details


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
            
        # 3. 追加 ±20% 的真实随机扰动 (Jitter) 以破坏机器固定节拍
        jitter = random.uniform(0.8, 1.2)
        delay *= jitter
        
        # 4. 边界裁剪
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
    delay_min: float = 120.0  # 保守值：降低被检测风险
    delay_max: float = 600.0  # 保守值：降低被检测风险
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
        这极大提高了防抽几率（本土作战）。同时跳过已被该吧封禁的账号。
        """
        from sqlalchemy import select
        from ..db.models import Forum
        
        async with self.db.async_session() as session:
            stmt = select(Forum.account_id).where(
                Forum.fname == target_fname,
                Forum.is_post_target == True,
                Forum.is_banned == False,  # 跳过已被该吧封禁的账号
                Forum.account_id.in_(task.accounts)
            )
            result = await session.execute(stmt)
            native_accounts = result.scalars().all()
            
            if native_accounts:
                # 在这些拥有本土优势的账号中随机挑选一个
                return random.choice(native_accounts)
            
            # 回退：在大盘账号中排除已封禁的
            stmt_fallback = select(Forum.account_id).where(
                Forum.fname == target_fname,
                Forum.is_banned == False,
                Forum.account_id.in_(task.accounts)
            )
            result_fallback = await session.execute(stmt_fallback)
            available_accounts = result_fallback.scalars().all()
            if available_accounts:
                return random.choice(available_accounts)
                
        # 如果没有原生号储备，回落大盘调度策略（空降打法）
        return await self._pick_account(task, step, weights)

    async def execute_task(self, task: BatchPostTask) -> AsyncGenerator[dict, None]:
        """
        执行批量发帖任务。支持多贴吧、三种策略、临时权重覆盖。

        Yields:
            dict: {status, tid/msg, progress, total, fname, account_id}
        """
        task.progress = 0

        # [风控增强] 执行前时段风险评估
        time_dispatcher = TimeWindowDispatcher()
        if time_dispatcher.is_quiet_hours():
            current_hour = datetime.now().hour
            await log_warn(
                f"⚠️ 时段风险检测: 当前 {current_hour}:00 处于高风险时段 (凌晨1-6点)，"
                f"系统将自动启用双倍延迟保护"
            )

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

        # 每账号独立RPM限制器：替代全局 RateLimiter，实现精细化控制
        per_account_limiter = PerAccountRateLimiter(rpm=5)  # 每账号5帖/分
        # 账号-贴吧独立冷却跟踪器：防止同账号短时间跨多贴吧被检测
        af_tracker = AccountForumCooldown(cooldown_seconds=600)  # 10分钟独立冷却
        # 验证码熔断器：检测验证码后自动暂停账号
        captcha_breaker = CaptchaCircuitBreaker(cooldown_minutes=30)
        # 内容重复度检测器：避免发送高度相似内容
        similarity_detector = ContentSimilarityDetector(similarity_threshold=0.7)
        # 连续失败熔断器：连续失败N次后暂停账号
        failure_breaker = FailureCircuitBreaker(max_consecutive_failures=3, cooldown_minutes=60)
        # 时段智能分散器：根据时段动态调整延迟
        time_dispatcher = TimeWindowDispatcher()
        # 根据时段调整发帖延迟
        delay_min, delay_max = time_dispatcher.get_adjusted_delay(task.delay_min, task.delay_max)

        for i in range(actual_total):
            # 选取账号：智能撮合，本土原生号优先
            account_id = await self._pick_optimal_account_for_target(task, fnames[0], i, weighted_accounts)

            # 验证码熔断检查
            if captcha_breaker.is_in_cooldown(account_id):
                remaining = captcha_breaker.get_remaining_cooldown(account_id)
                await log_warn(
                    f"账号 [{account_id}] 处于验证码熔断中 (剩余 {remaining:.0f}s)，跳过"
                )
                yield {"status": "skipped", "msg": f"账号 {account_id} 验证码熔断中", "progress": task.progress, "total": task.total}
                continue

            # 连续失败熔断检查
            if failure_breaker.is_in_cooldown(account_id):
                await log_warn(f"账号 [{account_id}] 连续失败熔断中，跳过")
                yield {"status": "skipped", "msg": f"账号 {account_id} 连续失败熔断", "progress": task.progress, "total": task.total}
                continue

            # 账号-贴吧隔离：为该账号找一个不在冷却中的贴吧
            target_fname = await af_tracker.get_available_forum(account_id, fnames)
            if not target_fname:
                remaining = af_tracker.get_remaining_cooldown(account_id, fnames[0])
                await log_warn(
                    f"账号 [{account_id}] 所有目标贴吧均在冷却中 (剩余 {remaining:.0f}s)，跳过本次轮次"
                )
                yield {"status": "skipped", "msg": f"账号 {account_id} 贴吧冷却中", "progress": task.progress, "total": task.total}
                continue

            # 每账号独立限流检查（先选账号再限流，确保每个账号独立计数）
            await per_account_limiter.wait_if_needed(account_id)

            # 检查代理状态：代理失效则跳过该账号
            acc = account_map.get(account_id)
            if acc and acc.status == "suspended_proxy":
                await log_warn(f"账号 [{account_id}] 代理已挂起，跳过本次发帖")
                await failure_breaker.record_failure(account_id)
                yield {"status": "skipped", "msg": f"账号 {account_id} 代理挂起，已跳过", "progress": task.progress, "total": task.total}
                continue

            creds = await get_account_credentials(self.db, account_id)
            if not creds:
                await log_warn(f"账号 [{account_id}] 凭证获取失败，跳过")
                await failure_breaker.record_failure(account_id)
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

            # [Step 1.5: 内容重复度检测]
            passed, max_sim = await similarity_detector.check(title, content)
            if not passed:
                await log_warn(
                    f"[Step 1.5] 内容重复度过高 ({max_sim:.0%})，跳过物料 [{current_material.id}]。"
                    f"建议：启用 AI 改写或等待一段时间后再发。"
                )
                await self.db.update_material_status(
                    current_material.id, "failed", 
                    f"内容重复度过高 ({max_sim:.0%})，疑似机器批量行为"
                )
                yield {"status": "skipped", "msg": f"内容重复度 {max_sim:.0%}", "fname": target_fname, "progress": task.progress, "total": task.total}
                continue

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
                                
                                # 验证码熔断检测
                                triggered = await captcha_breaker.check_and_trigger(account_id, err_msg, err_code)
                                if triggered:
                                    await failure_breaker.record_failure(account_id)
                                    yield {"status": "captcha", "msg": f"账号 {account_id} 触发验证码，暂停", "fname": target_fname, "progress": task.progress, "total": task.total}
                                    continue
                                
                                # 识别全局级封禁与吧务封禁逻辑
                                if err_code == 4 or "封禁" in err_msg or "屏蔽" in err_msg:
                                    if err_code == 3250004 or "本吧" in err_msg or "此吧" in err_msg:
                                        # 局部封禁：仅在该吧熔断
                                        await log_error(f"检测到吧务封禁！账号 {account_id} 已在 {target_fname} 自动熔断。")
                                        await self.db.mark_forum_banned(account_id, target_fname, reason="Step 3 发射检测到吧务封禁")
                                        await failure_breaker.record_failure(account_id)
                                        try: await client.unfollow_forum(target_fname)
                                        except: pass
                                    else:
                                        # 全局封禁：账号级隔离
                                        await log_error(f"🚨 警报：检测到全局封禁特征！账号 {account_id} 已遭到百度彻底封杀。")
                                        await self.db.update_account_status(account_id, "banned")
                                        await failure_breaker.record_failure(account_id)
                                elif err_code == 340001:
                                    await log_warn(f"{target_fname} 正在升级中，已跳过该点位。")
                                    await failure_breaker.record_failure(account_id)
                                    continue
                                else:
                                    # 其他错误也记录失败次数
                                    await failure_breaker.record_failure(account_id)
                    except Exception as e:
                        # [环境诊断] 如果进入此块，str(e) 将明确告知是否仍为 encoding 错误
                        err_msg = f"[Step 3] 通讯过程异常 (账号:{account_id} @ {target_fname}): {str(e)}"
                        await log_error(err_msg)
                        await failure_breaker.record_failure(account_id)
                        await self.db.update_material_status(current_material.id, "failed", err_msg, posted_fname=target_fname, posted_account_id=account_id)
                        yield {"status": "error", "msg": err_msg, "fname": target_fname, "progress": task.progress, "total": task.total}
                        continue

                    if success:
                        task.progress += 1
                        # 重置连续失败计数（成功发帖）
                        await failure_breaker.record_success(account_id)
                        # 账号-贴吧隔离：记录本次发帖，更新冷却时间
                        await af_tracker.record_post(account_id, target_fname)
                        # 记录发帖内容到历史（用于重复度检测）
                        await similarity_detector.record(title, content)
                        await self.db.update_material_status(
                            current_material.id, 
                            "success", 
                            posted_fname=target_fname, 
                            posted_tid=tid,
                            posted_account_id=account_id,
                            posted_time=datetime.now()
                        )
                        await self.db.update_target_pool_status(target_fname, is_success=True)
                        await log_info(
                            f"[{task.strategy}] 账号 {account_id} → {target_fname} 发帖成功 ({task.progress}/{task.total})"
                        )
                        # 拟人化随机休眠逻辑（使用时段调整后的延迟）
                        if i < actual_total - 1:
                            await BionicDelay.sleep(delay_min, delay_max)
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
                        await log_warn(f"回帖熔断取消关注失败: {ue}")
                        
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

    async def follow_forums_bulk(self, fnames: list[str], account_ids: list[int] = None, progress_callback=None) -> dict:
        """
        批量关注贴吧并记录失败结果。

        Args:
            fnames: 要关注的贴吧名称列表
            account_ids: 指定账号列表，None 则使用所有活跃账号
            progress_callback: 进度回调 (current, total)

        Returns:
            dict: {
                "success": [{"account_id", "fname"}],
                "failed": [{"account_id", "fname", "reason"}],
                "skipped": [{"account_id", "fname", "reason"}]
            }
        """
        from .account import get_account_credentials

        result = {"success": [], "failed": [], "skipped": []}

        # 1. 确定要操作的账号
        if account_ids is None:
            all_accounts = await self.db.get_accounts()
            account_ids = [acc.id for acc in all_accounts if acc.status in ("active", "pending")]

        if not account_ids:
            result["failed"].append({"account_id": None, "fname": None, "reason": "无可用账号"})
            return result

        # 2. 过滤掉已经关注且未封禁的吧（避免重复操作）
        async with self.db.async_session() as session:
            from sqlalchemy import select
            from ..db.models import Forum
            stmt = select(Forum.fname, Forum.account_id).where(
                Forum.fname.in_(fnames),
                Forum.account_id.in_(account_ids),
                Forum.is_banned == False  # 已关注且未被封禁
            )
            res = await session.execute(stmt)
            already_following = {(row.account_id, row.fname) for row in res}

        total_actions = len(account_ids) * len(fnames)
        current_action = 0

        for acc_id in account_ids:
            creds = await get_account_credentials(self.db, acc_id)
            if not creds:
                result["skipped"].append({"account_id": acc_id, "fname": None, "reason": "无法获取账号凭证"})
                continue

            _, bduss, stoken, proxy_id, cuid, ua = creds
            try:
                async with await create_client(
                    self.db,
                    bduss=bduss,
                    stoken=stoken,
                    proxy_id=proxy_id,
                    cuid=cuid,
                    ua=ua
                ) as client:
                    for fname in fnames:
                        # 跳过已关注的吧
                        if (acc_id, fname) in already_following:
                            result["skipped"].append({"account_id": acc_id, "fname": fname, "reason": "已关注"})
                            current_action += 1
                            continue

                        try:
                            await client.follow_forum(fname)
                            result["success"].append({"account_id": acc_id, "fname": fname})
                            await log_info(f"账号 {acc_id} 成功关注 [{fname}]")

                            # 同时更新数据库记录
                            from ..db.models import Forum
                            async with self.db.async_session() as session:
                                from sqlalchemy import select
                                # 使用 fname + account_id 查询（独立于 fid 唯一约束）
                                existing = await session.execute(
                                    select(Forum).where(Forum.fname == fname, Forum.account_id == acc_id)
                                )
                                forum = existing.scalar_one_or_none()
                                if not forum:
                                    # 生成唯一的 fid（使用时间戳+随机数确保唯一）
                                    import time
                                    unique_fid = int(time.time() * 1000) % (2**31)
                                    session.add(Forum(fid=unique_fid, fname=fname, account_id=acc_id))
                                    await session.commit()
                        except Exception as e:
                            err_msg = str(e)
                            if "3250004" in err_msg or "已关注" in err_msg:
                                result["skipped"].append({"account_id": acc_id, "fname": fname, "reason": "已关注或无法关注"})
                            elif "被拉黑" in err_msg or "400013" in err_msg:
                                result["failed"].append({"account_id": acc_id, "fname": fname, "reason": "账号被该吧拉黑"})
                                # 标记为封禁（如果 Forum 记录不存在则先创建）
                                import time
                                unique_fid = int(time.time() * 1000) % (2**31)
                                async with self.db.async_session() as session:
                                    from sqlalchemy import select
                                    from ..db.models import Forum
                                    existing = await session.execute(
                                        select(Forum).where(Forum.fname == fname, Forum.account_id == acc_id)
                                    )
                                    forum = existing.scalar_one_or_none()
                                    if forum:
                                        forum.is_banned = True
                                        forum.ban_reason = "批量关注时检测到拉黑"
                                        forum.is_post_target = False
                                    else:
                                        session.add(Forum(
                                            fid=unique_fid, fname=fname, account_id=acc_id,
                                            is_banned=True, ban_reason="批量关注时检测到拉黑"
                                        ))
                                    await session.commit()
                            else:
                                result["failed"].append({"account_id": acc_id, "fname": fname, "reason": err_msg[:50]})
                            await log_error(f"账号 {acc_id} 关注 [{fname}] 失败: {err_msg}")

                        current_action += 1
                        if progress_callback:
                            await progress_callback(current_action, total_actions)

                        # 防止高频拦截
                        await asyncio.sleep(random.uniform(0.5, 1.5))
            except Exception as e:
                result["skipped"].append({"account_id": acc_id, "fname": None, "reason": f"创建客户端失败: {str(e)[:30]}"})
                await log_error(f"创建客户端执行关注任务失败(ID:{acc_id}): {e}")

        await log_info(
            f"批量关注完成：成功 {len(result['success'])}, 失败 {len(result['failed'])}, 跳过 {len(result['skipped'])}"
        )
        return result


class AutoBumpManager:
    """自动回帖(自顶)调度管理器"""
    def __init__(self, db):
        self.db = db
        self.post_manager = BatchPostManager(db)

    async def process_all_candidates(self):
        """扫描并处理所有待自顶的物料"""
        from datetime import datetime, timedelta
        
        # 1. 读取全局配置
        try:
            max_bump_count = int(await self.db.get_setting("max_bump_count", "20"))
            bump_cooldown_minutes = int(await self.db.get_setting("bump_cooldown_minutes", "45"))
            bump_matrix_enabled = (await self.db.get_setting("bump_matrix_enabled", "0")) == "1"
        except Exception:
            max_bump_count, bump_cooldown_minutes, bump_matrix_enabled = 20, 60, False  # 保守值
            
        # 2. 获取开启了自动回帖、发帖成功、且满足冷却时间的物料
        async with self.db.async_session() as session:
            from ..db.models import MaterialPool
            from sqlalchemy import select, and_
            
            threshold_time = datetime.now() - timedelta(minutes=bump_cooldown_minutes)
            
            stmt = select(MaterialPool).where(
                and_(
                    MaterialPool.status == "success",
                    MaterialPool.is_auto_bump == True,
                    MaterialPool.posted_tid != None,
                    MaterialPool.posted_tid != 0,
                    MaterialPool.bump_count < max_bump_count, # 动态安全阈值
                    (MaterialPool.last_bumped_at == None) | (MaterialPool.last_bumped_at < threshold_time)
                )
            )
            result = await session.execute(stmt)
            candidates = result.scalars().all()
            
            if not candidates:
                return

            await log_info(f"发现 {len(candidates)} 个物料满足自动回帖条件 (上限: {max_bump_count})，开始执行...")
            
            # 运行时 Skip-List：防止在同一次扫描中反复背刺已被封的账号
            banned_pairs = set() # (account_id, fname)
            
            # 获取当前全局活跃号作为后备
            default_acc = await self.db.get_active_account()
            
            # 如果开启了矩阵式协同，预加载所有可用账号池
            matrix_pool = []
            if bump_matrix_enabled:
                matrix_pool = await self.db.get_matrix_accounts()

            for material in candidates:
                # --- 账号选取策略：矩阵轮询（推荐）或跳过 ---
                if bump_matrix_enabled and matrix_pool:
                    # 矩阵轮询算法：利用 (物料ID + 已顶次数) 作为种子选取账号，确保同一个物料能轮换使用不同账号
                    # 过滤掉发帖原号，避免看起来不够"协同"
                    potential_accounts = [acc for acc in matrix_pool if acc.id != material.posted_account_id]
                    if not potential_accounts:
                        # 如果没有其他可用号，跳过本次自顶而非回落到原号（防止同号自顶被封）
                        await log_warn(
                            f"物料 [{material.id}] 跳过自顶：矩阵池为空，建议添加更多协同账号再试"
                        )
                        continue
                    else:
                        target_account_id = potential_accounts[material.bump_count % len(potential_accounts)].id
                else:
                    # 未开启矩阵模式时，同号自顶风险极高，直接跳过并警告
                    await log_warn(
                        f"物料 [{material.id}] 跳过自顶：未开启矩阵协同模式。"
                        f"同号自顶极易触发风控导致封号，请在「自顶配置」中开启「矩阵协同模式」。"
                    )
                    continue
                
                if not target_account_id:
                    continue
                
                # 检查动态黑名单
                if (target_account_id, material.posted_fname) in banned_pairs:
                    continue

                # --- 拟人化随机文案引擎 (自然化版本，避免触发反垃圾) ---
                # 新设计原则：多样化长度(5-25字)、多风格、降低模板重合度
                BUMP_TEMPLATES = [
                    # 短句互动类（5-10字）- 模拟路人随手一评
                    "路过", "看了", "点赞", "顶", "收藏",
                    "不错的", "可以", "好贴", "来了", "路过~",
                    # 中等长度类（10-15字）- 轻量评价感
                    "看了下，还行", "写的挺好的", "收藏了", "支持楼主",
                    "内容不错，赞", "有意思", "帮顶一下", "可以可以",
                    "这个确实可以", "路过支持", "mark一下", "看看再说",
                    "内容挺充实的", "感谢分享", "不错的帖子",
                    # 较长评论类（15-25字）- 模拟认真阅读后的评价
                    "看完了，内容挺充实的，赞一个", "写得不错，已收藏",
                    "感谢楼主的整理，辛苦了", "这个系列真的挺好的，收藏了",
                    "认真看完了，支持一下", "内容挺用心的，点赞",
                    "不错的帖子，帮顶支持", "楼主辛苦了，感谢分享",
                    "看完了，感觉还挺有收获的", "收藏了，期待更多好内容",
                    # 随机行为模拟类 - 降低模板可识别性
                    "👍", "✨👍", "好帖", "顶", "已阅", "mark",
                    "👍👍", "好内容", "支持", "写的不错",
                ]
                # emoji 只在低概率下使用，降低可识别性
                RANDOM_EMOJIS = ["[赞]", "✨", "👍", "👍👍", ""]

                # 基于物料标题和随机词库构造（自然化）
                base_text = random.choice(BUMP_TEMPLATES)
                if random.random() < 0.2:  # 20% 概率轻量提及标题关键词（原40%过高易被识别为推广）
                    keyword = (material.title or "")[:8]
                    base_text = f"{keyword} 还行，{base_text}"

                bump_content = f"{base_text} {random.choice(RANDOM_EMOJIS)}"
                    
                success = await self.post_manager.reply_to_thread(
                    target_account_id, 
                    material.posted_fname, 
                    material.posted_tid, 
                    bump_content
                )
                
                if success:
                    await self.db.update_material_bump(material.id)
                    await log_info(f"物料 [{material.id}] 自顶成功 (账号:{target_account_id} | TID:{material.posted_tid})")
                    await asyncio.sleep(random.uniform(15, 45))  # 保守值：增加间隔降低检测风险
                else:
                    banned_pairs.add((target_account_id, material.posted_fname))
                    await log_warn(f"物料 [{material.id}] 自顶失败 (账号:{target_account_id})")

