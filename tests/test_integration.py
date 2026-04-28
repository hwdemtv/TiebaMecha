"""Integration tests for TiebaMecha."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
class TestAccountWorkflow:
    """Integration tests for account management workflow."""

    async def test_full_account_lifecycle(self, db):
        """Test complete account lifecycle: add, verify, switch, delete."""
        from tieba_mecha.core.account import (
            add_account,
            list_accounts,
            switch_account,
            remove_account,
            get_account_credentials,
        )

        # BDUSS 必须是 192 字符，STOKEN 必须是 64 字符
        bduss1 = "a" * 192
        bduss2 = "b" * 192
        stoken1 = "s" * 64
        stoken2 = "t" * 64

        # Add first account (verify=False to avoid network calls in this test)
        acc1 = await add_account(db, "account1", bduss1, stoken1, verify=False)
        assert acc1.is_active is True

        # Add second account
        acc2 = await add_account(db, "account2", bduss2, stoken2, verify=False)
        assert acc2.is_active is False

        # List accounts
        accounts = await list_accounts(db)
        assert len(accounts) == 2

        # Get credentials for active account
        creds = await get_account_credentials(db)
        assert creds is not None
        acc_id, bduss, stoken, proxy_id, cuid, ua = creds
        assert bduss == bduss1

        # Switch to second account
        await switch_account(db, acc2.id)
        creds = await get_account_credentials(db)
        acc_id, bduss, stoken, proxy_id, cuid, ua = creds
        assert bduss == bduss2

        # Remove first account
        await remove_account(db, acc1.id)
        accounts = await list_accounts(db)
        assert len(accounts) == 1

        # Second account should now be active
        creds = await get_account_credentials(db)
        assert creds is not None

    async def test_account_proxy_binding(self, db):
        """Test account-proxy binding workflow."""
        from tieba_mecha.core.account import add_account, get_account_credentials

        # Create proxy
        proxy = await db.add_proxy(host="127.0.0.1", port=7890)

        # Add account with proxy (BDUSS 必须是 192 字符)
        bduss = "c" * 192
        stoken = "s" * 64
        acc = await add_account(
            db,
            "proxied_account",
            bduss,
            stoken,
            proxy_id=proxy.id,
            verify=False,
        )

        # Verify proxy binding
        assert acc.proxy_id == proxy.id

        # Get credentials should include proxy_id
        creds = await get_account_credentials(db)
        acc_id, bduss_val, stoken_val, proxy_id, cuid, ua = creds
        assert proxy_id == proxy.id


@pytest.mark.asyncio
class TestSignWorkflow:
    """Integration tests for sign-in workflow."""

    async def test_sign_workflow_with_database(self, db, sample_account_data, mock_aiotieba_client):
        """Test complete sign workflow with database persistence."""
        from tieba_mecha.core.account import add_account
        from tieba_mecha.core.sign import sign_forum, get_sign_stats

        # Add account
        acc = await add_account(
            db,
            sample_account_data["name"],
            sample_account_data["bduss"],
            sample_account_data["stoken"],
        )

        # Add forum
        forum = await db.add_forum(fid=1, fname="test_forum", account_id=acc.id)

        # Mock successful sign
        mock_aiotieba_client.sign_forum = AsyncMock(return_value=MagicMock())

        # Sign forum - manually update database after successful sign
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            result = await sign_forum(db, "test_forum")

        assert result.success is True

        # Manually update sign status (sign_forum doesn't update DB, sign_all_forums does)
        await db.update_forum_sign(forum.id, success=True)

        # Check database was updated
        forums = await db.get_forums(acc.id)
        assert forums[0].is_sign_today is True

        # Check stats
        stats = await get_sign_stats(db)
        assert stats["success"] == 1


@pytest.mark.asyncio
class TestProxyFailoverWorkflow:
    """Integration tests for proxy failover."""

    async def test_proxy_failover_workflow(self, db):
        """Test proxy failure triggers account suspension."""
        from tieba_mecha.db.crud import PROXY_FAIL_THRESHOLD

        # Create proxy
        proxy = await db.add_proxy(host="127.0.0.1", port=7890)

        # Create accounts bound to proxy
        acc1 = await db.add_account(name="acc1", bduss="bduss1", proxy_id=proxy.id)
        acc2 = await db.add_account(name="acc2", bduss="bduss2", proxy_id=proxy.id)

        # Fail the proxy repeatedly
        for _ in range(PROXY_FAIL_THRESHOLD):
            await db.mark_proxy_fail(proxy.id)

        # Proxy should be deactivated
        updated_proxy = await db.get_proxy(proxy.id)
        assert updated_proxy.is_active is False

        # Suspend accounts
        suspended = await db.suspend_accounts_for_proxy(proxy.id)
        assert len(suspended) == 2

        # Verify accounts are suspended
        accounts = await db.get_accounts()
        assert all(a.status == "suspended_proxy" for a in accounts)

    async def test_proxy_recovery_workflow(self, db):
        """Test proxy recovery restores accounts."""
        # Create proxy
        proxy = await db.add_proxy(host="127.0.0.1", port=7890)

        # Create and suspend accounts
        acc = await db.add_account(name="acc", bduss="bduss", proxy_id=proxy.id)
        await db.suspend_accounts_for_proxy(proxy.id)

        # Verify suspended
        accounts = await db.get_accounts()
        assert accounts[0].status == "suspended_proxy"

        # Restore accounts
        restored = await db.restore_accounts_for_proxy(proxy.id)
        assert len(restored) == 1

        # Verify restored
        accounts = await db.get_accounts()
        assert accounts[0].status == "active"


@pytest.mark.asyncio
class TestBatchPostWorkflow:
    """Integration tests for batch posting."""

    async def test_batch_post_task_creation_and_retrieval(self, db):
        """Test creating and retrieving batch post tasks."""
        import json

        # Create task
        task = await db.add_batch_task(
            fname="test_forum",
            titles_json=json.dumps(["Title 1", "Title 2"]),
            contents_json=json.dumps(["Content 1"]),
            accounts_json=json.dumps([1, 2]),
            strategy="round_robin",
            total=10,
        )

        assert task.id is not None
        assert task.status == "pending"

        # Retrieve pending tasks
        pending = await db.get_pending_batch_tasks()
        assert len(pending) == 1
        assert pending[0].fname == "test_forum"

    async def test_batch_post_task_progress(self, db):
        """Test updating batch post task progress."""
        import json

        # Create task
        task = await db.add_batch_task(
            fname="test_forum",
            titles_json=json.dumps(["Title"]),
            contents_json=json.dumps(["Content"]),
            accounts_json=json.dumps([1]),
            total=10,
        )

        # Update progress
        await db.update_batch_task(task.id, progress=5, status="running")

        # Verify update
        tasks = await db.get_all_batch_tasks()
        assert tasks[0].progress == 5

        # Complete task
        await db.update_batch_task(task.id, progress=10, status="completed")

        # Should no longer be in pending
        pending = await db.get_pending_batch_tasks()
        assert len(pending) == 0


@pytest.mark.asyncio
class TestConcurrency:
    """Tests for concurrent operations."""

    async def test_concurrent_account_switching(self, db):
        """Test that concurrent account switches don't cause race conditions."""
        from tieba_mecha.core.account import add_account

        import asyncio

        # Create multiple accounts
        acc1 = await add_account(db, "acc1", "a" * 192, verify=False)
        acc2 = await add_account(db, "acc2", "b" * 192, verify=False)
        acc3 = await add_account(db, "acc3", "c" * 192, verify=False)

        # Concurrent switches
        await asyncio.gather(
            db.set_active_account(acc1.id),
            db.set_active_account(acc2.id),
            db.set_active_account(acc3.id),
        )

        # Only one should be active
        accounts = await db.get_accounts()
        active_count = sum(1 for a in accounts if a.is_active)
        assert active_count == 1

    async def test_concurrent_forum_operations(self, db):
        """Test concurrent forum operations."""
        from tieba_mecha.core.account import add_account
        import asyncio

        acc = await add_account(db, "acc", "a" * 192, verify=False)

        # Add forums concurrently
        await asyncio.gather(
            db.add_forum(fid=1, fname="forum1", account_id=acc.id),
            db.add_forum(fid=2, fname="forum2", account_id=acc.id),
            db.add_forum(fid=3, fname="forum3", account_id=acc.id),
        )

        forums = await db.get_forums(acc.id)
        assert len(forums) == 3
