"""Tests for TargetPool success_count (击穿数) and related backfill/update logic."""

import pytest
import pytest_asyncio
from datetime import datetime


@pytest.mark.asyncio
class TestUpdateTargetPoolStatus:
    """测试 update_target_pool_status：击穿数递增、拦截计数、熔断机制"""

    async def test_success_increments_count(self, db):
        """成功发帖 → success_count +1"""
        await db.upsert_target_pools(["test_forum"], "test")
        await db.update_target_pool_status("test_forum", is_success=True)

        pool = await db._get_target_pool("test_forum")
        assert pool is not None
        assert pool.success_count == 1

    async def test_success_resets_fail_count(self, db):
        """成功发帖 → fail_count 归零（恢复生命值）"""
        await db.upsert_target_pools(["test_forum"], "test")
        # 先积累一些失败
        await db.update_target_pool_status("test_forum", is_success=False, error_reason="test")
        await db.update_target_pool_status("test_forum", is_success=False, error_reason="test")
        pool = await db._get_target_pool("test_forum")
        assert pool.fail_count == 2

        # 成功后 fail_count 应归零
        await db.update_target_pool_status("test_forum", is_success=True)
        pool = await db._get_target_pool("test_forum")
        assert pool.fail_count == 0
        assert pool.success_count == 1

    async def test_fail_increments_fail_count(self, db):
        """失败 → fail_count +1，记录原因"""
        await db.upsert_target_pools(["test_forum"], "test")
        await db.update_target_pool_status("test_forum", is_success=False, error_reason="发射检测吧封")

        pool = await db._get_target_pool("test_forum")
        assert pool.fail_count == 1
        assert pool.last_fail_reason == "发射检测吧封"

    async def test_circuit_breaker_at_3_fails(self, db):
        """连续 3 次失败 → 触发熔断 (is_active=False)"""
        await db.upsert_target_pools(["test_forum"], "test")

        for i in range(3):
            await db.update_target_pool_status("test_forum", is_success=False, error_reason=f"fail_{i}")

        pool = await db._get_target_pool("test_forum")
        assert pool.is_active is False
        assert pool.fail_count == 3

    async def test_no_circuit_breaker_under_3_fails(self, db):
        """连续 2 次失败 → 不触发熔断"""
        await db.upsert_target_pools(["test_forum"], "test")

        await db.update_target_pool_status("test_forum", is_success=False, error_reason="fail_1")
        await db.update_target_pool_status("test_forum", is_success=False, error_reason="fail_2")

        pool = await db._get_target_pool("test_forum")
        assert pool.is_active is True

    async def test_nonexistent_fname_auto_created(self, db):
        """fname 不在 target_pool 中 → 自动创建记录"""
        await db.update_target_pool_status("auto_created_forum", is_success=True)
        pool = await db._get_target_pool("auto_created_forum")
        assert pool is not None
        assert pool.success_count == 1

    async def test_multiple_successes_accumulate(self, db):
        """多次成功 → success_count 累加"""
        await db.upsert_target_pools(["test_forum"], "test")

        for _ in range(5):
            await db.update_target_pool_status("test_forum", is_success=True)

        pool = await db._get_target_pool("test_forum")
        assert pool.success_count == 5

    async def test_last_used_at_updated(self, db):
        """每次更新都刷新 last_used_at"""
        await db.upsert_target_pools(["test_forum"], "test")

        await db.update_target_pool_status("test_forum", is_success=True)
        pool = await db._get_target_pool("test_forum")
        assert pool.last_used_at is not None


