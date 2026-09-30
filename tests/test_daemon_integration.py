"""Integration tests for TiebaMecha Daemon."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from tieba_mecha.core.daemon import TiebaMechaDaemon, do_sign_task, do_auto_monitor_task

@pytest.mark.asyncio
class TestDaemonIntegration:
    """Integration tests for the global daemon."""

    async def test_daemon_singleton(self):
        """Test that TiebaMechaDaemon is a singleton."""
        daemon1 = TiebaMechaDaemon()
        daemon2 = TiebaMechaDaemon()
        assert daemon1 is daemon2

    async def test_daemon_start_and_stop(self, db):
        """Test daemon startup and job registration."""
        daemon = TiebaMechaDaemon()
        
        # Mock dependencies that run on start
        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.daemon.do_auth_check_task", new_callable=AsyncMock) as mock_auth:
            
            await daemon.start()
            
            assert daemon._started is True
            # Check default jobs
            job_ids = [job.id for job in daemon.scheduler.get_jobs()]
            assert "global_monitor_job" in job_ids
            assert "batch_post_job" in job_ids
            assert "update_check_job" in job_ids
            assert "auth_check_job" in job_ids
            assert "auto_bump_job" in job_ids
            # 反馈闭环周期任务（行为审计治理 + 存活反馈治理）
            assert "behavior_audit_job" in job_ids
            assert "survival_governance_job" in job_ids
            
            daemon.stop()
            assert daemon._started is False

    async def test_daemon_reload_config(self, db):
        """Test reloading daemon configuration from database."""
        daemon = TiebaMechaDaemon()
        
        # 1. Initially disabled
        await db.set_setting("schedule", json.dumps({"enabled": False}))
        await daemon.reload(db)
        assert daemon.scheduler.get_job(daemon.sign_job_id) is None
        
        # 2. Enable with specific time
        sign_time = "10:45"
        await db.set_setting("schedule", json.dumps({
            "enabled": True,
            "sign_time": sign_time,
            "mode": "single"
        }))
        
        await daemon.reload(db)
        
        job = daemon.scheduler.get_job(daemon.sign_job_id)
        assert job is not None
        # APScheduler cron trigger fields: year, month, day, week, day_of_week, hour, minute, second
        assert str(job.trigger.fields[5]) == "10" # Hour
        assert str(job.trigger.fields[6]) == "45" # Minute

    async def test_do_sign_task_serial_flow_invoked(self, db):
        """去模式化: 错峰关闭且有参与账号时，守护触发串行全扫"""
        from tieba_mecha.core.account import add_account

        await db.set_setting("schedule", json.dumps({"sign_time": "08:00"}))
        await db.set_setting("sign_stagger_minutes", "0")
        await add_account(db=db, name="flow_acc", bduss="a" * 192, stoken="b" * 64)

        with patch("tieba_mecha.core.daemon.get_db", return_value=db),              patch("tieba_mecha.core.daemon.sign_all_accounts") as mock_flow,              patch("asyncio.sleep", new_callable=AsyncMock):

            async def empty_gen(*args, **kwargs):
                if False: yield {}

            mock_flow.return_value = empty_gen()
            await do_sign_task()
            mock_flow.assert_called_once()

    async def test_do_sign_task_resets_daily_state(self, db):
        """回归: 守护签到前必须重置跨天状态, 否则纯后台使用时连续天数/成功统计永久冻结。

        场景: 昨日已签到 (is_sign_today=True), 今日守护进程直接触发签到 (无 UI 参与重置)。
        """
        from tieba_mecha.core.account import add_account
        from types import SimpleNamespace
        from datetime import datetime, timedelta

        from tieba_mecha.db.models import Forum

        acc = await add_account(db=db, name="acc_daily", bduss="a" * 192, stoken="b" * 64)
        forum = await db.add_forum(fid=1, fname="daily_forum", account_id=acc.id)

        # Day 1: 签到成功
        await db.update_forum_sign(forum.id, True)
        # 跨天: last_sign_date 回拨到昨天, is_sign_today 仍为 True
        async with db.async_session() as session:
            f = await session.get(Forum, forum.id)
            f.last_sign_date = datetime.now() - timedelta(days=1)
            await session.commit()

        # Day 2: 守护进程触发, mock 客户端返回签到成功
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_threads = AsyncMock(return_value=None)
        mock_client.sign_forum = AsyncMock(
            return_value=SimpleNamespace(err=None, __bool__=lambda self: True)
        )

        await db.set_setting("schedule", json.dumps({"mode": "single"}))
        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.sign.create_client", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await do_sign_task()

        forums = await db.get_forums(acc.id)
        assert forums[0].sign_count == 2, "守护签到第二天应正常累计连续天数 (修复前冻结在 1)"
        assert forums[0].history_success == 2
        assert forums[0].is_sign_today is True

    async def test_do_sign_task_skips_when_sign_flow_locked(self, db):
        """回归: 已有签到流 (手动) 在执行时, 定时触发不并发执行"""
        from tieba_mecha.core.sign import sign_flow_lock
        from tieba_mecha.core.daemon import daemon_instance

        await db.set_setting("schedule", json.dumps({"mode": "single"}))
        # 整改#15 后遇锁会挂当日重试 job，测试结束须清理防污染后续用例
        leftover = f"sign_retry_{datetime.now().date().isoformat()}"

        async with sign_flow_lock:
            with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
                 patch("tieba_mecha.core.daemon.sign_all_accounts") as mock_single, \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                await do_sign_task()
                mock_single.assert_not_called()

        if daemon_instance.scheduler.get_job(leftover):
            daemon_instance.scheduler.remove_job(leftover)

    async def test_do_sign_task_schedules_retry_when_locked(self, db):
        """整改#15: 遇锁改为挂 40 分钟重试而非当日放弃；attempt≥3 才放弃"""
        from tieba_mecha.core.sign import sign_flow_lock
        from tieba_mecha.core.daemon import daemon_instance

        from tieba_mecha.core.account import add_account

        await db.set_setting("schedule", json.dumps({"sign_time": "08:00"}))
        await add_account(db=db, name="retry_acc", bduss="a" * 192, stoken="b" * 64)
        job_id = f"sign_retry_{datetime.now().date().isoformat()}"
        # 防御：清掉可能残留的同日重试 job（单例调度器跨用例共享）
        if daemon_instance.scheduler.get_job(job_id):
            daemon_instance.scheduler.remove_job(job_id)

        async with sign_flow_lock:
            with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
                 patch("tieba_mecha.core.daemon.sign_all_accounts") as mock_single, \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                await do_sign_task(attempt=1)
                mock_single.assert_not_called()

        assert daemon_instance.scheduler.get_job(job_id) is not None, "遇锁应挂重试任务"
        daemon_instance.scheduler.remove_job(job_id)

        async with sign_flow_lock:
            with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                await do_sign_task(attempt=3)

        assert daemon_instance.scheduler.get_job(job_id) is None, "attempt≥3 应放弃不再挂重试"

    async def test_do_sign_task_marks_completion_date(self, db):
        """整改#15: 跑完写 last_daemon_sign_date 幂等标记（补跑判断依据）"""
        await db.set_setting("schedule", json.dumps({"mode": "single"}))

        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.daemon.sign_all_accounts") as mock_single, \
             patch("asyncio.sleep", new_callable=AsyncMock):

            async def empty_gen(*args, **kwargs):
                if False: yield {}

            mock_single.return_value = empty_gen()
            await do_sign_task()

        assert await db.get_setting("last_daemon_sign_date", "") == datetime.now().date().isoformat()

    async def test_do_sign_task_matrix_stagger_registers(self, db):
        """错峰: 矩阵+窗口>0 → 派发排程而非串行全扫；写当日完成标记"""
        await db.set_setting("schedule", json.dumps({"mode": "matrix", "sign_time": "06:45"}))
        await db.set_setting("sign_stagger_minutes", "90")

        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.daemon.sign_all_accounts") as mock_matrix, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await do_sign_task(jitter=False)

        mock_matrix.assert_not_called(), "错峰模式不得再走串行全扫"
        assert await db.get_setting("last_daemon_sign_date", "") == datetime.now().date().isoformat()

    async def test_stagger_offset_deterministic_and_spread(self, db):
        """错峰偏移: 同账号同日稳定、跨日不同；不同账号当日互相错开"""
        from tieba_mecha.core.sign import stagger_offset_seconds
        from datetime import date as real_date

        d1 = real_date(2026, 10, 1)
        d2 = real_date(2026, 10, 2)
        o1a = stagger_offset_seconds(7, d1, 90)
        assert stagger_offset_seconds(7, d1, 90) == o1a, "同账号同日偏移必须稳定"
        assert stagger_offset_seconds(7, d2, 90) != o1a, "跨日偏移应变化"
        others = [stagger_offset_seconds(i, d1, 90) for i in (1, 2, 3, 4)]
        assert all(0 <= o < 90 * 60 for o in [o1a] + others)
        assert len(set(others)) == 4, "不同账号当日偏移应互不相同"

    async def test_get_scheduled_sign_accounts_filters(self, db):
        """参与集合: 仅矩阵可用且 is_sign_scheduled 的账号进入守护排程"""
        from tieba_mecha.core.account import add_account

        acc1 = await add_account(db=db, name="sch_a", bduss="a" * 192, stoken="b" * 64)
        acc2 = await add_account(db=db, name="sch_b", bduss="c" * 192, stoken="d" * 64)
        acc3 = await add_account(db=db, name="sch_c", bduss="e" * 192, stoken="f" * 64)
        await db.update_account(acc2.id, is_sign_scheduled=False)
        await db.update_account(acc3.id, status="suspended")

        ids = [a.id for a in await db.get_scheduled_sign_accounts()]
        assert acc1.id in ids
        assert acc2.id not in ids, "退出参与的账号不得进入排程"
        assert acc3.id not in ids, "挂起账号不得进入排程"

    async def test_serial_path_respects_scheduled_subset(self, db):
        """去模式化: 错峰关闭的串行全扫只签参与定时的账号"""
        from tieba_mecha.core.account import add_account

        acc1 = await add_account(db=db, name="ser_a", bduss="a" * 192, stoken="b" * 64)
        acc2 = await add_account(db=db, name="ser_b", bduss="c" * 192, stoken="d" * 64)
        await db.update_account(acc2.id, is_sign_scheduled=False)
        await db.set_setting("schedule", json.dumps({"sign_time": "08:00"}))
        await db.set_setting("sign_stagger_minutes", "0")

        with patch("tieba_mecha.core.daemon.get_db", return_value=db),              patch("tieba_mecha.core.daemon.sign_all_accounts") as mock_flow,              patch("asyncio.sleep", new_callable=AsyncMock):

            async def empty_gen(*args, **kwargs):
                if False: yield {}

            mock_flow.return_value = empty_gen()
            await do_sign_task(jitter=False)

        kwargs = mock_flow.call_args.kwargs
        assert kwargs.get("account_ids") == [acc1.id], "串行路径应只传参与账号集合"

    async def test_stagger_worker_skips_fully_signed_account(self, db):
        """错峰 worker: 账号今日已全签 → 零请求秒退（重启重放安全性的根基）"""
        from tieba_mecha.core.account import add_account
        from tieba_mecha.core import daemon as daemon_mod
        from datetime import timedelta as td

        acc = await add_account(db=db, name="acc_stg", bduss="a" * 192, stoken="b" * 64)
        f = await db.add_forum(fid=1, fname="stg_forum", account_id=acc.id)
        await db.update_forum_sign(f.id, True)

        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.sign.create_client") as mock_client:
            await daemon_mod._stagger_account_worker(
                acc.id, datetime.now() - td(minutes=1)
            )

        mock_client.assert_not_called(), "已全签账号不得创建客户端/发请求"

    async def test_stagger_worker_signs_pending_account(self, db):
        """错峰 worker: 有待签的账号独立完成本账号签到（不触及其他账号）"""
        from tieba_mecha.core.account import add_account
        from tieba_mecha.core import daemon as daemon_mod
        from datetime import timedelta as td
        from types import SimpleNamespace

        acc_a = await add_account(db=db, name="acc_stg_a", bduss="a" * 192, stoken="b" * 64)
        acc_b = await add_account(db=db, name="acc_stg_b", bduss="c" * 192, stoken="d" * 64)
        fa = await db.add_forum(fid=1, fname="stg_a", account_id=acc_a.id)
        fb = await db.add_forum(fid=2, fname="stg_b", account_id=acc_b.id)
        await db.set_setting("sign_skip_probability", "0")

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_threads = AsyncMock(return_value=None)
        mock_client.sign_forum = AsyncMock(
            return_value=SimpleNamespace(err=None, __bool__=lambda self: True)
        )

        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.sign.create_client", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await daemon_mod._stagger_account_worker(
                acc_a.id, datetime.now() - td(minutes=1)
            )

        mock_client.sign_forum.assert_awaited_once_with("stg_a")
        forums = {f.fname: f for f in await db.get_forums(acc_b.id)}
        assert forums["stg_b"].is_sign_today is False, "其他账号不得被连带签到"

    async def test_do_sign_task_tolerates_corrupted_schedule_json(self, db):
        """回归: schedule 为损坏 JSON 时守护流程不整体失败（错峰锚点解析回退默认 08:00）"""
        from tieba_mecha.core.account import add_account

        await db.set_setting("schedule", "{not-json")
        await db.set_setting("sign_stagger_minutes", "90")
        await add_account(db=db, name="corr_acc", bduss="a" * 192, stoken="b" * 64)

        with patch("tieba_mecha.core.daemon.get_db", return_value=db),              patch("asyncio.sleep", new_callable=AsyncMock):
            await do_sign_task(jitter=False)  # 不应抛异常

        assert await db.get_setting("last_daemon_sign_date", "") != ""

    async def test_do_auto_monitor_task_workflow(self, db):
        """Test auto monitor task triggers rule application."""
        from tieba_mecha.core.account import add_account
        
        # Add account and active rules
        acc = await add_account(db, "test", "a"*192, verify=False)
        rule = await db.add_auto_rule(fname="test_forum", rule_type="keyword", pattern="bad", action="delete")
        # rule is active by default (is_active=True)
        
        # Mock client and rule application
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get_threads.return_value = [{"title": "bad post", "tid": 1}]
        
        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.daemon.get_account_credentials", return_value=(acc.id, "bduss", "", None, "", "")), \
             patch("tieba_mecha.core.daemon.create_client", new_callable=AsyncMock, return_value=mock_client), \
             patch("tieba_mecha.core.daemon.apply_rules_to_threads", new_callable=AsyncMock) as mock_apply:
            
            await do_auto_monitor_task()
            
            mock_apply.assert_called_once()
            args = mock_apply.call_args[0]
            assert args[1] == "test_forum"
            assert len(args[2]) == 1
