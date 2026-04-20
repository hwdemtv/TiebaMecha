"""Tests for TargetPool 击穿数/熔断逻辑 和 backfill 统计。

设计原则：
- success_count: 由 backfill_success_count() 从 BatchPostLog 实时统计，update_target_pool_status 不维护
- fail_count: 由 update_target_pool_status 维护（连续失败计数，成功清零，≥3 熔断）
"""

import pytest
import pytest_asyncio
from datetime import datetime


@pytest.mark.asyncio
class TestUpdateTargetPoolStatus:
    """测试 update_target_pool_status：fail_count 维护和熔断机制"""

    async def test_success_resets_fail_count(self, db):
        """成功发帖 → fail_count 归零"""
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
        await db.update_target_pool_status("auto_created_forum", is_success=False, error_reason="test")
        pool = await db._get_target_pool("auto_created_forum")
        assert pool is not None
        assert pool.fail_count == 1

    async def test_last_used_at_updated(self, db):
        """每次更新都刷新 last_used_at"""
        await db.upsert_target_pools(["test_forum"], "test")

        await db.update_target_pool_status("test_forum", is_success=True)
        pool = await db._get_target_pool("test_forum")
        assert pool.last_used_at is not None

    async def test_success_does_not_modify_success_count(self, db):
        """update_target_pool_status 成功时不修改 success_count（由 backfill 统一维护）"""
        await db.upsert_target_pools(["test_forum"], "test")
        await db.update_target_pool_status("test_forum", is_success=True)

        pool = await db._get_target_pool("test_forum")
        assert pool.success_count == 0  # 不在此递增

    async def test_fail_then_success_resets_fail_and_no_count_change(self, db):
        """失败后成功 → fail_count 归零，success_count 不变"""
        await db.upsert_target_pools(["test_forum"], "test")
        await db.update_target_pool_status("test_forum", is_success=False, error_reason="test")
        await db.update_target_pool_status("test_forum", is_success=True)

        pool = await db._get_target_pool("test_forum")
        assert pool.fail_count == 0
        assert pool.success_count == 0  # 由 backfill 维护


@pytest.mark.asyncio
class TestBackfillSuccessCount:
    """测试 backfill_success_count：从 BatchPostLog 统计击穿数（success_count 的唯一数据来源）"""

    async def test_backfill_from_logs(self, db):
        """BatchPostLog 中有成功记录 → 同步到 success_count"""
        await db.upsert_target_pools(["forum_a", "forum_b"], "test")

        await db.add_batch_post_log(task_id="t1", fname="forum_a", status="success", title="post1")
        await db.add_batch_post_log(task_id="t2", fname="forum_a", status="success", title="post2")
        await db.add_batch_post_log(task_id="t3", fname="forum_b", status="success", title="post3")

        updated = await db.backfill_success_count()
        assert updated >= 2

        pool_a = await db._get_target_pool("forum_a")
        pool_b = await db._get_target_pool("forum_b")
        assert pool_a.success_count == 2
        assert pool_b.success_count == 1

    async def test_backfill_syncs_to_log_count(self, db):
        """backfill 以 BatchPostLog 统计值为准，覆盖旧值"""
        await db.upsert_target_pools(["forum_a"], "test")

        # 先写入 1 条日志
        await db.add_batch_post_log(task_id="t1", fname="forum_a", status="success", title="post1")
        await db.backfill_success_count()
        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 1

        # 再写入 2 条日志，总共 3 条
        await db.add_batch_post_log(task_id="t2", fname="forum_a", status="success", title="post2")
        await db.add_batch_post_log(task_id="t3", fname="forum_a", status="success", title="post3")

        updated = await db.backfill_success_count()
        assert updated >= 1
        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 3  # 以日志统计值为准

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

    async def test_backfill_creates_nonexistent_forum(self, db):
        """BatchPostLog 中的 fname 在 target_pool 不存在 → 自动创建并同步"""
        await db.add_batch_post_log(task_id="t1", fname="phantom_forum", status="success", title="post1")

        updated = await db.backfill_success_count()
        assert updated >= 1
        pool = await db._get_target_pool("phantom_forum")
        assert pool is not None
        assert pool.success_count == 1

    async def test_backfill_idempotent(self, db):
        """重复调用幂等：值与日志一致时不产生更新"""
        await db.upsert_target_pools(["forum_a"], "test")
        await db.add_batch_post_log(task_id="t1", fname="forum_a", status="success", title="post1")

        await db.backfill_success_count()
        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 1

        # 第二次回填：success_count 已经与日志一致
        updated = await db.backfill_success_count()
        assert updated == 0
        pool = await db._get_target_pool("forum_a")
        assert pool.success_count == 1


