"""本地帖子记录 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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


class ThreadRepository:
    """本地帖子记录（依赖宿主类提供 async_session/engine）。"""

    async def upsert_thread_records(self, threads: list[dict]) -> int:
        """
        批量插入或更新帖子记录 (UPSERT)

        Args:
            threads: 帖子字典列表，每个字典需包含 tid, title, fname 等字段

        Returns:
            处理的记录数量
        """
        if not threads:
            return 0

        async with self.async_session() as session:
            for t in threads:
                tid = t.get("tid")
                if not tid:
                    continue
                tid = int(tid)  # 强制转换为 int，处理大数或字符串

                existing = await session.get(ThreadRecord, tid)
                if existing:
                    # 更新现有记录
                    existing.title = t.get("title", existing.title)
                    existing.author_name = t.get("author_name", existing.author_name)
                    existing.author_id = t.get("author_id", existing.author_id)
                    existing.reply_num = t.get("reply_num", existing.reply_num)
                    existing.text = t.get("text", existing.text)
                    existing.fname = t.get("fname", existing.fname)
                    existing.is_good = t.get("is_good", existing.is_good)
                else:
                    # 插入新记录
                    record = ThreadRecord(
                        tid=tid,
                        title=t.get("title", ""),
                        author_name=t.get("author_name", ""),
                        author_id=t.get("author_id", 0),
                        reply_num=t.get("reply_num", 0),
                        text=t.get("text"),
                        fname=t.get("fname", ""),
                        is_good=t.get("is_good", False),
                    )
                    session.add(record)

            await session.commit()
        return len(threads)
    async def get_thread_records(self, fname: str | None = None, limit: int = 100) -> list[ThreadRecord]:
        """
        获取本地存储的帖子记录

        Args:
            fname: 贴吧名称过滤，None 表示获取所有
            limit: 返回数量限制

        Returns:
            ThreadRecord 列表
        """
        async with self.async_session() as session:
            query = select(ThreadRecord).order_by(ThreadRecord.updated_at.desc())
            if fname:
                query = query.where(ThreadRecord.fname == fname)
            query = query.limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())
    async def delete_thread_record(self, tid: int) -> bool:
        """
        从本地数据库删除帖子记录

        Args:
            tid: 帖子ID

        Returns:
            是否删除成功
        """
        async with self.async_session() as session:
            tid = int(tid)  # 强制转换为 int
            record = await session.get(ThreadRecord, tid)
            if record:
                await session.delete(record)
                await session.commit()
                return True
            return False
    async def delete_thread_records_bulk(self, tids: list[int]) -> int:
        """
        批量删除本地帖子记录

        Args:
            tids: 帖子ID列表

        Returns:
            删除的数量
        """
        if not tids:
            return 0

        async with self.async_session() as session:
            count = 0
            for tid in tids:
                tid = int(tid)  # 强制转换为 int
                record = await session.get(ThreadRecord, tid)
                if record:
                    await session.delete(record)
                    count += 1
            await session.commit()
            return count
