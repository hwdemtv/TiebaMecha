"""靶场池与矩阵统计 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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


class TargetPoolRepository:
    """靶场池与矩阵统计（依赖宿主类提供 async_session/engine）。"""

    async def auto_sync_post_target(self) -> int:
        """根据 is_banned 和 deleted_count 自动同步 is_post_target 字段。

        自动判定规则（只关闭，不自动打开，避免覆盖用户手动设置）：
        - 被封禁 → is_post_target=False
        - 有被吧务删帖等"贴吧侧风险"记录 → is_post_target=False
        - 安全状态不自动恢复，需用户手动在 UI 中重新开启

        死亡原因分流（存活反馈闭环）：
        - deleted_by_system（系统风控删除）是内容问题而非贴吧问题，
          关吧无法解决——路由给 AI 改写策略（存活样本注入/深度改写建议），
          不在此处关停贴吧。

        Returns: 更新的记录数
        """
        from sqlalchemy import func, update as sa_update
        async with self.async_session() as session:
            # 获取有贴吧侧风险删帖记录的贴吧名集合
            # （排除用户自删/探测异常，以及内容侧风险的系统删除）
            _excluded_reasons = ["deleted_by_user", "captcha_required", "error", "deleted_by_system"]
            dead_stmt = (
                select(MaterialPool.posted_fname)
                .where(
                    MaterialPool.survival_status == "dead",
                    ~MaterialPool.death_reason.in_(_excluded_reasons),
                    MaterialPool.posted_fname.isnot(None),
                    MaterialPool.posted_fname != "",
                )
                .distinct()
            )
            dead_result = await session.execute(dead_stmt)
            deleted_fnames = {row.posted_fname for row in dead_result.all()}

            updated = 0
            # 1. 封禁的贴吧 → is_post_target=False
            ban_result = await session.execute(
                sa_update(Forum)
                .where(Forum.is_banned == True, Forum.is_post_target == True)
                .values(is_post_target=False)
            )
            updated += ban_result.rowcount

            # 2. 有被删帖记录的贴吧 → is_post_target=False
            if deleted_fnames:
                del_result = await session.execute(
                    sa_update(Forum)
                    .where(Forum.fname.in_(deleted_fnames), Forum.is_post_target == True, Forum.is_banned == False)
                    .values(is_post_target=False)
                )
                updated += del_result.rowcount

            # 不再自动恢复 is_post_target=True，避免覆盖用户手动设置
            # 用户可通过 UI 的"批量切换火力"或单贴吧开关手动重新开启

            await session.commit()
            return updated
    async def backfill_success_count(self) -> int:
        """从 BatchPostLog 实时统计击穿数，同步到 TargetPool.success_count。
        
        设计原则：success_count 的唯一数据来源，每次调用都从日志重新统计，
        确保 success_count 与 BatchPostLog 始终一致，避免手动递增导致的计数问题。
        
        Returns: 更新的记录数
        """
        from sqlalchemy import func
        async with self.async_session() as session:
            # 1. 统计每个 fname 的成功发帖次数
            success_stmt = (
                select(BatchPostLog.fname, func.count(BatchPostLog.id).label("cnt"))
                .where(BatchPostLog.status == "success")
                .group_by(BatchPostLog.fname)
            )
            result = await session.execute(success_stmt)
            success_map = {row.fname: row.cnt for row in result.all()}

            logger.info(f"[击穿同步] BatchPostLog 中有 {len(success_map)} 个吧存在成功记录")

            # 2. 获取当前 target_pool 状态
            pool_stmt = select(TargetPool.fname, TargetPool.success_count)
            pool_result = await session.execute(pool_stmt)
            pool_map = {row.fname: row.success_count for row in pool_result.all()}

            updated = 0

            # 3. 仅更新已有记录的 success_count（不自动创建，避免删除后自动恢复）
            for fname, cnt in success_map.items():
                if fname not in pool_map:
                    # 不自动创建，用户手动删除后不应自动恢复
                    continue
                elif cnt != pool_map.get(fname, 0):
                    # 日志统计值与当前值不一致时同步
                    r = await session.execute(
                        update(TargetPool)
                        .where(TargetPool.fname == fname)
                        .values(success_count=cnt)
                    )
                    updated += r.rowcount

            if updated > 0:
                await session.commit()
            logger.info(f"[击穿同步] 同步了 {updated} 条靶场击穿数记录")
            return updated
    async def get_native_post_targets(self, account_id: int | None = None) -> list[str]:
        """获取已标记为 is_post_target=True 且未被封禁的本机安全贴吧名池（自动判定）"""
        async with self.async_session() as session:
            stmt = select(Forum.fname).where(
                Forum.is_post_target == True,
                Forum.is_banned == False
            )
            if account_id:
                stmt = stmt.where(Forum.account_id == account_id)
            result = await session.execute(stmt.distinct())
            return result.scalars().all()
    async def get_forum_matrix_stats(self) -> list[dict]:
        """
        获取矩阵全局吧库统计数据。
        汇总所有账号关注的贴吧，并补充 TargetPool 中的吧组/标签信息。
        返回格式: [{'fname', 'account_count', 'account_names', 'post_group', 'is_target', 'is_post_target', 'success_count'}]
        """
        from sqlalchemy import func
        async with self.async_session() as session:
            # 1. 汇总所有账号关注的贴吧 (去重汇总)
            # 使用聚合函数获取详细数据
            stmt = (
                select(
                    Forum.fname,
                    func.count(Forum.account_id).label("account_count"),
                    func.group_concat(Account.name).label("account_names"),
                    func.max(Forum.is_post_target).label("is_post_target"),
                    func.max(Forum.is_banned).label("is_banned"),
                )
                .join(Account, Forum.account_id == Account.id)
                .group_by(Forum.fname)
            )
            
            result = await session.execute(stmt)
            forum_rows = result.all()
            
            # 2. 获取 TargetPool 中的扩展信息
            target_stmt = select(TargetPool)
            target_result = await session.execute(target_stmt)
            target_map = {t.fname: t for t in target_result.scalars().all()}
            
            # 2.5 获取每个贴吧的被删帖数量（帖子阵亡且非用户自删/探测异常）
            # 排除原因：deleted_by_user（用户自删）、captcha_required（验证码拦截）、error（探测异常）
            _excluded_reasons = ["deleted_by_user", "captcha_required", "error"]
            dead_stmt = (
                select(
                    MaterialPool.posted_fname,
                    func.count(MaterialPool.id).label("deleted_count"),
                )
                .where(
                    MaterialPool.survival_status == "dead",
                    ~MaterialPool.death_reason.in_(_excluded_reasons),
                    MaterialPool.posted_fname.isnot(None),
                    MaterialPool.posted_fname != "",
                )
                .group_by(MaterialPool.posted_fname)
            )
            dead_result = await session.execute(dead_stmt)
            deleted_count_map = {row.posted_fname: row.deleted_count for row in dead_result.all()}
            
            # 2.6 从 BatchPostLog 直接统计击穿数（与 TargetPool 锁定状态无关）
            success_stmt = (
                select(BatchPostLog.fname, func.count(BatchPostLog.id).label("cnt"))
                .where(BatchPostLog.status == "success")
                .group_by(BatchPostLog.fname)
            )
            success_result = await session.execute(success_stmt)
            success_count_map = {row.fname: row.cnt for row in success_result.all()}
            
            # 3. 合并数据
            stats_list = []
            for row in forum_rows:
                fname = row.fname
                target_info = target_map.get(fname)
                
                stats_list.append({
                    "fname": fname,
                    "account_count": row.account_count,
                    "account_names": row.account_names,
                    "post_group": target_info.post_group if target_info else "",
                    "is_target": target_info is not None,
                    "is_post_target": bool(row.is_post_target),
                    "is_banned": bool(row.is_banned),
                    "deleted_count": deleted_count_map.get(fname, 0),
                    "success_count": success_count_map.get(fname, 0),
                    "is_active": target_info.is_active if target_info else True
                })
            
            # 4. 补充在 TargetPool 中但目前没有任何号关注的吧 (空降预备役)
            followed_fnames = {row.fname for row in forum_rows}
            for fname, target in target_map.items():
                if fname not in followed_fnames:
                    stats_list.append({
                        "fname": fname,
                        "account_count": 0,
                        "account_names": "",
                        "post_group": target.post_group,
                        "is_target": True,
                        "is_post_target": False,
                        "is_banned": False,
                        "deleted_count": deleted_count_map.get(fname, 0),
                        "success_count": success_count_map.get(fname, 0),
                        "is_active": target.is_active
                    })
            
            # 按兵力部署多少排序
            return sorted(stats_list, key=lambda x: x["account_count"], reverse=True)
    async def get_banned_forums_detail(self) -> list[dict]:
        """
        获取所有被封禁的贴吧详情（含封禁原因和关联账号）。
        用于矩阵视图和封禁列表展示。
        Returns: [{'fname', 'account_id', 'account_name', 'ban_reason', 'is_banned'}]
        """
        async with self.async_session() as session:
            stmt = (
                select(Forum.fname, Forum.account_id, Account.name, Forum.ban_reason, Forum.is_banned)
                .join(Account, Forum.account_id == Account.id)
                .where(Forum.is_banned == True)
                .order_by(Forum.fname)
            )
            result = await session.execute(stmt)
            return [
                {
                    "fname": row.fname,
                    "account_id": row.account_id,
                    "account_name": row.name,
                    "ban_reason": row.ban_reason or "未记录",
                    "is_banned": row.is_banned,
                }
                for row in result.all()
            ]
    async def delete_target_pool_by_fnames(self, fnames: list[str]) -> int:
        """从全局靶场池批量移除指定贴吧"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            result = await session.execute(
                delete(TargetPool).where(TargetPool.fname.in_(fnames))
            )
            await session.commit()
            return result.rowcount or 0
    async def get_target_pool_groups(self) -> list[str]:
        """获取靶场池所有存在的分组名"""
        async with self.async_session() as session:
            result = await session.execute(select(TargetPool.post_group).where(TargetPool.post_group != ""))
            groups = set()
            for row in result.scalars().all():
                for tag in row.split(","):
                    groups.add(tag.strip())
            return sorted(list(groups))
    async def get_all_target_pools_raw(self) -> list[TargetPool]:
        async with self.async_session() as session:
            result = await session.execute(select(TargetPool))
            return result.scalars().all()
    async def get_target_pools_by_group(self, group: str, active_only: bool = True) -> list[str]:
        """根据分组名获取全局靶场的标的吧名"""
        async with self.async_session() as session:
            stmt = select(TargetPool.fname)
            if active_only:
                stmt = stmt.where(TargetPool.is_active == True)
            # 简化实现，若存在多个标签逗号分隔则需要模糊匹配
            stmt = stmt.where(TargetPool.post_group.like(f"%{group}%"))
            result = await session.execute(stmt)
            return result.scalars().all()
    async def update_target_pool_status(self, fname: str, is_success: bool, error_reason: str = "") -> None:
        """记录靶场投递结果：仅维护 fail_count 和熔断逻辑。
        
        设计原则：
        - success_count: 由 backfill_success_count() 从 BatchPostLog 实时统计，不在此手动递增
        - fail_count: 记录连续失败次数，成功时清零，≥3 次触发熔断 (is_active=False)
        - 懒初始化：fname 不在 target_pool 中时自动创建记录
        
        调用场景：
        - 发帖成功 → is_success=True, fail_count 归零
        - 发帖失败 → is_success=False, fail_count 递增
        - 发射检测到吧封 → is_success=False
        """
        async with self.async_session() as session:
            result = await session.execute(select(TargetPool).where(TargetPool.fname == fname))
            pool = result.scalar()
            if not pool:
                logger.info(f"[靶场] fname='{fname}' 不在 target_pool 中，自动创建")
                pool = TargetPool(fname=fname)
                session.add(pool)
                await session.flush()

            if is_success:
                pool.fail_count = 0  # 成功 → 清零连续失败
                # success_count 由 backfill_success_count() 从 BatchPostLog 统计，不在此递增
                logger.info(f"[靶场] {fname}: 发帖成功, fail_count 归零")
            else:
                pool.fail_count = (pool.fail_count or 0) + 1
                pool.last_fail_reason = error_reason
                logger.info(f"[靶场] {fname}: fail_count={pool.fail_count}, reason={error_reason}")
                if pool.fail_count >= 3:
                    pool.is_active = False

            pool.last_used_at = datetime.now()
            await session.commit()
    async def upsert_target_pools(self, fnames: list[str], group: str = "") -> int:
        """批量入库/覆盖靶场池（创建时自动填充历史击穿数）。

        唯一实现，勿重复定义（历史上曾有两份签名不同的实现相互覆盖）。
        """
        from sqlalchemy import func
        added_count = 0
        async with self.async_session() as session:
            for fname in set(fnames):
                if not fname.strip(): continue
                result = await session.execute(select(TargetPool).where(TargetPool.fname == fname.strip()))
                pool = result.scalar()
                current_group = (pool.post_group or "") if pool else ""
                if pool:
                    # 追加 group（post_group 可能为 NULL，先兜底为空串）
                    if group and group not in current_group.split(","):
                        pool.post_group = f"{current_group},{group}".strip(",")
                else:
                    # 查询历史击穿数
                    cnt_result = await session.execute(
                        select(func.count(BatchPostLog.id))
                        .where(BatchPostLog.fname == fname.strip(), BatchPostLog.status == "success")
                    )
                    success_count = cnt_result.scalar() or 0
                    session.add(TargetPool(fname=fname.strip(), post_group=group, success_count=success_count))
                    added_count += 1
            await session.commit()
        return added_count
    async def bulk_update_target_group(self, fnames: list[str], group: str) -> int:
        """批量更新贴吧的行业分类/吧组标签"""
        if not fnames: return 0
        async with self.async_session() as session:
            # 确保这些 fname 在 TargetPool 中存在，不存在则先插入
            existing_result = await session.execute(
                select(TargetPool.fname).where(TargetPool.fname.in_(fnames))
            )
            existing_fnames = set(existing_result.scalars().all())
            
            # 批量插入缺失的
            missing_fnames = set(fnames) - existing_fnames
            if missing_fnames:
                session.add_all([
                    TargetPool(fname=fname, post_group=group)
                    for fname in missing_fnames
                ])
            
            # 更新已存在的
            if existing_fnames:
                await session.execute(
                    update(TargetPool)
                    .where(TargetPool.fname.in_(list(existing_fnames)))
                    .values(post_group=group)
                )
            
            await session.commit()
            return len(fnames)
    async def toggle_forum_post_target(self, forum_id: int, is_post_target: bool) -> None:
        """切换贴吧的发帖许可状态（按 fname 级联，多号同开同关）"""
        async with self.async_session() as session:
            # 先通过 PK 找到记录，获取 fname，再按 fname 级联更新
            result = await session.execute(select(Forum).where(Forum.id == forum_id))
            forum = result.scalar_one_or_none()
            if not forum:
                return
            # 级联更新同一贴吧名的所有记录
            cascade_result = await session.execute(
                select(Forum).where(Forum.fname == forum.fname)
            )
            for f in cascade_result.scalars().all():
                f.is_post_target = is_post_target
            await session.commit()
    async def toggle_forum_post_target_by_fname(self, fname: str, is_post_target: bool) -> int:
        """按贴吧名切换所有账号的发帖许可状态，返回更新的记录数"""
        from sqlalchemy import update as sa_update
        async with self.async_session() as session:
            result = await session.execute(
                sa_update(Forum)
                .where(Forum.fname == fname)
                .values(is_post_target=is_post_target)
            )
            await session.commit()
            return result.rowcount or 0
