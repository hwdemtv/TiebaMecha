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

    async def test_do_sign_task_execution_flow(self, db):
        """Test the execution flow of the sign task (matrix vs single)."""
        # Set up settings
        await db.set_setting("schedule", json.dumps({"mode": "matrix"}))

        # Mock the actual sign functions
        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.daemon.sign_all_accounts") as mock_matrix, \
             patch("tieba_mecha.core.daemon.sign_all_forums") as mock_single:

            # Setup mock generators
            async def empty_gen(*args, **kwargs):
                if False: yield {}

            mock_matrix.return_value = empty_gen()
            mock_single.return_value = empty_gen()

            # Execute task
            await do_sign_task()

            # Should have called matrix
            mock_matrix.assert_called_once()
            mock_single.assert_not_called()

            # Switch to single mode
            await db.set_setting("schedule", json.dumps({"mode": "single"}))
            mock_matrix.reset_mock()
            mock_single.reset_mock()

            await do_sign_task()
            mock_matrix.assert_not_called()
            mock_single.assert_called_once()

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
        """回归: 已有签到流 (手动) 在执行时, 定时触发直接跳过, 不并发执行"""
        from tieba_mecha.core.sign import sign_flow_lock

        await db.set_setting("schedule", json.dumps({"mode": "single"}))

        async with sign_flow_lock:
            with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
                 patch("tieba_mecha.core.daemon.sign_all_forums") as mock_single:
                await do_sign_task()
                mock_single.assert_not_called()

    async def test_do_sign_task_tolerates_corrupted_schedule_json(self, db):
        """回归: schedule 为损坏 JSON 时按默认模式执行而非整体失败"""
        await db.set_setting("schedule", "{not-json")

        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.daemon.sign_all_forums") as mock_single:

            async def empty_gen(*args, **kwargs):
                if False: yield {}

            mock_single.return_value = empty_gen()

            await do_sign_task()  # 不应抛异常
            mock_single.assert_called_once()  # 回退到 single 默认模式

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
