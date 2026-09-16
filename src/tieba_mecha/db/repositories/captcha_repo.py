"""验证码/异常事件 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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


class CaptchaRepository:
    """验证码/异常事件（依赖宿主类提供 async_session/engine）。"""

    async def save_captcha_event(
        self,
        account_id: int | None = None,
        task_id: int | None = None,
        event_type: str = "captcha",
        reason: str = "",
    ) -> int:
        """保存验证码事件"""
        from ..models import CaptchaEvent
        async with self.async_session() as session:
            event = CaptchaEvent(
                account_id=account_id,
                task_id=task_id,
                event_type=event_type,
                reason=reason,
                status="pending",
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event.id
    async def get_captcha_events(
        self,
        account_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """获取验证码事件列表"""
        from ..models import CaptchaEvent
        async with self.async_session() as session:
            query = select(CaptchaEvent).order_by(CaptchaEvent.created_at.desc())
            if account_id is not None:
                query = query.where(CaptchaEvent.account_id == account_id)
            if status is not None:
                query = query.where(CaptchaEvent.status == status)
            query = query.limit(limit)
            result = await session.execute(query)
            events = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "account_id": e.account_id,
                    "task_id": e.task_id,
                    "event_type": e.event_type,
                    "reason": e.reason,
                    "status": e.status,
                    "created_at": e.created_at,
                    "resolved_at": e.resolved_at,
                    "resolved_by": e.resolved_by,
                    "notes": e.notes,
                }
                for e in events
            ]
    async def resolve_captcha_event(
        self,
        event_id: int,
        resolved_by: str = "manual",
        notes: str = "",
    ) -> bool:
        """解决验证码事件"""
        from ..models import CaptchaEvent
        async with self.async_session() as session:
            event = await session.get(CaptchaEvent, event_id)
            if event:
                event.status = "resolved"
                event.resolved_at = datetime.now()
                event.resolved_by = resolved_by
                event.notes = notes
                await session.commit()
                return True
            return False
    async def get_pending_captcha_count(self) -> int:
        """获取待处理的验证码事件数量"""
        from ..models import CaptchaEvent
        async with self.async_session() as session:
            result = await session.execute(
                select(CaptchaEvent).where(CaptchaEvent.status == "pending")
            )
            return len(result.scalars().all())
    async def clear_resolved_captcha_events(self) -> int:
        """清除已解决的验证码事件记录"""
        from ..models import CaptchaEvent
        from sqlalchemy import delete as sql_delete
        async with self.async_session() as session:
            result = await session.execute(
                sql_delete(CaptchaEvent).where(CaptchaEvent.status == "resolved")
            )
            await session.commit()
            return result.rowcount
