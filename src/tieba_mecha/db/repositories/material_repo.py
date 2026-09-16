"""物料池与存活分析 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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


class MaterialRepository:
    """物料池与存活分析（依赖宿主类提供 async_session/engine）。"""

    async def reset_materials_for_task(self, strategy: str = "reuse", restore_original: bool = False, task_id: str | None = None) -> int:
        """
        为循环任务重置物料状态。

        Args:
            strategy: "reuse"=重置复用
            restore_original: 是否恢复原始内容（AI改写场景下，下次发帖会重新改写）
            task_id: 可选，限定只重置该任务关联的物料。为 None 时重置所有物料（向后兼容）

        Returns:
            重置的物料数量
        """
        async with self.async_session() as session:
            values = {
                "status": "pending",
                "last_error": "",
            }
            if restore_original:
                # 恢复原始内容，标记待改写；下次发帖时 AI 会重新改写
                values["ai_status"] = "none"

            where_conditions = [MaterialPool.status.in_(["success", "failed"])]
            if task_id:
                where_conditions.append(MaterialPool.task_id == task_id)

            result = await session.execute(
                update(MaterialPool).where(*where_conditions).values(**values)
            )
            # 恢复原文：将 original_title/original_content 写回 title/content
            if restore_original:
                restore_conditions = [
                    MaterialPool.status == "pending",
                    MaterialPool.original_title != None,
                ]
                if task_id:
                    restore_conditions.append(MaterialPool.task_id == task_id)
                materials = await session.execute(
                    select(MaterialPool).where(*restore_conditions)
                )
                for m in materials.scalars().all():
                    if m.original_title:
                        m.title = m.original_title
                    if m.original_content:
                        m.content = m.original_content
            await session.commit()
            return result.rowcount
    async def add_materials_bulk(self, pairs: list[tuple[str, str]]) -> int:
        """批量添加物料，返回添加成功的条数，执行基于内容的去重逻辑"""
        if not pairs:
            return 0

        added = 0
        async with self.async_session() as session:
            # 分批查询现有内容，避免一次性加载全部到内存
            # 每批查询1000条，使用内容的前100字符进行快速匹配
            batch_size = 1000
            existing_contents = set()

            # 提取所有待添加内容的前缀用于快速匹配
            content_prefixes = {c[:100] for _, c in pairs}

            # 分批查询数据库
            for prefix_batch_start in range(0, len(content_prefixes), batch_size):
                prefix_batch = list(content_prefixes)[prefix_batch_start:prefix_batch_start + batch_size]
                if prefix_batch:
                    # 使用 LIKE 查询可能匹配的记录
                    from sqlalchemy import or_
                    conditions = [MaterialPool.content.like(f"{p}%") for p in prefix_batch]
                    result = await session.execute(
                        select(MaterialPool.content).where(or_(*conditions))
                    )
                    existing_contents.update(result.scalars().all())

            # 添加新物料
            for t, c in pairs:
                if c not in existing_contents:
                    material = MaterialPool(title=t, content=c)
                    session.add(material)
                    existing_contents.add(c)
                    added += 1

            if added > 0:
                await session.commit()
        return added
    async def get_material_success_stats(self) -> dict[str, int]:
        """获取各贴吧的发帖成功次数统计"""
        async with self.async_session() as session:
            from sqlalchemy import func
            result = await session.execute(
                select(
                    MaterialPool.posted_fname,
                    func.count(MaterialPool.id)
                )
                .where(MaterialPool.posted_fname.isnot(None))
                .where(MaterialPool.status == "success")
                .group_by(MaterialPool.posted_fname)
            )
            return {row[0]: row[1] for row in result.all() if row[0]}
    async def get_survival_stats(self) -> dict:
        """获取存活统计概览（仅统计已发帖成功的物料）"""
        async with self.async_session() as session:
            from sqlalchemy import func
            result = await session.execute(
                select(
                    MaterialPool.survival_status,
                    func.count(MaterialPool.id)
                )
                .where(MaterialPool.status == "success")
                .where(MaterialPool.posted_tid.isnot(None))
                .where(MaterialPool.posted_tid != 0)
                .group_by(MaterialPool.survival_status)
            )
            stats = {"total": 0, "alive": 0, "dead": 0, "unknown": 0}
            for status, count in result.all():
                if status in stats:
                    stats[status] = count
                stats["total"] += count
            return stats
    async def get_survival_by_account(self) -> list[dict]:
        """获取按账号分组的存活统计"""
        async with self.async_session() as session:
            from sqlalchemy import func
            result = await session.execute(
                select(
                    MaterialPool.posted_account_id,
                    Account.name,
                    MaterialPool.survival_status,
                    func.count(MaterialPool.id)
                )
                .join(Account, MaterialPool.posted_account_id == Account.id, isouter=True)
                .where(MaterialPool.posted_tid.isnot(None))
                .where(MaterialPool.posted_tid != 0)
                .group_by(
                    MaterialPool.posted_account_id,
                    Account.name,
                    MaterialPool.survival_status
                )
            )
            
            # 按账号聚合数据
            account_stats = {}
            for account_id, account_name, status, count in result.all():
                if account_id not in account_stats:
                    account_stats[account_id] = {
                        "account_id": account_id,
                        "account_name": account_name or f"账号{account_id}",
                        "total": 0,
                        "alive": 0,
                        "dead": 0,
                        "unknown": 0
                    }
                account_stats[account_id][status] = count
                account_stats[account_id]["total"] += count
            
            return list(account_stats.values())
    async def get_materials_paginated(
        self,
        survival_status: str | None = None,
        account_id: int | None = None,
        fname: str | None = None,
        death_reason: str | None = None,
        date_from=None,
        date_to=None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MaterialPool], int]:
        """分页查询物料，返回 (列表, 总数)"""
        async with self.async_session() as session:
            from sqlalchemy import func, select as sa_select
            # 基础条件：仅统计已发帖成功的物料
            base_where = [
                MaterialPool.status == "success",
                MaterialPool.posted_tid.isnot(None),
                MaterialPool.posted_tid != 0,
            ]
            if survival_status:
                base_where.append(MaterialPool.survival_status == survival_status)
            if account_id:
                base_where.append(MaterialPool.posted_account_id == account_id)
            if fname:
                base_where.append(MaterialPool.posted_fname == fname)
            if death_reason:
                base_where.append(MaterialPool.death_reason == death_reason)
            if date_from:
                base_where.append(MaterialPool.posted_time >= date_from)
            if date_to:
                base_where.append(MaterialPool.posted_time <= date_to)

            # 总数
            count_stmt = sa_select(func.count(MaterialPool.id)).where(*base_where)
            total = (await session.execute(count_stmt)).scalar() or 0

            # 分页数据
            offset = (page - 1) * page_size
            data_stmt = (
                select(MaterialPool)
                .where(*base_where)
                .order_by(MaterialPool.id.asc())
                .offset(offset)
                .limit(page_size)
            )
            result = await session.execute(data_stmt)
            return list(result.scalars().all()), total
    async def get_distinct_fnames(self) -> list[str]:
        """获取物料池中所有不同的贴吧名"""
        async with self.async_session() as session:
            from sqlalchemy import func, select as sa_select, distinct
            result = await session.execute(
                sa_select(distinct(MaterialPool.posted_fname))
                .where(MaterialPool.posted_fname.isnot(None))
                .where(MaterialPool.posted_fname != "")
                .order_by(MaterialPool.posted_fname)
            )
            return [row[0] for row in result.all()]
    async def get_distinct_death_reasons(self) -> list[str]:
        """获取物料池中所有不同的阵亡原因"""
        async with self.async_session() as session:
            from sqlalchemy import func, select as sa_select, distinct
            result = await session.execute(
                sa_select(distinct(MaterialPool.death_reason))
                .where(MaterialPool.death_reason.isnot(None))
                .where(MaterialPool.death_reason != "")
                .order_by(MaterialPool.death_reason)
            )
            return [row[0] for row in result.all()]
    async def get_materials_by_ids(self, ids: list[int]) -> list[MaterialPool]:
        """按 ID 列表批量查询物料"""
        if not ids:
            return []
        async with self.async_session() as session:
            result = await session.execute(
                select(MaterialPool).where(MaterialPool.id.in_(ids))
            )
            return list(result.scalars().all())
    async def get_survival_cache_data(self) -> dict[int, str]:
        """获取所有已发物料的 {tid: survival_status} 映射，用于初始化缓存（轻量查询）"""
        async with self.async_session() as session:
            result = await session.execute(
                select(MaterialPool.posted_tid, MaterialPool.survival_status)
                .where(MaterialPool.status == "success")
                .where(MaterialPool.posted_tid.isnot(None))
                .where(MaterialPool.posted_tid != 0)
                .where(MaterialPool.survival_status != "unknown")
            )
            return {row[0]: row[1] for row in result.all()}
    async def get_materials_by_status_paginated(
        self,
        statuses: list[str] | None = None,
        search_text: str | None = None,
        page: int = 1,
        page_size: int = 50,
        order_desc: bool = False,
        survival_status: str | None = None,
    ) -> tuple[list[MaterialPool], int]:
        """按状态列表分页查询物料，支持标题/内容模糊搜索，返回 (列表, 总数)

        Args:
            order_desc: 是否按 ID 降序排列（大的在前），默认升序（小的在前）
            survival_status: 存活状态过滤 (alive/dead/unknown)，None 表示不过滤
        """
        async with self.async_session() as session:
            from sqlalchemy import func, select as sa_select, or_
            base_where = []
            if statuses:
                base_where.append(MaterialPool.status.in_(statuses))
            if search_text:
                keyword = f"%{search_text}%"
                base_where.append(or_(
                    MaterialPool.title.ilike(keyword),
                    MaterialPool.content.ilike(keyword),
                    MaterialPool.posted_fname.ilike(keyword),
                ))
            if survival_status:
                base_where.append(MaterialPool.survival_status == survival_status)
            # 归档库仅展示有 posted_tid 的记录
            if "success" in (statuses or []):
                base_where.append(MaterialPool.posted_tid.isnot(None))
                base_where.append(MaterialPool.posted_tid != 0)
            # 总数
            count_stmt = sa_select(func.count(MaterialPool.id)).where(*base_where)
            total = (await session.execute(count_stmt)).scalar() or 0
            # 分页数据
            offset = (page - 1) * page_size
            order_col = MaterialPool.id.desc() if order_desc else MaterialPool.id.asc()
            data_stmt = (
                select(MaterialPool)
                .where(*base_where)
                .order_by(order_col)
                .offset(offset)
                .limit(page_size)
            )
            result = await session.execute(data_stmt)
            return list(result.scalars().all()), total
    async def get_material_ids_by_status(
        self,
        statuses: list[str] | None = None,
        search_text: str | None = None,
        survival_status: str | None = None,
    ) -> list[int]:
        """按状态查询物料 ID 列表（不加载完整对象，用于跨页全选）"""
        async with self.async_session() as session:
            from sqlalchemy import select as sa_select, or_
            base_where = []
            if statuses:
                base_where.append(MaterialPool.status.in_(statuses))
            if search_text:
                keyword = f"%{search_text}%"
                base_where.append(or_(
                    MaterialPool.title.ilike(keyword),
                    MaterialPool.content.ilike(keyword),
                    MaterialPool.posted_fname.ilike(keyword),
                ))
            if survival_status:
                base_where.append(MaterialPool.survival_status == survival_status)
            # 归档库全选仅选中 posted_tid 有效的记录，与分页查询保持一致
            if "success" in (statuses or []):
                base_where.append(MaterialPool.posted_tid.isnot(None))
                base_where.append(MaterialPool.posted_tid != 0)
            stmt = sa_select(MaterialPool.id).where(*base_where)
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]
    async def get_materials_status_counts(self) -> dict[str, int]:
        """获取各状态的物料数量统计"""
        async with self.async_session() as session:
            from sqlalchemy import func, select as sa_select
            result = await session.execute(
                sa_select(MaterialPool.status, func.count(MaterialPool.id))
                .group_by(MaterialPool.status)
            )
            return {row[0]: row[1] for row in result.all()}
    async def get_success_survival_counts(self) -> dict[str, int]:
        """获取 success 状态物料的存活统计（基于数据库字段，非内存缓存）"""
        async with self.async_session() as session:
            from sqlalchemy import func, select as sa_select
            result = await session.execute(
                sa_select(MaterialPool.survival_status, func.count(MaterialPool.id))
                .where(MaterialPool.status == "success")
                .where(MaterialPool.posted_tid.isnot(None))
                .where(MaterialPool.posted_tid != 0)
                .group_by(MaterialPool.survival_status)
            )
            counts = {"alive": 0, "dead": 0, "unknown": 0}
            for status, count in result.all():
                # NULL 或空字符串视为 unknown
                key = status if status in counts else "unknown"
                counts[key] = counts.get(key, 0) + count
            return counts
    async def get_materials(self, status: str | None = None, limit: int | None = None) -> list[MaterialPool]:
        async with self.async_session() as session:
            stmt = select(MaterialPool).order_by(MaterialPool.id)
            if status:
                stmt = stmt.where(MaterialPool.status == status)
            if limit:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())
    async def delete_material(self, material_id: int) -> bool:
        async with self.async_session() as session:
            m = await session.get(MaterialPool, material_id)
            if m:
                await session.delete(m)
                await session.commit()
                return True
            return False
    async def update_material_status(
        self,
        material_id: int,
        status: str,
        last_error: str | None = None,
        posted_fname: str | None = None,
        posted_tid: int | None = None,
        posted_account_id: int | None = None,
        posted_time: datetime | None = None,
        task_id: str | None = None
    ) -> None:
        async with self.async_session() as session:
            m = await session.get(MaterialPool, material_id)
            if m:
                m.status = status
                m.last_used_at = datetime.now()
                if posted_time is not None:
                    m.posted_time = posted_time

                # [修复] 状态重置为 pending 时清空自顶计数与记录；成功发帖时保留历史自顶数据
                if status == "pending":
                    m.bump_count = 0
                    m.last_bumped_at = None

                if last_error is not None: m.last_error = last_error
                if posted_fname is not None: m.posted_fname = posted_fname
                if posted_tid is not None: m.posted_tid = posted_tid
                if posted_account_id is not None: m.posted_account_id = posted_account_id
                if task_id is not None: m.task_id = task_id
                await session.commit()
    async def update_material_survival_status(self, material_id: int, status: str, death_reason: str = "") -> None:
        """更新物料的存活探测状态，并联动更新 Forum 封禁标记。
        
        联动规则：
        - 帖子阵亡 (dead) + 被删原因 (deleted_by_system/deleted_by_mod)
          → 标记该账号在该贴吧 Forum.is_banned=True, is_post_target=False
          (banned_by_mod 为历史版本拼写, 仅作输入兼容保留)
        - 仅更新 Forum，不更新 TargetPool（TargetPool 的 fail_count 仅由发帖环节维护，
          避免发帖失败 + 存活检测重复计数）
        """
        async with self.async_session() as session:
            m = await session.get(MaterialPool, material_id)
            if m:
                m.survival_status = status
                m.death_reason = death_reason
                m.last_checked_at = datetime.now()

                # 联动标记：帖子被删除时，标记该账号在该贴吧为封禁/风控状态
                # (banned_by_mod 仅兼容历史数据, 当前分类器只产出 deleted_by_mod)
                ban_reason_map = {
                    "banned_by_mod": "存活探测：帖子被吧务删除",
                    "deleted_by_system": "存活探测：帖子被系统风控删除",
                    "deleted_by_mod": "存活探测：帖子被吧务删除",
                }
                if status == "dead" and death_reason in ban_reason_map and m.posted_account_id and m.posted_fname:
                    ban_reason = ban_reason_map[death_reason]
                    forum = await session.execute(
                        select(Forum).where(
                            Forum.account_id == m.posted_account_id,
                            Forum.fname == m.posted_fname
                        )
                    )
                    forum_obj = forum.scalar_one_or_none()
                    if forum_obj:
                        if not forum_obj.is_banned:
                            forum_obj.is_banned = True
                            forum_obj.ban_reason = ban_reason
                            forum_obj.is_post_target = False
                    else:
                        # 不创建虚假的Forum记录，仅记录封禁信息到日志
                        # 避免使用随机FID导致数据冲突
                        logger.warning(
                            f"物料 {material_id} 封禁记录：账号 {m.posted_account_id} 在 {m.posted_fname} 被封禁，"
                            f"原因: {ban_reason}。未创建Forum记录（需要真实的FID）。"
                        )

                await session.commit()
    async def update_material_bump(self, material_id: int) -> None:
        """更新自动回帖(自顶)计数与时间记录"""
        async with self.async_session() as session:
            m = await session.get(MaterialPool, material_id)
            if m:
                m.bump_count += 1
                m.last_bumped_at = datetime.now()
                await session.commit()
    async def update_material_ai(self, material_id: int, new_title: str, new_content: str) -> None:
        async with self.async_session() as session:
            m = await session.get(MaterialPool, material_id)
            if m:
                # 保护最初始的主干文案
                if m.ai_status != "rewritten":
                    m.original_title = m.title
                    m.original_content = m.content
                m.title = new_title
                m.content = new_content
                m.ai_status = "rewritten"
                await session.commit()
    async def update_material_content(self, material_id: int, new_title: str, new_content: str) -> None:
        """手动修改物料文案"""
        async with self.async_session() as session:
            m = await session.get(MaterialPool, material_id)
            if m:
                m.title = new_title
                m.content = new_content
                await session.commit()
    async def clear_materials(self, only_status: str | None = None) -> None:
        from sqlalchemy import delete
        async with self.async_session() as session:
            if only_status:
                await session.execute(delete(MaterialPool).where(MaterialPool.status == only_status))
            else:
                await session.execute(delete(MaterialPool))
            await session.commit()
    async def reset_materials_status(self) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(MaterialPool).values(
                    status="pending", 
                    last_error="",
                    bump_count=0,
                    last_bumped_at=None
                )
            )
            await session.commit()
