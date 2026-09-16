"""批量发帖任务 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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


class BatchTaskRepository:
    """批量发帖任务（依赖宿主类提供 async_session/engine）。"""

    async def add_batch_task(self, **kwargs) -> BatchPostTask:
        """添加批量发帖任务"""
        async with self.async_session() as session:
            task = BatchPostTask(**kwargs)
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task
    async def get_pending_batch_tasks(self) -> list[BatchPostTask]:
        """获取所有待执行（或到达执行时间）的定时任务"""
        now = datetime.now()
        async with self.async_session() as session:
            result = await session.execute(
                select(BatchPostTask).where(
                    BatchPostTask.status == "pending",
                    (BatchPostTask.schedule_time == None) | (BatchPostTask.schedule_time <= now)
                )
            )
            return list(result.scalars().all())
    async def update_batch_task(self, task_id: int, **kwargs) -> None:
        """更新任务状态及进度"""
        async with self.async_session() as session:
            task = await session.get(BatchPostTask, task_id)
            if task:
                for k, v in kwargs.items():
                    if hasattr(task, k):
                        setattr(task, k, v)
                if kwargs.get("status") == "completed":
                    task.completed_at = datetime.now()
                await session.commit()
    async def get_all_batch_tasks(self, limit: int = 50) -> list[BatchPostTask]:
        """获取所有批量任务列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(BatchPostTask).order_by(BatchPostTask.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())
    async def delete_batch_task(self, task_id: int) -> bool:
        """删除批量发帖任务记录"""
        async with self.async_session() as session:
            task = await session.get(BatchPostTask, task_id)
            if task:
                await session.delete(task)
                await session.commit()
                return True
            return False
