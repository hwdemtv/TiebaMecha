"""Tests for sign-in functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import asdict

from tieba_mecha.core.sign import SignResult, ForumInfo


class TestSignResult:
    """Tests for SignResult dataclass."""

    def test_sign_result_creation(self):
        """Test SignResult creation with all fields."""
        result = SignResult(
            fname="test_forum",
            success=True,
            message="签到成功",
            sign_count=5,
        )

        assert result.fname == "test_forum"
        assert result.success is True
        assert result.message == "签到成功"
        assert result.sign_count == 5

    def test_sign_result_defaults(self):
        """Test SignResult default values."""
        result = SignResult(fname="test", success=False, message="failed")

        assert result.sign_count == 0

    def test_sign_result_failure(self):
        """Test creating a failure SignResult."""
        result = SignResult(
            fname="forum",
            success=False,
            message="已签到过",
        )

        assert result.success is False
        assert "已签到过" in result.message


class TestForumInfo:
    """Tests for ForumInfo dataclass."""

    def test_forum_info_creation(self):
        """Test ForumInfo creation."""
        info = ForumInfo(
            fid=12345,
            fname="test_forum",
            is_sign_today=True,
            sign_count=10,
        )

        assert info.fid == 12345
        assert info.fname == "test_forum"
        assert info.is_sign_today is True
        assert info.sign_count == 10

    def test_forum_info_defaults(self):
        """Test ForumInfo with minimal args."""
        info = ForumInfo(
            fid=1,
            fname="forum",
            is_sign_today=False,
            sign_count=0,
        )

        assert info.fid == 1
        assert info.is_sign_today is False
        assert info.sign_count == 0


@pytest.mark.asyncio
class TestSignForum:
    """Tests for sign_forum function."""

    async def test_sign_forum_no_credentials(self, db):
        """Test sign_forum returns failure when no credentials."""
        from tieba_mecha.core.sign import sign_forum

        result = await sign_forum(db, "test_forum")

        assert result.success is False
        assert "未找到账号凭证" in result.message

    async def test_sign_forum_success(self, db, sample_account_data, mock_aiotieba_client):
        """Test successful sign_forum."""
        from tieba_mecha.core.sign import sign_forum
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Mock the client to return success
        mock_aiotieba_client.sign_forum = AsyncMock(return_value=MagicMock())

        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            result = await sign_forum(db, "test_forum")

        assert result.success is True
        assert result.message == "签到成功"

    async def test_sign_forum_failure(self, db, sample_account_data, mock_aiotieba_client):
        """Test sign_forum when API returns failure."""
        from tieba_mecha.core.sign import sign_forum
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Mock the client to return None (failure)
        mock_aiotieba_client.sign_forum = AsyncMock(return_value=None)

        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            result = await sign_forum(db, "test_forum")

        assert result.success is False


@pytest.mark.asyncio
class TestGetSignStats:
    """Tests for get_sign_stats function."""

    async def test_get_sign_stats_no_account(self, db):
        """Test get_sign_stats with no account."""
        from tieba_mecha.core.sign import get_sign_stats

        stats = await get_sign_stats(db)

        assert stats["total"] == 0
        assert stats["success"] == 0
        assert stats["failure"] == 0

    async def test_get_sign_stats_no_forums(self, db, sample_account_data):
        """Test get_sign_stats with account but no forums."""
        from tieba_mecha.core.sign import get_sign_stats
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        stats = await get_sign_stats(db)

        assert stats["total"] == 0
        assert stats["success"] == 0

    async def test_get_sign_stats_with_forums(self, db, sample_account_data):
        """Test get_sign_stats with forums."""
        from tieba_mecha.core.sign import get_sign_stats
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Add forums
        forum1 = await db.add_forum(fid=1, fname="forum1", account_id=acc.id)
        forum2 = await db.add_forum(fid=2, fname="forum2", account_id=acc.id)

        # Mark one as signed
        await db.update_forum_sign(forum1.id, success=True)

        stats = await get_sign_stats(db)

        assert stats["total"] == 2
        assert stats["success"] == 1
        assert stats["failure"] == 0


@pytest.mark.asyncio
class TestSyncForumsToDB:
    """Tests for sync_forums_to_db function."""

    async def test_sync_forums_no_account(self, db):
        """Test sync_forums_to_db with no account."""
        from tieba_mecha.core.sign import sync_forums_to_db

        count = await sync_forums_to_db(db)
        assert count == 0

    async def test_sync_forums_adds_new(self, db, sample_account_data, mock_aiotieba_client):
        """Test sync_forums_to_db adds new forums."""
        from tieba_mecha.core.sign import sync_forums_to_db, get_follow_forums
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Mock user info
        mock_user_info = MagicMock()
        mock_user_info.user_id = 12345
        mock_aiotieba_client.get_self_info = AsyncMock(return_value=mock_user_info)

        # Mock follow forums
        mock_forum = MagicMock()
        mock_forum.fid = 12345
        mock_forum.fname = "test_forum"
        mock_aiotieba_client.get_follow_forums = AsyncMock(return_value=[mock_forum])

        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("tieba_mecha.core.sign.get_follow_forums", wraps=get_follow_forums):
                count = await sync_forums_to_db(db)

        assert count == 1


@pytest.mark.asyncio
class TestSignAllForums:
    """Tests for sign_all_forums function."""

    async def test_sign_all_forums_no_account(self, db):
        """Test sign_all_forums with no account."""
        from tieba_mecha.core.sign import sign_all_forums

        results = []
        async for result in sign_all_forums(db):
            results.append(result)

        assert len(results) == 1
        assert results[0].success is False
        assert "未找到活跃账号" in results[0].message

    async def test_sign_all_forums_no_forums(self, db, sample_account_data, mock_aiotieba_client):
        """Test sign_all_forums with account but no forums."""
        from tieba_mecha.core.sign import sign_all_forums
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            async for result in sign_all_forums(db, delay_min=0, delay_max=0):
                results.append(result)

        # Should complete without error even with no forums
        assert len(results) == 0

    async def test_sign_all_forums_success(self, db, sample_account_data, mock_aiotieba_client):
        """Test sign_all_forums successful execution."""
        from tieba_mecha.core.sign import sign_all_forums
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Add a forum
        await db.add_forum(fid=1, fname="test_forum", account_id=acc.id)

        # Mock sign_forum to succeed
        mock_aiotieba_client.sign_forum = AsyncMock(return_value=MagicMock())

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for result in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(result)

        assert len(results) == 1
        assert results[0].success is True


@pytest.mark.asyncio
class TestSignAllAccounts:
    """Tests for sign_all_accounts matrix function."""

    async def test_sign_all_accounts_no_accounts(self, db):
        """Test sign_all_accounts with no accounts."""
        from tieba_mecha.core.sign import sign_all_accounts

        results = []
        async for result in sign_all_accounts(db):
            results.append(result)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "无可用账号" in results[0]["message"]

    async def test_sign_all_accounts_skips_suspended(self, db, sample_account_data):
        """Test sign_all_accounts skips suspended accounts."""
        from tieba_mecha.core.sign import sign_all_accounts
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Suspend the account
        await db.update_account(acc.id, status="suspended_proxy")

        results = []
        async for result in sign_all_accounts(db):
            results.append(result)

        # Should report no available accounts
        assert len(results) == 1
        assert results[0]["success"] is False

    async def test_sign_all_accounts_proxy_status(self, db, sample_account_data, mock_aiotieba_client):
        """Test sign_all_accounts reports proxy status correctly."""
        from tieba_mecha.core.sign import sign_all_accounts
        from tieba_mecha.core.account import add_account

        # Create account without proxy
        acc = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Add forum
        await db.add_forum(fid=1, fname="test_forum", account_id=acc.id)

        mock_aiotieba_client.sign_forum = AsyncMock(return_value=MagicMock())

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for result in sign_all_accounts(db, delay_min=0, delay_max=0):
                    results.append(result)

        # Should warn about missing proxy
        assert len(results) >= 1
        assert results[0]["proxy_status"] == "missing"
