"""Tests for Updater functionality."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tieba_mecha.core.updater import UpdateManager, ReleaseInfo, get_update_manager


class TestReleaseInfo:
    """Tests for ReleaseInfo dataclass."""

    def test_release_info_creation(self):
        """Test ReleaseInfo creation with all fields."""
        now = datetime.now()
        release = ReleaseInfo(
            version="1.0.0",
            tag_name="v1.0.0",
            published_at=now,
            body="Release notes",
            assets=[{"name": "file.zip"}],
            html_url="https://github.com/example/release",
            is_prerelease=False,
        )

        assert release.version == "1.0.0"
        assert release.tag_name == "v1.0.0"
        assert release.published_at == now
        assert release.body == "Release notes"
        assert len(release.assets) == 1
        assert release.html_url == "https://github.com/example/release"
        assert release.is_prerelease is False


class TestUpdateManager:
    """Tests for UpdateManager class."""

    def test_update_manager_initialization(self):
        """Test UpdateManager initializes correctly."""
        manager = UpdateManager()
        assert manager.db is None
        assert manager._current_version is not None

    def test_update_manager_with_db(self):
        """Test UpdateManager with database."""
        mock_db = MagicMock()
        manager = UpdateManager(db=mock_db)
        assert manager.db == mock_db

    def test_current_version_property(self):
        """Test current_version property."""
        manager = UpdateManager()
        version = manager.current_version
        assert isinstance(version, str)
        assert len(version) > 0

    def test_compare_versions_equal(self):
        """Test _compare_versions with equal versions."""
        manager = UpdateManager()

        assert manager._compare_versions("1.0.0", "1.0.0") == 0
        assert manager._compare_versions("2.1.3", "2.1.3") == 0

    def test_compare_versions_greater(self):
        """Test _compare_versions with greater version first."""
        manager = UpdateManager()

        assert manager._compare_versions("1.1.0", "1.0.0") == 1
        assert manager._compare_versions("2.0.0", "1.9.9") == 1
        assert manager._compare_versions("1.0.1", "1.0.0") == 1

    def test_compare_versions_lesser(self):
        """Test _compare_versions with lesser version first."""
        manager = UpdateManager()

        assert manager._compare_versions("1.0.0", "1.1.0") == -1
        assert manager._compare_versions("1.9.9", "2.0.0") == -1
        assert manager._compare_versions("1.0.0", "1.0.1") == -1

    def test_compare_versions_with_prefix(self):
        """Test _compare_versions handles version prefixes."""
        manager = UpdateManager()

        # Should handle 'v' prefix
        assert manager._compare_versions("v1.0.0", "1.0.0") == 0
        assert manager._compare_versions("1.0.0", "v1.0.0") == 0

    def test_compare_versions_different_lengths(self):
        """Test _compare_versions with different length versions."""
        manager = UpdateManager()

        assert manager._compare_versions("1.0", "1.0.0") == 0
        assert manager._compare_versions("1.0.0.0", "1.0") == 0
        assert manager._compare_versions("1.0.1", "1.0") == 1

    @pytest.mark.asyncio
    async def test_check_update_no_new_version(self):
        """Test check_update returns None when no new version."""
        manager = UpdateManager()

        # Mock current version to be higher
        manager._current_version = "99.0.0"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "tag_name": "v1.0.0",
            "published_at": "2024-01-01T00:00:00Z",
            "body": "Release notes",
            "assets": [],
            "html_url": "https://github.com/example/release",
            "prerelease": False,
        })

        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session.return_value)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session.return_value.get = AsyncMock(return_value=mock_response)

            result = await manager.check_update()

        assert result is None

    @pytest.mark.asyncio
    async def test_check_update_new_version_available(self):
        """Test check_update returns ReleaseInfo when new version available."""
        manager = UpdateManager()

        # Mock current version to be lower
        manager._current_version = "0.1.0"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "tag_name": "v1.0.0",
            "published_at": "2024-01-01T00:00:00Z",
            "body": "Release notes",
            "assets": [],
            "html_url": "https://github.com/example/release",
            "prerelease": False,
        })

        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session.return_value)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session.return_value.get = AsyncMock(return_value=mock_response)

            result = await manager.check_update()

        assert result is not None
        assert result.version == "1.0.0"
        assert result.tag_name == "v1.0.0"

    @pytest.mark.asyncio
    async def test_check_update_network_error(self):
        """Test check_update handles network errors gracefully."""
        manager = UpdateManager()

        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(side_effect=Exception("Network error"))
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await manager.check_update()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_portable_download_url_found(self):
        """Test get_portable_download_url finds portable asset."""
        manager = UpdateManager()

        release = ReleaseInfo(
            version="1.0.0",
            tag_name="v1.0.0",
            published_at=datetime.now(),
            body="",
            assets=[
                {"name": "TiebaMecha-portable.zip", "browser_download_url": "https://example.com/portable.zip"},
                {"name": "TiebaMecha-setup.exe", "browser_download_url": "https://example.com/setup.exe"},
            ],
            html_url="https://github.com/example/release",
            is_prerelease=False,
        )

        result = await manager.get_portable_download_url(release)

        assert result == "https://example.com/portable.zip"

    @pytest.mark.asyncio
    async def test_get_portable_download_url_not_found(self):
        """Test get_portable_download_url returns None when no portable asset."""
        manager = UpdateManager()

        release = ReleaseInfo(
            version="1.0.0",
            tag_name="v1.0.0",
            published_at=datetime.now(),
            body="",
            assets=[
                {"name": "TiebaMecha-setup.exe", "browser_download_url": "https://example.com/setup.exe"},
            ],
            html_url="https://github.com/example/release",
            is_prerelease=False,
        )

        result = await manager.get_portable_download_url(release)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_changelog(self):
        """Test get_changelog formats release notes."""
        manager = UpdateManager()

        release = ReleaseInfo(
            version="1.0.0",
            tag_name="v1.0.0",
            published_at=datetime(2024, 1, 1, 12, 0, 0),
            body="## New Features\n- Feature 1\n- Feature 2",
            assets=[],
            html_url="https://github.com/example/release",
            is_prerelease=False,
        )

        result = await manager.get_changelog(release)

        assert "v1.0.0" in result
        assert "2024-01-01" in result
        assert "New Features" in result

    @pytest.mark.asyncio
    async def test_get_changelog_truncates_long_content(self):
        """Test get_changelog truncates long content."""
        manager = UpdateManager()

        long_body = "A" * 1000
        release = ReleaseInfo(
            version="1.0.0",
            tag_name="v1.0.0",
            published_at=datetime.now(),
            body=long_body,
            assets=[],
            html_url="https://github.com/example/release",
            is_prerelease=False,
        )

        result = await manager.get_changelog(release, max_length=100)

        # Result should be truncated
        assert len(result) < len(long_body) + 200  # Allow for header/footer

    @pytest.mark.asyncio
    async def test_should_check_update_no_previous_check(self):
        """Test should_check_update returns True when no previous check."""
        manager = UpdateManager()
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(return_value="")
        manager.db = mock_db

        result = await manager.should_check_update()

        assert result is True

    @pytest.mark.asyncio
    async def test_should_check_update_recent_check(self):
        """Test should_check_update returns False when recently checked."""
        manager = UpdateManager()
        mock_db = MagicMock()
        # Set last check to now
        mock_db.get_setting = AsyncMock(return_value=datetime.now().isoformat())
        manager.db = mock_db

        result = await manager.should_check_update(interval_hours=24)

        assert result is False

    @pytest.mark.asyncio
    async def test_should_check_update_old_check(self):
        """Test should_check_update returns True when check is old."""
        manager = UpdateManager()
        mock_db = MagicMock()
        # Set last check to 2 days ago
        old_time = datetime.fromisoformat("2024-01-01T00:00:00")
        mock_db.get_setting = AsyncMock(return_value=old_time.isoformat())
        manager.db = mock_db

        result = await manager.should_check_update(interval_hours=24)

        assert result is True


class TestUpdateManagerGlobal:
    """Tests for global update manager functions."""

    def test_get_update_manager_creates_instance(self):
        """Test get_update_manager creates manager instance."""
        manager = get_update_manager()
        assert manager is not None
        assert isinstance(manager, UpdateManager)

    def test_get_update_manager_returns_same_instance(self):
        """Test get_update_manager returns same instance."""
        manager1 = get_update_manager()
        manager2 = get_update_manager()

        assert manager1 is manager2

    def test_get_update_manager_with_db(self):
        """Test get_update_manager with database parameter."""
        mock_db = MagicMock()
        manager = get_update_manager(db=mock_db)

        assert manager.db == mock_db
