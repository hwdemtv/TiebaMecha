"""Tests for Notification Manager functionality."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tieba_mecha.core.notification import (
    NotificationManager,
    NotificationData,
    NotificationType,
    init_notification_manager,
    get_notification_manager,
)


class TestNotificationType:
    """Tests for NotificationType enum."""

    def test_notification_type_values(self):
        """Test all notification type values exist."""
        assert NotificationType.POST_SUCCESS.value == "post_success"
        assert NotificationType.POST_FAILED.value == "post_failed"
        assert NotificationType.BATCH_COMPLETE.value == "batch_complete"
        assert NotificationType.ACCOUNT_EXPIRED.value == "account_expired"
        assert NotificationType.UPDATE_AVAILABLE.value == "update_available"
        assert NotificationType.PROXY_FAILED.value == "proxy_failed"


class TestNotificationData:
    """Tests for NotificationData dataclass."""

    def test_notification_data_defaults(self):
        """Test NotificationData default values."""
        data = NotificationData()

        assert data.id is None
        assert data.type == "system_alert"
        assert data.title == ""
        assert data.message == ""
        assert data.is_read is False
        assert data.action_url is None
        assert data.extra == {}
        assert data.source == "local"
        assert data.created_at is not None

    def test_notification_data_custom_values(self):
        """Test NotificationData with custom values."""
        now = datetime.now()
        data = NotificationData(
            id=1,
            type="post_success",
            title="Test Title",
            message="Test Message",
            is_read=True,
            action_url="https://example.com",
            extra={"key": "value"},
            source="remote",
            remote_id="remote-123",
            created_at=now,
        )

        assert data.id == 1
        assert data.type == "post_success"
        assert data.title == "Test Title"
        assert data.message == "Test Message"
        assert data.is_read is True
        assert data.action_url == "https://example.com"
        assert data.extra == {"key": "value"}
        assert data.source == "remote"
        assert data.remote_id == "remote-123"
        assert data.created_at == now


class TestNotificationManager:
    """Tests for NotificationManager class."""

    def test_notification_manager_singleton(self):
        """Test NotificationManager is a singleton."""
        manager1 = NotificationManager()
        manager2 = NotificationManager()

        assert manager1 is manager2

    def test_notification_manager_set_db(self):
        """Test set_db method."""
        manager = NotificationManager()
        mock_db = MagicMock()
        manager.set_db(mock_db)

        assert manager.db == mock_db

    def test_notification_manager_set_page(self):
        """Test set_page method."""
        manager = NotificationManager()
        mock_page = MagicMock()
        manager.set_page(mock_page)

        assert manager.page == mock_page

    def test_notification_manager_add_listener(self):
        """Test add_listener method."""
        manager = NotificationManager()
        callback = MagicMock()
        manager.add_listener(callback)

        assert callback in manager._listeners

    def test_notification_manager_remove_listener(self):
        """Test remove_listener method."""
        manager = NotificationManager()
        callback = MagicMock()
        manager.add_listener(callback)
        manager.remove_listener(callback)

        assert callback not in manager._listeners

    @pytest.mark.asyncio
    async def test_push_without_db(self):
        """Test push does nothing when db is not set."""
        manager = NotificationManager()
        manager.db = None

        # Should not raise
        await manager.push(NotificationType.POST_SUCCESS, "Title", "Message")

    @pytest.mark.asyncio
    async def test_push_with_db(self):
        """Test push stores notification in database."""
        manager = NotificationManager()
        mock_db = MagicMock()
        mock_db.add_notification = AsyncMock(return_value=MagicMock(id=1))
        manager.db = mock_db

        await manager.push(
            NotificationType.POST_SUCCESS,
            "Test Title",
            "Test Message",
            action_url="https://example.com",
            extra={"key": "value"},
        )

        mock_db.add_notification.assert_called_once()
        call_args = mock_db.add_notification.call_args
        assert call_args.kwargs["type"] == "post_success"
        assert call_args.kwargs["title"] == "Test Title"
        assert call_args.kwargs["message"] == "Test Message"
        assert call_args.kwargs["action_url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_push_triggers_listeners(self):
        """Test push triggers registered listeners."""
        manager = NotificationManager()
        mock_db = MagicMock()
        mock_db.add_notification = AsyncMock(return_value=MagicMock(id=1))
        manager.db = mock_db

        listener_called = []
        async def test_listener(notification):
            listener_called.append(notification)

        manager.add_listener(test_listener)
        await manager.push(NotificationType.POST_SUCCESS, "Title", "Message")

        assert len(listener_called) == 1

    @pytest.mark.asyncio
    async def test_get_unread_without_db(self):
        """Test get_unread returns empty list when db is not set."""
        manager = NotificationManager()
        manager.db = None

        result = await manager.get_unread()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_unread_with_db(self):
        """Test get_unread fetches from database."""
        manager = NotificationManager()
        mock_db = MagicMock()
        mock_notification = MagicMock()
        mock_notification.id = 1
        mock_notification.type = "post_success"
        mock_notification.title = "Test"
        mock_notification.message = "Message"
        mock_notification.is_read = False
        mock_notification.action_url = None
        mock_notification.extra_json = "{}"
        mock_notification.source = "local"
        mock_notification.remote_id = None
        mock_notification.created_at = datetime.now()

        mock_db.get_unread_notifications = AsyncMock(return_value=[mock_notification])
        manager.db = mock_db

        result = await manager.get_unread()

        assert len(result) == 1
        assert result[0].id == 1
        assert result[0].type == "post_success"

    @pytest.mark.asyncio
    async def test_mark_read(self):
        """Test mark_read updates notification status."""
        manager = NotificationManager()
        mock_db = MagicMock()
        mock_db.mark_notification_read = AsyncMock(return_value=True)
        manager.db = mock_db

        result = await manager.mark_read(1)

        assert result is True
        mock_db.mark_notification_read.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_mark_all_read(self):
        """Test mark_all_read updates all notifications."""
        manager = NotificationManager()
        mock_db = MagicMock()
        mock_db.mark_all_notifications_read = AsyncMock(return_value=5)
        manager.db = mock_db

        result = await manager.mark_all_read()

        assert result == 5

    @pytest.mark.asyncio
    async def test_get_unread_count(self):
        """Test get_unread_count returns correct count."""
        manager = NotificationManager()
        mock_db = MagicMock()
        mock_db.get_unread_count = AsyncMock(return_value=3)
        manager.db = mock_db

        result = await manager.get_unread_count()

        assert result == 3


class TestNotificationManagerGlobal:
    """Tests for global notification manager functions."""

    def test_init_notification_manager(self):
        """Test init_notification_manager creates manager instance."""
        manager = init_notification_manager()
        assert manager is not None
        assert isinstance(manager, NotificationManager)

    def test_get_notification_manager(self):
        """Test get_notification_manager returns manager instance."""
        manager = get_notification_manager()
        assert manager is not None
