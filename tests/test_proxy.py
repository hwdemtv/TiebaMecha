"""Tests for proxy management functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aiotieba.config import ProxyConfig


@pytest.mark.asyncio
class TestProxyManagement:
    """Tests for proxy management functions."""

    async def test_get_best_proxy_config_no_proxy(self, db):
        """Test get_best_proxy_config returns None when no proxy specified and no proxies exist."""
        from tieba_mecha.core.proxy import get_best_proxy_config

        result = await get_best_proxy_config(db, proxy_id=None)
        assert result is None

    async def test_get_best_proxy_config_with_proxy_id(self, db):
        """Test get_best_proxy_config returns config for specified proxy."""
        from tieba_mecha.core.proxy import get_best_proxy_config

        proxy = await db.add_proxy(host="127.0.0.1", port=7890)

        result = await get_best_proxy_config(db, proxy_id=proxy.id)

        assert result is not None
        assert isinstance(result, ProxyConfig)
        assert "127.0.0.1:7890" in str(result.url)

    async def test_get_best_proxy_config_invalid_proxy_id(self, db):
        """Test get_best_proxy_config returns None for non-existent proxy."""
        from tieba_mecha.core.proxy import get_best_proxy_config

        result = await get_best_proxy_config(db, proxy_id=999)
        assert result is None

    async def test_get_best_proxy_config_fallback(self, db):
        """Test get_best_proxy_config falls back to another proxy."""
        from tieba_mecha.core.proxy import get_best_proxy_config

        # Create a proxy but don't specify ID
        await db.add_proxy(host="127.0.0.1", port=7890)

        result = await get_best_proxy_config(db, proxy_id=None)

        # Should return the available proxy
        assert result is not None

    async def test_get_best_proxy_config_inactive_proxy_fallback(self, db):
        """Test get_best_proxy_config falls back when specified proxy is inactive."""
        from tieba_mecha.core.proxy import get_best_proxy_config

        # Create inactive proxy
        proxy_inactive = await db.add_proxy(host="127.0.0.1", port=7890)
        await db.mark_proxy_fail(proxy_inactive.id)
        for _ in range(9):  # Reach threshold
            await db.mark_proxy_fail(proxy_inactive.id)

        # Create active proxy for fallback
        await db.add_proxy(host="127.0.0.2", port=7891)

        result = await get_best_proxy_config(db, proxy_id=proxy_inactive.id)

        # Should fall back to active proxy
        assert result is not None
        assert "127.0.0.2:7891" in str(result.url)


class TestBuildProxyConfig:
    """Tests for _build_proxy_config function."""

    def test_build_proxy_config_no_auth(self):
        """Test building proxy config without authentication."""
        from tieba_mecha.core.proxy import _build_proxy_config

        mock_proxy = MagicMock()
        mock_proxy.protocol = "http"
        mock_proxy.host = "127.0.0.1"
        mock_proxy.port = 7890
        mock_proxy.username = ""
        mock_proxy.password = ""

        result = _build_proxy_config(mock_proxy)

        assert str(result.url) == "http://127.0.0.1:7890"
        assert result.auth is None

    def test_build_proxy_config_with_auth(self):
        """Test building proxy config with authentication."""
        from tieba_mecha.core.proxy import _build_proxy_config
        from tieba_mecha.core.account import encrypt_value

        mock_proxy = MagicMock()
        mock_proxy.protocol = "http"
        mock_proxy.host = "127.0.0.1"
        mock_proxy.port = 7890
        mock_proxy.username = encrypt_value("testuser")
        mock_proxy.password = encrypt_value("testpass")

        result = _build_proxy_config(mock_proxy)

        assert str(result.url) == "http://127.0.0.1:7890"
        assert result.auth is not None
        assert result.auth.login == "testuser"
        assert result.auth.password == "testpass"

    def test_build_proxy_config_socks(self):
        """Test building proxy config for SOCKS protocol."""
        from tieba_mecha.core.proxy import _build_proxy_config

        mock_proxy = MagicMock()
        mock_proxy.protocol = "socks5"
        mock_proxy.host = "127.0.0.1"
        mock_proxy.port = 1080
        mock_proxy.username = ""
        mock_proxy.password = ""

        result = _build_proxy_config(mock_proxy)

        assert str(result.url) == "socks5://127.0.0.1:1080"


@pytest.mark.asyncio
class TestMarkProxyFailure:
    """Tests for mark_proxy_failure function."""

    async def test_mark_proxy_failure(self, db):
        """Test marking proxy failure."""
        from tieba_mecha.core.proxy import mark_proxy_failure

        proxy = await db.add_proxy(host="127.0.0.1", port=7890)

        await mark_proxy_failure(db, "http://127.0.0.1:7890")

        updated = await db.get_proxy(proxy.id)
        assert updated.fail_count == 1

    async def test_mark_proxy_failure_invalid_url(self, db):
        """Test marking proxy failure with invalid URL."""
        from tieba_mecha.core.proxy import mark_proxy_failure

        # Should not raise error
        await mark_proxy_failure(db, "invalid_url")


class TestProxyConfig:
    """Tests for ProxyConfig from aiotieba."""

    def test_proxy_config_creation(self):
        """Test creating a ProxyConfig."""
        config = ProxyConfig(url="http://127.0.0.1:7890")

        assert str(config.url) == "http://127.0.0.1:7890"

    def test_proxy_config_with_auth(self):
        """Test creating a ProxyConfig with auth."""
        from aiohttp import BasicAuth

        auth = BasicAuth("user", "pass")
        config = ProxyConfig(url="http://127.0.0.1:7890", auth=auth)

        assert config.auth is not None
        assert config.auth.login == "user"
