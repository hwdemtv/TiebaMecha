"""Data crawling functionality"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import aiotieba

from ..db.crud import Database
from .account import get_account_credentials
from .client_factory import create_client

# 默认输出目录
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent.parent.parent.parent / "output"


@dataclass
class CrawlProgress:
    """爬取进度"""

    task_id: int
    task_type: str
    target: str
    current: int = 0
    total: int = 0
    status: str = "running"
    message: str = ""


@dataclass
class ThreadData:
    """帖子数据"""

    tid: int
    pid: int
    title: str
    text: str
    author_id: int
    author_name: str
    reply_num: int
    agree: int
    create_time: int
    last_time: int
    is_good: bool
    is_top: bool
    fname: str = ""


@dataclass
class UserData:
    """用户数据"""

    user_id: int
    user_name: str
    nick_name: str
    portrait: str
    level: int
    gender: int
    sign: str
    tieba_uid: int = 0
    posts: list[dict] = field(default_factory=list)


async def crawl_threads(
    db: Database,
    fname: str,
    pages: int = 5,
    output_dir: Path | None = None,
) -> AsyncGenerator[CrawlProgress, None]:
    """
    爬取贴吧帖子

    Args:
        db: 数据库实例
        fname: 贴吧名称
        pages: 爬取页数
        output_dir: 输出目录

    Yields:
        CrawlProgress: 爬取进度
    """
    creds = await get_account_credentials(db)
    if not creds:
        yield CrawlProgress(0, "threads", fname, status="failed", message="未找到账号凭证")
        return

    bduss, stoken, proxy_id, cuid, ua = creds

    # 创建任务记录
    account = await db.get_active_account()
    task = await db.add_crawl_task(
        task_type="threads",
        target=fname,
        account_id=account.id if account else 0,
    )

    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    threads_data: list[ThreadData] = []
    total_expected = pages * 50  # 每页约50条

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        for pn in range(1, pages + 1):
            try:
                result = await client.get_threads(fname, pn=pn, rn=50)

                for thread in result:
                    threads_data.append(
                        ThreadData(
                            tid=thread.tid,
                            pid=thread.pid,
                            title=thread.title,
                            text=thread.text,
                            author_id=thread.author_id,
                            author_name=thread.user.user_name if thread.user else "",
                            reply_num=thread.reply_num,
                            agree=thread.agree,
                            create_time=thread.create_time,
                            last_time=thread.last_time,
                            is_good=thread.is_good,
                            is_top=thread.is_top,
                            fname=fname,
                        )
                    )

                progress = CrawlProgress(
                    task_id=task.id,
                    task_type="threads",
                    target=fname,
                    current=len(threads_data),
                    total=total_expected,
                    status="running",
                )

                await db.update_crawl_task(task.id, total_count=len(threads_data))
                yield progress

            except Exception as e:
                yield CrawlProgress(
                    task_id=task.id,
                    task_type="threads",
                    target=fname,
                    current=len(threads_data),
                    status="error",
                    message=str(e),
                )

    # 保存结果
    output_file = output_dir / f"threads_{fname}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            [t.__dict__ for t in threads_data],
            f,
            ensure_ascii=False,
            indent=2,
        )

    await db.update_crawl_task(
        task.id,
        status="completed",
        result_path=str(output_file),
        total_count=len(threads_data),
    )

    yield CrawlProgress(
        task_id=task.id,
        task_type="threads",
        target=fname,
        current=len(threads_data),
        status="completed",
        message=f"已保存到 {output_file}",
    )


async def crawl_user(
    db: Database,
    user_id: int | str,
    with_posts: bool = False,
    output_dir: Path | None = None,
) -> AsyncGenerator[CrawlProgress, None]:
    """
    爬取用户信息

    Args:
        db: 数据库实例
        user_id: 用户ID 或 portrait
        with_posts: 是否爬取发帖记录
        output_dir: 输出目录

    Yields:
        CrawlProgress: 爬取进度
    """
    creds = await get_account_credentials(db)
    if not creds:
        yield CrawlProgress(0, "user", str(user_id), status="failed", message="未找到账号凭证")
        return

    bduss, stoken, proxy_id, cuid, ua = creds

    account = await db.get_active_account()
    task = await db.add_crawl_task(
        task_type="user",
        target=str(user_id),
        account_id=account.id if account else 0,
    )

    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        # 获取用户信息
        try:
            if isinstance(user_id, int):
                user = await client.get_user_info(user_id)
            else:
                user = await client.get_user_info(user_id)

            user_data = UserData(
                user_id=user.user_id,
                user_name=user.user_name,
                nick_name=user.nick_name,
                portrait=user.portrait,
                level=user.level,
                gender=user.gender,
                sign=user.sign,
                tieba_uid=user.tieba_uid,
            )

            yield CrawlProgress(
                task_id=task.id,
                task_type="user",
                target=str(user_id),
                current=1,
                status="running",
                message=f"获取用户: {user.user_name}",
            )

            # 获取发帖记录
            if with_posts:
                posts = []
                async for post in client.get_user_posts(user.user_id):
                    posts.append(
                        {
                            "tid": post.tid,
                            "title": post.title,
                            "fname": post.fname,
                            "create_time": post.create_time,
                        }
                    )
                    if len(posts) % 50 == 0:
                        yield CrawlProgress(
                            task_id=task.id,
                            task_type="user",
                            target=str(user_id),
                            current=len(posts),
                            status="running",
                            message=f"获取发帖记录: {len(posts)} 条",
                        )

                user_data.posts = posts

        except Exception as e:
            yield CrawlProgress(
                task_id=task.id,
                task_type="user",
                target=str(user_id),
                status="failed",
                message=str(e),
            )
            return

    # 保存结果
    output_file = output_dir / f"user_{user_data.user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(user_data.__dict__, f, ensure_ascii=False, indent=2, default=str)

    await db.update_crawl_task(
        task.id,
        status="completed",
        result_path=str(output_file),
        total_count=1 + len(user_data.posts),
    )

    yield CrawlProgress(
        task_id=task.id,
        task_type="user",
        target=str(user_id),
        current=1 + len(user_data.posts),
        status="completed",
        message=f"已保存到 {output_file}",
    )


async def get_crawl_history(db: Database, limit: int = 20) -> list[dict]:
    """获取爬取历史"""
    tasks = await db.get_crawl_tasks(limit)
    return [
        {
            "id": t.id,
            "type": t.task_type,
            "target": t.target,
            "status": t.status,
            "count": t.total_count,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "result_path": t.result_path,
        }
        for t in tasks
    ]
