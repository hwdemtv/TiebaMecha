"""任务中心定时任务暂停/恢复功能测试。

覆盖：仓储层条件更新（防与认领路径竞态）、paused 状态被调度查询排除、
崩溃恢复不碰 paused、恢复时间重算（once 保留/立即、daily/interval 下一档）。
"""

from datetime import datetime, timedelta

import pytest

from tieba_mecha.core.daemon import calc_batch_task_resume_time


async def _make_task(db, **overrides):
    kwargs = dict(
        fname="test_bar",
        titles_json='["t"]',
        contents_json='["c"]',
        accounts_json="[1]",
        fnames_json='["test_bar"]',
        total=1,
        schedule_type="once",
        schedule_time=datetime.now() + timedelta(hours=2),
        status="pending",
    )
    kwargs.update(overrides)
    return await db.add_batch_task(**kwargs)


@pytest.mark.asyncio
class TestPauseResumeRepo:
    async def test_pause_pending_task(self, db):
        task = await _make_task(db)
        assert await db.pause_batch_task(task.id) is True
        assert (await db.get_batch_task(task.id)).status == "paused"

    async def test_pause_running_task_rejected(self, db):
        """与 claim_batch_task 同口径：running 不可被覆盖为 paused"""
        task = await _make_task(db, status="running")
        assert await db.pause_batch_task(task.id) is False
        assert (await db.get_batch_task(task.id)).status == "running"

    async def test_resume_paused_task(self, db):
        task = await _make_task(db, status="paused")
        assert await db.resume_batch_task(task.id) is True
        assert (await db.get_batch_task(task.id)).status == "pending"

    async def test_resume_pending_task_rejected(self, db):
        task = await _make_task(db)
        assert await db.resume_batch_task(task.id) is False
        assert (await db.get_batch_task(task.id)).status == "pending"

    async def test_pause_idempotent(self, db):
        task = await _make_task(db)
        assert await db.pause_batch_task(task.id) is True
        assert await db.pause_batch_task(task.id) is False


@pytest.mark.asyncio
class TestPausedExcludedFromScheduling:
    async def test_paused_not_polled_or_registered(self, db):
        """到期 pending 会被轮询捞走，paused 即使已到期也被排除"""
        await _make_task(db)  # pending 未到期
        due = await _make_task(db, schedule_time=datetime.now() - timedelta(minutes=5))
        paused = await _make_task(db, schedule_time=datetime.now() - timedelta(minutes=5))
        assert await db.pause_batch_task(paused.id) is True

        pending_due = await db.get_pending_batch_tasks()
        assert any(t.id == due.id for t in pending_due)
        assert all(t.id != paused.id for t in pending_due)

        scheduled = await db.get_scheduled_batch_tasks()
        assert all(t.id != paused.id for t in scheduled)

    async def test_crash_recovery_keeps_paused(self, db):
        """启动崩溃恢复只复位 running，不碰 paused"""
        task = await _make_task(db, status="paused")
        recovered = await db.reset_running_batch_tasks()
        assert recovered == 0
        assert (await db.get_batch_task(task.id)).status == "paused"


@pytest.mark.asyncio
class TestResumeTimeCalc:
    async def test_once_future_keeps_original(self, db):
        future = datetime.now() + timedelta(hours=3)
        task = await _make_task(db, schedule_time=future)
        assert abs((calc_batch_task_resume_time(task) - future).total_seconds()) < 1

    async def test_once_elapsed_runs_immediately(self, db):
        task = await _make_task(db, schedule_time=datetime.now() - timedelta(hours=3))
        before = datetime.now()
        resumed = calc_batch_task_resume_time(task)
        assert timedelta(0) <= resumed - before < timedelta(seconds=5)

    async def test_daily_recalc_next_slot(self, db):
        slot = datetime.now() + timedelta(hours=1)
        task = await _make_task(db, schedule_type="daily", schedule_time=slot)
        resumed = calc_batch_task_resume_time(task)
        assert resumed > datetime.now()
        assert (resumed.hour, resumed.minute) == (slot.hour, slot.minute)

    async def test_interval_recalc_from_now(self, db):
        task = await _make_task(
            db, schedule_type="interval", interval_hours=8, schedule_time=datetime.now()
        )
        before = datetime.now()
        resumed = calc_batch_task_resume_time(task)
        assert timedelta(hours=8) <= resumed - before < timedelta(hours=8, seconds=10)

    async def test_weekly_recalc_next_slot(self, db):
        now = datetime.now()
        slot = now + timedelta(hours=1)
        task = await _make_task(
            db,
            schedule_type="weekly",
            schedule_day_of_week=now.weekday(),
            schedule_time=slot,
        )
        resumed = calc_batch_task_resume_time(task)
        assert resumed > now
        assert (resumed.hour, resumed.minute) == (slot.hour, slot.minute)


@pytest.mark.asyncio
class TestDetailEditReschedule:
    """任务详情/编辑保存链路：改期/换号落库后，重算逻辑认新配置（2026-09-22）。"""

    async def test_daily_reschedule_honors_new_time(self, db):
        from tieba_mecha.core.daemon import _calc_next_schedule_time

        task = await _make_task(db, schedule_type="daily",
                                schedule_time=datetime.now() + timedelta(hours=5))
        # 模拟编辑改期：新时刻 03:07（以今天为载体落库，重算归一到下一档）
        await db.update_batch_task(
            task.id, schedule_time=datetime.now().replace(hour=3, minute=7, second=0, microsecond=0))
        fresh = await db.get_batch_task(task.id)
        nxt = _calc_next_schedule_time(fresh)
        assert nxt > datetime.now()
        assert (nxt.hour, nxt.minute) == (3, 7)

    async def test_once_reschedule_keeps_new_time(self, db):
        task = await _make_task(db)
        future = datetime.now().replace(second=0, microsecond=0) + timedelta(days=3)
        await db.update_batch_task(task.id, schedule_time=future)
        fresh = await db.get_batch_task(task.id)
        # once 任务重算保留新计划时刻（编辑保存链路与恢复共用 calc_batch_task_resume_time）
        assert abs((calc_batch_task_resume_time(fresh) - future).total_seconds()) < 1

    async def test_account_pool_update_persists(self, db):
        import json as _json

        task = await _make_task(db, accounts_json="[1]")
        await db.update_batch_task(task.id, accounts_json=_json.dumps([1, 5, 6]))
        fresh = await db.get_batch_task(task.id)
        assert _json.loads(fresh.accounts_json) == [1, 5, 6]
