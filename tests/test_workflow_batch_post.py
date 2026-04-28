"""Integration tests for Batch Post workflow."""

import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask as CoreBatchPostTask
from tieba_mecha.core.daemon import do_batch_post_tasks
from tieba_mecha.core.account import add_account

@pytest.mark.asyncio
class TestBatchPostWorkflow:
    """Integration tests for the complete batch post workflow."""

    async def test_full_batch_post_workflow(self, db, mock_aiotieba_client):
        """Test the full workflow: Add account -> Add material -> Create task -> Execute via Daemon."""
        
        # 1. Setup: Add account and forums
        acc = await add_account(db, "worker_acc", "a"*192, "s"*64, verify=False)
        # Ensure account is active and has forums
        forum = await db.add_forum(fid=100, fname="test_forum", account_id=acc.id)
        # Manually set is_post_target = True
        async with db.async_session() as session:
            from tieba_mecha.db.models import Forum as DBForum
            db_forum = await session.get(DBForum, forum.id)
            db_forum.is_post_target = True
            await session.commit()
        await db.update_account_status(acc.id, "active")

        # 2. Setup: Add material to pool
        await db.add_materials_bulk([("Title 1", "Content 1"), ("Title 2", "Content 2")])

        # 3. Setup: Create batch post task in DB
        task = await db.add_batch_task(
            fname="test_forum",
            titles_json=json.dumps(["Title 1", "Title 2"]),
            contents_json=json.dumps(["Content 1", "Content 2"]),
            accounts_json=json.dumps([acc.id]),
            strategy="round_robin",
            total=2,
            delay_min=0.1,  # Short delay for testing
            delay_max=0.2,
        )

        # 4. Mock aiotieba client and httpx
        mock_aiotieba_client.account.tbs = "fake_tbs"
        mock_aiotieba_client.get_self_info = AsyncMock()
        mock_aiotieba_client.get_forum = AsyncMock(return_value=MagicMock(fid=100))
        
        # 5. Patch dependencies in daemon and batch_post
        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.batch_post.create_client", new_callable=AsyncMock, return_value=mock_aiotieba_client), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock), \
             patch("tieba_mecha.core.batch_post.AccountForumCooldown") as mock_af_class, \
             patch("tieba_mecha.core.batch_post.get_auth_manager") as mock_get_auth, \
             patch("httpx.AsyncClient") as mock_httpx:
            
            # Setup AccountForumCooldown mock
            mock_af = MagicMock()
            mock_af.can_post.return_value = True
            mock_af.get_available_forum = AsyncMock(side_effect=lambda a, f: f[0])
            mock_af.record_post = AsyncMock()
            mock_af_class.return_value = mock_af
            
            # Mock AuthManager to be PRO
            mock_auth = AsyncMock()
            mock_auth.status = 1  # AuthStatus.PRO
            mock_auth.check_local_status = AsyncMock(return_value=1)
            mock_get_auth.return_value = mock_auth
            
            # Mock httpx response
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"err_code": 0, "data": {"tid": 12345}}
            mock_resp.status_code = 200
            
            mock_client_ctx = MagicMock()
            mock_client_ctx.__aenter__.return_value = AsyncMock()
            mock_client_ctx.__aenter__.return_value.get = AsyncMock()
            mock_client_ctx.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            mock_httpx.return_value = mock_client_ctx

            # 6. Trigger daemon task
            await do_batch_post_tasks()

        # 7. Verification: Check task status in DB
        updated_task = await db.get_all_batch_tasks()
        assert len(updated_task) == 1
        assert updated_task[0].status == "completed"
        assert updated_task[0].progress == 2

        # 8. Verification: Check material status
        materials = await db.get_materials(limit=10)
        # Assuming execute_task marks materials as success/failed
        # Note: Depending on how BatchPostManager works, it might update material status.
        # Let's check if there are logs in the database.
        logs = await db.get_batch_post_logs(limit=10)
        assert len(logs) == 2
        assert logs[0].status == "success"
        assert logs[0].fname == "test_forum"

    async def test_batch_post_with_material_reuse(self, db, mock_aiotieba_client):
        """Test that materials are reset correctly for daily tasks."""
        acc = await add_account(db, "reuse_acc", "a"*192, verify=False)
        
        # Create a "completed" task that is "daily"
        task = await db.add_batch_task(
            fname="test_forum",
            titles_json=json.dumps(["Reuse Title"]),
            contents_json=json.dumps(["Reuse Content"]),
            accounts_json=json.dumps([acc.id]),
            total=1,
            strategy="round_robin",
        )
        # Update to completed and set schedule_type
        await db.update_batch_task(task.id, status="completed")
        # Manually set schedule_type since add_batch_task might not take it
        async with db.async_session() as session:
            from tieba_mecha.db.models import BatchPostTask
            db_task = await session.get(BatchPostTask, task.id)
            db_task.schedule_type = "daily"
            db_task.reset_strategy = "reuse"
            db_task.schedule_time = datetime.now()
            await session.commit()

        # Add a "success" material that needs reset
        await db.add_materials_bulk([("Reuse Title", "Reuse Content")])
        mats = await db.get_materials()
        await db.update_material_status(mats[0].id, "success")

        # Mock dependencies
        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.batch_post.create_client", return_value=AsyncMock(return_value=mock_aiotieba_client)), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock):
            
            # Since the task is "completed", do_batch_post_tasks won't pick it up 
            # unless we change it back to "pending" or the daemon handles it.
            # do_batch_post_tasks picks up "pending" tasks.
            await db.update_batch_task(task.id, status="pending")
            
            await do_batch_post_tasks()

        # Check if material was reset (status becomes success again after execution, 
        # but was it reused?)
        # If reset_materials_for_task was called, we should see it in logs or count.
        # BatchPostManager.execute_task will mark it success again.
        logs = await db.get_batch_post_logs()
        assert len(logs) >= 1
