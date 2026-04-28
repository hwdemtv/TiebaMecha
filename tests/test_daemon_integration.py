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
