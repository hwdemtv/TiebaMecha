"""批量发帖核心逻辑：反风控 + AI 变体 + 三种账号调度策略 + 多贴吧支持"""

import asyncio
import json
import logging
import random
import time
import urllib.parse
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# aiotieba exceptions available via aiotieba.exception if needed

from ..db.crud import Database
from ..db.models import Account, Forum
from .account import get_account_credentials
from .client_factory import create_client
from .ai_optimizer import AIOptimizer
from .obfuscator import Obfuscator
from .logger import log_info, log_warn, log_error
from .auth import get_auth_manager, AuthStatus


class RateLimiter:
    """基于滑动时间窗的动态令牌限流器 (支持并发安全)"""
    def __init__(self, rpm: int = 15):
        self.rpm: int = rpm
        self.timestamps: list[float] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        
    async def wait_if_needed(self):
        async with self._lock:
            now: float = time.time()
            # 淘汰一分钟之前的记录
            self.timestamps = [t for t in self.timestamps if now - t < 60]
            
            if len(self.timestamps) >= self.rpm:
                # 基于最早时间戳计算休眠时长
                wait_time: float = 60 - (now - self.timestamps[0]) + 1
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
        self.rpm: int = rpm
        self._account_timestamps: dict[int, list[float]] = {}  # {account_id: [timestamps]}
        self._lock: asyncio.Lock = asyncio.Lock()
    
    async def wait_if_needed(self, account_id: int):
        """检查并等待该账号的限流（锁内计算等待时间，锁外执行休眠，避免阻塞其他账号）"""
        wait_time: float = 0.0
        async with self._lock:
            if account_id not in self._account_timestamps:
                self._account_timestamps[account_id] = []

            timestamps: list[float] = self._account_timestamps[account_id]
            now: float = time.time()

            # 淘汰一分钟之前的记录
            timestamps = [t for t in timestamps if now - t < 60]

            if len(timestamps) >= self.rpm:
                # 基于该账号最早时间戳计算休眠时长
                wait_time = 60 - (now - timestamps[0]) + 1
                # 先淘汰过期记录再写回（锁内），避免 sleep 回来后窗口计数偏低
                self._account_timestamps[account_id] = timestamps
                await log_warn(
                    f"账号 [{account_id}] 触发独立速率限制 ({self.rpm}帖/分)，等待 {wait_time:.1f}s..."
                )
            else:
                timestamps.append(now)
                self._account_timestamps[account_id] = timestamps

        # 在锁外执行休眠，不阻塞其他账号的限流检查
        if wait_time > 0:
            await asyncio.sleep(wait_time)
            # sleep 结束后写入本次发帖时间戳，确保窗口计数正确
            async with self._lock:
                now = time.time()
                ts = [t for t in self._account_timestamps.get(account_id, []) if now - t < 60]
                ts.append(now)
                self._account_timestamps[account_id] = ts
    
    def get_status(self, account_id: int) -> dict[str, Any]:
        """获取指定账号的限流状态"""
        if account_id not in self._account_timestamps:
            return {"account_id": account_id, "rpm": self.rpm, "recent_posts": 0, "can_post": True}
        
        now: float = time.time()
        recent: list[float] = [t for t in self._account_timestamps[account_id] if now - t < 60]
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
        self.cooldown_seconds: float = cooldown_seconds
        self._last_post: dict[tuple[int, str], float] = {}  # {(account_id, fname): timestamp}
        self._lock: asyncio.Lock = asyncio.Lock()
    
    def can_post(self, account_id: int, fname: str) -> bool:
        """检查账号-贴吧组合是否可以发帖（未在冷却中）"""
        key: tuple[int, str] = (account_id, fname)
        last_time: float = self._last_post.get(key, 0)
        return time.time() - last_time >= self.cooldown_seconds
    
    def get_remaining_cooldown(self, account_id: int, fname: str) -> float:
        """获取剩余冷却时间（秒）"""
        key: tuple[int, str] = (account_id, fname)
        last_time: float = self._last_post.get(key, 0)
        elapsed: float = time.time() - last_time
        return max(0, self.cooldown_seconds - elapsed)
    
    async def record_post(self, account_id: int, fname: str):
        """记录一次发帖，更新冷却时间"""
        async with self._lock:
            self._last_post[(account_id, fname)] = time.time()
    
    async def get_available_forum(self, account_id: int, fnames: list[str]) -> str | None:
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
    def __init__(self, cooldown_minutes: int = 30, db: Database | None = None):
        """
        Args:
            cooldown_minutes: 触发验证码后熔断时长，默认30分钟
            db: 数据库实例，传入后会将验证码事件持久化到 CaptchaEvent 表
        """
        self.cooldown_minutes: int = cooldown_minutes
        self._db = db
        self._captcha_triggers: dict[int, float] = {}  # {account_id: timestamp}
        self._lock: asyncio.Lock = asyncio.Lock()

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
                f"🚨 验证码熔断：账号 [{account_id}] 触发验证码，暂停发帖 {self.cooldown_minutes} 分钟。建议：手动验证账号或增加发帖间隔。"
            )
            # [Fix 1] 持久化验证码事件到数据库，供异常记录 tab 展示
            if self._db:
                try:
                    await self._db.save_captcha_event(
                        account_id=account_id,
                        event_type="captcha",
                        reason=f"验证码触发 (code={err_code}): {err_msg[:80]}",
                    )
                except Exception:
                    pass  # 非关键路径，不影响熔断逻辑
            return True
        return False
    
    def is_in_cooldown(self, account_id: int) -> bool:
        """检查账号是否处于验证码熔断中"""
        if account_id not in self._captcha_triggers:
            return False
        
        elapsed: float = time.time() - self._captcha_triggers[account_id]
        if elapsed >= self.cooldown_minutes * 60:
            # 熔断超时，自动解除
            del self._captcha_triggers[account_id]
            return False
        return True
    
    def get_remaining_cooldown(self, account_id: int) -> float:
        """获取验证码熔断剩余时间（秒）"""
        if account_id not in self._captcha_triggers:
            return 0
        elapsed: float = time.time() - self._captcha_triggers[account_id]
        remaining: float = self.cooldown_minutes * 60 - elapsed
        return max(0, remaining)


class ContentSimilarityDetector:
    """
    内容重复度检测器：避免批量发送高度相似的内容被识别为机器行为。
    基于 bigram (2-gram) 字符级 Jaccard 相似度，比字符集方案对长文本更敏感。
    时间窗口扩大到 24 小时，防止跨时段重复内容被百度回溯检测。
    """
    def __init__(self, similarity_threshold: float = 0.7, window_hours: float = 24.0):
        """
        Args:
            similarity_threshold: 相似度阈值，0-1，越高越严格
            window_hours: 检测时间窗口（小时），默认24小时
        """
        self.similarity_threshold: float = similarity_threshold
        self.window_hours: float = window_hours
        self._history: list[tuple[set[str], float]] = []  # [(bigram_set, timestamp)]
        self._lock: asyncio.Lock = asyncio.Lock()

    def _normalize_text(self, text: str) -> str:
        """标准化文本：去除标点、空格、小写化"""
        import re
        text = re.sub(r'[^\w一-鿿]', '', text)  # 只保留字母数字和中文
        return text.lower()

    def _bigrams(self, text: str) -> set[str]:
        """提取文本的 bigram (2-gram) 集合"""
        normalized = self._normalize_text(text)
        if len(normalized) < 2:
            return {normalized} if normalized else set()
        return {normalized[i:i+2] for i in range(len(normalized) - 1)}

    def _compute_similarity(self, bg1: set[str], bg2: set[str]) -> float:
        """计算两个 bigram 集合的 Jaccard 相似度"""
        if not bg1 or not bg2:
            return 0.0
        intersection = len(bg1 & bg2)
        union = len(bg1 | bg2)
        return intersection / union if union > 0 else 0.0

    async def check(self, title: str, content: str) -> tuple[bool, float]:
        """
        检查内容是否与历史内容过于相似。

        Returns:
            (是否通过检查, 最高相似度)
        """
        combined_text = f"{title} {content}"
        new_bigrams = self._bigrams(combined_text)

        async with self._lock:
            max_similarity = 0.0
            # 检查时间窗口内的历史记录（默认24小时）
            cutoff = time.time() - self.window_hours * 3600
            self._history = [(bg, ts) for bg, ts in self._history if ts > cutoff]

            for history_bigrams, _ in self._history:
                similarity = self._compute_similarity(new_bigrams, history_bigrams)
                max_similarity = max(max_similarity, similarity)

            return max_similarity < self.similarity_threshold, max_similarity

    async def record(self, title: str, content: str):
        """记录本次发帖内容的 bigram"""
        combined = f"{title} {content}"
        async with self._lock:
            self._history.append((self._bigrams(combined), time.time()))
            # 只保留最近100条记录
            if len(self._history) > 100:
                self._history = self._history[-100:]

