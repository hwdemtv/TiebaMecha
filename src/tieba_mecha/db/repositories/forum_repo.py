"""贴吧关注与签到日志 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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
    Forum,
    MaterialPool,
    Notification,
    Proxy,
    Setting,
    SignLog,
    TargetPool,
    ThreadRecord,
    WeightHistory,
)

logger = logging.getLogger(__name__)


class ForumRepository:
    """贴吧关注与签到日志（依赖宿主类提供 async_session/engine）。"""

    async def add_forum(
        self,
        fid: int,
        fname: str,
        account_id: int,
        sign_count: int = 0,
        level: int = 0,
    ) -> Forum:
        """添加关注贴吧 (带查重，同时刷新贴吧等级)"""
        async with self.async_session() as session:
            # 查重
            existing = await session.execute(
                select(Forum).where(Forum.fid == fid, Forum.account_id == account_id)
            )
            forum = existing.scalar_one_or_none()
            if forum:
                # 动态刷新等级 (仅刷新经验不破坏签到统计数据)
                forum.level = level
                await session.commit()
                return forum

            forum = Forum(fid=fid, fname=fname, account_id=account_id, sign_count=sign_count, level=level)
            session.add(forum)
            await session.commit()
            await session.refresh(forum)
            return forum
    async def get_forums(
        self,
        account_id: int | None = None,
        *,
        include_hidden: bool = False,
        include_banned: bool = True,
    ) -> list[Forum]:
        """获取贴吧列表

        include_banned=False 时排除已熔断 (is_banned) 的贴吧，
        供签到队列使用，避免每天重撞 3250004 封禁错误。
        """
        async with self.async_session() as session:
            conditions = []
            if account_id is not None:
                conditions.append(Forum.account_id == account_id)
            if not include_hidden:
                conditions.append(Forum.is_hidden == False)
            if not include_banned:
                conditions.append(Forum.is_banned == False)
            stmt = select(Forum)
            if conditions:
                stmt = stmt.where(*conditions)
            result = await session.execute(stmt.order_by(Forum.fname))
            return list(result.scalars().all())
    async def get_all_unique_fnames(self) -> list[str]:
        """获取所有账号关注过的唯一贴吧名称列表（唯一实现，勿重复定义）"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum.fname).distinct().order_by(Forum.fname)
            )
            return list(result.scalars().all())
    async def update_forum_sign(self, forum_id: int, success: bool) -> None:
        """
        更新签到状态（优化版：按天去重计数，保证 Total = Success + Failure）
        """
        async with self.async_session() as session:
            forum = await session.get(Forum, forum_id)
            if forum:
                if success:
                    # 如果今天还没成功过
                    if not forum.is_sign_today:
                        # 如果今日之前已经有过失败记录，则需要“冲抵”
                        if forum.last_sign_status == "failure":
                            # 失败数减1，因为这一天变成了成功日
                            forum.history_failed = max(0, forum.history_failed - 1)
                        
                        # 增加成功计数
                        forum.sign_count += 1
                        forum.history_success += 1
                        forum.is_sign_today = True
                        forum.last_sign_status = "success"
                else:
                    # 只有在今日既没成功也没记录过失败时，才增加失败计数
                    #（防止重复执行失败动作导致总数和失败数狂飙）
                    if not forum.is_sign_today and forum.last_sign_status != "failure":
                        forum.history_failed += 1
                        forum.last_sign_status = "failure"
                
                # 强制平衡：总数始终等于 成功 + 失败 (按天计费)
                forum.history_total = forum.history_success + forum.history_failed
                forum.last_sign_date = datetime.now()
                await session.commit()
    async def recalculate_all_forum_stats(self) -> dict:
        """
        全量回溯：基于 sign_logs 重构所有贴吧的 history_total/success/failed
        统计规则：对于每一天，如果有过成功记录则计为1次成功；否则若有失败记录计为1次失败。
        """
        from sqlalchemy import func
        
        async with self.async_session() as session:
            # 1. 获取所有存在记录的贴吧
            result = await session.execute(select(Forum.id))
            forum_ids = result.scalars().all()
            
            stats_updated = 0
            for fid in forum_ids:
                # SQLite 专用的日期转换统计：聚合并得出每日的最佳结果
                # MAX(success) 能确保如果这一天中有成功记录，则结果为 1
                stmt = select(
                    func.date(SignLog.signed_at).label("d"),
                    func.max(SignLog.success).label("s")
                ).where(SignLog.forum_id == fid).group_by(func.date(SignLog.signed_at))
                
                day_results = await session.execute(stmt)
                rows = day_results.all()
                
                success_days = sum(1 for r in rows if r.s)
                failed_days = sum(1 for r in rows if not r.s)
                
                forum = await session.get(Forum, fid)
                if forum:
                    forum.history_success = success_days
                    forum.history_failed = failed_days
                    forum.history_total = success_days + failed_days
                    stats_updated += 1
            
            await session.commit()
            return {"updated_count": stats_updated}
    async def reset_daily_sign(self) -> None:
        """重置每日签到状态(批量更新,避免N+1问题)"""
        async with self.async_session() as session:
            # 使用批量更新语句,一次性更新所有记录
            await session.execute(
                update(Forum).values(is_sign_today=False, last_sign_status="pending")
            )
            await session.commit()
    async def check_and_reset_daily_sign(self) -> None:
        """智能检测并重置跨天的签到状态（包含断签检测）"""
        from datetime import timedelta
        
        async with self.async_session() as session:
            now = datetime.now()
            today = now.date()
            yesterday = today - timedelta(days=1)
            
            result = await session.execute(select(Forum))
            forums = result.scalars().all()
            
            has_changes = False
            for forum in forums:
                # 只处理有签到记录或者被标记为已签到的数据
                if forum.last_sign_date:
                    last_date = forum.last_sign_date.date()
                    # 断签判定必须基于昨日最终状态，须在跨天重置覆盖前留存
                    original_status = forum.last_sign_status

                    # 1. 如果今天还没过完，没跨天，不需要重置签到状态
                    # 但是如果发现状态异常（比如之前某种错误导致没有重置），则以 last_date 为准
                    if last_date < today and forum.is_sign_today:
                        forum.is_sign_today = False
                        forum.last_sign_status = "pending"
                        has_changes = True

                    # 2. 断签检测：如果昨天签到失败，说明连续签到已经断开，清零连续天数
                    if last_date < yesterday and forum.sign_count > 0:
                        forum.sign_count = 0
                        has_changes = True
                    elif last_date == yesterday and original_status != "success" and forum.sign_count > 0:
                        forum.sign_count = 0
                        has_changes = True

            if has_changes:
                await session.commit()
    async def delete_forum(self, forum_id: int) -> bool:
        """删除贴吧"""
        async with self.async_session() as session:
            forum = await session.get(Forum, forum_id)
            if forum:
                await session.delete(forum)
                await session.commit()
                return True
            return False
    async def delete_forums_by_fids(self, account_id: int, fids: list[int]) -> None:
        """根据 FID 列表批量删除指定账号的贴吧"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            await session.execute(
                delete(Forum).where(Forum.account_id == account_id, Forum.fid.in_(fids))
            )
            await session.commit()
    async def mark_forum_banned(self, account_id: int, fname: str, reason: str) -> None:
        """标记账号在该贴吧被封禁"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum).where(Forum.account_id == account_id, Forum.fname == fname)
            )
            forum = result.scalar_one_or_none()
            if forum:
                forum.is_banned = True
                forum.ban_reason = reason
                # 被封后自动关闭发帖许可，防止调度器再次选中
                forum.is_post_target = False
                await session.commit()
    async def unban_forum(self, account_id: int, fname: str) -> bool:
        """解除账号在该贴吧的封禁状态，恢复发帖许可"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum).where(Forum.account_id == account_id, Forum.fname == fname)
            )
            forum = result.scalar_one_or_none()
            if forum and forum.is_banned:
                forum.is_banned = False
                forum.ban_reason = ""
                forum.is_post_target = True
                await session.commit()
                return True
            return False
    async def unban_forum_globally(self, fname: str) -> int:
        """解除所有账号在指定贴吧的封禁状态"""
        from sqlalchemy import update as sa_update
        async with self.async_session() as session:
            result = await session.execute(
                sa_update(Forum)
                .where(Forum.fname == fname, Forum.is_banned == True)
                .values(is_banned=False, ban_reason="", is_post_target=True)
            )
            await session.commit()
            return result.rowcount or 0
    async def delete_forum_by_name(self, account_id: int, fname: str) -> bool:
        """根据贴吧名删除指定账号的贴吧记录"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            await session.execute(
                delete(Forum).where(Forum.account_id == account_id, Forum.fname == fname)
            )
            await session.commit()
            return True
    async def delete_forum_memberships_globally(self, fnames: list[str]) -> int:
        """从所有账号的关注列表中全局移除指定贴吧"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            result = await session.execute(
                delete(Forum).where(Forum.fname.in_(fnames))
            )
            await session.commit()
            return result.rowcount or 0
    async def add_sign_log(
        self,
        forum_id: int,
        fname: str,
        success: bool,
        message: str = "",
    ) -> SignLog:
        """添加签到日志"""
        async with self.async_session() as session:
            log = SignLog(forum_id=forum_id, fname=fname, success=success, message=message)
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log
    async def get_sign_logs(self, limit: int = 100, forum_id: int | None = None) -> list[SignLog]:
        """获取签到日志"""
        async with self.async_session() as session:
            stmt = select(SignLog)
            if forum_id is not None:
                stmt = stmt.where(SignLog.forum_id == forum_id)
            
            result = await session.execute(
                stmt.order_by(SignLog.signed_at.desc()).limit(limit)
            )
            return list(result.scalars().all())
    async def get_all_unique_forums(self) -> list[dict]:
        """获取所有不重复的贴吧基本信息和权限状态（排除已隐藏的贴吧）"""
        from sqlalchemy import func
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum.fid, Forum.fname, func.max(Forum.is_post_target), func.max(Forum.is_banned))
                .where(Forum.is_hidden == False)
                .group_by(Forum.fname)
                .order_by(Forum.fname)
            )
            return [
                {
                    "fid": row.fid, 
                    "fname": row.fname, 
                    "is_post_target": bool(row[2]),
                    "is_banned": bool(row[3])
                } for row in result.all()
            ]
