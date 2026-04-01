"""Database CRUD operations"""

from pathlib import Path
from typing import TypeVar

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import (
    Account,
    AutoRule,
    Base,
    BatchPostTask,
    CrawlTask,
    Forum,
    MaterialPool,
    PostCache,
    Proxy,
    Setting,
    SignLog,
    TargetPool,
)

T = TypeVar("T", bound=Base)

# 默认数据库路径
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent.parent.parent / "data" / "tieba_mecha.db"

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

    async def init_db(self) -> None:
        """初始化数据库表并执行轻量级迁移"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
        # 简单模式：尝试添加缺失的列 (SQLite 不支持一次添加多个)
        from sqlalchemy import text
        columns = [
            ("status", "VARCHAR(20) DEFAULT 'unknown'"), 
            ("last_verified", "DATETIME"),
            ("cuid", "VARCHAR(100) DEFAULT ''"),
            ("user_agent", "VARCHAR(255) DEFAULT ''"),
            ("created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
            ("updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
            ("proxy_id", "INTEGER"),
            ("post_weight", "INTEGER DEFAULT 5"),
            ("suspended_reason", "VARCHAR(200) DEFAULT ''"),
        ]
        for column, col_type in columns:
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {column} {col_type}"))
            except Exception:
                pass

        # BatchPostTask 新字段迎移
        batch_columns = [
            ("batch_post_tasks", "fnames_json", "TEXT DEFAULT '[]'"),
            ("batch_post_tasks", "strategy", "VARCHAR(20) DEFAULT 'round_robin'"),
        ]
        for table, column, col_type in batch_columns:
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            except Exception:
                pass  # 忽略错误，通常是因为列已存在

        # 贴吧字段迁移
        for new_col in [
            "last_sign_status VARCHAR(20) DEFAULT 'pending'",
            "history_total INTEGER DEFAULT 0",
            "history_success INTEGER DEFAULT 0",
            "history_failed INTEGER DEFAULT 0",
            "level INTEGER DEFAULT 0"
        ]:
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(text(f"ALTER TABLE forums ADD COLUMN {new_col}"))
            except Exception:
                pass

        # MaterialPool 新字段迁移
        for col_sql in [
            "ALTER TABLE material_pool ADD COLUMN posted_fname VARCHAR(100) DEFAULT NULL",
            "ALTER TABLE material_pool ADD COLUMN posted_tid INTEGER DEFAULT NULL",
            "ALTER TABLE material_pool ADD COLUMN is_auto_bump BOOLEAN DEFAULT 0",
            "ALTER TABLE material_pool ADD COLUMN bump_count INTEGER DEFAULT 0",
            "ALTER TABLE material_pool ADD COLUMN last_bumped_at DATETIME DEFAULT NULL",
        ]:
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(text(col_sql))
            except Exception:
                pass

        # Forums 表新字段迁移 (作为本号发帖目标)
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text("ALTER TABLE forums ADD COLUMN is_post_target BOOLEAN DEFAULT 0"))
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                print(f"[DB MIGRATION WARNING] Failed to add is_post_target to forums: {e}")


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
    ) -> Account:
        """添加账号，自动注入指纹"""
        import uuid
        import random
        
        # 默认 UA 库 (高仿真移动端)
        UA_POOL = [
            "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/UD1A.230803.041) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 13; SM-S918B Build/TP1A.220624.014) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.163 Mobile Safari/537.36",
            "Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
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
                account.last_verified = __import__("datetime").datetime.now()
                await session.commit()

    async def update_account(self, account_id: int, **kwargs) -> Account | None:
        """更新账号信息"""
        async with self.async_session() as session:
            account = await session.get(Account, account_id)
            if account:
                for key, value in kwargs.items():
                    if hasattr(account, key):
                        setattr(account, key, value)
                await session.commit()
                await session.refresh(account)
                return account
            return None

    async def get_matrix_accounts(self) -> list[Account]:
        """获取矩阵可用账号：is_active=True 且 status != 'suspended_proxy'"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.is_active == True,
                    Account.status != "suspended_proxy"
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
        """更新签到状态"""
        async with self.async_session() as session:
            forum = await session.get(Forum, forum_id)
            if forum:
                forum.is_sign_today = success
                forum.last_sign_status = "success" if success else "failure"
                if success:
                    forum.sign_count += 1
                    forum.history_success += 1
                else:
                    forum.history_failed += 1
                forum.history_total += 1
                forum.last_sign_date = __import__("datetime").datetime.now()
                await session.commit()

    async def reset_daily_sign(self) -> None:
        """重置每日签到状态(批量更新,避免N+1问题)"""
        async with self.async_session() as session:
            # 使用批量更新语句,一次性更新所有记录
            await session.execute(
                update(Forum).values(is_sign_today=False, last_sign_status="pending")
            )
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
                    task.completed_at = __import__("datetime").datetime.now()
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
            await session.execute(__import__("sqlalchemy").delete(PostCache))
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
        from datetime import datetime
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
                    task.completed_at = __import__("datetime").datetime.now()
                await session.commit()

    async def get_all_batch_tasks(self, limit: int = 50) -> list[BatchPostTask]:
        """获取所有批量任务列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(BatchPostTask).order_by(BatchPostTask.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    async def get_matrix_accounts(self) -> list[Account]:
        """获取矩阵发帖可用账号列表"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Account).order_by(Account.post_weight.desc())
            )
            return list(result.scalars().all())

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
                select(Forum.fid, Forum.fname, func.max(Forum.is_post_target))
                .group_by(Forum.fname)
                .order_by(Forum.fname)
            )
            return [{"fid": row.fid, "fname": row.fname, "is_post_target": bool(row[2])} for row in result.all()]


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

    async def get_materials(self, status: str | None = None, limit: int | None = None) -> list[MaterialPool]:
        from sqlalchemy import text
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

    async def update_material_status(self, material_id: int, status: str, last_error: str | None = None, posted_fname: str | None = None, posted_tid: int | None = None) -> None:
        async with self.async_session() as session:
            m = await session.get(MaterialPool, material_id)
            if m:
                m.status = status
                from datetime import datetime
                m.last_used_at = datetime.now()
                if last_error is not None:
                    m.last_error = last_error
                if posted_fname is not None:
                    m.posted_fname = posted_fname
                if posted_tid is not None:
                    m.posted_tid = posted_tid
                await session.commit()

    async def update_material_bump(self, material_id: int) -> None:
        """更新自动回帖(自顶)计数与时间记录"""
        async with self.async_session() as session:
            m = await session.get(MaterialPool, material_id)
            if m:
                from datetime import datetime
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
                update(MaterialPool).values(status="pending", last_error="")
            )
            await session.commit()

    # ========== Forum Targets & Global Target Pool ==========

    async def get_native_post_targets(self, account_id: int | None = None) -> list[str]:
        """获取已标记为 is_post_target=True 的本机安全贴吧名池"""
        async with self.async_session() as session:
            stmt = select(Forum.fname).where(Forum.is_post_target == True)
            if account_id:
                stmt = stmt.where(Forum.account_id == account_id)
            result = await session.execute(stmt.distinct())
            return result.scalars().all()

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
        """记录靶场投递结果并发动熔断"""
        from datetime import datetime
        async with self.async_session() as session:
            result = await session.execute(select(TargetPool).where(TargetPool.fname == fname))
            pool = result.scalar()
            if not pool: return

            if is_success:
                pool.success_count += 1
                pool.fail_count = 0  # 恢复生命值
            else:
                pool.fail_count += 1
                pool.last_fail_reason = error_reason
                # 连续被封禁阈值，触发熔断
                if pool.fail_count >= 3:
                    pool.is_active = False

            pool.last_used_at = datetime.now()
            await session.commit()

    async def upsert_target_pools(self, fnames: list[str], group: str) -> int:
        """批量入库/覆盖靶场池"""
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
                    session.add(TargetPool(fname=fname.strip(), post_group=group))
                    added_count += 1
            await session.commit()
        return added_count

    async def toggle_forum_post_target(self, fid: int, is_post_target: bool) -> None:
        """切换本号某个特定贴吧的发帖许可状态"""
        async with self.async_session() as session:
            result = await session.execute(select(Forum).where(Forum.fid == fid))
            # 级联更新该 fid 绑定的所有记录（如果多号关注同一个吧，同开同关）
            for f in result.scalars().all():
                f.is_post_target = is_post_target
            await session.commit()

# 全局数据库实例
_db: Database | None = None

async def get_db() -> Database:
    """获取数据库实例"""
    global _db
    if _db is None:
        _db = Database()
        await _db.init_db()
    return _db
