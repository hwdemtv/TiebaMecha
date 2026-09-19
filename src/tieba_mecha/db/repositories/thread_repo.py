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

    # --- 我的帖子统一视图（MaterialPool 已发物料 + ThreadRecord 监控记录合并） ---

    # 可确认的删除原因（dead + 该集合 → 展示层"已删除"；否则视为"疑似删除"）
    CONFIRMED_DEATH_REASONS = frozenset({
        "deleted_by_system", "deleted_by_mod", "deleted_by_user",
        "deleted_unknown", "auto_removed",
    })

    async def get_my_posts(
        self,
        account_id: int | None = None,
        fname: str | None = None,
        survival: str | None = None,
        is_good: bool | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        keyword: str | None = None,
        limit: int = 5000,
    ) -> list:
        """
        合并「已发物料」与「本地帖子记录」为统一的"我的帖子"视图。

        - MaterialPool (status=success 且有 posted_tid)：有完整生命周期数据
          （存活状态/删除原因/自顶/AI 改写），是视图主体；
        - ThreadRecord：监控/导入的帖子缓存，仅补充没有对应物料的行
          （存活状态恒为 unknown）。

        Args:
            account_id: 发帖账号/作者 ID
            fname: 贴吧名
            survival: alive/dead/suspected/unknown，None 表示不过滤。
                suspected = dead 且删除原因不可确认（检测异常/验证码等）
            is_good: 是否精品（物料行经 ThreadRecord 按 tid 关联）
            date_from/date_to: 发布时间范围（记录行按 updated_at 近似）
            keyword: 标题/正文模糊匹配
            limit: 返回行数上限（内存合并，桌面单机场景足够）

        Returns:
            统一行对象列表（SimpleNamespace），按发布时间倒序。
        """
        from types import SimpleNamespace

        from sqlalchemy import or_, select as sa_select

        async with self.async_session() as session:
            # 0. 物料 tid 身份集合（记录行去重依据）：同一 tid 一律以物料行为准。
            #    仅按账号圈定——贴吧/时间/关键字在两表字段上可能不一致
            #    （如记录行的 updated_at 近似值），纳入会导致漏去重、产生幽灵记录行。
            tid_conds = [
                MaterialPool.status == "success",
                MaterialPool.posted_tid.isnot(None),
                MaterialPool.posted_tid != 0,
            ]
            if account_id:
                tid_conds.append(MaterialPool.posted_account_id == account_id)
            tid_stmt = sa_select(MaterialPool.posted_tid).where(*tid_conds)
            material_tids: set[int] = {
                int(tid) for (tid,) in (await session.execute(tid_stmt)).all() if tid
            }

            # 1. 本地帖子记录（含未入物料池的监控/导入行），同时作为 tid → 记录 映射
            rec_conds = []
            if account_id:
                rec_conds.append(ThreadRecord.author_id == account_id)
            if fname:
                rec_conds.append(ThreadRecord.fname == fname)
            if date_from:
                rec_conds.append(ThreadRecord.updated_at >= date_from)
            if date_to:
                rec_conds.append(ThreadRecord.updated_at <= date_to)
            if keyword:
                kw = f"%{keyword}%"
                rec_conds.append(or_(ThreadRecord.title.ilike(kw), ThreadRecord.text.ilike(kw)))
            if is_good is not None:
                rec_conds.append(ThreadRecord.is_good == is_good)
            rec_stmt = sa_select(ThreadRecord).order_by(ThreadRecord.updated_at.desc()).limit(limit)
            if rec_conds:
                rec_stmt = rec_stmt.where(*rec_conds)
            records = list((await session.execute(rec_stmt)).scalars().all())
            record_by_tid = {r.tid: r for r in records}

            # 2. 已发物料（视图主体）
            mat_conds = [
                MaterialPool.status == "success",
                MaterialPool.posted_tid.isnot(None),
                MaterialPool.posted_tid != 0,
            ]
            if account_id:
                mat_conds.append(MaterialPool.posted_account_id == account_id)
            if fname:
                mat_conds.append(MaterialPool.posted_fname == fname)
            if survival == "alive":
                mat_conds.append(MaterialPool.survival_status == "alive")
            elif survival in ("dead", "suspected"):
                mat_conds.append(MaterialPool.survival_status == "dead")
            elif survival == "unknown":
                mat_conds.append(MaterialPool.survival_status == "unknown")
            if date_from:
                mat_conds.append(MaterialPool.posted_time >= date_from)
            if date_to:
                mat_conds.append(MaterialPool.posted_time <= date_to)
            if keyword:
                kw = f"%{keyword}%"
                mat_conds.append(or_(MaterialPool.title.ilike(kw), MaterialPool.content.ilike(kw)))
            mat_stmt = sa_select(MaterialPool).where(*mat_conds).limit(limit)
            materials = list((await session.execute(mat_stmt)).scalars().all())

        rows: list = []
        for m in materials:
            tid = int(m.posted_tid)
            if survival == "suspected" and (m.death_reason or "") in self.CONFIRMED_DEATH_REASONS:
                continue
            rec = record_by_tid.get(tid)
            if is_good is not None and (rec.is_good if rec else False) != is_good:
                continue
            post_time = m.posted_time or m.created_at
            rows.append(SimpleNamespace(
                src="material",
                material_id=m.id,
                tid=tid,
                title=m.title or "",
                content=m.content or "",
                fname=m.posted_fname or "",
                account_id=m.posted_account_id,
                post_time=post_time,
                reply_num=rec.reply_num if rec else 0,
                is_good=rec.is_good if rec else False,
                survival_status=m.survival_status or "unknown",
                death_reason=m.death_reason or "",
                last_checked_at=m.last_checked_at,
                ai_status=m.ai_status or "none",
                original_title=m.original_title,
                original_content=m.original_content,
                is_auto_bump=bool(m.is_auto_bump),
                bump_count=m.bump_count or 0,
                last_bumped_at=m.last_bumped_at,
                bump_mode=m.bump_mode or "once",
                bump_hour=m.bump_hour or 10,
                bump_duration_days=m.bump_duration_days or 0,
                bump_start_date=m.bump_start_date,
                task_id=m.task_id,
                mat_status=m.status,
            ))

        # 3. 无物料对应的记录行（监控/导入），存活恒为 unknown
        for r in records:
            if r.tid in material_tids:
                continue
            if survival in ("alive", "dead", "suspected"):
                continue  # 记录行无存活数据，只匹配 unknown/不过滤
            rows.append(SimpleNamespace(
                src="record",
                material_id=None,
                tid=r.tid,
                title=r.title or "",
                content=r.text or "",
                fname=r.fname or "",
                account_id=r.author_id or None,
                post_time=r.updated_at,
                reply_num=r.reply_num or 0,
                is_good=r.is_good,
                survival_status="unknown",
                death_reason="",
                last_checked_at=None,
                ai_status="none",
                original_title=None,
                original_content=None,
                is_auto_bump=False,
                bump_count=0,
                last_bumped_at=None,
                bump_mode="once",
                bump_hour=10,
                bump_duration_days=0,
                bump_start_date=None,
                task_id=None,
                mat_status=None,
            ))

        rows.sort(key=lambda x: x.post_time or datetime.min, reverse=True)
        return rows[:limit]
