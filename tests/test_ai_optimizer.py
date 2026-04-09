"""Tests for AI Optimizer functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json

from tieba_mecha.core.ai_optimizer import AIOptimizer


class TestAIOptimizer:
    """Tests for AIOptimizer class."""

    def test_ai_optimizer_initialization(self):
        """Test AIOptimizer initializes correctly."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        assert optimizer.db is not None

    @pytest.mark.asyncio
    async def test_get_config_default_values(self):
        """Test _get_config returns default values when not set."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(side_effect=lambda k, d: d)

        optimizer = AIOptimizer(mock_db)
        config = await optimizer._get_config()

        assert config["api_key"] == ""
        assert config["base_url"] == "https://open.bigmodel.cn/api/paas/v4/"
        assert config["model"] == "glm-4-flash"

    @pytest.mark.asyncio
    async def test_get_config_custom_values(self):
        """Test _get_config returns custom values from database."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(side_effect=lambda k, d: {
            "ai_api_key": "test-key-123",
            "ai_base_url": "https://api.deepseek.com/v1/",
            "ai_model": "deepseek-chat",
        }.get(k, d))

        optimizer = AIOptimizer(mock_db)
        config = await optimizer._get_config()

        assert config["api_key"] == "test-key-123"
        assert config["base_url"] == "https://api.deepseek.com/v1/"
        assert config["model"] == "deepseek-chat"

    @pytest.mark.asyncio
    async def test_optimize_post_no_api_key(self):
        """Test optimize_post returns error when API key not configured."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(return_value="")

        optimizer = AIOptimizer(mock_db)

        # Mock require_pro decorator to pass through
        with patch('tieba_mecha.core.ai_optimizer.require_pro', lambda f: f):
            success, title, content, error = await optimizer.optimize_post(
                "Test Title", "Test Content"
            )

        assert success is False
        assert title == "Test Title"
        assert content == "Test Content"
        assert "API Key" in error

    @pytest.mark.asyncio
    async def test_optimize_post_api_success(self):
        """Test optimize_post successfully processes API response."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(side_effect=lambda k, d: {
            "ai_api_key": "test-key",
            "ai_base_url": "https://api.test.com/v1/",
            "ai_model": "test-model",
            "ai_system_prompt": "",
        }.get(k, d))

        optimizer = AIOptimizer(mock_db)

        # Mock successful API response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "title": "Optimized Title",
                        "content": "Optimized content with natural language."
                    })
                }
            }]
        })

        # Create a proper async context manager mock
        mock_post_context = AsyncMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('tieba_mecha.core.ai_optimizer.require_pro', lambda f: f):
            with patch('aiohttp.ClientSession', return_value=mock_session):
                success, title, content, error = await optimizer.optimize_post(
                    "Original Title", "Original Content"
                )

        assert success is True
        assert title == "Optimized Title"
        assert "Optimized content" in content
        assert error == ""

    @pytest.mark.asyncio
    async def test_optimize_post_api_error(self):
        """Test optimize_post handles API errors gracefully."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(side_effect=lambda k, d: {
            "ai_api_key": "test-key",
            "ai_base_url": "https://api.test.com/v1/",
            "ai_model": "test-model",
            "ai_system_prompt": "",
        }.get(k, d))

        optimizer = AIOptimizer(mock_db)

        # Mock failed API response
        mock_response = MagicMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value="Unauthorized")

        # Create a proper async context manager mock
        mock_post_context = AsyncMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('tieba_mecha.core.ai_optimizer.require_pro', lambda f: f):
            with patch('aiohttp.ClientSession', return_value=mock_session):
                success, title, content, error = await optimizer.optimize_post(
                    "Original Title", "Original Content"
                )

        assert success is False
        assert title == "Original Title"
        assert content == "Original Content"
        assert "401" in error

    @pytest.mark.asyncio
    async def test_optimize_post_malformed_json(self):
        """Test optimize_post handles malformed JSON response."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(side_effect=lambda k, d: {
            "ai_api_key": "test-key",
            "ai_base_url": "https://api.test.com/v1/",
            "ai_model": "test-model",
            "ai_system_prompt": "",
        }.get(k, d))

        optimizer = AIOptimizer(mock_db)

        # Mock response with malformed JSON but extractable content
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": 'Here is the result: {"title": "Extracted Title", "content": "Extracted content."}'
                }
            }]
        })

        # Create a proper async context manager mock
        mock_post_context = AsyncMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('tieba_mecha.core.ai_optimizer.require_pro', lambda f: f):
            with patch('aiohttp.ClientSession', return_value=mock_session):
                success, title, content, error = await optimizer.optimize_post(
                    "Original Title", "Original Content"
                )

        # Should still succeed with extracted content
        assert success is True
        assert title == "Extracted Title"

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        """Test test_connection returns success on valid API response."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(side_effect=lambda k, d: {
            "ai_api_key": "test-key",
            "ai_base_url": "https://api.test.com/v1/",
            "ai_model": "test-model",
        }.get(k, d))

        optimizer = AIOptimizer(mock_db)

        mock_response = MagicMock()
        mock_response.status = 200

        # Create a proper async context manager mock
        mock_post_context = AsyncMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            success, message = await optimizer.test_connection()

        assert success is True
        assert "成功" in message

    @pytest.mark.asyncio
    async def test_test_connection_no_api_key(self):
        """Test test_connection fails when no API key configured."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(return_value="")

        optimizer = AIOptimizer(mock_db)
        success, message = await optimizer.test_connection()

        assert success is False
        assert "API Key" in message
