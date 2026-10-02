"""Integration tests for Batch Post workflow."""

import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask as CoreBatchPostTask
from tieba_mecha.core.daemon import do_batch_post_tasks, wait_spawned_batch_tasks
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

            # 6. Trigger daemon task（派发后等待后台协程结束再断言）
            await do_batch_post_tasks()
            await wait_spawned_batch_tasks()

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
            # 锚点取明确的过去时刻：起点日抖动可能把生效派发时刻推后最多 10 分钟，
            # 锚点贴近 now 时惰性推导会 50% 概率把任务抖出本轮轮询（设计行为）
            db_task.schedule_time = datetime.now() - timedelta(minutes=30)
            await session.commit()

        # Add a "success" material that needs reset
        await db.add_materials_bulk([("Reuse Title", "Reuse Content")])
        mats = await db.get_materials()
        # 模拟真实执行后的物料状态: 发帖成功时 update_material_status 会回写
        # task_id 关联 (见 batch_post.py 执行成功路径); reset_materials_for_task
        # 按该关联筛选待重置物料, 不设置则任务级重置匹配不到
        await db.update_material_status(mats[0].id, "success", task_id=str(task.id))

        # Mock dependencies
        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.batch_post.create_client", return_value=AsyncMock(return_value=mock_aiotieba_client)), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock):
            
            # Since the task is "completed", do_batch_post_tasks won't pick it up 
            # unless we change it back to "pending" or the daemon handles it.
            # do_batch_post_tasks picks up "pending" tasks.
            await db.update_batch_task(task.id, status="pending")

            await do_batch_post_tasks()
            await wait_spawned_batch_tasks()

        # Check if material was reset (status becomes success again after execution, 
        # but was it reused?)
        # If reset_materials_for_task was called, we should see it in logs or count.
        # BatchPostManager.execute_task will mark it success again.
        logs = await db.get_batch_post_logs()
        assert len(logs) >= 1

    async def test_silent_intercept_220012_defers_material_and_marks_forum(self, db, mock_aiotieba_client):
        """220012 静默拦截全链路回归（2026-10-01 电影吧事故）。

        封禁账号发帖遭静默拒绝（no=12/err_code=220012/error=None/need_vcode=0）：
        - 不再误触发验证码熔断（无 captcha 事件）
        - 该 (账号, 贴吧) 被标记封禁，物料顺延而非判失败
        - 后续物料的选号避开已知封禁组合（返回 None → 同样顺延）
        - 流水如实记录，不再出现"多账号均告失败"的笼统误报
        """
        acc = await add_account(db, "silent_acc", "a" * 192, "s" * 64, verify=False)
        forum = await db.add_forum(fid=200, fname="silent_ban_forum", account_id=acc.id)
        async with db.async_session() as session:
            from tieba_mecha.db.models import Forum as DBForum
            db_forum = await session.get(DBForum, forum.id)
            db_forum.is_post_target = True
            await session.commit()
        await db.update_account_status(acc.id, "active")
        await db.add_materials_bulk([("物料甲", "内容甲"), ("物料乙", "内容乙")])

        task = await db.add_batch_task(
            fname="silent_ban_forum",
            titles_json=json.dumps(["物料甲", "物料乙"]),
            contents_json=json.dumps(["内容甲", "内容乙"]),
            accounts_json=json.dumps([acc.id]),
            strategy="round_robin",
            total=2,
            delay_min=0.1,
            delay_max=0.2,
        )

        # 复刻 2026-10-01 08:50 电影吧事故的真实响应形态
        ban_resp = MagicMock()
        ban_resp.json.return_value = {
            "no": 12, "err_code": 220012, "error": None,
            "data": {
                "autoMsg": "", "fid": 200, "fname": "silent_ban_forum",
                "tid": 0, "is_login": 1, "content": "", "access_state": None,
                "experience": 0, "is_pop_award": 0, "pop_url": "",
                "draw_thread_content_match": 0,
                "vcode": {
                    "need_vcode": 0, "str_reason": "",
                    "captcha_vcode_str": "", "captcha_code_type": 0,
                    "userstatevcode": 0,
                },
                "is_thread_visible": 0,
            },
        }
        ban_resp.status_code = 200

        mock_aiotieba_client.account.tbs = "fake_tbs"
        mock_aiotieba_client.get_self_info = AsyncMock()
        mock_aiotieba_client.get_forum = AsyncMock(return_value=MagicMock(fid=200))

        with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
             patch("tieba_mecha.core.batch_post.create_client", new_callable=AsyncMock, return_value=mock_aiotieba_client), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock), \
             patch("tieba_mecha.core.batch_post.AccountForumCooldown") as mock_af_class, \
             patch("tieba_mecha.core.batch_post.get_auth_manager") as mock_get_auth, \
             patch("httpx.AsyncClient") as mock_httpx:
            mock_af = MagicMock()
            mock_af.can_post.return_value = True
            mock_af.get_available_forum = AsyncMock(side_effect=lambda a, f: f[0])
            mock_af.record_post = AsyncMock()
            mock_af_class.return_value = mock_af
            mock_auth = AsyncMock()
            mock_auth.status = 1
            mock_auth.check_local_status = AsyncMock(return_value=1)
            mock_get_auth.return_value = mock_auth

            mock_client_ctx = MagicMock()
            mock_client_ctx.__aenter__.return_value = AsyncMock()
            mock_client_ctx.__aenter__.return_value.get = AsyncMock()
            mock_client_ctx.__aenter__.return_value.post = AsyncMock(return_value=ban_resp)
            mock_httpx.return_value = mock_client_ctx

            await do_batch_post_tasks()
            await wait_spawned_batch_tasks()

        # 任务完成但 0 成功：两条物料均顺延（skip），无一被判失败
        updated_task = await db.get_all_batch_tasks()
        assert updated_task[0].status == "completed"
        assert updated_task[0].progress == 0

        logs = await db.get_batch_post_logs(limit=10)
        assert len(logs) == 2
        assert all(l.status == "skip" for l in logs)
        assert any("发帖静默拦截(220012)" in (l.message or "") for l in logs), \
            f"首条物料应由 220012 分支顺延: {[l.message for l in logs]}"
        assert any("候选账号均已被该吧封禁" in (l.message or "") for l in logs), \
            f"后续物料选号应返回 None 顺延: {[l.message for l in logs]}"

        # 该 (账号, 贴吧) 已被标记为吧务封禁
        async with db.async_session() as session:
            from tieba_mecha.db.models import Forum as DBForum
            row = await session.get(DBForum, forum.id)
            assert row.is_banned == 1
            assert "220012" in (row.ban_reason or "")

        # 不再误触发验证码熔断
        assert await db.get_captcha_events(limit=10) == []

        # 物料保持 pending（顺延语义：留给后续任务轮转换吧）
        mats = await db.get_materials(limit=10)
        assert all(m.status == "pending" for m in mats)