class FailureCircuitBreaker:
    """
    渐进式连续失败熔断器：
    - 第 1 次触发（连续 5 次失败）：暂停 30 分钟
    - 第 2 次触发（24h 内）：暂停 2 小时
    - 第 3 次触发（24h 内）：暂停 6 小时
    避免一刀切导致误杀，也防止短时间内反复触发。
    """
    def __init__(self, max_consecutive_failures: int = 5, base_cooldown: int = 30):
        """
        Args:
            max_consecutive_failures: 连续失败多少次触发熔断
            base_cooldown: 基础熔断时长（分钟），逐级倍增
        """
        self.max_consecutive_failures: int = max_consecutive_failures
        self.base_cooldown: int = base_cooldown
        self._failure_counts: dict[int, tuple[int, float]] = {}  # {account_id: (count, last_failure_time)}
        self._trigger_history: dict[int, list[float]] = {}  # {account_id: [trigger_timestamps]}
        self._lock: asyncio.Lock = asyncio.Lock()

    def _get_cooldown_minutes(self, account_id: int) -> int:
        """根据 24h 内触发次数计算渐进式冷却时长"""
        triggers = self._trigger_history.get(account_id, [])
        now = time.time()
        recent = [t for t in triggers if now - t < 86400]
        level = len(recent)
        multipliers = [1, 4, 12]  # 30min, 2h, 6h
        idx = min(level, len(multipliers) - 1)
        return self.base_cooldown * multipliers[idx]

    async def record_failure(self, account_id: int) -> bool:
        """
        记录一次失败。

        Returns:
            True 表示触发了熔断；False 表示正常
        """
        async with self._lock:
            now: float = time.time()
            if account_id in self._failure_counts:
                count: int
                last_time: float
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
                cooldown = self._get_cooldown_minutes(account_id)
                self._trigger_history.setdefault(account_id, []).append(now)
                # 保留 24h 内的记录
                self._trigger_history[account_id] = [
                    t for t in self._trigger_history[account_id] if now - t < 86400
                ]
                await log_error(
                    f"🚨 渐进式熔断：账号 [{account_id}] 连续失败 {count} 次，"
                    f"暂停 {cooldown} 分钟（24h 内第 {len(self._trigger_history[account_id])} 次触发）。"
                    f"请检查账号状态或网络。"
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

        count: int
        last_time: float
        count, last_time = self._failure_counts[account_id]
        if count < self.max_consecutive_failures:
            return False

        cooldown = self._get_cooldown_minutes(account_id)
        elapsed: float = time.time() - last_time
        if elapsed >= cooldown * 60:
            del self._failure_counts[account_id]
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
        self.quiet_start: int = quiet_start
        self.quiet_end: int = quiet_end
    
    def is_quiet_hours(self) -> bool:
        """判断是否处于静默时段"""
        current_hour = datetime.now().hour
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= current_hour < self.quiet_end
        else:  # 跨天情况如 23-5
            return current_hour >= self.quiet_start or current_hour < self.quiet_end
    
    def get_multiplier(self) -> float:
        """
        获取延迟倍率（区分工作日/周末）：
        - 凌晨 1-6 点：3.0x（风险极高）
        - 早高峰 7-10 点：1.5x
        - 周末活跃窗口更长、延迟更低
        - 工作日午休(12-14点)小高峰：0.9x
        """
        current_hour = datetime.now().hour
        is_weekend = datetime.now().weekday() >= 5

        if 1 <= current_hour < 6:
            return 3.0
        elif 7 <= current_hour < 10:
            return 1.5

        if is_weekend:
            # 周末：用户活跃时段更长，整体延迟偏低
            if 10 <= current_hour <= 22:
                return 0.8
            elif 22 < current_hour <= 24 or 0 <= current_hour < 1:
                return 1.5
            else:
                return 2.5
        else:
            # 工作日：午休和下班后有小高峰
            if 12 <= current_hour < 14:
                return 0.9
            elif 18 <= current_hour <= 22:
                return 0.85
            elif 22 < current_hour <= 24 or 0 <= current_hour < 1:
                return 2.0
            else:
                return 1.0
    
    def get_adjusted_delay(self, min_delay: float, max_delay: float) -> tuple[float, float]:
        """获取调整后的延迟范围"""
        multiplier = self.get_multiplier()
        return (min_delay * multiplier, max_delay * multiplier)


class AutoWeightCalculator:
    """
    自动权重计算器：根据账号多维度指标自动计算推荐发帖权重。

    计算因素及默认权重：
    - 平均贴吧等级 (30%): 线性插值平滑过渡
    - 签到成功率 (25%): success/(total||1) * 10
    - 账号状态 (20%): active=10, pending=7, error=3, 其他=1
    - 代理绑定 (15%): 已绑定=10, 未绑定=5
    - 验证时效 (10%): 7天内=10, 30天内=7, 60天+=5, 从未=1
    """

    # 默认权重比例 (用户可通过 settings 表自定义)
    DEFAULT_WEIGHTS: dict[str, float] = {
        "level": 0.30,
        "sign": 0.25,
        "status": 0.20,
        "proxy": 0.15,
        "verified": 0.10,
    }

    _WEIGHT_LABELS: dict[str, str] = {
        "level": "贴吧等级",
        "sign": "签到成功率",
        "status": "账号状态",
        "proxy": "代理绑定",
        "verified": "验证时效",
    }

    @classmethod
    async def get_weight_ratios(cls, db) -> dict[str, float]:
        """从 settings 表加载自定义权重比例，若无配置或无效则返回默认值"""
        raw = await db.get_setting("auto_weight_ratios", "")
        if raw:
            try:
                ratios = json.loads(raw)
                if all(k in ratios for k in cls.DEFAULT_WEIGHTS):
                    total = sum(ratios.values())
                    if 0.95 <= total <= 1.05:
                        return {k: float(ratios[k]) for k in cls.DEFAULT_WEIGHTS}
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        return cls.DEFAULT_WEIGHTS.copy()

    @classmethod
    def calc_level_score(cls, avg_level: float) -> float:
        """根据平均等级计算得分 (1-10)，使用线性插值实现平滑过渡"""
        # 节点: (level, score) — 保留原始分档的关键点，中间线性插值
        knots = [
            (0, 1.0),
            (1, 2.0),
            (3, 3.0),
            (6, 5.0),
            (10, 7.0),
            (15, 10.0),
        ]

        if avg_level <= knots[0][0]:
            return knots[0][1]
        if avg_level >= knots[-1][0]:
            return knots[-1][1]

        for i in range(len(knots) - 1):
            lv_low, sc_low = knots[i]
            lv_high, sc_high = knots[i + 1]
            if lv_low <= avg_level <= lv_high:
                t = (avg_level - lv_low) / (lv_high - lv_low)
                return sc_low + t * (sc_high - sc_low)

        return knots[-1][1]

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
    def calculate_sync(
        cls,
        account: Account,
        forums: list[Forum] | None = None,
        ratios: dict[str, float] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """
        同步版权重计算（供批量循环调用，避免重复 db 查询）。

        Args:
            account: Account 对象
            forums: 关联的 Forum 列表，可选
            ratios: 权重比例字典，为 None 时使用 DEFAULT_WEIGHTS

        Returns:
            (推荐权重 1-10, 详细得分字典)
        """
        if ratios is None:
            ratios = cls.DEFAULT_WEIGHTS

        # 1. 计算平均等级得分
        if forums:
            levels: list[int] = [f.level for f in forums if f.level > 0]
            avg_level: float = sum(levels) / len(levels) if levels else 0.0
        else:
            avg_level = 0.0
        level_score = cls.calc_level_score(avg_level)

        # 2. 计算签到成功率得分
        if forums:
            total_signs: int = sum(f.history_total for f in forums)
            success_signs: int = sum(f.history_success for f in forums)
        else:
            total_signs, success_signs = 0, 0
        sign_score = cls.calc_sign_score(success_signs, total_signs)

        # 3. 账号状态得分
        status_score: float = cls.calc_status_score(account.status)

        # 4. 代理绑定得分
        proxy_score: float = cls.calc_proxy_score(account.proxy_id is not None)

        # 5. 验证时效得分
        verified_score: float = cls.calc_verified_score(account.last_verified)

        # 6. 加权计算总分 (使用动态比例)
        total_score: float = (
            level_score * ratios["level"]
            + sign_score * ratios["sign"]
            + status_score * ratios["status"]
            + proxy_score * ratios["proxy"]
            + verified_score * ratios["verified"]
        )

        # 7. 映射到 1-10 范围
        final_weight: int = max(1, min(10, round(total_score)))

        # 7.5 终态账号强制下限: banned/suspended/expired 权重锁定为 1
        _TERMINAL_STATUSES = {"banned", "suspended", "expired"}
        if account.status in _TERMINAL_STATUSES:
            final_weight = 1

        # 8. 构建详细得分报告
        details: dict[str, Any] = {
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
            "ratios": {k: round(v, 2) for k, v in ratios.items()},
        }
        if account.status in _TERMINAL_STATUSES:
            details["note"] = f"账号状态为 {account.status}，权重强制设为 1"

        return final_weight, details

    @classmethod
    async def calculate(
        cls,
        account: Account,
        forums: list[Forum] | None = None,
        db=None,
    ) -> tuple[int, dict[str, Any]]:
        """
        异步版权重计算：自动从 settings 加载权重比例后委托给 calculate_sync。

        Args:
            account: Account 对象
            forums: 关联的 Forum 列表，可选
            db: Database 实例，用于读取自定义权重比例

        Returns:
            (推荐权重 1-10, 详细得分字典)
        """
        ratios = cls.DEFAULT_WEIGHTS
        if db is not None:
            ratios = await cls.get_weight_ratios(db)
        return cls.calculate_sync(account, forums, ratios)


class BionicDelay:
    """拟人化随机延迟驱动器 (基于高斯分布与生物钟权重)"""
    @staticmethod
    def get_delay(min_sec: float, max_sec: float) -> float:
        min_sec = max(1, min_sec)
        max_sec = max(min_sec, max_sec)
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
        
        # 4. 边界裁剪 (严格限制在 [min_sec, max_sec] 范围内)
        return max(min_sec, min(delay, max_sec))

    @staticmethod
    async def sleep(min_sec: float, max_sec: float):
        delay = BionicDelay.get_delay(min_sec, max_sec)
        await log_info(f"拟人化休眠 {delay:.1f}s")
        await asyncio.sleep(delay)


@dataclass
class BatchPostTask:
    """批量发帖任务配置"""
    id: str
    # 目标贴吧（支持多个）
    fname: str                              # 兼容旧字段
    fnames: list[str] = field(default_factory=list)  # 多贴吧列表（优先）
    accounts: list[int] = field(default_factory=list)
    # 发帖策略: round_robin（轮询）/ random（随机）/ weighted（加权）
    strategy: str = "round_robin"
    # 文案组合模式: random (随机) / strict (精准匹配：物料索引与贴吧索引绑定)
    pairing_mode: str = "random"
    # 账号临时权重覆盖（key=account_id, value=权重1–10）
    # 不为空时，覆盖数据库中的全局 post_weight
    weight_override: dict[int, int] = field(default_factory=dict)
    delay_min: float = 120.0  # 保守值：降低被检测风险
    delay_max: float = 600.0  # 保守值：降低被检测风险
    use_ai: bool = False
    ai_persona: str = None  # AI 人格选择（None = 自动按时段轮换）
    status: str = "pending"
    progress: int = 0
    total: int = 0
    start_time: datetime | None = None

    def get_fnames(self) -> list[str]:
        """获取目标贴吧列表（优先使用 fnames，回落 fname）"""
        if self.fnames:
            return self.fnames
        if self.fname:
            return [self.fname]
        return []


class BatchPostManager:
    """管理大规模异步发帖任务，支持三种账号调度策略"""

    def __init__(self, db: Database):
        self.db: Database = db
        self._active_tasks: dict[str, Any] = {}

    def _weighted_choice(self, accounts_with_weights: list[tuple[int, int]]) -> int:
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

    # 应完全排除出加权池的终态状态
    _EXCLUDED_STATUSES: set[str] = {"banned", "suspended", "expired"}

    async def _build_weighted_accounts(self, task: BatchPostTask, all_accounts: list[Account] | None = None) -> list[tuple[int, int]]:
        """
        构建账号+权重列表。临时覆盖优先于数据库全局权重。
        封禁/过期/停用账号会被自动排除。

        Returns:
            [(account_id, weight), ...]

        Note:
            预构建权重列表,避免在发帖循环中重复查询数据库(N+1问题)
        """
        # 优先使用传入的账号列表，避免重复查询
        if all_accounts is None:
            all_accounts = await self.db.get_accounts()
        account_map = {acc.id: acc for acc in all_accounts}

        result: list[tuple[int, int]] = []
        for acc_id in task.accounts:
            acc_obj = account_map.get(acc_id)

            # 终态账号直接排除，不参与加权选择
            if acc_obj and acc_obj.status in self._EXCLUDED_STATUSES:
                continue

            # 优先使用临时覆盖权重
            if acc_id in task.weight_override:
                weight: int = max(1, min(10, task.weight_override[acc_id]))
            else:
                weight = acc_obj.post_weight if acc_obj else 5
            result.append((acc_id, weight))
        return result

    async def _pick_account(self, task: BatchPostTask, step: int, weights: list[tuple[int, int]]) -> int:
        """
        根据任务策略选取本次发帖使用的账号 ID。

        Args:
            task: 任务对象
            step: 当前步骤序号（从 0 开始）
            weights: [(account_id, weight), ...]

        Returns:
            account_id

        Raises:
            ValueError: 当没有可用账号时
        """
        # 检查账号列表是否为空
        if not task.accounts:
            raise ValueError("没有可用的账号，请先配置发帖账号")

        if task.strategy in ("round_robin", "strict_round_robin"):
            return task.accounts[step % len(task.accounts)]
        elif task.strategy == "random":
            return random.choice(task.accounts)
        elif task.strategy == "weighted":
            if not weights:
                # 如果权重列表为空，回退到随机选择
                return random.choice(task.accounts)
            return self._weighted_choice(weights)
        else:
            return task.accounts[step % len(task.accounts)]

    @staticmethod
    def get_tactical_advice(err_msg: str) -> dict[str, str]:
        """战术情报分析：将生硬的报错转化为实战建议"""
        advice_map: dict[str, dict[str, str]] = {
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

    async def _build_native_account_map(self, accounts: list[int]) -> dict[str, list[int]]:
        """
        预构建 贴吧名→原生账号ID列表 映射，避免在发帖循环中逐次查库 (N+1问题)。

        Returns:
            {fname: [account_id, ...]} - 已关注且 is_post_target=True 且未封禁的账号列表
        """
        from sqlalchemy import select

        async with self.db.async_session() as session:
            stmt = select(Forum.fname, Forum.account_id).where(
                Forum.is_post_target == True,
                Forum.is_banned == False,
                Forum.account_id.in_(accounts)
            ).order_by(Forum.account_id.asc())
            result = await session.execute(stmt)
            mapping: dict[str, list[int]] = {}
            for fname, acc_id in result.all():
                mapping.setdefault(fname, []).append(acc_id)
            return mapping

    async def _build_followed_account_map(self, accounts: list[int]) -> dict[str, list[int]]:
        """
        预构建 贴吧名→普通关注账号ID列表 映射 (仅 is_post_target=False，原生号由 native_map 单独处理)。

        Returns:
            {fname: [account_id, ...]} - 已关注且未封禁的普通关注账号列表
        """
        from sqlalchemy import select

        async with self.db.async_session() as session:
            stmt = select(Forum.fname, Forum.account_id).where(
                Forum.is_banned == False,
                Forum.is_post_target == False,
                Forum.account_id.in_(accounts)
            ).order_by(Forum.account_id.asc())
            result = await session.execute(stmt)
            mapping: dict[str, list[int]] = {}
            for fname, acc_id in result.all():
                mapping.setdefault(fname, []).append(acc_id)
            return mapping

    async def _pick_optimal_account_for_target(self, task: BatchPostTask, target_fname: str, step: int, weights: list[tuple[int, int]], native_map: dict[str, list[int]], followed_map: dict[str, list[int]]) -> int:
        """
        靶场智能撮合核心：优先寻找本号已关注且 is_post_target=True 的原生号
        这极大提高了防抽几率（本土作战）。同时跳过已被该吧封禁的账号。
        
        Args:
            native_map: 预构建的贴吧→原生安全账号映射
            followed_map: 预构建的贴吧→关注账号映射
        """
        # 严格轮询：从轮询位置向后搜索，优先找到已关注该吧的账号
        # 搜索顺序：原生号 → 关注号 → 纯轮询（空降）
        # 既保持全局均匀分配，又最大化成功率
        if task.strategy == "strict_round_robin":
            n = len(task.accounts)
            native_accounts = native_map.get(target_fname, [])
            available_accounts = followed_map.get(target_fname, [])
            for offset in range(n):
                candidate = task.accounts[(step + offset) % n]
                if candidate in native_accounts:
                    return candidate
            for offset in range(n):
                candidate = task.accounts[(step + offset) % n]
                if candidate in available_accounts:
                    return candidate
            return task.accounts[step % len(task.accounts)]

        # 1. 优先尝试：安全原生号 (关注了该吧且设为发布目标)
        native_accounts = native_map.get(target_fname, [])
        if native_accounts:
            if task.strategy == "round_robin":
                # 从轮询位置向后搜索，找到第一个在原生列表中的账号，保持全局均匀
                n = len(task.accounts)
                for offset in range(n):
                    candidate = task.accounts[(step + offset) % n]
                    if candidate in native_accounts:
                        return candidate
            elif task.strategy == "weighted":
                filtered = [(a, w) for a, w in weights if a in native_accounts]
                if filtered:
                    return self._weighted_choice(filtered)
            return random.choice(native_accounts)

        # 2. 次优尝试：普通关注号 (关注了该吧但未设为目标，或未勾选安全开关)
        available_accounts = followed_map.get(target_fname, [])
        if available_accounts:
            if task.strategy == "round_robin":
                n = len(task.accounts)
                for offset in range(n):
                    candidate = task.accounts[(step + offset) % n]
                    if candidate in available_accounts:
                        return candidate
            elif task.strategy == "weighted":
                filtered = [(a, w) for a, w in weights if a in available_accounts]
                if filtered:
                    return self._weighted_choice(filtered)
            return random.choice(available_accounts)

        # 3. 最终回退：大盘调度策略 (空降兵打法)
        return await self._pick_account(task, step, weights)

    async def execute_task(self, task: BatchPostTask, material_ids: list[int] | None = None) -> AsyncGenerator[dict[str, Any], None]:
        """
        执行批量发帖任务。支持多贴吧、三种策略、临时权重覆盖。

        Args:
            task: 任务配置对象
            material_ids: 可选，指定要使用的物料 ID 列表。为 None 时使用全局 pending 物料。

        Yields:
            dict: {status, tid/msg, progress, total, fname, account_id}
        """
        import httpx
        task.progress = 0

        # [风控增强] 执行前时段风险评估
        time_dispatcher = TimeWindowDispatcher()
        if time_dispatcher.is_quiet_hours():
            current_hour = datetime.now().hour
            await log_warn(
                f"⚠️ 时段风险检测: 当前 {current_hour}:00 处于高风险时段 (凌晨1-6点)，系统将自动启用双倍延迟保护"
            )

        # --- 授权门控与配额限制 ---
        am = await get_auth_manager()
        # 强制在任务启动前刷新一次本地状态，避免异步加载延迟导致的权限误判 (修复 AI 开启无效问题)
        _ = await am.check_local_status()
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
                stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
                if stdout_reconfigure:
                    stdout_reconfigure(encoding='utf-8', errors='replace')
                stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
                if stderr_reconfigure:
                    stderr_reconfigure(encoding='utf-8', errors='replace')
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
        # 同时复用此查询结果构建 account_map，避免重复查询
        all_accounts = await self.db.get_accounts()
        account_map = {acc.id: acc for acc in all_accounts}
        weighted_accounts = await self._build_weighted_accounts(task, all_accounts)

        # 预构建贴吧→账号映射，避免循环中 N+1 查询
        native_map = await self._build_native_account_map(task.accounts)
        followed_map = await self._build_followed_account_map(task.accounts)

        # 从数据库拉取物料：指定 ID 列表或全局 pending 物料
        if material_ids:
            pending_materials = await self.db.get_materials_by_ids(material_ids)
            # 只保留仍为 pending 状态的（防止并发任务争抢）
            pending_materials = [m for m in pending_materials if m.status == "pending"]
        else:
            pending_materials = await self.db.get_materials(status="pending")
        if not pending_materials:
            yield {"status": "failed", "msg": "物料池为空或没有待发(pending)物料，请先录入或重置状态"}
            return

        # 调整总执行次数不超过物料上限
        if task.total <= 0:
            yield {"status": "failed", "msg": "发帖数量未设置（total=0），请在任务配置中指定发帖数量"}
            return
        actual_total = min(task.total, len(pending_materials))
        if actual_total < task.total:
            await log_warn(f"可用 pending 物料不足：需要 {task.total} 条，实际可用 {len(pending_materials)} 条，已自动调整")
        task.total = actual_total

        # 每账号独立RPM限制器：替代全局 RateLimiter，实现精细化控制
        per_account_limiter = PerAccountRateLimiter(rpm=5)  # 每账号5帖/分
        # 账号-贴吧独立冷却跟踪器：防止同账号短时间跨多贴吧被检测
        af_tracker = AccountForumCooldown(cooldown_seconds=600)  # 10分钟独立冷却
        # 验证码熔断器：检测验证码后自动暂停账号
        captcha_breaker = CaptchaCircuitBreaker(cooldown_minutes=30, db=self.db)
        # 内容重复度检测器：避免发送高度相似内容（24h窗口）
        similarity_detector = ContentSimilarityDetector(similarity_threshold=0.7, window_hours=24.0)
        # 渐进式连续失败熔断器：连续失败N次后暂停，24h内重复触发逐级加重
        failure_breaker = FailureCircuitBreaker(max_consecutive_failures=5, base_cooldown=30)
        # 根据时段调整发帖延迟（复用顶部已创建的 time_dispatcher）
        delay_min, delay_max = time_dispatcher.get_adjusted_delay(task.delay_min, task.delay_max)

        # [重构核心] 智能化调度与多账号 Failover 循环体系
        material_ptr = 0
        consecutive_no_account_skips = 0  # 连续"无可用账号"跳过计数，用于检测死锁
        while task.progress < actual_total and material_ptr < len(pending_materials):
            current_material = pending_materials[material_ptr]
            
            # --- [Step 0: 阵地轮替与账号精准匹配] ---
            # strict 模式：物料索引与贴吧索引严格绑定（1对1精准匹配，不允许阵地跳转）
            # random 模式：物料索引轮替贴吧（负载均衡，允许冷却时动态跳转）
            base_target_fname = fnames[material_ptr % len(fnames)]
            
            # [死锁检测] 检查是否还有任何账号可用（非熔断、非封禁、非暂停代理）
            available_account_ids = [
                aid for aid in task.accounts
                if not captcha_breaker.is_in_cooldown(aid)
                and not failure_breaker.is_in_cooldown(aid)
                and (aid in account_map and account_map[aid].status != "suspended_proxy")
            ]
            if not available_account_ids:
                consecutive_no_account_skips += 1
                if consecutive_no_account_skips >= 3:
                    await log_warn("⚠️ 连续3个物料无可用账号（全部熔断/封禁/暂停），提前终止任务")
                    yield {
                        "status": "error",
                        "msg": f"所有账号均不可用（熔断/封禁），任务提前终止。已成功 {task.progress}/{task.total}",
                        "progress": task.progress, "total": task.total,
                    }
                    break
                # 还没达到阈值，跳过本物料等待账号恢复
                await log_warn(f"物料 [{current_material.id}] 暂无可用账号，跳过等待恢复 ({consecutive_no_account_skips}/3)")
                material_ptr += 1
                continue
            else:
                consecutive_no_account_skips = 0  # 有可用账号则重置计数
            
            # 容灾跟踪
            tried_accounts = set()
            max_account_retries = min(3, len(available_account_ids))
            success_for_this_material = False
            forum_permission_denied = False  # 贴吧级权限不足标志
            current_target_fname = base_target_fname  # 初始化，避免循环内未绑定
            
            for attempt_idx in range(max_account_retries):
                # 选取账号：传入 material_ptr+attempt_idx 以保证 Failover 时的轮转顺序
                account_id = await self._pick_optimal_account_for_target(
                    task, base_target_fname, material_ptr + attempt_idx, weighted_accounts, native_map, followed_map
                )
                
                # 强行排除重复尝试
                if account_id in tried_accounts:
                    remaining = [aid for aid in task.accounts if aid not in tried_accounts]
                    if not remaining: break
                    account_id = random.choice(remaining)
                
                tried_accounts.add(account_id)
                current_target_fname = base_target_fname
                
                # --- [状态预检] ---
                if captcha_breaker.is_in_cooldown(account_id) or failure_breaker.is_in_cooldown(account_id):
                    continue

                # [防检测] 代理预热期检查：新绑定代理的账号在预热期内不允许发帖
                from .proxy import get_warmup_manager
                warmup_mgr = get_warmup_manager()
                if await warmup_mgr.needs_warmup(self.db, account_id):
                    remaining = await warmup_mgr.get_remaining_hours(self.db, account_id)
                    await log_warn(
                        f"账号 [{account_id}] 代理预热期中，剩余 {remaining:.1f}h，跳过发帖"
                    )
                    continue

                # 阵地冷却检查与动态跳转
                if not af_tracker.can_post(account_id, current_target_fname):
                    if task.pairing_mode == "strict":
                        # 严格配对模式：不允许阵地跳转，该账号此贴吧冷却中则换号
                        continue
                    alt_fname = await af_tracker.get_available_forum(account_id, fnames)
                    if alt_fname:
                        current_target_fname = alt_fname
                    else:
                        continue # 该账号没坑位了，换下一个号尝试本物料
                
                acc = account_map.get(account_id)
                if not acc or acc.status == "suspended_proxy":
                    continue
                
                # 独立限流等待
                await per_account_limiter.wait_if_needed(account_id)
                
                creds = await get_account_credentials(self.db, account_id)
                if not creds: continue
                _, bduss, stoken, proxy_id, cuid, ua = creds

                # --- [Step 1: 文案重组与仿生微扰] ---
                title = current_material.title
                content = current_material.content
                
                # AI 改写
                if task.use_ai:
                    try:
                        optimizer = AIOptimizer(self.db)
                        s_ai, opt_t, opt_c, _ = await asyncio.wait_for(optimizer.optimize_post(title, content, persona=task.ai_persona), timeout=30.0)
                        if s_ai: 
                            title, content = opt_t, opt_c
                            await self.db.update_material_ai(current_material.id, title, content)
                    except Exception as ai_err:
                        await log_warn(f"AI改写失败，使用原文: {ai_err}")
                
                # [关键强化] 注入随机符号/表情，打破内容 Hash
                content = Obfuscator.inject_random_symbols(content)
                
                # 零宽字符与间距混淆
                content = Obfuscator.inject_zero_width_chars(content, density=0.15)
                safe_content = Obfuscator.humanize_spacing(content)
                
                # 重复度熔断
                passed, _ = await similarity_detector.check(title, safe_content)
                if not passed:
                    await log_warn(f"物料 [{current_material.id}] 历史重复度过高，已跳过")
                    break # 该物料本身有问题，不再换号试，直接换物料

                # --- [Step 2: 仿真协议发射链] ---
                try:
                    async with await create_client(self.db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
                        # 2.1 模拟个人身份获取 (产生轨迹并获取 TBS)
                        try: await client.get_self_info()
                        except Exception: pass  # 非关键操作，TBS获取失败会在后续判断中跳过
                        if not getattr(client.account, 'tbs', None): continue
                        
                        # 2.2 仿生预热浏览 (模拟进入贴吧首页)
                        quoted_fname = urllib.parse.quote(current_target_fname)
                        
                        # 代理处理：SOCKS5 必须将凭据嵌入 URL，BasicAuth 对 SOCKS5 握手层无效
                        proxy_model = await self.db.get_proxy(proxy_id) if proxy_id else None
                        proxy_url = None
                        if proxy_model:
                            from .account import decrypt_value
                            p_user = decrypt_value(proxy_model.username) if proxy_model.username else ""
                            p_pwd = decrypt_value(proxy_model.password) if proxy_model.password else ""
                            proto = proxy_model.protocol
                            host_port = f"{proxy_model.host}:{proxy_model.port}"

                            if p_user and p_pwd:
                                # 将认证信息直接嵌入 URL，适用于 SOCKS5 / HTTP 所有协议
                                # 参考 proxy.py _build_proxy_config 的处理逻辑保持一致
                                u = urllib.parse.quote(p_user, safe="")
                                p = urllib.parse.quote(p_pwd, safe="")
                                proxy_url = f"{proto}://{u}:{p}@{host_port}"
                            else:
                                # 匿名代理（无认证）
                                proxy_url = f"{proto}://{host_port}"

                        headers = {
                            "Cookie": f"BDUSS={bduss}; STOKEN={stoken}",
                            "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            "Referer": f"https://tieba.baidu.com/f?kw={quoted_fname}",
                            "Origin": "https://tieba.baidu.com",
                            "Accept-Language": f"zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        }
                        # 安全提示：headers 中包含敏感凭证(BDUSS/STOKEN)，禁止在日志中打印此对象

                        # 统一使用 proxy_url（凭据已嵌入），不再单独传 auth 参数
                        # 使用 try-finally 确保 HTTP 客户端在所有情况下都能正确关闭
                        _http_client = httpx.AsyncClient(proxy=proxy_url)
                        async with _http_client as http_client:
                            # 预热延迟
                            try:
                                await http_client.get(f"https://tieba.baidu.com/f?kw={quoted_fname}", headers=headers, timeout=10.0)
                                await asyncio.sleep(random.uniform(1.2, 3.5))
                            except Exception as _e: logging.debug(f"预热浏览非关键失败: {_e}")
                                
                            forum_info = await client.get_forum(current_target_fname)
                            # 贴吧API需要\r\n作为换行符，而不是\n
                            safe_content_tieba = safe_content.replace('\n', '\r\n')
                            data = {
                                "ie": "utf-8", "kw": current_target_fname, "fid": getattr(forum_info, 'fid', 0),
                                "tbs": client.account.tbs, "title": title, "content": safe_content_tieba, "anonymous": 0
                            }
                            body_content = urllib.parse.urlencode(data).encode('utf-8')
                            
                            res = await http_client.post(
                                "https://tieba.baidu.com/f/commit/thread/add",
                                headers=headers, content=body_content, timeout=25.0
                            )
                            res_json = res.json()
                            err_code = res_json.get("err_code", 0)
                            
                            if err_code == 0:
                                tid = res_json.get("data", {}).get("tid", 0)
                                success_for_this_material = True
                                task.progress += 1
                                await failure_breaker.record_success(account_id)
                                await af_tracker.record_post(account_id, current_target_fname)
                                await similarity_detector.record(title, safe_content)
                                await self.db.update_material_status(
                                    current_material.id, "success",
                                    posted_fname=current_target_fname, posted_tid=tid,
                                    posted_account_id=account_id, posted_time=datetime.now(),
                                    task_id=str(task.id)
                                )
                                # --- 集成：流水持久化 ---
                                await self.db.add_batch_post_log(
                                    task_id=str(task.id),
                                    account_id=account_id,
                                    account_name=acc.user_name or acc.name if acc else f"账号(ID:{account_id})",
                                    fname=current_target_fname,
                                    title=title,
                                    tid=tid,
                                    status="success",
                                    data={"progress": task.progress, "total": task.total}
                                )
                                acc_display = acc.user_name or acc.name if acc else f"账号(ID:{account_id})"
                                await log_info(f"[{task.strategy}] 成功: {acc_display} @ {current_target_fname} ({task.progress}/{task.total})")
                                # 记录靶场击穿
                                await self.db.update_target_pool_status(current_target_fname, is_success=True)
                                if task.progress < actual_total:
                                    await BionicDelay.sleep(delay_min, delay_max)
                                yield {
                                    "status": "success", "tid": tid, "fname": current_target_fname,
                                    "account_id": account_id,
                                    "account_name": acc.user_name or acc.name if acc else f"账号(ID:{account_id})",
                                    "title": title,
                                    "material_id": current_material.id,
                                    "progress": task.progress, "total": task.total,
                                }
                                break # 退出账号重试循环
                            else:
                                err_msg = str(res_json.get('error') or res_json)
                                # 验证码与熔断逻辑集成
                                await captcha_breaker.check_and_trigger(account_id, err_msg, err_code)
                                await failure_breaker.record_failure(account_id)
                                
                                # 封禁逻辑识别
                                if err_code == 4 or "封禁" in err_msg:
                                    if "本吧" in err_msg:
                                        await self.db.mark_forum_banned(account_id, current_target_fname, reason="发射检测吧封")
                                        await self.db.update_target_pool_status(current_target_fname, is_success=False, error_reason="发射检测吧封")
                                    else:
                                        await self.db.update_account_status(account_id, "banned")
                                
                                # 权限不足识别：贴吧级限制 → 换贴吧而非换号
                                elif "没有权限" in err_msg or "权限不足" in err_msg or "无权" in err_msg:
                                    await self.db.mark_forum_banned(account_id, current_target_fname, reason="用户没有权限")
                                    await self.db.update_target_pool_status(current_target_fname, is_success=False, error_reason="用户没有权限")
                                    await log_warn(f"贴吧 [{current_target_fname}] 权限不足，标记靶场并换贴吧继续...")
                                    forum_permission_denied = True
                                    break  # 退出账号重试循环，让外层换贴吧/物料
                                elif "等级" in err_msg or "级别" in err_msg:
                                    await self.db.mark_forum_banned(account_id, current_target_fname, reason=f"等级限制: {err_msg}")
                                    await self.db.update_target_pool_status(current_target_fname, is_success=False, error_reason=f"等级限制: {err_msg}")
                                    await log_warn(f"贴吧 [{current_target_fname}] 存在等级限制，标记靶场并换贴吧继续...")
                                    forum_permission_denied = True
                                    break  # 退出账号重试循环，让外层换贴吧/物料
                                else:
                                    acc_display = acc.user_name or acc.name if acc else f"账号(ID:{account_id})"
                                    await log_warn(f"账号 {acc_display} 发射遭拦截: {err_msg}，准备换号重试...")
                except Exception as ex:
                    acc_info = account_map.get(account_id)
                    acc_display = acc_info.user_name or acc_info.name if acc_info else f"账号(ID:{account_id})"
                    await log_error(f"执行链异常 ({acc_display} @ {current_target_fname}): {str(ex)}")
                    await failure_breaker.record_failure(account_id)
            
            if not success_for_this_material:
                if forum_permission_denied:
                    # 贴吧权限不足：不标记物料失败，尝试下一个贴吧/物料
                    await self.db.add_batch_post_log(
                        task_id=str(task.id),
                        fname=current_target_fname,
                        status="skip",
                        message=f"贴吧权限不足，跳过: {current_target_fname}",
                        title=current_material.title,
                        data={"progress": task.progress, "total": task.total}
                    )
                    yield {"status": "skipped", "msg": f"贴吧 [{current_target_fname}] 权限不足，跳过换吧", "progress": task.progress, "total": task.total}
                else:
                    # --- 集成：失败流水持久化 ---
                    await self.db.add_batch_post_log(
                        task_id=str(task.id),
                        fname=base_target_fname,
                        status="error",
                        message="物料已由多个账号尝试均告失败，可能触发内容风控",
                        title=current_material.title,
                        data={"progress": task.progress, "total": task.total}
                    )
                    await self.db.update_material_status(current_material.id, "failed", last_error="多账号 Failover 尝试后均失败", task_id=str(task.id))
                    # 记录靶场拦截
                    await self.db.update_target_pool_status(base_target_fname, is_success=False, error_reason="多账号尝试均失败")
                    yield {"status": "error", "msg": "物料已由多个账号尝试均告失败，可能内容已变味", "progress": task.progress, "total": task.total}

            material_ptr += 1

        task.status = "completed"
        await log_info(f"批量发帖任务完成: {fnames} | 成功: {task.progress}/{task.total}")

    async def reply_to_thread(self, account_id: int, fname: str, tid: int, content: str) -> bool:
        """基础回帖/自顶实现"""
        from .account import get_account_credentials
        from .client_factory import create_client
        from .obfuscator import Obfuscator
        
        # 预构建账号名映射
        _all_accs = await self.db.get_accounts()
        _reply_acc_name = next((a.user_name or a.name or f"账号(ID:{a.id})" for a in _all_accs if a.id == account_id), f"账号(ID:{account_id})")
        
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
                    await self.db.update_target_pool_status(fname, is_success=False, error_reason="回帖触发吧务封禁")
                    try:
                        await client.unfollow_forum(fname)
                    except Exception as ue:
                        await log_warn(f"回帖熔断取消关注失败: {ue}")
                        
                    # 寻找关联物料并关闭自动回帖，防止持续背刺
                    async with self.db.async_session() as session:
                        from ..db.models import MaterialPool
                        from sqlalchemy import update
                        _ = await session.execute(
                            update(MaterialPool).where(MaterialPool.posted_tid == tid).values(is_auto_bump=False)
                        )
                        await session.commit()
                    await log_warn(f"账号 {_reply_acc_name} 在 {fname} 遭遇封禁，已转入标记熔断并紧急关闭 TID:{tid} 的自动回帖。")
                
                return False

    async def unfollow_forums_bulk(self, fnames: list[str], progress_callback=None):
        """
        批量取消关注并清理数据库记录。
        内置反风控防护：PerAccountRateLimiter / CaptchaCircuitBreaker /
        FailureCircuitBreaker / BionicDelay / TimeWindowDispatcher / 账号间延迟。
        """
        from .account import get_account_credentials

        # ---- 反风控组件初始化 ----
        rate_limiter = PerAccountRateLimiter(rpm=8)
        captcha_breaker = CaptchaCircuitBreaker(cooldown_minutes=30, db=self.db)
        failure_breaker = FailureCircuitBreaker(max_consecutive_failures=3, cooldown_minutes=60)
        time_window = TimeWindowDispatcher(quiet_start=1, quiet_end=6)

        # 1. 识别受影响的账号
        account_ids = await self.db.get_account_ids_following_forums(fnames)

        # 跟踪每个账号成功取关的贴吧 {(account_id, fname)}
        successful_unfollows: set[tuple[int, str]] = set()
        # 跟踪完全未尝试取关的账号（熔断/无凭证跳过）
        skipped_accounts: set[int] = set()

        # 如果没有账号关注这些吧，直接清理数据库
        if not account_ids:
            _ = await self.db.delete_forum_memberships_globally(fnames)
            _ = await self.db.delete_target_pool_by_fnames(fnames)
            return True

        # 预构建账号 ID -> 名称映射
        _unf_acc_list = await self.db.get_accounts()
        _unf_acc_name_map = {a.id: (a.user_name or a.name or f"账号(ID:{a.id})") for a in _unf_acc_list}

        total_actions = len(account_ids) * len(fnames)
        current_action = 0
        failed_count = 0

        for acc_idx, acc_id in enumerate(account_ids):
            # ---- 账号间延迟 ----
            if acc_idx > 0:
                account_gap = random.uniform(30, 60)
                await log_info(f"账号切换冷却，休眠 {account_gap:.0f}s 后处理账号 {_unf_acc_name_map.get(acc_id, f'账号-{acc_id}')}...")
                await asyncio.sleep(account_gap)

            # ---- 熔断检查 ----
            if captcha_breaker.is_in_cooldown(acc_id):
                await log_warn(f"账号 [{_unf_acc_name_map.get(acc_id, f'账号-{acc_id}')}] 验证码熔断中，跳过取关")
                skipped_accounts.add(acc_id)
                continue
            if failure_breaker.is_in_cooldown(acc_id):
                await log_warn(f"账号 [{_unf_acc_name_map.get(acc_id, f'账号-{acc_id}')}] 连续失败熔断中，跳过取关")
                skipped_accounts.add(acc_id)
                continue

            creds = await get_account_credentials(self.db, acc_id)
            if not creds:
                skipped_accounts.add(acc_id)
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
                        # ---- RPM 限流 ----
                        await rate_limiter.wait_if_needed(acc_id)

                        # ---- 熔断二次检查 ----
                        if captcha_breaker.is_in_cooldown(acc_id):
                            await log_warn(f"账号 [{_unf_acc_name_map.get(acc_id, f'账号-{acc_id}')}] 验证码熔断，跳过 [{fname}]")
                            continue

                        try:
                            _ = await client.unfollow_forum(fname)
                            successful_unfollows.add((acc_id, fname))
                            await log_info(f"账号 {_unf_acc_name_map.get(acc_id, f'账号-{acc_id}')} 已成功取消关注 [{fname}]")
                            await failure_breaker.record_success(acc_id)
                        except Exception as e:
                            err_msg = str(e)
                            failed_count += 1

                            # ---- 验证码熔断 ----
                            import re as _re
                            code_match = _re.search(r'(\d{4,})', err_msg)
                            err_code = int(code_match.group(1)) if code_match else 0
                            is_captcha = await captcha_breaker.check_and_trigger(acc_id, err_msg, err_code)
                            if not is_captcha:
                                await failure_breaker.record_failure(acc_id)
                            await log_error(f"账号 {_unf_acc_name_map.get(acc_id, f'账号-{acc_id}')} 取消关注 [{fname}] 失败: {err_msg}")

                        current_action += 1
                        if progress_callback:
                            await progress_callback(current_action, total_actions)

                        # ---- 拟人化延迟 + 时段倍率 ----
                        adj_min, adj_max = time_window.get_adjusted_delay(3.0, 8.0)
                        await BionicDelay.sleep(adj_min, adj_max)
            except Exception as e:
                failed_count += len(fnames)
                skipped_accounts.add(acc_id)
                await log_error(f"创建客户端执行取关任务失败(ID:{_unf_acc_name_map.get(acc_id, f'账号-{acc_id}')}): {e}")

        # 2. 仅清理成功取关的数据库记录
        if successful_unfollows:
            del_membership_count = 0
            from ..db.models import Forum
            async with self.db.async_session() as session:
                from sqlalchemy import delete, or_
                conditions = [
                    (Forum.account_id == acc_id) & (Forum.fname == fname)
                    for acc_id, fname in successful_unfollows
                ]
                stmt = delete(Forum).where(or_(*conditions))
                result = await session.execute(stmt)
                del_membership_count = result.rowcount or 0
                await session.commit()

            # 仅当所有贴吧的所有账号都成功取关时，才清理靶场数据
            all_pairs = {(acc_id, fname) for acc_id in account_ids for fname in fnames}
            if successful_unfollows == all_pairs:
                del_target_count = await self.db.delete_target_pool_by_fnames(fnames)
                await log_info(f"全局阵地清理完成：移除了 {del_membership_count} 条关注记录，移除了 {del_target_count} 个靶场目标。")
            else:
                await log_info(f"部分阵地清理完成：移除了 {del_membership_count} 条关注记录（{failed_count} 次取关失败，{len(skipped_accounts)} 个账号跳过）。")
                if failed_count > 0:
                    await log_warn(f"注意：{failed_count} 次取关失败，对应贴吧的数据库记录已保留以维持一致性。")
        elif failed_count > 0 or skipped_accounts:
            await log_warn(f"所有取关操作均未成功（失败 {failed_count} 次，跳过 {len(skipped_accounts)} 个账号），数据库记录已保留。")
        return True

    async def follow_forums_bulk(self, fnames: list[str], account_ids: list[int] | None = None, progress_callback: Any = None) -> dict[str, Any]:
        """
        批量关注贴吧并记录失败结果。
        内置6层反风控防护：PerAccountRateLimiter / CaptchaCircuitBreaker /
        FailureCircuitBreaker / BionicDelay / TimeWindowDispatcher / 账号间延迟。

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

        result: dict[str, list[dict[str, Any]]] = {"success": [], "failed": [], "skipped": []}

        # ---- 反风控组件初始化 ----
        rate_limiter = PerAccountRateLimiter(rpm=8)           # 每账号8次/分
        captcha_breaker = CaptchaCircuitBreaker(cooldown_minutes=30, db=self.db)
        failure_breaker = FailureCircuitBreaker(max_consecutive_failures=3, cooldown_minutes=60)
        time_window = TimeWindowDispatcher(quiet_start=1, quiet_end=6)

        # 1. 确定要操作的账号
        if account_ids is None:
            all_accounts = await self.db.get_accounts()
            account_ids = [acc.id for acc in all_accounts if acc.status in ("active", "pending")]

        # 预构建账号 ID -> 名称映射
        _fol_acc_list = await self.db.get_accounts()
        _fol_acc_name_map = {a.id: (a.user_name or a.name or f"账号(ID:{a.id})") for a in _fol_acc_list}

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

        for acc_idx, acc_id in enumerate(account_ids):
            # ---- 账号间延迟（防关联核心防线）----
            if acc_idx > 0:
                account_gap = random.uniform(30, 60)
                await log_info(f"账号切换冷却，休眠 {account_gap:.0f}s 后处理账号 {_fol_acc_name_map.get(acc_id, f'账号-{acc_id}')}...")
                await asyncio.sleep(account_gap)

            # ---- 熔断检查 ----
            if captcha_breaker.is_in_cooldown(acc_id):
                remaining = captcha_breaker.get_remaining_cooldown(acc_id)
                await log_warn(f"账号 [{_fol_acc_name_map.get(acc_id, f'账号-{acc_id}')}] 处于验证码熔断中（剩余 {remaining:.0f}s），跳过")
                result["skipped"].append({"account_id": acc_id, "fname": None, "reason": f"验证码熔断中（剩余 {remaining:.0f}s）"})
                continue
            if failure_breaker.is_in_cooldown(acc_id):
                result["skipped"].append({"account_id": acc_id, "fname": None, "reason": "连续失败熔断中"})
                continue

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

                        # ---- RPM 限流 ----
                        await rate_limiter.wait_if_needed(acc_id)

                        # ---- 熔断二次检查（可能在上一步等待期间被触发）----
                        if captcha_breaker.is_in_cooldown(acc_id):
                            remaining = captcha_breaker.get_remaining_cooldown(acc_id)
                            result["skipped"].append({"account_id": acc_id, "fname": fname, "reason": f"验证码熔断（剩余 {remaining:.0f}s）"})
                            current_action += 1
                            continue

                        try:
                            _ = await client.follow_forum(fname)
                            result["success"].append({"account_id": acc_id, "fname": fname})
                            await log_info(f"账号 {_fol_acc_name_map.get(acc_id, f'账号-{acc_id}')} 成功关注 [{fname}]")

                            # 成功 → 重置失败计数
                            await failure_breaker.record_success(acc_id)

                            # 获取真实贴吧 fid（而非随机生成）
                            real_fid = 0
                            try:
                                forum_info = await client.get_forum(fname)
                                real_fid = getattr(forum_info, 'fid', 0) or 0
                            except Exception:
                                pass

                            # 同时更新数据库记录
                            from ..db.models import Forum
                            async with self.db.async_session() as session:
                                from sqlalchemy import select
                                existing = await session.execute(
                                    select(Forum).where(Forum.fname == fname, Forum.account_id == acc_id)
                                )
                                forum = existing.scalar_one_or_none()
                                if not forum:
                                    session.add(Forum(fid=real_fid, fname=fname, account_id=acc_id))
                                    await session.commit()
                                elif real_fid and forum.fid != real_fid:
                                    # 补全之前随机 fid 的记录
                                    forum.fid = real_fid
                                    await session.commit()
                        except Exception as e:
                            err_msg = str(e)

                            # ---- 验证码熔断检测 ----
                            # 尝试从错误消息中提取数字错误码
                            import re as _re
                            code_match = _re.search(r'(\d{4,})', err_msg)
                            err_code = int(code_match.group(1)) if code_match else 0
                            is_captcha = await captcha_breaker.check_and_trigger(acc_id, err_msg, err_code)
                            if is_captcha:
                                result["failed"].append({"account_id": acc_id, "fname": fname, "reason": "触发验证码，已熔断"})

                            # ---- 连续失败熔断 ----
                            elif await failure_breaker.record_failure(acc_id):
                                result["failed"].append({"account_id": acc_id, "fname": fname, "reason": "连续失败熔断"})

                            elif "3250004" in err_msg or "已关注" in err_msg:
                                result["skipped"].append({"account_id": acc_id, "fname": fname, "reason": "已关注或无法关注"})
                            elif "被拉黑" in err_msg or "400013" in err_msg:
                                result["failed"].append({"account_id": acc_id, "fname": fname, "reason": "账号被该吧拉黑"})
                                # 标记为封禁，尝试获取真实 fid
                                ban_fid = 0
                                try:
                                    ban_forum_info = await client.get_forum(fname)
                                    ban_fid = getattr(ban_forum_info, 'fid', 0) or 0
                                except Exception:
                                    pass
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
                                        if ban_fid and forum.fid != ban_fid:
                                            forum.fid = ban_fid
                                    else:
                                        session.add(Forum(
                                            fid=ban_fid, fname=fname, account_id=acc_id,
                                            is_banned=True, ban_reason="批量关注时检测到拉黑"
                                        ))
                                    await session.commit()
                            else:
                                result["failed"].append({"account_id": acc_id, "fname": fname, "reason": err_msg[:50]})
                            await log_error(f"账号 {_fol_acc_name_map.get(acc_id, f'账号-{acc_id}')} 关注 [{fname}] 失败: {err_msg}")

                        current_action += 1
                        if progress_callback:
                            await progress_callback(current_action, total_actions)

                        # ---- 拟人化延迟 + 时段倍率 ----
                        adj_min, adj_max = time_window.get_adjusted_delay(3.0, 8.0)
                        await BionicDelay.sleep(adj_min, adj_max)
            except Exception as e:
                result["skipped"].append({"account_id": acc_id, "fname": None, "reason": f"创建客户端失败: {str(e)[:30]}"})
                await log_error(f"创建客户端执行关注任务失败(ID:{_fol_acc_name_map.get(acc_id, f'账号-{acc_id}')}): {e}")

        await log_info(
            f"批量关注完成：成功 {len(result['success'])}, 失败 {len(result['failed'])}, 跳过 {len(result['skipped'])}"
        )
        return result


class AutoBumpManager:
    """自动回帖(自顶)调度管理器"""
    def __init__(self, db):
        self.db = db
        self.post_manager = BatchPostManager(db)

    def _should_bump_this_cycle(self, material) -> tuple[bool, str]:
        """
        判断物料是否应该在本次周期执行自顶
        返回: (should_bump, reason)
        """
        from datetime import date, datetime, timedelta

        bump_mode = getattr(material, 'bump_mode', 'once') or 'once'
        today = date.today()

        def _ensure_date(val):
            """确保 bump_start_date 是 date 类型，兼容 str/None"""
            if val is None:
                return None
            if isinstance(val, date) and not isinstance(val, datetime):
                return val
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, str):
                try:
                    return date.fromisoformat(val)
                except (ValueError, TypeError):
                    return None
            return None
        
        if bump_mode == "once":
            # 模式1: 次数上限模式 (原有逻辑)
            return True, "once"
            
        elif bump_mode == "scheduled":
            # 模式2: 定时周期模式 - 每天指定时间执行一次
            bump_hour = getattr(material, 'bump_hour', 10) or 10
            bump_duration = getattr(material, 'bump_duration_days', 0) or 0
            bump_start = _ensure_date(getattr(material, 'bump_start_date', None))

            # 检查是否在有效期内
            if bump_start and bump_duration > 0:
                end_date = bump_start + timedelta(days=bump_duration)
                if today > end_date:
                    return False, f"已超过持续期({bump_duration}天)"

            # 检查今日是否已执行
            last_date = getattr(material, 'bump_last_date', None)
            if last_date == today:
                return False, "今日已执行"

            # 检查当前时间是否到达设定时间
            current_hour = datetime.now().hour
            if current_hour < bump_hour:
                return False, f"未到执行时间({bump_hour}点)"

            return True, "scheduled"

        elif bump_mode == "matrix_loop":
            # 模式3: 矩阵轮换循环模式 - 每日一次，账号轮换，不设上限
            bump_duration = getattr(material, 'bump_duration_days', 0) or 0
            bump_start = _ensure_date(getattr(material, 'bump_start_date', None))

            # 检查是否在有效期内 (0=永久)
            if bump_start and bump_duration > 0:
                end_date = bump_start + timedelta(days=bump_duration)
                if today > end_date:
                    return False, f"已超过持续期({bump_duration}天)"
            
            # 检查今日是否已执行
            last_date = getattr(material, 'bump_last_date', None)
            if last_date == today:
                return False, "今日已执行"
            
            # 检查当前时间是否到达设定时间
            bump_hour = getattr(material, 'bump_hour', 10) or 10
            current_hour = datetime.now().hour
            if current_hour < bump_hour:
                return False, f"未到执行时间({bump_hour}点)"
            
            return True, "matrix_loop"
        
        return False, "未知模式"

    def _select_account_for_bump(self, material: Any, matrix_pool: list[Any]) -> int | None:
        """为自顶选取合适的账号"""
        bump_mode = getattr(material, 'bump_mode', 'once') or 'once'
        
        # 过滤掉发帖原号
        potential_accounts: list[Any] = [acc for acc in matrix_pool if acc.id != material.posted_account_id]
        if not potential_accounts:
            return None
        
        if bump_mode == "matrix_loop":
            # 矩阵轮换模式：使用 bump_account_index 进行轮换
            account_ids_json = getattr(material, 'bump_account_ids', None) or "[]"
            try:
                account_ids = json.loads(account_ids_json)
                if account_ids:
                    # 使用物料自带的账号列表进行轮换
                    current_idx = getattr(material, 'bump_account_index', 0) or 0
                    target_acc_id = account_ids[current_idx % len(account_ids)]
                    # 检查该账号是否在当前活跃矩阵池中
                    if any(acc.id == target_acc_id for acc in matrix_pool):
                        return target_acc_id
            except (json.JSONDecodeError, TypeError):
                pass
            
            # 回退：使用 bump_count 进行轮换
            return potential_accounts[material.bump_count % len(potential_accounts)].id
        else:
            # 其他模式：使用 bump_count 进行轮换
            return potential_accounts[material.bump_count % len(potential_accounts)].id

    async def process_all_candidates(self):
        """扫描并处理所有待自顶的物料"""
        from datetime import date, datetime, timedelta
        
        # 1. 读取全局配置
        try:
            _max_bump_count = int(await self.db.get_setting("max_bump_count", "20"))
            bump_cooldown_minutes = int(await self.db.get_setting("bump_cooldown_minutes", "45"))
            bump_matrix_enabled = (await self.db.get_setting("bump_matrix_enabled", "0")) == "1"
            bump_ai_content_enabled = (await self.db.get_setting("bump_ai_content", "1")) == "1"
        except Exception:
            _max_bump_count, bump_cooldown_minutes, bump_matrix_enabled, bump_ai_content_enabled = 20, 60, False, True  # 保守值
            
        # 2. 获取开启了自动回帖、发帖成功、且满足冷却时间的物料
        async with self.db.async_session() as session:
            from ..db.models import MaterialPool
            from sqlalchemy import select, and_
            
            threshold_time = datetime.now() - timedelta(minutes=bump_cooldown_minutes)
            
            # 查询所有成功的物料 (不再在这里限制次数，改为在 _should_bump_this_cycle 中判断)
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
            all_candidates = result.scalars().all()
            
            # 过滤真正需要执行的物料
            candidates = []
            for material in all_candidates:
                should_bump, reason = self._should_bump_this_cycle(material)
                if should_bump:
                    candidates.append(material)
                else:
                    await log_info(f"物料 [{material.id}] 跳过本次自顶: {reason}")
            
            if not candidates:
                return

            await log_info(f"发现 {len(candidates)} 个物料满足自动回帖条件，开始执行...")
            
            # 运行时 Skip-List：防止在同一次扫描中反复背刺已被封的账号
            banned_pairs = set() # (account_id, fname)
            
            # 获取当前全局活跃号作为后备
            default_acc = await self.db.get_active_account()
            
            # 预加载所有可用账号池
            matrix_pool = []
            if bump_matrix_enabled:
                matrix_pool = await self.db.get_matrix_accounts()
            if not matrix_pool:
                matrix_pool = [default_acc] if default_acc else []
            # 构建账号 ID -> 名称映射，用于日志中显示可读名称
            bump_acc_name_map = {a.id: (a.name or f"账号-{a.id}") for a in matrix_pool if a}

            # 预加载 AI 人格化设定和优化器（None = 自动按时段轮换）
            ai_persona = await self.db.get_setting("ai_persona", None)
            from .ai_optimizer import AIOptimizer
            optimizer = AIOptimizer(self.db)

            for material in candidates:
                bump_mode = getattr(material, 'bump_mode', 'once') or 'once'
                
                # --- 账号选取策略 ---
                target_account_id = None
                
                if bump_mode == "matrix_loop":
                    # 矩阵轮换模式
                    target_account_id = self._select_account_for_bump(material, matrix_pool)
                elif bump_matrix_enabled and matrix_pool:
                    # 矩阵模式
                    target_account_id = self._select_account_for_bump(material, matrix_pool)
                else:
                    # 未开启矩阵模式时，同号自顶风险极高
                    await log_warn(
                        f"物料 [{material.id}] 跳过自顶：未开启矩阵协同模式。同号自顶极易触发风控导致封号，请在「自顶配置」中开启「矩阵协同模式」。"
                    )
                    continue
                
                if not target_account_id:
                    await log_warn(f"物料 [{material.id}] 跳过自顶：无可用账号")
                    continue
                
                # 检查动态黑名单
                if (target_account_id, material.posted_fname) in banned_pairs:
                    continue

                # --- 自顶内容生成 ---
                if bump_ai_content_enabled:
                    # AI 生成模式
                    try:
                        ai_success, ai_content, ai_err = await optimizer.generate_bump_content(
                            material.title or "", ai_persona
                        )
                        if ai_success and ai_content:
                            bump_content = ai_content
                            await log_info(f"物料 [{material.id}] AI生成自顶内容: {bump_content}")
                        else:
                            # AI生成失败时使用兜底模板
                            templates = [
                                "路过", "看了", "点赞", "顶", "收藏",
                                "不错的", "可以", "好贴", "来了", "路过~",
                                "看了下，还行", "写得挺好的", "收藏了", "支持楼主",
                                "内容不错，赞", "有意思", "帮顶一下", "可以可以",
                                "这个确实可以", "路过支持", "mark一下", "看看再说",
                                "内容挺充实的", "感谢分享", "不错的帖子",
                                "看完了，内容挺充实的，赞一个", "写得不错，已收藏",
                                "感谢楼主的整理，辛苦了", "认真看完了，支持一下",
                                "👍", "✨👍", "好帖", "顶", "已阅", "mark",
                            ]
                            emojis = ["[赞]", "✨", "👍", "👍👍", ""]
                            base_text = random.choice(templates)
                            if random.random() < 0.2:
                                keyword = (material.title or "")[:8]
                                base_text = f"{keyword} 还行，{base_text}"
                            bump_content = f"{base_text} {random.choice(emojis)}"
                            await log_warn(f"物料 [{material.id}] AI生成失败，使用模板: {ai_err}")
                    except Exception as gen_err:
                        await log_error(f"自顶内容生成异常: {gen_err}")
                        bump_content = "路过，看了下挺好的"
                else:
                    # 固定模板模式
                    templates = [
                        "路过", "看了", "点赞", "顶", "收藏",
                        "不错的", "可以", "好贴", "来了", "路过~",
                        "看了下，还行", "写得挺好的", "收藏了", "支持楼主",
                        "内容不错，赞", "有意思", "帮顶一下", "可以可以",
                        "这个确实可以", "路过支持", "mark一下", "看看再说",
                        "内容挺充实的", "感谢分享", "不错的帖子",
                        "看完了，内容挺充实的，赞一个", "写得不错，已收藏",
                        "感谢楼主的整理，辛苦了", "认真看完了，支持一下",
                        "👍", "✨👍", "好帖", "顶", "已阅", "mark",
                    ]
                    emojis = ["[赞]", "✨", "👍", "👍👍", ""]
                    base_text = random.choice(templates)
                    if random.random() < 0.2:
                        keyword = (material.title or "")[:8]
                        base_text = f"{keyword} 还行，{base_text}"
                    bump_content = f"{base_text} {random.choice(emojis)}"

                success = await self.post_manager.reply_to_thread(
                    target_account_id, 
                    material.posted_fname, 
                    material.posted_tid, 
                    bump_content
                )
                
                today = date.today()
                if success:
                    # 更新 bump_count 和轮换信息
                    async with self.db.async_session() as upd_session:
                        from ..db.models import MaterialPool
                        mat = await upd_session.get(MaterialPool, material.id)
                        if mat:
                            mat.bump_count = (mat.bump_count or 0) + 1
                            mat.last_bumped_at = datetime.now()
                            mat.last_date = today
                            
                            # 矩阵轮换模式：更新账号索引
                            if bump_mode == "matrix_loop":
                                account_ids_json = getattr(mat, 'bump_account_ids', None) or "[]"
                                try:
                                    account_ids = json.loads(account_ids_json)
                                    if account_ids:
                                        mat.bump_account_index = ((mat.bump_account_index or 0) + 1) % len(account_ids)
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            
                            await upd_session.commit()
                    
                    await log_info(f"物料 [{material.id}] 自顶成功 (账号:{bump_acc_name_map.get(target_account_id, f'账号-{target_account_id}')} | TID:{material.posted_tid} | 累计:{material.bump_count + 1}次)")
                    await asyncio.sleep(random.uniform(15, 45))
                else:
                    banned_pairs.add((target_account_id, material.posted_fname))
                    await log_warn(f"物料 [{material.id}] 自顶失败 (账号:{bump_acc_name_map.get(target_account_id, f'账号-{target_account_id}')})")

