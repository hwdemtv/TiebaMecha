"""Database 门面：引擎/会话/迁移 + 各领域 Repository mixin 组合。

历史为 2600+ 行单类（上帝类），现按领域拆分至 repositories/，
对外 API（db.get_accounts() 等）保持不变。
"""

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

# PROXY_FAIL_THRESHOLD 定义在 repositories/proxy_repo.py（重导出保持兼容）
from .repositories.proxy_repo import PROXY_FAIL_THRESHOLD  # noqa: F401



from .repositories import (
    AccountRepository,
    ForumRepository,
    SettingRepository,
    ProxyRepository,
    RuleRepository,
    BatchTaskRepository,
    MaterialRepository,
    TargetPoolRepository,
    CaptchaRepository,
    NotificationRepository,
    ThreadRepository,
    BatchLogRepository,
    BumpLogRepository,
)


class Database(
    AccountRepository,
    ForumRepository,
    SettingRepository,
    ProxyRepository,
    RuleRepository,
    BatchTaskRepository,
    MaterialRepository,
    TargetPoolRepository,
    CaptchaRepository,
    NotificationRepository,
    ThreadRepository,
    BatchLogRepository,
    BumpLogRepository,
):
    """异步数据库管理器（领域方法由 repositories/ 下的 mixin 组合提供）。"""

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False)

        # SQLite 并发调优：WAL 允许 Web 多会话与 daemon 后台任务并发读写，
        # busy_timeout 避免写锁竞争时立刻抛 database is locked
        from sqlalchemy import event

        @event.listens_for(self.engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
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
                ("last_weight_calc_at", "DATETIME DEFAULT NULL"),
            ]
            for col_name, col_type in accounts_migrations:
                await self._safe_add_column(conn, "accounts", col_name, col_type, accounts_cols)

            # 索引迁移 - post_weight 索引优化加权查询
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_accounts_post_weight ON accounts (post_weight DESC)"
            ))

            # BatchPostTask 新字段迁移
            batch_migrations = [
                ("fnames_json", "TEXT DEFAULT '[]'"),
                ("strategy", "VARCHAR(20) DEFAULT 'round_robin'"),
                ("pairing_mode", "VARCHAR(20) DEFAULT 'random'"),
                ("ai_persona", "VARCHAR(50) DEFAULT 'normal'"),
                ("schedule_type", "VARCHAR(20) DEFAULT 'once'"),
                ("interval_hours", "INTEGER DEFAULT 0"),
                ("schedule_day_of_week", "INTEGER DEFAULT NULL"),
                ("reset_strategy", "VARCHAR(20) DEFAULT 'new_only'"),
                ("cycle_count", "INTEGER DEFAULT 0"),
                ("forum_offset", "INTEGER DEFAULT 0"),
                ("config_json", "TEXT DEFAULT '{}'"),
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
                ("task_id", "VARCHAR(50) DEFAULT NULL"),
                ("link_url", "VARCHAR(500) DEFAULT NULL"),
                ("link_reply_at", "DATETIME DEFAULT NULL"),
                ("link_reply_pid", "BIGINT DEFAULT NULL"),
                ("link_reply_fail_count", "INTEGER DEFAULT 0"),
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

            # WeightHistory 索引迁移
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_weight_history_account_id ON weight_history (account_id)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_weight_history_created_at ON weight_history (created_at)"
            ))

            # 老库索引迁移（create_all 不会为已存在的表补建索引，此处显式补齐）
            # 索引清单与 models.py 中 __table_args__ 保持一致
            legacy_index_migrations = [
                # 按吧名查询/全局删除/封禁标记
                "CREATE INDEX IF NOT EXISTS ix_forums_fname ON forums (fname)",
                # 签到统计按吧聚合
                "CREATE INDEX IF NOT EXISTS ix_sign_logs_forum_id ON sign_logs (forum_id)",
                # 存活分析高频筛选
                "CREATE INDEX IF NOT EXISTS ix_material_pool_posted_tid ON material_pool (posted_tid)",
                "CREATE INDEX IF NOT EXISTS ix_material_pool_posted_fname ON material_pool (posted_fname)",
                "CREATE INDEX IF NOT EXISTS ix_material_pool_survival_status ON material_pool (survival_status)",
                "CREATE INDEX IF NOT EXISTS ix_material_pool_task_id ON material_pool (task_id)",
                # 矩阵统计按吧+状态聚合
                "CREATE INDEX IF NOT EXISTS ix_batch_post_logs_fname_status ON batch_post_logs (fname, status)",
            ]
            for stmt in legacy_index_migrations:
                await conn.execute(text(stmt))

        # 数据自愈：确保所有账号的 post_weight 都有默认值 5
        try:
            async with self.async_session() as session:
                await session.execute(text("UPDATE accounts SET post_weight = 5 WHERE post_weight IS NULL"))
        except Exception as e:
            logger.warning(f"Failed to heal post_weight data: {e}")
    async def close(self) -> None:
        """关闭数据库连接"""
        await self.engine.dispose()

# 全局数据库实例
_db: Database | None = None


async def get_db() -> Database:
    """获取数据库实例"""
    global _db
    if _db is None:
        _db = Database()
        await _db.init_db()
    return _db
