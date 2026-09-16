"""批量发帖流水 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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


class BatchLogRepository:
    """批量发帖流水（依赖宿主类提供 async_session/engine）。"""

    async def add_batch_post_log(
        self,
        task_id: str | None,
        fname: str,
        status: str,
        account_id: int | None = None,
        account_name: str | None = None,
        title: str | None = None,
        tid: int | None = None,
        message: str | None = None,
        data: dict | None = None,
    ) -> int:
        """记录一条批量发帖流水"""
        import json
        async with self.async_session() as session:
            log = BatchPostLog(
                task_id=task_id,
                account_id=account_id,
                account_name=account_name,
                fname=fname,
                title=title,
                tid=tid,
                status=status,
                message=message,
                data_json=json.dumps(data or {}),
            )
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log.id
    async def get_batch_post_logs(self, limit: int = 200, task_id: str | None = None) -> list[BatchPostLog]:
        """获取流水日志列表"""
        async with self.async_session() as session:
            stmt = select(BatchPostLog).order_by(BatchPostLog.created_at.desc())
            if task_id:
                stmt = stmt.where(BatchPostLog.task_id == task_id)
            result = await session.execute(stmt.limit(limit))
            return list(result.scalars().all())
    async def clear_old_batch_post_logs(self, keep_count: int = 500) -> int:
        """清理旧流水点位，仅保留最近的 keep_count 条"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            if keep_count <= 0:
                # 清除所有记录
                res = await session.execute(delete(BatchPostLog))
                await session.commit()
                return res.rowcount or 0
            # 找到第 keep_count 条之后的 ID
            cutoff_stmt = select(BatchPostLog.id).order_by(BatchPostLog.created_at.desc()).offset(keep_count).limit(1)
            result = await session.execute(cutoff_stmt)
            cutoff_id = result.scalar()
            
            if cutoff_id:
                del_stmt = delete(BatchPostLog).where(BatchPostLog.id <= cutoff_id)
                res = await session.execute(del_stmt)
                await session.commit()
                return res.rowcount or 0
        return 0
