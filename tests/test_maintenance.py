"""Tests for Maintenance (BioWarming) functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tieba_mecha.core.maintenance import MaintManager, do_warming


class TestMaintManager:
    """Tests for MaintManager class."""

    def test_maint_manager_initialization(self):
        """Test MaintManager initializes correctly."""
        mock_db = MagicMock()
        manager = MaintManager(mock_db)
        assert manager.db == mock_db

    @pytest.mark.asyncio
    async def test_run_maint_cycle_no_credentials(self):
        """Test run_maint_cycle handles missing credentials."""
        mock_db = MagicMock()
        manager = MaintManager(mock_db)

        with patch('tieba_mecha.core.maintenance.get_account_credentials', AsyncMock(return_value=None)):
            result = await manager.run_maint_cycle(account_id=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_run_maint_cycle_success(self):
        """Test run_maint_cycle executes successfully."""
        mock_db = MagicMock()
        mock_db.update_maint_status = AsyncMock()
        manager = MaintManager(mock_db)

        # Mock credentials
        mock_creds = (1, "test_bduss", "test_stoken", None, "test_cuid", "test_ua")

        # Mock client
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_self_info = AsyncMock(return_value=MagicMock(user_id=12345))
        mock_client.get_follow_forums = AsyncMock(return_value=MagicMock(objs=[
            MagicMock(fname="test_forum")
        ]))
        mock_client.get_threads = AsyncMock(return_value=MagicMock(objs=[
            MagicMock(tid=123456, title="Test Thread Title")
        ]))
        mock_client.get_posts = AsyncMock()
        mock_client.agree = AsyncMock()

        with patch('tieba_mecha.core.maintenance.get_account_credentials', AsyncMock(return_value=mock_creds)):
            with patch('tieba_mecha.core.maintenance.create_client', AsyncMock(return_value=mock_client)):
                result = await manager.run_maint_cycle(account_id=1)

        assert result is True
        mock_db.update_maint_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_maint_cycle_empty_forum_list(self):
        """Test run_maint_cycle handles empty forum list."""
        mock_db = MagicMock()
        manager = MaintManager(mock_db)

        mock_creds = (1, "test_bduss", "test_stoken", None, "test_cuid", "test_ua")

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_self_info = AsyncMock(return_value=MagicMock(user_id=12345))
        mock_client.get_follow_forums = AsyncMock(return_value=MagicMock(objs=[]))

        with patch('tieba_mecha.core.maintenance.get_account_credentials', AsyncMock(return_value=mock_creds)):
            with patch('tieba_mecha.core.maintenance.create_client', AsyncMock(return_value=mock_client)):
                result = await manager.run_maint_cycle(account_id=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_run_maint_cycle_exception_handling(self):
        """Test run_maint_cycle handles exceptions gracefully."""
        mock_db = MagicMock()
        manager = MaintManager(mock_db)

        with patch('tieba_mecha.core.maintenance.get_account_credentials', AsyncMock(side_effect=Exception("Test error"))):
            result = await manager.run_maint_cycle(account_id=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_human_sleep(self):
        """Test _human_sleep adds random delay."""
        import time

        mock_db = MagicMock()
        manager = MaintManager(mock_db)

        start = time.time()
        await manager._human_sleep(0.1, 0.2)
        elapsed = time.time() - start

        # Should have slept between 0.1 and 0.2 seconds
        assert 0.08 <= elapsed <= 0.3  # Allow some margin


class TestDoWarming:
    """Tests for do_warming convenience function."""

    @pytest.mark.asyncio
    async def test_do_warming_calls_manager(self):
        """Test do_warming calls MaintManager.run_maint_cycle."""
        mock_db = MagicMock()

        with patch('tieba_mecha.core.maintenance.MaintManager.run_maint_cycle', AsyncMock(return_value=True)) as mock_run:
            result = await do_warming(mock_db, account_id=1)

        assert result is True
        mock_run.assert_called_once_with(1)
