"""Database CRUD operations"""

import logging
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

from .models import (
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
)

T = TypeVar("T", bound=Base)

import sys

# 默认数据库路径：动态判断运行环境
_base_path = Path(__file__).parent.parent.parent

if getattr(sys, 'frozen', False):
    # PyInstaller 打包环境: 可执行文件外层
    _db_dir = Path(sys.executable).parent / "data"
elif _base_path.name == "src":
    # 源码开发环境: src 的上一级 (项目根目录)
    _db_dir = _base_path.parent / "data"
else:
    # 绿色便携版环境: 当前目录即为产品打包根目录 (TiebaMecha_Portable)
    _db_dir = _base_path / "data"

DEFAULT_DB_PATH = _db_dir / "tieba_mecha.db"

# ==================== 配置常量 ====================
PROXY_FAIL_THRESHOLD = 10  # 代理失败阈值,超过此值自动禁用


class Database:
    """异步数据库管理器"""

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def _get_existing_columns(self, conn, table_name: str) -> set[str]:
        """使用 PRAGMA table_info 检查表中已有的列名，避免盲目 ALTER TABLE"""
        result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
        return {row[1] for row in result}

    async def _safe_add_column(self, conn, table_name: str, column_name: str, column_def: str, existing: set[str] | None = None):
        """安全地添加列，仅在列不存在时执行 ALTER TABLE"""
        if existing is None:
            existing = await self._get_existing_columns(conn, table_name)
        if column_name not in existing:
            await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"))
            logger.debug(f"Added column '{column_name}' to {table_name}")

    async def init_db(self) -> None:
        """初始化数据库表并执行轻量级迁移"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # 预查询各表已有列，避免重复 ALTER TABLE 尝试
            accounts_cols = await self._get_existing_columns(conn, "accounts")
            forums_cols = await self._get_existing_columns(conn, "forums")
            material_cols = await self._get_existing_columns(conn, "material_pool")
            batch_cols = await self._get_existing_columns(conn, "batch_post_tasks")

            # Accounts 字段迁移
            accounts_migrations = [
                ("status", "VARCHAR(20) DEFAULT 'unknown'"),
                ("last_verified", "DATETIME"),
                ("cuid", "VARCHAR(100) DEFAULT ''"),
                ("user_agent", "VARCHAR(255) DEFAULT ''"),
                ("created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
                ("updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
                ("proxy_id", "INTEGER"),
                ("post_weight", "INTEGER DEFAULT 5"),
                ("suspended_reason", "VARCHAR(200) DEFAULT ''"),
                ("is_maint_enabled", "BOOLEAN DEFAULT 0"),
                ("last_maint_at", "DATETIME DEFAULT NULL"),
            ]
            for col_name, col_type in accounts_migrations:
                await self._safe_add_column(conn, "accounts", col_name, col_type, accounts_cols)

            # BatchPostTask 新字段迁移
            batch_migrations = [
                ("fnames_json", "TEXT DEFAULT '[]'"),
                ("strategy", "VARCHAR(20) DEFAULT 'round_robin'"),
                ("ai_persona", "VARCHAR(50) DEFAULT 'normal'"),
            ]
            for col_name, col_type in batch_migrations:
                await self._safe_add_column(conn, "batch_post_tasks", col_name, col_type, batch_cols)

            # MaterialPool 字段迁移
            material_migrations = [
                ("survival_status", "VARCHAR(20) DEFAULT 'unknown'"),
                ("death_reason", "VARCHAR(100) DEFAULT ''"),
                ("last_checked_at", "DATETIME DEFAULT NULL"),
                ("posted_fname", "VARCHAR(100) DEFAULT NULL"),
                ("posted_tid", "BIGINT DEFAULT NULL"),
                ("posted_account_id", "INTEGER DEFAULT NULL"),
                ("is_auto_bump", "BOOLEAN DEFAULT 0"),
                ("bump_count", "INTEGER DEFAULT 0"),
                ("last_bumped_at", "DATETIME DEFAULT NULL"),
                ("posted_time", "DATETIME DEFAULT NULL"),
                ("bump_mode", "VARCHAR(20) DEFAULT 'once'"),
                ("bump_hour", "INTEGER DEFAULT 10"),
                ("bump_duration_days", "INTEGER DEFAULT 0"),
                ("bump_start_date", "DATE DEFAULT NULL"),
                ("bump_account_ids", "TEXT DEFAULT NULL"),
                ("bump_account_index", "INTEGER DEFAULT 0"),
                ("bump_last_date", "DATE DEFAULT NULL"),
            ]
            for col_name, col_type in material_migrations:
                await self._safe_add_column(conn, "material_pool", col_name, col_type, material_cols)

            # Forums 字段迁移
            forums_migrations = [
                ("last_sign_status", "VARCHAR(20) DEFAULT 'pending'"),
                ("history_total", "INTEGER DEFAULT 0"),
                ("history_success", "INTEGER DEFAULT 0"),
                ("history_failed", "INTEGER DEFAULT 0"),
                ("level", "INTEGER DEFAULT 0"),
                ("is_post_target", "BOOLEAN DEFAULT 0"),
                ("is_hidden", "BOOLEAN DEFAULT 0"),
                ("is_banned", "BOOLEAN DEFAULT 0"),
                ("ban_reason", "VARCHAR(200) DEFAULT NULL"),
            ]
            for col_name, col_type in forums_migrations:
                await self._safe_add_column(conn, "forums", col_name, col_type, forums_cols)

        # 数据自愈：确保所有账号的 post_weight 都有默认值 5
        try:
            async with self.async_session() as session:
                await session.execute(text("UPDATE accounts SET post_weight = 5 WHERE post_weight IS NULL"))
        except Exception as e:
            logger.warning(f"Failed to heal post_weight data: {e}")


    async def close(self) -> None:
        """关闭数据库连接"""
        await self.engine.dispose()

    # ========== Account CRUD ==========

    async def add_account(
        self,
        name: str,
        bduss: str,
        stoken: str = "",
        user_id: int = 0,
        user_name: str = "",
        proxy_id: int | None = None,
        cuid: str = "",
        user_agent: str = "",
        post_weight: int = 5,
    ) -> Account:
        """添加账号，自动注入指纹"""
        import uuid
        import random
        
        # 默认 UA 库 (高仿真移动端)
        UA_POOL = [
            # Android 14 / Pixel 8
            "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/UD1A.230803.041) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
            # iOS 17.2 / iPhone 15
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
            # Android 13 / Samsung S23
            "Mozilla/5.0 (Linux; Android 13; SM-S918B Build/TP1A.220624.014) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.163 Mobile Safari/537.36",
            # HarmonyOS / Mate 60
            "Mozilla/5.0 (Linux; Android 12; ALN-AL00 Build/HUAWEIALN-AL00) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36",
            # iPad OS 17.1
            "Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
            # Xiaomi 14
            "Mozilla/5.0 (Linux; Android 14; 23127PN0CC Build/UKQ1.230804.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36",
        ]

        if not cuid:
            # 百度常用的 CUID 通常是一个类似 UUID 的大写字符串
            cuid = uuid.uuid4().hex.upper()
        if not user_agent:
            user_agent = random.choice(UA_POOL)

        async with self.async_session() as session:
            # 如果这是第一个账号，自动设为活跃
            existing = await session.execute(select(Account))
            is_first = existing.scalar() is None

            account = Account(
                name=name,
                bduss=bduss,
                stoken=stoken,
                user_id=user_id,
                user_name=user_name,
                is_active=is_first,
                proxy_id=proxy_id,
                cuid=cuid,
                user_agent=user_agent,
                post_weight=post_weight,
                status="active" if is_first else "pending", # 确保初次添加就有明确状态
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            return account

    async def get_accounts(self) -> list[Account]:
        """获取所有账号"""
        async with self.async_session() as session:
            result = await session.execute(select(Account).order_by(Account.id))
            return list(result.scalars().all())

    async def get_active_account(self) -> Account | None:
        """获取当前活跃账号 (带多峰收敛保护)"""
        async with self.async_session() as session:
            result = await session.execute(select(Account).where(Account.is_active == True))
            return result.scalars().first()

    async def set_active_account(self, account_id: int) -> None:
        """设置活跃账号 (事务内原子操作,避免竞态条件)
        
        Args:
            account_id: 要设为活跃的账号ID
            
        Note:
            使用数据库事务保证原子性,避免并发操作导致多个账号同时活跃
        """
        async with self.async_session() as session:
            async with session.begin():  # 显式事务
                # 先取消所有账号的活跃状态
                await session.execute(update(Account).values(is_active=False))
                # 设置指定账号为活跃
                await session.execute(
                    update(Account).where(Account.id == account_id).values(is_active=True)
                )
                # 事务自动提交,保证原子性

    async def delete_account(self, account_id: int) -> bool:
        """删除账号 (带级联删除：同时删除关联的贴吧，避免产生孤儿数据)"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                was_active = account.is_active
                
                # 1. 级联删除关联的贴吧
                await session.execute(delete(Forum).where(Forum.account_id == account_id))
                
                # 2. 删除账号本身
                await session.delete(account)
                await session.commit()
                if was_active:
                    # 尝试推举新活跃账号
                    remaining_acct = await session.execute(select(Account).order_by(Account.id))
                    first_acc = remaining_acct.scalars().first()
                    if first_acc:
                        await self.set_active_account(first_acc.id)
                return True
            return False

    async def update_account_status(self, account_id: int, status: str) -> None:
        """更新账号验证状态"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                account.status = status
                account.last_verified = datetime.now()
                await session.commit()

    # update_account 允许修改的字段白名单
    _ACCOUNT_UPDATABLE_FIELDS = frozenset({
        "name", "bduss", "stoken", "user_id", "user_name",
        "proxy_id", "cuid", "user_agent", "post_weight",
        "is_active", "status", "last_verified", "suspended_reason",
        "is_maint_enabled", "last_maint_at",
    })

    async def get_account_by_id(self, account_id: int) -> Account | None:
        """根据 ID 获取单个账号（直接查询，避免全表扫描）"""
        async with self.async_session() as session:
            return await session.get(Account, account_id)

    async def update_account(self, account_id: int, **kwargs) -> Account | None:
        """更新账号信息（仅允许白名单中的字段）"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                for key, value in kwargs.items():
                    if key in self._ACCOUNT_UPDATABLE_FIELDS:
                        setattr(account, key, value)
                await session.commit()
                await session.refresh(account)
                return account
            return None

    async def get_matrix_accounts(self) -> list[Account]:
        """获取矩阵可用账号：过滤掉挂起及封禁状态"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.status.notin_(["suspended", "suspended_proxy", "banned", "expired"])
                ).order_by(Account.post_weight.desc())
            )
            return list(result.scalars().all())

    async def get_accounts_by_proxy(self, proxy_id: int) -> list[Account]:
        """获取所有绑定了指定代理的账号"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(Account.proxy_id == proxy_id)
            )
            return list(result.scalars().all())

    async def get_maint_accounts(self) -> list[Account]:
        """获取需要执行 BioWarming 养号任务的账号"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(Account.is_maint_enabled == True)
            )
            return list(result.scalars().all())

    async def update_maint_status(self, account_id: int) -> None:
        """更新账号的最后养号时间"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                account.last_maint_at = datetime.now()
                await session.commit()

    async def suspend_accounts_for_proxy(self, proxy_id: int, reason: str = "代理失效自动隔离") -> list[Account]:
        """代理失效时批量挂起关联账号，返回被挂起的账号列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(Account.proxy_id == proxy_id)
            )
            accounts = list(result.scalars().all())
            suspended = []
            for acc in accounts:
                if acc.status != "suspended_proxy":
                    acc.status = "suspended_proxy"
                    acc.suspended_reason = reason
                    suspended.append(acc)
            await session.commit()
            return suspended

    async def restore_accounts_for_proxy(self, proxy_id: int) -> list[Account]:
        """代理恢复时，解挂所有因该代理而挂起的账号，返回被恢复的账号列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.proxy_id == proxy_id,
                    Account.status == "suspended_proxy"
                )
            )
            accounts = list(result.scalars().all())
            for acc in accounts:
                acc.status = "active"
                acc.suspended_reason = ""
            await session.commit()
            return accounts

    async def update_account_weight(self, account_id: int, weight: int) -> None:
        """更新单个账号的发帖权重"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                account.post_weight = max(1, min(10, weight))
                await session.commit()

    async def batch_update_weights(self, weight_updates: list[tuple[int, int]]) -> dict:
        """
        批量更新多个账号的权重。
        
        Args:
            weight_updates: [(account_id, weight), ...] 列表
            
        Returns:
            {"updated": count, "failed": count}
        """
        updated = 0
        failed = 0
        async with self.async_session() as session:
            for acc_id, weight in weight_updates:
                try:
                    account = await session.get(Account, acc_id)
                    if account:
                        account.post_weight = max(1, min(10, weight))
                        updated += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
            await session.commit()
        return {"updated": updated, "failed": failed}

    async def get_accounts_with_forums(self) -> list[tuple[Account, list[Forum]]]:
        """获取所有账号及其关联的贴吧列表（批量查询避免 N+1 问题）"""
        async with self.async_session() as session:
            # 一次性获取所有账号
            acc_stmt = select(Account).order_by(Account.id)
            acc_result = await session.execute(acc_stmt)
            accounts = list(acc_result.scalars().all())

            # 一次性获取所有贴吧，按 account_id 分组
            forum_stmt = select(Forum).order_by(Forum.account_id)
            forum_result = await session.execute(forum_stmt)
            all_forums = list(forum_result.scalars().all())

            # 构建 account_id → forums 映射
            forums_by_account: dict[int, list[Forum]] = {}
            for f in all_forums:
                forums_by_account.setdefault(f.account_id, []).append(f)

            return [(acc, forums_by_account.get(acc.id, [])) for acc in accounts]

    async def get_accounts_not_following_forum(self, fname: str) -> list[Account]:
        """
        获取未关注指定贴吧的账号列表（排除已封禁的）
        用于补齐关注功能
        """
        async with self.async_session() as session:
            from sqlalchemy import select, not_
            # 获取已关注的账号ID
            followed_stmt = select(Forum.account_id).where(
                Forum.fname == fname,
                Forum.is_banned == False
            )
            followed_result = await session.execute(followed_stmt)
            followed_ids = {row[0] for row in followed_result}
            
            # 获取所有活跃账号（排除已关注的）
            all_accounts_stmt = select(Account).where(
                Account.status.notin_(["suspended", "suspended_proxy", "banned", "expired"])
            )
            all_result = await session.execute(all_accounts_stmt)
            all_accounts = list(all_result.scalars().all())
            
            # 过滤出未关注的
            missing_accounts = [acc for acc in all_accounts if acc.id not in followed_ids]
            return missing_accounts

    # ========== Forum CRUD ==========

    async def add_forum(
        self,
        fid: int,
        fname: str,
        account_id: int,
        sign_count: int = 0,
        level: int = 0,
    ) -> Forum:
        """添加关注贴吧 (带查重，同时刷新贴吧等级)"""
        async with self.async_session() as session:
            # 查重
            existing = await session.execute(
                select(Forum).where(Forum.fid == fid, Forum.account_id == account_id)
            )
            forum = existing.scalar_one_or_none()
            if forum:
                # 动态刷新等级 (仅刷新经验不破坏签到统计数据)
                forum.level = level
                await session.commit()
                return forum

            forum = Forum(fid=fid, fname=fname, account_id=account_id, sign_count=sign_count, level=level)
            session.add(forum)
            await session.commit()
            await session.refresh(forum)
            return forum

    async def get_forums(self, account_id: int | None = None) -> list[Forum]:
        """获取贴吧列表"""
        async with self.async_session() as session:
            if account_id:
                result = await session.execute(
                    select(Forum).where(Forum.account_id == account_id).order_by(Forum.fname)
                )
            else:
                result = await session.execute(select(Forum).order_by(Forum.fname))
            return list(result.scalars().all())

    async def get_all_unique_fnames(self) -> list[str]:
        """获取所有账号关注过的唯一贴吧名称列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum.fname).distinct().order_by(Forum.fname)
            )
            return list(result.scalars().all())

    async def update_forum_sign(self, forum_id: int, success: bool) -> None:
        """
        更新签到状态（优化版：按天去重计数，保证 Total = Success + Failure）
        """
        async with self.async_session() as session:
            forum = await session.get(Forum, forum_id)
            if forum:
                if success:
                    # 如果今天还没成功过
                    if not forum.is_sign_today:
                        # 如果今日之前已经有过失败记录，则需要“冲抵”
                        if forum.last_sign_status == "failure":
                            # 失败数减1，因为这一天变成了成功日
                            forum.history_failed = max(0, forum.history_failed - 1)
                        
                        # 增加成功计数
                        forum.sign_count += 1
                        forum.history_success += 1
                        forum.is_sign_today = True
                        forum.last_sign_status = "success"
                else:
                    # 只有在今日既没成功也没记录过失败时，才增加失败计数
                    #（防止重复执行失败动作导致总数和失败数狂飙）
                    if not forum.is_sign_today and forum.last_sign_status != "failure":
                        forum.history_failed += 1
                        forum.last_sign_status = "failure"
                
                # 强制平衡：总数始终等于 成功 + 失败 (按天计费)
                forum.history_total = forum.history_success + forum.history_failed
                forum.last_sign_date = datetime.now()
                await session.commit()

    async def recalculate_all_forum_stats(self) -> dict:
        """
        全量回溯：基于 sign_logs 重构所有贴吧的 history_total/success/failed
        统计规则：对于每一天，如果有过成功记录则计为1次成功；否则若有失败记录计为1次失败。
        """
        from sqlalchemy import func
        
        async with self.async_session() as session:
            # 1. 获取所有存在记录的贴吧
            result = await session.execute(select(Forum.id))
            forum_ids = result.scalars().all()
            
            stats_updated = 0
            for fid in forum_ids:
                # SQLite 专用的日期转换统计：聚合并得出每日的最佳结果
                # MAX(success) 能确保如果这一天中有成功记录，则结果为 1
                stmt = select(
                    func.date(SignLog.signed_at).label("d"),
                    func.max(SignLog.success).label("s")
                ).where(SignLog.forum_id == fid).group_by(func.date(SignLog.signed_at))
                
                day_results = await session.execute(stmt)
                rows = day_results.all()
                
                success_days = sum(1 for r in rows if r.s)
                failed_days = sum(1 for r in rows if not r.s)
                
                forum = await session.get(Forum, fid)
                if forum:
                    forum.history_success = success_days
                    forum.history_failed = failed_days
                    forum.history_total = success_days + failed_days
                    stats_updated += 1
            
            await session.commit()
            return {"updated_count": stats_updated}


    async def reset_daily_sign(self) -> None:
        """重置每日签到状态(批量更新,避免N+1问题)"""
        async with self.async_session() as session:
            # 使用批量更新语句,一次性更新所有记录
            await session.execute(
                update(Forum).values(is_sign_today=False, last_sign_status="pending")
            )
            await session.commit()

    async def check_and_reset_daily_sign(self) -> None:
        """智能检测并重置跨天的签到状态（包含断签检测）"""
        from datetime import timedelta
        
        async with self.async_session() as session:
            now = datetime.now()
            today = now.date()
            yesterday = today - timedelta(days=1)
            
            result = await session.execute(select(Forum))
            forums = result.scalars().all()
            
            has_changes = False
            for forum in forums:
                # 只处理有签到记录或者被标记为已签到的数据
                if forum.last_sign_date:
                    last_date = forum.last_sign_date.date()
                    
                    # 1. 如果今天还没过完，没跨天，不需要重置签到状态
                    # 但是如果发现状态异常（比如之前某种错误导致没有重置），则以 last_date 为准
                    if last_date < today and forum.is_sign_today:
                        forum.is_sign_today = False
                        forum.last_sign_status = "pending"
                        has_changes = True
                    
                    # 2. 断签检测：如果距离最后一次签到的日期已经早于“昨天”，说明断签了，清零连续天数
                    if last_date < yesterday and forum.sign_count > 0:
                        forum.sign_count = 0
                        has_changes = True

            if has_changes:
                await session.commit()


    async def delete_forum(self, forum_id: int) -> bool:
        """删除贴吧"""
        async with self.async_session() as session:
            forum = await session.get(Forum, forum_id)
            if forum:
                await session.delete(forum)
                await session.commit()
                return True
            return False

    async def delete_forums_by_fids(self, account_id: int, fids: list[int]) -> None:
        """根据 FID 列表批量删除指定账号的贴吧"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            await session.execute(
                delete(Forum).where(Forum.account_id == account_id, Forum.fid.in_(fids))
            )
            await session.commit()

    async def mark_forum_banned(self, account_id: int, fname: str, reason: str) -> None:
        """标记账号在该贴吧被封禁"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum).where(Forum.account_id == account_id, Forum.fname == fname)
            )
            forum = result.scalar_one_or_none()
            if forum:
                forum.is_banned = True
                forum.ban_reason = reason
                # 被封后自动关闭发帖许可，防止调度器再次选中
                forum.is_post_target = False
                await session.commit()

    async def delete_forum_by_name(self, account_id: int, fname: str) -> bool:
        """根据贴吧名删除指定账号的贴吧记录"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            await session.execute(
                delete(Forum).where(Forum.account_id == account_id, Forum.fname == fname)
            )
            await session.commit()
            return True

    async def get_account_ids_following_forums(self, fnames: list[str]) -> list[int]:
        """获取关注了指定贴吧列表的所有账号 ID"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum.account_id).where(Forum.fname.in_(fnames))
            )
            return sorted(list(set(result.scalars().all())))

    async def delete_forum_memberships_globally(self, fnames: list[str]) -> int:
        """从所有账号的关注列表中全局移除指定贴吧"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            result = await session.execute(
                delete(Forum).where(Forum.fname.in_(fnames))
            )
            await session.commit()
            return result.rowcount or 0

    # ========== SignLog CRUD ==========

    async def add_sign_log(
        self,
        forum_id: int,
        fname: str,
        success: bool,
        message: str = "",
    ) -> SignLog:
        """添加签到日志"""
        async with self.async_session() as session:
            log = SignLog(forum_id=forum_id, fname=fname, success=success, message=message)
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    async def get_sign_logs(self, limit: int = 100, forum_id: int | None = None) -> list[SignLog]:
        """获取签到日志"""
        async with self.async_session() as session:
            stmt = select(SignLog)
            if forum_id is not None:
                stmt = stmt.where(SignLog.forum_id == forum_id)
            
            result = await session.execute(
                stmt.order_by(SignLog.signed_at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    # ========== CrawlTask CRUD ==========

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

    # ========== PostCache CRUD ==========

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

    # ========== Settings CRUD ==========

    async def get_setting(self, key: str, default: str = "") -> str:
        """获取设置"""
        async with self.async_session() as session:
            setting = await session.get(Setting, key)
            return setting.value if setting else default

    async def set_setting(self, key: str, value: str) -> None:
        """保存设置"""
        async with self.async_session() as session:
            setting = await session.get(Setting, key)
            if setting:
                setting.value = value
            else:
                setting = Setting(key=key, value=value)
                session.add(setting)
            await session.commit()

    # ========== Proxy CRUD ==========

    async def add_proxy(
        self,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        protocol: str = "http",
    ) -> Proxy:
        """添加代理（自动加密凭证）"""
        from ..core.account import encrypt_value
        
        # 对非空密码执行保护倒灌
        enc_username = encrypt_value(username) if username else ""
        enc_password = encrypt_value(password) if password else ""
        
        async with self.async_session() as session:
            proxy = Proxy(
                host=host, port=port, username=enc_username, password=enc_password, protocol=protocol
            )
            session.add(proxy)
            await session.commit()
            await session.refresh(proxy)
            return proxy

    async def get_proxy(self, proxy_id: int) -> Proxy | None:
        """根据 ID 获取代理"""
        async with self.async_session() as session:
            return await session.get(Proxy, proxy_id)

    async def get_active_proxies(self) -> list[Proxy]:
        """获取所有可用代理"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Proxy).where(Proxy.is_active == True).order_by(Proxy.fail_count)
            )
            return list(result.scalars().all())

    async def mark_proxy_fail(self, proxy_id: int) -> None:
        """根据 ID 标记代理失败"""
        async with self.async_session() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy:
                proxy.fail_count += 1
                if proxy.fail_count >= PROXY_FAIL_THRESHOLD:  # 使用常量
                    proxy.is_active = False
                await session.commit()

    async def mark_proxy_fail_by_url(self, url: str) -> None:
        """根据 URL 标记代理失败"""
        import re
        match = re.search(r'//([^:/]+):(\d+)', url)
        if not match: return
        
        host, port = match.groups()
        async with self.async_session() as session:
            result = await session.execute(
                select(Proxy).where(Proxy.host == host, Proxy.port == int(port))
            )
            proxy = result.scalar_one_or_none()
            if proxy:
                proxy.fail_count += 1
                if proxy.fail_count >= PROXY_FAIL_THRESHOLD:  # 使用常量
                    proxy.is_active = False
                await session.commit()

    async def delete_proxy(self, proxy_id: int) -> bool:
        """删除代理"""
        async with self.async_session() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy:
                await session.delete(proxy)
                await session.commit()
                return True
            return False

    async def update_proxy(self, proxy_id: int, **kwargs) -> Proxy | None:
        """更新代理信息"""
        from ..core.account import encrypt_value
        async with self.async_session() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy:
                for key, value in kwargs.items():
                    if hasattr(proxy, key):
                        # 对于认证信息执行加密
                        if key in ("username", "password") and value:
                            value = encrypt_value(value)
                        setattr(proxy, key, value)
                await session.commit()
                await session.refresh(proxy)
                return proxy
            return None

    # ========== AutoRule CRUD ==========

    async def add_auto_rule(
        self,
        fname: str,
        rule_type: str,
        pattern: str,
        action: str = "delete",
    ) -> AutoRule:
        """添加自动化规则"""
        async with self.async_session() as session:
            rule = AutoRule(fname=fname, rule_type=rule_type, pattern=pattern, action=action)
            session.add(rule)
            await session.commit()
            await session.refresh(rule)
            return rule

    async def get_auto_rules(self, fname: str | None = None) -> list[AutoRule]:
        """获取规则列表"""
        async with self.async_session() as session:
            if fname:
                result = await session.execute(
                    select(AutoRule).where(AutoRule.fname == fname).order_by(AutoRule.id)
                )
            else:
                result = await session.execute(select(AutoRule).order_by(AutoRule.id))
            return list(result.scalars().all())

    async def toggle_rule(self, rule_id: int, is_active: bool) -> None:
        """开启/关闭规则"""
        async with self.async_session() as session:
            rule = await session.get(AutoRule, rule_id)
            if rule:
                rule.is_active = is_active
                await session.commit()

    async def delete_auto_rule(self, rule_id: int) -> bool:
        """删除规则"""
        async with self.async_session() as session:
            rule = await session.get(AutoRule, rule_id)
            if rule:
                await session.delete(rule)
                await session.commit()
                return True
            return False


    # ========== BatchPostTask CRUD ==========
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


    async def get_all_unique_fnames(self) -> list[str]:
        """获取所有贴吧唯一名称"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum.fname).distinct().order_by(Forum.fname)
            )
            return list(result.scalars().all())

    async def get_all_unique_forums(self) -> list[dict]:
        """获取所有不重复的贴吧基本信息和权限状态"""
        from sqlalchemy import func
        async with self.async_session() as session:
            result = await session.execute(
                select(Forum.fid, Forum.fname, func.max(Forum.is_post_target), func.max(Forum.is_banned))
                .group_by(Forum.fname)
                .order_by(Forum.fname)
            )
            return [
                {
                    "fid": row.fid, 
                    "fname": row.fname, 
                    "is_post_target": bool(row[2]),
                    "is_banned": bool(row[3])
                } for row in result.all()
            ]


    # ========== MaterialPool CRUD ==========
    async def add_materials_bulk(self, pairs: list[tuple[str, str]]) -> int:
        """批量添加物料，返回添加成功的条数，执行基于内容的去重逻辑"""
        added = 0
        async with self.async_session() as session:
            result = await session.execute(select(MaterialPool.content))
            existing_contents = set(result.scalars().all())

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
            base_where = []
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
                .order_by(MaterialPool.id.desc())
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
    ) -> tuple[list[MaterialPool], int]:
        """按状态列表分页查询物料，支持标题/内容模糊搜索，返回 (列表, 总数)"""
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
            # 总数
            count_stmt = sa_select(func.count(MaterialPool.id)).where(*base_where)
            total = (await session.execute(count_stmt)).scalar() or 0
            # 分页数据
            offset = (page - 1) * page_size
            data_stmt = (
                select(MaterialPool)
                .where(*base_where)
                .order_by(MaterialPool.id.desc())
                .offset(offset)
                .limit(page_size)
            )
            result = await session.execute(data_stmt)
            return list(result.scalars().all()), total

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
                if status in counts:
                    counts[status] = count
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
        posted_time: datetime | None = None
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
                await session.commit()

    async def update_material_survival_status(self, material_id: int, status: str, death_reason: str = "") -> None:
        """更新物料的存活探测状态，并联动更新 Forum 封禁标记。
        
        联动规则：
        - 帖子阵亡 (dead) + 被删原因 (banned_by_mod/deleted_by_system/deleted_by_mod)
          → 标记该账号在该贴吧 Forum.is_banned=True, is_post_target=False
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
                        import random
                        session.add(Forum(
                            fid=random.randint(1, 2**31 - 1),
                            fname=m.posted_fname,
                            account_id=m.posted_account_id,
                            is_banned=True,
                            ban_reason=ban_reason,
                            is_post_target=False,
                        ))

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

    # ========== Forum Targets & Global Target Pool ==========

    async def auto_sync_post_target(self) -> int:
        """根据 is_banned 和 deleted_count 自动同步 is_post_target 字段。
        
        自动判定规则：
        - 安全 (is_post_target=True): 未被封禁 且 无被吧务删帖记录
        - 不安全 (is_post_target=False): 被封禁 或 有被吧务删帖记录
        
        Returns: 更新的记录数
        """
        from sqlalchemy import func, update as sa_update
        async with self.async_session() as session:
            # 获取有被删帖记录的贴吧名集合（帖子阵亡且非用户自删/探测异常）
            _excluded_reasons = ["deleted_by_user", "captcha_required", "error"]
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
            
            # 3. 未被封禁且无删帖记录的贴吧 → is_post_target=True
            safe_stmt = sa_update(Forum).where(
                Forum.is_banned == False,
                Forum.is_post_target == False,
            )
            if deleted_fnames:
                safe_stmt = safe_stmt.where(Forum.fname.notin_(deleted_fnames))
            safe_result = await session.execute(safe_stmt.values(is_post_target=True))
            updated += safe_result.rowcount
            
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

    async def upsert_target_pools(self, fnames: list[str], post_group: str = "") -> int:
        """将若干吧名标记为火力目标（不存在则创建并填充历史击穿数，已存在则追加分组）。"""
        from sqlalchemy import func
        added = 0
        async with self.async_session() as session:
            for fname in fnames:
                existing = await session.execute(
                    select(TargetPool).where(TargetPool.fname == fname)
                )
                pool = existing.scalar_one_or_none()
                if pool is None:
                    # 查询历史击穿数
                    cnt_result = await session.execute(
                        select(func.count(BatchPostLog.id))
                        .where(BatchPostLog.fname == fname, BatchPostLog.status == "success")
                    )
                    success_count = cnt_result.scalar() or 0
                    session.add(TargetPool(fname=fname, post_group=post_group, success_count=success_count))
                    added += 1
                else:
                    # 已存在则追加分组
                    if post_group and post_group not in (pool.post_group or "").split(","):
                        pool.post_group = f"{pool.post_group or ''},{post_group}".strip(",")
            await session.commit()
        return added

    async def delete_target_pool_by_fnames(self, fnames: list[str]) -> int:
        """从 TargetPool 中删除指定的贴吧列表。返回删除的数量。"""
        from sqlalchemy import delete as sql_delete
        deleted = 0
        async with self.async_session() as session:
            for fname in fnames:
                result = await session.execute(
                    sql_delete(TargetPool).where(TargetPool.fname == fname)
                )
                deleted += result.rowcount
            await session.commit()
        return deleted

    async def bulk_update_target_group(self, fnames: list[str], post_group: str) -> None:
        """批量更新 TargetPool 中指定贴吧的分组/标签。若该贴吧不在池中则先插入。"""
        async with self.async_session() as session:
            for fname in fnames:
                existing = await session.execute(
                    select(TargetPool).where(TargetPool.fname == fname)
                )
                pool = existing.scalar_one_or_none()
                if pool:
                    pool.post_group = post_group
                else:
                    session.add(TargetPool(fname=fname, post_group=post_group))
            await session.commit()

    async def get_target_pool_groups(self) -> list[str]:
        """获取靶场池所有存在的分组名"""
        async with self.async_session() as session:
            result = await session.execute(select(TargetPool.post_group).where(TargetPool.post_group != ""))
            groups = set()
            for row in result.scalars().all():
                for tag in row.split(","):
                    groups.add(tag.strip())
            return sorted(list(groups))

    # ==================== 验证码事件管理 ====================

    async def save_captcha_event(
        self,
        account_id: int | None = None,
        task_id: int | None = None,
        event_type: str = "captcha",
        reason: str = "",
    ) -> int:
        """保存验证码事件"""
        from .models import CaptchaEvent
        async with self.async_session() as session:
            event = CaptchaEvent(
                account_id=account_id,
                task_id=task_id,
                event_type=event_type,
                reason=reason,
                status="pending",
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event.id

    async def get_captcha_events(
        self,
        account_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """获取验证码事件列表"""
        from .models import CaptchaEvent
        async with self.async_session() as session:
            query = select(CaptchaEvent).order_by(CaptchaEvent.created_at.desc())
            if account_id is not None:
                query = query.where(CaptchaEvent.account_id == account_id)
            if status is not None:
                query = query.where(CaptchaEvent.status == status)
            query = query.limit(limit)
            result = await session.execute(query)
            events = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "account_id": e.account_id,
                    "task_id": e.task_id,
                    "event_type": e.event_type,
                    "reason": e.reason,
                    "status": e.status,
                    "created_at": e.created_at,
                    "resolved_at": e.resolved_at,
                    "resolved_by": e.resolved_by,
                    "notes": e.notes,
                }
                for e in events
            ]

    async def resolve_captcha_event(
        self,
        event_id: int,
        resolved_by: str = "manual",
        notes: str = "",
    ) -> bool:
        """解决验证码事件"""
        from .models import CaptchaEvent
        async with self.async_session() as session:
            event = await session.get(CaptchaEvent, event_id)
            if event:
                event.status = "resolved"
                event.resolved_at = datetime.now()
                event.resolved_by = resolved_by
                event.notes = notes
                await session.commit()
                return True
            return False

    async def get_pending_captcha_count(self) -> int:
        """获取待处理的验证码事件数量"""
        from .models import CaptchaEvent
        async with self.async_session() as session:
            result = await session.execute(
                select(CaptchaEvent).where(CaptchaEvent.status == "pending")
            )
            return len(result.scalars().all())

    async def clear_resolved_captcha_events(self) -> int:
        """清除已解决的验证码事件记录"""
        from .models import CaptchaEvent
        from sqlalchemy import delete as sql_delete
        async with self.async_session() as session:
            result = await session.execute(
                sql_delete(CaptchaEvent).where(CaptchaEvent.status == "resolved")
            )
            await session.commit()
            return result.rowcount

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
                pool.success_count = (pool.success_count or 0) + 1  # 实时累加命中数
                logger.info(f"[靶场] {fname}: 发帖成功, fail_count 归零, 击穿数={pool.success_count}")
            else:
                pool.fail_count = (pool.fail_count or 0) + 1
                pool.last_fail_reason = error_reason
                logger.info(f"[靶场] {fname}: fail_count={pool.fail_count}, reason={error_reason}")
                if pool.fail_count >= 3:
                    pool.is_active = False

            pool.last_used_at = datetime.now()
            await session.commit()

    async def upsert_target_pools(self, fnames: list[str], group: str) -> int:
        """批量入库/覆盖靶场池（创建时自动填充历史击穿数）"""
        from sqlalchemy import func
        added_count = 0
        async with self.async_session() as session:
            for fname in set(fnames):
                if not fname.strip(): continue
                result = await session.execute(select(TargetPool).where(TargetPool.fname == fname.strip()))
                pool = result.scalar()
                if pool:
                    # 追加 group
                    if group and group not in pool.post_group.split(","):
                        pool.post_group = f"{pool.post_group},{group}".strip(",")
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

    async def delete_target_pool_by_fnames(self, fnames: list[str]) -> int:
        """从全局靶场池批量移除指定贴吧"""
        from sqlalchemy import delete
        async with self.async_session() as session:
            result = await session.execute(
                delete(TargetPool).where(TargetPool.fname.in_(fnames))
            )
            await session.commit()
            return result.rowcount or 0

    async def bulk_update_target_group(self, fnames: list[str], group: str) -> int:
        """批量更新贴吧的行业分类/吧组标签"""
        if not fnames: return 0
        async with self.async_session() as session:
            # 确保这些 fname 在 TargetPool 中存在，不存在则先插入
            existing_result = await session.execute(
                select(TargetPool.fname).where(TargetPool.fname.in_(fnames))
            )
            existing_fnames = set(existing_result.scalars().all())
            
            # 插入缺失的
            missing_fnames = set(fnames) - existing_fnames
            for fname in missing_fnames:
                session.add(TargetPool(fname=fname, post_group=group))
            
            # 更新已存在的
            if existing_fnames:
                await session.execute(
                    update(TargetPool)
                    .where(TargetPool.fname.in_(list(existing_fnames)))
                    .values(post_group=group)
                )
            
            await session.commit()
            return len(fnames)

    async def toggle_forum_post_target(self, fid: int, is_post_target: bool) -> None:
        """切换本号某个特定贴吧的发帖许可状态"""
        async with self.async_session() as session:
            result = await session.execute(select(Forum).where(Forum.fid == fid))
            # 级联更新该 fid 绑定的所有记录（如果多号关注同一个吧，同开同关）
            for f in result.scalars().all():
                f.is_post_target = is_post_target
            await session.commit()

    # ========== Notification CRUD ==========

    async def add_notification(
        self,
        type: str,
        title: str,
        message: str,
        action_url: str | None = None,
        extra: dict | None = None,
        source: str = "local",
        remote_id: str | None = None,
    ) -> Notification:
        """添加通知"""
        import json
        async with self.async_session() as session:
            notification = Notification(
                type=type,
                title=title,
                message=message,
                action_url=action_url,
                extra_json=json.dumps(extra or {}),
                source=source,
                remote_id=remote_id,
            )
            session.add(notification)
            await session.commit()
            await session.refresh(notification)
            return notification

    async def get_unread_notifications(self, limit: int = 50) -> list[Notification]:
        """获取未读通知列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Notification)
                .where(Notification.is_read == False)
                .order_by(Notification.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_all_notifications(self, limit: int = 100) -> list[Notification]:
        """获取所有通知列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Notification)
                .order_by(Notification.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def mark_notification_read(self, notification_id: int) -> bool:
        """标记通知为已读"""
        async with self.async_session() as session:
            notification = await session.get(Notification, notification_id)
            if notification:
                notification.is_read = True
                await session.commit()
                return True
            return False

    async def mark_all_notifications_read(self) -> int:
        """标记所有通知为已读，返回更新的数量"""
        async with self.async_session() as session:
            result = await session.execute(
                update(Notification).where(Notification.is_read == False).values(is_read=True)
            )
            await session.commit()
            return result.rowcount

    async def delete_notification(self, notification_id: int) -> bool:
        """删除通知"""
        async with self.async_session() as session:
            notification = await session.get(Notification, notification_id)
            if notification:
                await session.delete(notification)
                await session.commit()
                return True
            return False

    async def clear_old_notifications(self, days: int = 30) -> int:
        """清除指定天数前的已读通知"""
        from datetime import timedelta
        async with self.async_session() as session:
            cutoff = datetime.now() - timedelta(days=days)
            from sqlalchemy import delete
            result = await session.execute(
                delete(Notification).where(
                    Notification.is_read == True,
                    Notification.created_at < cutoff
                )
            )
            await session.commit()
            return result.rowcount

    async def get_unread_count(self) -> int:
        """获取未读通知数量"""
        async with self.async_session() as session:
            from sqlalchemy import func
            result = await session.execute(
                select(func.count(Notification.id)).where(Notification.is_read == False)
            )
            return result.scalar() or 0

    async def notification_exists(self, remote_id: str) -> bool:
        """检查远程通知是否已存在（避免重复入库）"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Notification.id).where(Notification.remote_id == remote_id).limit(1)
            )
            return result.scalar() is not None

    # ==================== ThreadRecord CRUD ====================

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

    # ==================== BatchPostLog CRUD ====================

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

# 全局数据库实例
_db: Database | None = None


async def get_db() -> Database:
    """获取数据库实例"""
    global _db
    if _db is None:
        _db = Database()
        await _db.init_db()
    return _db
