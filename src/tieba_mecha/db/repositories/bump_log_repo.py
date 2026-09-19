"""自顶事件流水 Repository mixin（bump_logs 表）。"""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..models import BumpLog

logger = logging.getLogger(__name__)


class BumpLogRepository:
    """自动回帖(自顶)流水（依赖宿主类提供 async_session/engine）。"""

    async def add_bump_log(
        self,
        material_id: int | None,
        tid: int | None,
        fname: str = "",
        account_id: int | None = None,
        account_name: str = "",
        content: str = "",
        success: bool = True,
        message: str = "",
    ) -> int:
        """
        记录一次自顶尝试（成功/失败都记）。

        Args:
            material_id: 关联物料 ID（可能已被删除，允许空）
            tid: 被自顶的帖子 TID
            fname: 贴吧名称快照
            account_id/account_name: 执行账号（含名称快照，防账号删除后无法溯源）
            content: 回帖内容
            success: 是否成功
            message: 失败原因/备注

        Returns:
            新记录 ID（写入异常时返回 0，不抛出——流水记录不应影响自顶主流程）
        """
        try:
            async with self.async_session() as session:
                log = BumpLog(
                    material_id=material_id,
                    tid=int(tid) if tid else None,
                    fname=fname or "",
                    account_id=account_id,
                    account_name=account_name or "",
                    content=content or "",
                    success=success,
                    message=(message or "")[:500],
                )
                session.add(log)
                await session.commit()
                return log.id
        except Exception as ex:
            logger.warning(f"写入自顶流水失败(不影响主流程): {ex}")
            return 0

    async def get_bump_logs(
        self,
        material_id: int | None = None,
        tid: int | None = None,
        limit: int = 50,
    ) -> list[BumpLog]:
        """
        查询自顶历史，按时间倒序。

        Args:
            material_id: 按物料过滤
            tid: 按帖子 TID 过滤
            limit: 返回条数上限

        Returns:
            BumpLog 列表（时间倒序）
        """
        async with self.async_session() as session:
            conds = []
            if material_id is not None:
                conds.append(BumpLog.material_id == material_id)
            if tid is not None:
                conds.append(BumpLog.tid == int(tid))
            stmt = select(BumpLog).order_by(BumpLog.created_at.desc()).limit(limit)
            if conds:
                stmt = stmt.where(*conds)
            result = await session.execute(stmt)
            return list(result.scalars().all())
