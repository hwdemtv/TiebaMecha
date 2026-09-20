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
    async def get_batch_task(self, task_id) -> Optional[BatchPostTask]:
        """按主键获取单个批量任务"""
        async with self.async_session() as session:
            return await session.get(BatchPostTask, task_id)
    async def get_scheduled_batch_tasks(self) -> list[BatchPostTask]:
        """获取所有未执行的批量任务（含未到期），供精确调度注册触发器"""
        async with self.async_session() as session:
            result = await session.execute(
                select(BatchPostTask).where(BatchPostTask.status == "pending")
            )
            return list(result.scalars().all())
    async def claim_batch_task(self, task_id) -> bool:
        """原子认领任务：仅当仍处于 pending 时置为 running。

        精确触发与轮询兜底可能同时到达，认领失败的一方直接放弃，
        避免同一任务被并发执行两次。
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(BatchPostTask)
                .where(BatchPostTask.id == task_id, BatchPostTask.status == "pending")
                .values(status="running")
            )
            await session.commit()
            return (result.rowcount or 0) > 0
    async def get_running_batch_tasks(self) -> list[BatchPostTask]:
        """获取所有执行中的批量任务（派发前同计划互斥检查用）"""
        async with self.async_session() as session:
            result = await session.execute(
                select(BatchPostTask).where(BatchPostTask.status == "running")
            )
            return list(result.scalars().all())
    async def reset_running_batch_tasks(self) -> int:
        """把遗留的 running 任务复位为 pending（进程启动时的崩溃恢复）。

        单进程部署下启动时不可能有任务真正在跑，running 只可能是上次
        异常退出（断电/kill）留下的残值；不复位会导致任务永久卡死。
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(BatchPostTask)
                .where(BatchPostTask.status == "running")
                .values(status="pending")
            )
            await session.commit()
            return result.rowcount or 0
    async def pause_batch_task(self, task_id: int) -> bool:
        """暂停待执行任务：仅当仍处于 pending 时置为 paused。

        条件更新与认领（claim_batch_task）同口径：任务刚好开始执行时
        暂停会失败，避免把 running 覆盖成 paused 导致执行流状态错乱。
        paused 任务被所有调度查询（pending 过滤）天然排除。
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(BatchPostTask)
                .where(BatchPostTask.id == task_id, BatchPostTask.status == "pending")
                .values(status="paused")
            )
            await session.commit()
            return (result.rowcount or 0) > 0
    async def resume_batch_task(self, task_id: int) -> bool:
        """恢复暂停任务：仅当仍处于 paused 时复位为 pending。

        恢复后的 schedule_time 重算与触发器注册由调用方完成。
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(BatchPostTask)
                .where(BatchPostTask.id == task_id, BatchPostTask.status == "paused")
                .values(status="pending")
            )
            await session.commit()
            return (result.rowcount or 0) > 0
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