@pytest.mark.asyncio
class TestAutoSyncPostTarget:
    """测试 auto_sync_post_target：自动判定本土作战许可"""

    async def test_safe_when_not_banned_no_deletions(self, db):
        """未封禁 + 无删帖记录 → is_post_target=True"""
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


@pytest.mark.asyncio
class TestMaterialSurvivalMarking:
    """测试 update_material_survival_status 的贴吧标记联动逻辑"""

    async def test_dead_by_mod_marks_forum_banned(self, db):
        """帖子被吧务删除 → 对应 Forum 标记为 is_banned=True, is_post_target=False"""
        # 准备：创建账号 + 贴吧 + 物料
        acc = await db.add_account(name="test_acc", bduss="x" * 192)
        await db.add_forum(fid=1, fname="target_forum", account_id=acc.id)
        # 创建物料（需要 posted_tid, posted_account_id, posted_fname）
        from tieba_mecha.db.models import MaterialPool
        async with db.async_session() as session:
            m = MaterialPool(
                title="test post",
                content="test content",
                status="success",
                posted_tid=12345,
                posted_account_id=acc.id,
                posted_fname="target_forum",
            )
            session.add(m)
            await session.commit()
            mid = m.id

        # 执行：帖子被吧务删除
        await db.update_material_survival_status(mid, "dead", "deleted_by_mod")

        # 验证：Forum 被标记封禁
        forums = await db.get_forums(account_id=acc.id)
        target = [f for f in forums if f.fname == "target_forum"]
        assert len(target) == 1
        assert target[0].is_banned is True
        assert target[0].is_post_target is False
        assert "吧务删除" in target[0].ban_reason

    async def test_dead_by_system_marks_forum_banned(self, db):
        """帖子被系统风控删除 → Forum 标记封禁"""
        acc = await db.add_account(name="test_acc2", bduss="y" * 192)
        await db.add_forum(fid=2, fname="sys_forum", account_id=acc.id)
        from tieba_mecha.db.models import MaterialPool
        async with db.async_session() as session:
            m = MaterialPool(
                title="test post",
                content="test content",
                status="success",
                posted_tid=54321,
                posted_account_id=acc.id,
                posted_fname="sys_forum",
            )
            session.add(m)
            await session.commit()
            mid = m.id

        await db.update_material_survival_status(mid, "dead", "deleted_by_system")

        forums = await db.get_forums(account_id=acc.id)
        target = [f for f in forums if f.fname == "sys_forum"]
        assert len(target) == 1
        assert target[0].is_banned is True
        assert target[0].is_post_target is False

    async def test_alive_does_not_mark_forum_banned(self, db):
        """帖子存活 → 不标记封禁"""
        acc = await db.add_account(name="test_acc3", bduss="z" * 192)
        await db.add_forum(fid=3, fname="alive_forum", account_id=acc.id)
        from tieba_mecha.db.models import MaterialPool
        async with db.async_session() as session:
            m = MaterialPool(
                title="test post",
                content="test content",
                status="success",
                posted_tid=99999,
                posted_account_id=acc.id,
                posted_fname="alive_forum",
            )
            session.add(m)
            await session.commit()
            mid = m.id

        await db.update_material_survival_status(mid, "alive", "")

        forums = await db.get_forums(account_id=acc.id)
        target = [f for f in forums if f.fname == "alive_forum"]
        assert len(target) == 1
        assert target[0].is_banned is False

    async def test_dead_unknown_reason_does_not_mark_banned(self, db):
        """帖子阵亡但原因不在封禁列表 → 不标记封禁"""
        acc = await db.add_account(name="test_acc4", bduss="w" * 192)
        await db.add_forum(fid=4, fname="unknown_forum", account_id=acc.id)
        from tieba_mecha.db.models import MaterialPool
        async with db.async_session() as session:
            m = MaterialPool(
                title="test post",
                content="test content",
                status="success",
                posted_tid=11111,
                posted_account_id=acc.id,
                posted_fname="unknown_forum",
            )
            session.add(m)
            await session.commit()
            mid = m.id

        # "deleted_by_user" 不在 ban_reason_map 中
        await db.update_material_survival_status(mid, "dead", "deleted_by_user")

        forums = await db.get_forums(account_id=acc.id)
        target = [f for f in forums if f.fname == "unknown_forum"]
        assert len(target) == 1
        assert target[0].is_banned is False

    async def test_deleted_count_in_matrix_stats(self, db):
        """被删帖数量在矩阵统计中正确计算"""
        acc = await db.add_account(name="test_acc5", bduss="v" * 192)
        await db.add_forum(fid=5, fname="deleted_forum", account_id=acc.id)
        await db.upsert_target_pools(["deleted_forum"], "test")

        from tieba_mecha.db.models import MaterialPool
        async with db.async_session() as session:
            for i in range(3):
                m = MaterialPool(
                    title=f"post {i}",
                    content="content",
                    status="success",
                    posted_tid=20000 + i,
                    posted_account_id=acc.id,
                    posted_fname="deleted_forum",
                )
                session.add(m)
            await session.commit()
            # 手动设置存活状态
            for i, m_obj in enumerate(
                (await session.execute(
                    __import__("sqlalchemy").select(MaterialPool).where(MaterialPool.posted_fname == "deleted_forum")
                )).scalars().all()
            ):
                if i < 2:
                    m_obj.survival_status = "dead"
                    m_obj.death_reason = "deleted_by_mod"
                else:
                    m_obj.survival_status = "alive"
                    m_obj.death_reason = ""
            await session.commit()

        # 获取矩阵统计
        stats = await db.get_forum_matrix_stats()
        deleted_stat = [s for s in stats if s["fname"] == "deleted_forum"]
        assert len(deleted_stat) == 1
        assert deleted_stat[0]["deleted_count"] == 2

    async def test_deleted_count_includes_empty_death_reason(self, db):
        """历史数据 death_reason 为空 → 仍计入被删数（排除法逻辑）"""
        acc = await db.add_account(name="test_acc6", bduss="u" * 192)
        await db.add_forum(fid=6, fname="legacy_forum", account_id=acc.id)
        await db.upsert_target_pools(["legacy_forum"], "test")

        from tieba_mecha.db.models import MaterialPool
        async with db.async_session() as session:
            m1 = MaterialPool(
                title="old post",
                content="content",
                status="success",
                posted_tid=30000,
                posted_account_id=acc.id,
                posted_fname="legacy_forum",
                survival_status="dead",
                death_reason="",  # 历史数据：death_reason 为空
            )
            m2 = MaterialPool(
                title="new post",
                content="content",
                status="success",
                posted_tid=30001,
                posted_account_id=acc.id,
                posted_fname="legacy_forum",
                survival_status="dead",
                death_reason="deleted_by_user",  # 用户自删，不计入
            )
            session.add_all([m1, m2])
            await session.commit()

        stats = await db.get_forum_matrix_stats()
        legacy = [s for s in stats if s["fname"] == "legacy_forum"]
        assert len(legacy) == 1
        assert legacy[0]["deleted_count"] == 1  # 空原因计入，用户自删不计


# ---- Helper ----
@pytest_asyncio.fixture
async def db(temp_db_path):
    """扩展 conftest 的 db fixture，增加辅助方法"""
    from tieba_mecha.db.crud import Database
    from tieba_mecha.db.models import TargetPool
    from sqlalchemy import select

    database = Database(temp_db_path)
    await database.init_db()

    async def _get_target_pool(fname: str):
        async with database.async_session() as session:
            result = await session.execute(
                select(TargetPool).where(TargetPool.fname == fname)
            )
            return result.scalar_one_or_none()

    database._get_target_pool = _get_target_pool

    yield database
    await database.close()
