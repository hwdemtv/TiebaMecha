"""爬取任务与帖子缓存 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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


class CrawlRepository:
    """爬取任务与帖子缓存（依赖宿主类提供 async_session/engine）。"""

    async def add_crawl_task(
        self,
        task_type: str,
        target: str,
        account_id: int,
    ) -> CrawlTask:
        """添加爬取任务"""
        async with self.async_session() as session:
            task = CrawlTask(task_type=task_type, target=target, account_id=account_id)
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task
    async def update_crawl_task(
        self,
        task_id: int,
        status: str | None = None,
        result_path: str | None = None,
        total_count: int | None = None,
    ) -> CrawlTask | None:
        """更新爬取任务"""
        async with self.async_session() as session:
            task = await session.get(CrawlTask, task_id)
            if task:
                if status:
                    task.status = status
                if result_path:
                    task.result_path = result_path
                if total_count is not None:
                    task.total_count = total_count
                if status == "completed":
                    task.completed_at = datetime.now()
                await session.commit()
                await session.refresh(task)
                return task
            return None
    async def get_crawl_tasks(self, limit: int = 50) -> list[CrawlTask]:
        """获取爬取任务列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(CrawlTask).order_by(CrawlTask.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())
    async def get_crawl_task_count(self) -> int:
        """获取爬取任务总数"""
        async with self.async_session() as session:
            from sqlalchemy import func
            result = await session.execute(select(func.count(CrawlTask.id)))
            return result.scalar() or 0
    async def delete_crawl_task(self, task_id: int) -> bool:
        """删除爬取任务记录（同时删除关联的结果文件）"""
        from pathlib import Path
        async with self.async_session() as session:
            task = await session.get(CrawlTask, task_id)
            if task:
                # 删除关联的JSON文件
                if task.result_path:
                    try:
                        file_path = Path(task.result_path)
                        if file_path.exists():
                            file_path.unlink()
                    except Exception:
                        pass  # 文件删除失败不影响记录删除

                await session.delete(task)
                await session.commit()
                return True
            return False
    async def clear_old_crawl_tasks(self, days: int = 30) -> tuple[int, int]:
        """
        清理指定天数前的爬取任务

        Returns:
            (删除的任务数, 删除的文件数)
        """
        from datetime import timedelta
        from pathlib import Path

        cutoff = datetime.now() - timedelta(days=days)
        deleted_tasks = 0
        deleted_files = 0

        async with self.async_session() as session:
            from sqlalchemy import delete
            # 查找要删除的任务
            result = await session.execute(
                select(CrawlTask).where(CrawlTask.created_at < cutoff)
            )
            tasks_to_delete = result.scalars().all()

            for task in tasks_to_delete:
                # 删除关联文件
                if task.result_path:
                    try:
                        file_path = Path(task.result_path)
                        if file_path.exists():
                            file_path.unlink()
                            deleted_files += 1
                    except Exception:
                        pass

            # 批量删除记录
            delete_result = await session.execute(
                delete(CrawlTask).where(CrawlTask.created_at < cutoff)
            )
            deleted_tasks = delete_result.rowcount or 0
            await session.commit()

        return deleted_tasks, deleted_files
    async def cache_posts(self, posts: list[dict]) -> int:
        """缓存帖子列表"""
        async with self.async_session() as session:
            for post_data in posts:
                cache = PostCache(
                    tid=post_data["tid"],
                    pid=post_data["pid"],
                    fname=post_data["fname"],
                    title=post_data.get("title", ""),
                    author_id=post_data.get("author_id", 0),
                    author_name=post_data.get("author_name", ""),
                )
                session.add(cache)
            await session.commit()
            return len(posts)
    async def get_cached_posts(self, fname: str | None = None) -> list[PostCache]:
        """获取缓存的帖子"""
        async with self.async_session() as session:
            if fname:
                result = await session.execute(
                    select(PostCache)
                    .where(PostCache.fname == fname)
                    .order_by(PostCache.cached_at.desc())
                )
            else:
                result = await session.execute(select(PostCache).order_by(PostCache.cached_at.desc()))
            return list(result.scalars().all())
    async def clear_post_cache(self) -> None:
        """清空帖子缓存"""
        async with self.async_session() as session:
            await session.execute(delete(PostCache))
            await session.commit()
