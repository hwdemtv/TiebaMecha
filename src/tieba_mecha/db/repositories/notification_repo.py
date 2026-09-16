"""系统通知 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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


class NotificationRepository:
    """系统通知（依赖宿主类提供 async_session/engine）。"""

    async def add_notification(
        self,
        type: str,
        title: str,
        message: str,
        action_url: str | None = None,
        extra: dict | None = None,
        source: str = "local",
        remote_id: str | None = None,
    ) -> Notification:
        """添加通知"""
        import json
        async with self.async_session() as session:
            notification = Notification(
                type=type,
                title=title,
                message=message,
                action_url=action_url,
                extra_json=json.dumps(extra or {}),
                source=source,
                remote_id=remote_id,
            )
            session.add(notification)
            await session.commit()
            await session.refresh(notification)
            return notification
    async def get_unread_notifications(self, limit: int = 50) -> list[Notification]:
        """获取未读通知列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Notification)
                .where(Notification.is_read == False)
                .order_by(Notification.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
    async def get_all_notifications(self, limit: int = 100) -> list[Notification]:
        """获取所有通知列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Notification)
                .order_by(Notification.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
    async def mark_notification_read(self, notification_id: int) -> bool:
        """标记通知为已读"""
        async with self.async_session() as session:
            notification = await session.get(Notification, notification_id)
            if notification:
                notification.is_read = True
                await session.commit()
                return True
            return False
    async def mark_all_notifications_read(self) -> int:
        """标记所有通知为已读，返回更新的数量"""
        async with self.async_session() as session:
            result = await session.execute(
                update(Notification).where(Notification.is_read == False).values(is_read=True)
            )
            await session.commit()
            return result.rowcount
    async def delete_notification(self, notification_id: int) -> bool:
        """删除通知"""
        async with self.async_session() as session:
            notification = await session.get(Notification, notification_id)
            if notification:
                await session.delete(notification)
                await session.commit()
                return True
            return False
    async def clear_old_notifications(self, days: int = 30) -> int:
        """清除指定天数前的已读通知"""
        from datetime import timedelta
        async with self.async_session() as session:
            cutoff = datetime.now() - timedelta(days=days)
            from sqlalchemy import delete
            result = await session.execute(
                delete(Notification).where(
                    Notification.is_read == True,
                    Notification.created_at < cutoff
                )
            )
            await session.commit()
            return result.rowcount
    async def get_unread_count(self) -> int:
        """获取未读通知数量"""
        async with self.async_session() as session:
            from sqlalchemy import func
            result = await session.execute(
                select(func.count(Notification.id)).where(Notification.is_read == False)
            )
            return result.scalar() or 0
    async def notification_exists(self, remote_id: str) -> bool:
        """检查远程通知是否已存在（避免重复入库）"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Notification.id).where(Notification.remote_id == remote_id).limit(1)
            )
            return result.scalar() is not None