@pytest.mark.asyncio
class TestBackfillSuccessCount:
    """测试 backfill_success_count：从 BatchPostLog 回填击穿数"""

    async def test_backfill_from_logs(self, db):
        """BatchPostLog 中有成功记录 → 回填到 success_count"""
        # 创建 target_pool 记录
        await db.upsert_target_pools(["forum_a", "forum_b"], "test")

        # 写入 BatchPostLog 成功记录
        await db.add_batch_post_log(task_id="t1", fname="forum_a", status="success", title="post1")
        await db.add_batch_post_log(task_id="t2", fname="forum_a", status="success", title="post2")
        await db.add_batch_post_log(task_id="t3", fname="forum_b", status="success", title="post3")

        # 执行回填
        updated = await db.backfill_success_count()
        assert updated >= 2  # 至少更新了 forum_a 和 forum_b

        # 验证
        pool_a = await db._get_target_pool("forum_a")
        pool_b = await db._get_target_pool("forum_b")
        assert pool_a.success_count == 2
        assert pool_b.success_count == 1

    async def test_backfill_only_updates_zero(self, db):
        """只回填 success_count=0 的记录，不覆盖已有数据"""
        await db.upsert_target_pools(["forum_a"], "test")

        # 先手动设置 success_count = 5
        await db.update_target_pool_status("forum_a", is_success=True)
        await db.update_target_pool_status("forum_a", is_success=True)
        await db.update_target_pool_status("forum_a", is_success=True)
        await db.update_target_pool_status("forum_a", is_success=True)
        await db.update_target_pool_status("forum_a", is_success=True)
        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 5

        # 写入 BatchPostLog（1条成功），但不应覆盖已有的5
        await db.add_batch_post_log(task_id="t1", fname="forum_a", status="success", title="post1")

        updated = await db.backfill_success_count()
        assert updated == 0  # 没有更新（因为 success_count != 0）

        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 5  # 原值不变

    async def test_backfill_empty_logs(self, db):
        """BatchPostLog 无记录 → 返回 0"""
        await db.upsert_target_pools(["forum_a"], "test")
        updated = await db.backfill_success_count()
        assert updated == 0

    async def test_backfill_only_counts_success(self, db):
        """只统计 status='success' 的记录，忽略 error/skipped"""
        await db.upsert_target_pools(["forum_a"], "test")

        await db.add_batch_post_log(task_id="t1", fname="forum_a", status="success", title="post1")
        await db.add_batch_post_log(task_id="t2", fname="forum_a", status="error", message="fail")
        await db.add_batch_post_log(task_id="t3", fname="forum_a", status="success", title="post2")
        await db.add_batch_post_log(task_id="t4", fname="forum_a", status="skipped", message="skip")

        updated = await db.backfill_success_count()
        assert updated >= 1

        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 2  # 只有 2 条 success

    async def test_backfill_skips_nonexistent_forum(self, db):
        """BatchPostLog 中的 fname 在 target_pool 不存在 → 跳过"""
        # 只写日志，不创建 target_pool 记录
        await db.add_batch_post_log(task_id="t1", fname="phantom_forum", status="success", title="post1")

        updated = await db.backfill_success_count()
        assert updated == 0

    async def test_backfill_idempotent(self, db):
        """重复调用幂等：第二次不会改变已有数据"""
        await db.upsert_target_pools(["forum_a"], "test")
        await db.add_batch_post_log(task_id="t1", fname="forum_a", status="success", title="post1")

        # 第一次回填
        await db.backfill_success_count()
        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 1

        # 第二次回填：success_count 已经不为 0，不会被覆盖
        updated = await db.backfill_success_count()
        assert updated == 0
        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 1


@pytest.mark.asyncio
class TestAutoSyncPostTarget:
    """测试 auto_sync_post_target：自动判定本土作战许可"""

    async def test_safe_when_not_banned_no_deletions(self, db):
        """未封禁 + 无删帖记录 → is_post_target=True"""
        # 创建账号和关注记录
        acc = await db.add_account(name="test_acc", bduss="x" * 192)
        await db.add_forum(fid=1, fname="safe_forum", account_id=acc.id)

        await db.auto_sync_post_target()

        forums = await db.get_forums(account_id=acc.id)
        safe = [f for f in forums if f.fname == "safe_forum"]
        assert len(safe) == 1
        assert safe[0].is_post_target is True

    async def test_unsafe_when_banned(self, db):
        """已封禁 → is_post_target=False"""
        acc = await db.add_account(name="test_acc", bduss="x" * 192)
        await db.add_forum(fid=1, fname="banned_forum", account_id=acc.id)
        await db.mark_forum_banned(acc.id, "banned_forum", reason="test ban")

        await db.auto_sync_post_target()

        forums = await db.get_forums(account_id=acc.id)
        banned = [f for f in forums if f.fname == "banned_forum"]
        assert len(banned) == 1
        assert banned[0].is_post_target is False


# ---- Helper: 获取 TargetPool 记录 ----
# 需要在 Database 类上加一个测试辅助方法，或者直接查询
@pytest_asyncio.fixture
async def db(temp_db_path):
    """扩展 conftest 的 db fixture，增加辅助方法"""
    from tieba_mecha.db.crud import Database
    from tieba_mecha.db.models import TargetPool
    from sqlalchemy import select

    database = Database(temp_db_path)
    await database.init_db()

    # 注入测试辅助方法
    async def _get_target_pool(fname: str):
        async with database.async_session() as session:
            result = await session.execute(
                select(TargetPool).where(TargetPool.fname == fname)
            )
            return result.scalar_one_or_none()

    database._get_target_pool = _get_target_pool

    yield database
    await database.close()
