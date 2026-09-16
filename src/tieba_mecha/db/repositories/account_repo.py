"""账号与权重历史 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select, text, update

from ..models import (
    Account,
    AutoRule,
    Base,
    BatchPostLog,
    BatchPostTask,
    CaptchaEvent,
    CrawlTask,
    Forum,
    MaterialPool,
    Notification,
    PostCache,
    Proxy,
    Setting,
    SignLog,
    TargetPool,
    ThreadRecord,
    WeightHistory,
)

logger = logging.getLogger(__name__)


class AccountRepository:
    """账号与权重历史（依赖宿主类提供 async_session/engine）。"""

    async def add_account(
        self,
        name: str,
        bduss: str,
        stoken: str = "",
        user_id: int = 0,
        user_name: str = "",
        proxy_id: int | None = None,
        cuid: str = "",
        user_agent: str = "",
        post_weight: int = 5,
    ) -> Account:
        """添加账号，自动注入指纹"""
        import uuid
        import random
        
        # 默认 UA 库 (高仿真移动端)
        UA_POOL = [
            # Android 14 / Pixel 8
            "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/UD1A.230803.041) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
            # iOS 17.2 / iPhone 15
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
            # Android 13 / Samsung S23
            "Mozilla/5.0 (Linux; Android 13; SM-S918B Build/TP1A.220624.014) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.163 Mobile Safari/537.36",
            # HarmonyOS / Mate 60
            "Mozilla/5.0 (Linux; Android 12; ALN-AL00 Build/HUAWEIALN-AL00) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36",
            # iPad OS 17.1
            "Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
            # Xiaomi 14
            "Mozilla/5.0 (Linux; Android 14; 23127PN0CC Build/UKQ1.230804.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36",
        ]

        if not cuid:
            # 百度常用的 CUID 通常是一个类似 UUID 的大写字符串
            cuid = uuid.uuid4().hex.upper()
        if not user_agent:
            user_agent = random.choice(UA_POOL)

        async with self.async_session() as session:
            # 如果这是第一个账号，自动设为活跃
            existing = await session.execute(select(Account))
            is_first = existing.scalar() is None

            account = Account(
                name=name,
                bduss=bduss,
                stoken=stoken,
                user_id=user_id,
                user_name=user_name,
                is_active=is_first,
                proxy_id=proxy_id,
                cuid=cuid,
                user_agent=user_agent,
                post_weight=post_weight,
                status="active" if is_first else "pending", # 确保初次添加就有明确状态
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            return account
    async def get_accounts(self) -> list[Account]:
        """获取所有账号"""
        async with self.async_session() as session:
            result = await session.execute(select(Account).order_by(Account.id))
            return list(result.scalars().all())
    async def get_active_account(self) -> Account | None:
        """获取当前活跃账号 (带多峰收敛保护)"""
        async with self.async_session() as session:
            result = await session.execute(select(Account).where(Account.is_active == True))
            return result.scalars().first()
    async def set_active_account(self, account_id: int) -> None:
        """设置活跃账号 (事务内原子操作,避免竞态条件)
        
        Args:
            account_id: 要设为活跃的账号ID
            
        Note:
            使用数据库事务保证原子性,避免并发操作导致多个账号同时活跃
        """
        async with self.async_session() as session:
            async with session.begin():  # 显式事务
                # 先取消所有账号的活跃状态
                await session.execute(update(Account).values(is_active=False))
                # 设置指定账号为活跃
                await session.execute(
                    update(Account).where(Account.id == account_id).values(is_active=True)
                )
    async def delete_account(self, account_id: int) -> bool:
        """删除账号 (带级联删除：同时删除关联的贴吧/权重历史/异常事件，避免产生孤儿数据)"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                was_active = account.is_active

                # 1. 级联删除关联的贴吧
                await session.execute(delete(Forum).where(Forum.account_id == account_id))
                # 2. 级联删除权重变更历史与验证码/异常事件
                await session.execute(delete(WeightHistory).where(WeightHistory.account_id == account_id))
                await session.execute(delete(CaptchaEvent).where(CaptchaEvent.account_id == account_id))
                # 3. 删除账号本身
                await session.delete(account)
                await session.commit()
                if was_active:
                    # 尝试推举新活跃账号
                    remaining_acct = await session.execute(select(Account).order_by(Account.id))
                    first_acc = remaining_acct.scalars().first()
                    if first_acc:
                        await self.set_active_account(first_acc.id)
                return True
            return False

    # update_account 允许修改的字段白名单
    _ACCOUNT_UPDATABLE_FIELDS = frozenset({
        "name", "bduss", "stoken", "user_id", "user_name",
        "proxy_id", "cuid", "user_agent", "post_weight",
        "is_active", "status", "last_verified", "suspended_reason",
        "is_maint_enabled", "last_maint_at",
    })

    async def update_account_status(self, account_id: int, status: str) -> None:
        """更新账号验证状态"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                account.status = status
                account.last_verified = datetime.now()
                await session.commit()
    async def get_account_by_id(self, account_id: int) -> Account | None:
        """根据 ID 获取单个账号（直接查询，避免全表扫描）"""
        async with self.async_session() as session:
            return await session.get(Account, account_id)
    async def update_account(self, account_id: int, **kwargs) -> Account | None:
        """更新账号信息（仅允许白名单中的字段）"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                for key, value in kwargs.items():
                    if key in self._ACCOUNT_UPDATABLE_FIELDS:
                        setattr(account, key, value)
                await session.commit()
                await session.refresh(account)
                return account
            return None
    async def get_matrix_accounts(self) -> list[Account]:
        """获取矩阵可用账号：过滤掉挂起及封禁状态"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.status.notin_(["suspended", "suspended_proxy", "banned", "expired"])
                ).order_by(Account.post_weight.desc())
            )
            return list(result.scalars().all())
    async def get_accounts_by_proxy(self, proxy_id: int) -> list[Account]:
        """获取所有绑定了指定代理的账号"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(Account.proxy_id == proxy_id)
            )
            return list(result.scalars().all())
    async def get_maint_accounts(self) -> list[Account]:
        """获取需要执行 BioWarming 养号任务的账号"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(Account.is_maint_enabled == True)
            )
            return list(result.scalars().all())
    async def update_maint_status(self, account_id: int) -> None:
        """更新账号的最后养号时间"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                account.last_maint_at = datetime.now()
                await session.commit()
    async def suspend_accounts_for_proxy(self, proxy_id: int, reason: str = "代理失效自动隔离") -> list[Account]:
        """代理失效时批量挂起关联账号，返回被挂起的账号列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(Account.proxy_id == proxy_id)
            )
            accounts = list(result.scalars().all())
            suspended = []
            for acc in accounts:
                if acc.status != "suspended_proxy":
                    acc.status = "suspended_proxy"
                    acc.suspended_reason = reason
                    suspended.append(acc)
            await session.commit()
            return suspended
    async def restore_accounts_for_proxy(self, proxy_id: int) -> list[Account]:
        """代理恢复时，解挂所有因该代理而挂起的账号，返回被恢复的账号列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.proxy_id == proxy_id,
                    Account.status == "suspended_proxy"
                )
            )
            accounts = list(result.scalars().all())
            for acc in accounts:
                acc.status = "active"
                acc.suspended_reason = ""
            await session.commit()
            return accounts
    async def update_account_weight(self, account_id: int, weight: int, source: str = "manual") -> None:
        """更新单个账号的发帖权重，并记录变更历史"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                old_w = account.post_weight
                account.post_weight = max(1, min(10, weight))
                if old_w != account.post_weight:
                    session.add(WeightHistory(
                        account_id=account_id,
                        account_name=account.name or "",
                        old_weight=old_w,
                        new_weight=account.post_weight,
                        source=source,
                    ))
                await session.commit()
    async def batch_update_weights(self, weight_updates: list[tuple[int, int]], source: str = "auto_calculate") -> dict:
        """
        批量更新多个账号的权重，并记录变更历史。

        Args:
            weight_updates: [(account_id, weight), ...] 列表
            source: 变更来源标记

        Returns:
            {"updated": count, "failed": count}
        """
        updated = 0
        failed = 0
        async with self.async_session() as session:
            for acc_id, weight in weight_updates:
                try:
                    account = await session.get(Account, acc_id)
                    if account:
                        old_w = account.post_weight
                        account.post_weight = max(1, min(10, weight))
                        if old_w != account.post_weight:
                            session.add(WeightHistory(
                                account_id=acc_id,
                                account_name=account.name or "",
                                old_weight=old_w,
                                new_weight=account.post_weight,
                                source=source,
                            ))
                        updated += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
            await session.commit()
        return {"updated": updated, "failed": failed}
    async def get_weight_history(self, account_id: int | None = None, limit: int = 50) -> list[WeightHistory]:
        """查询权重变更历史"""
        async with self.async_session() as session:
            stmt = select(WeightHistory).order_by(WeightHistory.created_at.desc()).limit(limit)
            if account_id is not None:
                stmt = stmt.where(WeightHistory.account_id == account_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())
    async def update_weight_calc_timestamp(self, account_ids: list[int]) -> None:
        """更新账号的最后权重计算时间"""
        async with self.async_session() as session:
            now = datetime.now()
            for acc_id in account_ids:
                account = await session.get(Account, acc_id)
                if account:
                    account.last_weight_calc_at = now
            await session.commit()
    async def get_accounts_needing_weight_recalc(self, since: datetime | None = None) -> list[tuple[Account, list[Forum]]]:
        """获取自上次权重计算以来有变更的账号 (增量模式)"""
        async with self.async_session() as session:
            if since is None:
                return await self.get_accounts_with_forums()

            acc_stmt = select(Account).where(Account.updated_at > since).order_by(Account.id)
            acc_result = await session.execute(acc_stmt)
            changed_accounts = list(acc_result.scalars().all())

            if not changed_accounts:
                return []

            changed_ids = {a.id for a in changed_accounts}
            forum_stmt = select(Forum).where(Forum.account_id.in_(changed_ids)).order_by(Forum.account_id)
            forum_result = await session.execute(forum_stmt)
            all_forums = list(forum_result.scalars().all())

            forums_by_account: dict[int, list[Forum]] = {}
            for f in all_forums:
                forums_by_account.setdefault(f.account_id, []).append(f)

            return [(acc, forums_by_account.get(acc.id, [])) for acc in changed_accounts]
    async def get_accounts_with_forums(self) -> list[tuple[Account, list[Forum]]]:
        """获取所有账号及其关联的贴吧列表（批量查询避免 N+1 问题）"""
        async with self.async_session() as session:
            # 一次性获取所有账号
            acc_stmt = select(Account).order_by(Account.id)
            acc_result = await session.execute(acc_stmt)
            accounts = list(acc_result.scalars().all())

            # 一次性获取所有贴吧，按 account_id 分组
            forum_stmt = select(Forum).order_by(Forum.account_id)
            forum_result = await session.execute(forum_stmt)
            all_forums = list(forum_result.scalars().all())

            # 构建 account_id → forums 映射
            forums_by_account: dict[int, list[Forum]] = {}
            for f in all_forums:
                forums_by_account.setdefault(f.account_id, []).append(f)

            return [(acc, forums_by_account.get(acc.id, [])) for acc in accounts]
    async def get_accounts_not_following_forum(self, fname: str) -> list[Account]:
        """
        获取未关注指定贴吧的账号列表（排除已封禁的）
        用于补齐关注功能
        """
        async with self.async_session() as session:
            from sqlalchemy import select, not_
            # 获取已关注的账号ID
            followed_stmt = select(Forum.account_id).where(
                Forum.fname == fname,
                Forum.is_banned == False
            )
            followed_result = await session.execute(followed_stmt)
            followed_ids = {row[0] for row in followed_result}
            
            # 获取所有活跃账号（排除已关注的）
            all_accounts_stmt = select(Account).where(
                Account.status.notin_(["suspended", "suspended_proxy", "banned", "expired"])
            )
            all_result = await session.execute(all_accounts_stmt)
            all_accounts = list(all_result.scalars().all())
            
            # 过滤出未关注的
            missing_accounts = [acc for acc in all_accounts if acc.id not in followed_ids]
            return missing_accounts
    async def get_accounts_not_following_any_forums(self, fnames: list[str]) -> list[Account]:
        """
        获取未关注指定贴吧列表中任意一个的活跃账号（批量版，N+1 优化）。
        用于批量补齐关注：一次查询替代逐吧调用 get_accounts_not_following_forum。
        """
        if not fnames:
            return []

        async with self.async_session() as session:
            # 获取关注了任一指定贴吧的账号 ID（用于排除）
            followed_stmt = select(Forum.account_id).where(
                Forum.fname.in_(fnames),
                Forum.is_banned == False
            ).distinct()
            followed_result = await session.execute(followed_stmt)
            followed_ids = {row[0] for row in followed_result}

            # 获取所有活跃账号
            all_accounts_stmt = select(Account).where(
                Account.status.notin_(["suspended", "suspended_proxy", "banned", "expired"])
            )
            all_result = await session.execute(all_accounts_stmt)
            all_accounts = list(all_result.scalars().all())

            # 返回未关注至少一个指定贴吧的账号
            return [acc for acc in all_accounts if acc.id not in followed_ids]
    async def get_account_ids_following_forums(self, fnames: list[str]) -> list[int]:
        """获取关注了指定贴吧列表的所有账号 ID"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum.account_id).where(Forum.fname.in_(fnames))
            )
            return sorted(list(set(result.scalars().all())))
