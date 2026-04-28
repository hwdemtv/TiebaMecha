"""Tests for AI Optimizer functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json

from tieba_mecha.core.ai_optimizer import AIOptimizer, _encrypt_api_key, _decrypt_api_key, _URL_PATTERN


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


class TestApiKeyEncryption:
    """Tests for API Key encryption/decryption helpers."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test encrypt then decrypt returns original value."""
        original = "test-api-key-12345"
        encrypted = _encrypt_api_key(original)
        assert encrypted != original  # Should be encrypted
        decrypted = _decrypt_api_key(encrypted)
        assert decrypted == original

    def test_encrypt_empty_string(self):
        """Test encrypting empty string returns empty."""
        assert _encrypt_api_key("") == ""

    def test_decrypt_empty_string(self):
        """Test decrypting empty string returns empty."""
        assert _decrypt_api_key("") == ""

    def test_decrypt_plaintext_fallback(self):
        """Test decrypting non-encrypted text returns it unchanged."""
        plaintext = "just-a-plain-key"
        assert _decrypt_api_key(plaintext) == plaintext

    def test_decrypt_garbage_fallback(self):
        """Test decrypting garbage text returns it unchanged."""
        garbage = "not-valid-base64-or-fernet!!!"
        assert _decrypt_api_key(garbage) == garbage


class TestSEOOptimizations:
    """Tests for the 7 SEO optimizations."""

    # ── Optimization 4: New personas ──
    def test_tech_expert_persona_exists(self):
        """tech_expert persona should be available."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        assert "tech_expert" in optimizer.PERSONA_PROMPTS
        assert optimizer.PERSONA_PROMPTS["tech_expert"]["name"] == "技术老炮"

    def test_warm_netizen_persona_exists(self):
        """warm_netizen persona should be available."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        assert "warm_netizen" in optimizer.PERSONA_PROMPTS
        assert optimizer.PERSONA_PROMPTS["warm_netizen"]["name"] == "热心网友"

    def test_unknown_persona_falls_back_to_normal(self):
        """Unknown persona key should fall back to normal."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        p_config = optimizer.PERSONA_PROMPTS.get("nonexistent", optimizer.PERSONA_PROMPTS["normal"])
        assert p_config["name"] == "标准 SEO (默认)"

    # ── Optimization 6: Keyword Density ──
    def test_keyword_density_under_limit(self):
        """Keywords appearing <=3 times should not be modified."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        title = "Python自动化办公"
        content = "Python自动化办公真的很好用，推荐Python自动化给所有人。"
        new_title, new_content = optimizer._enforce_keyword_density(title, content, title)
        assert new_title == title
        assert new_content == content

    def test_keyword_density_over_limit(self):
        """Keywords appearing >3 times should be reduced."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        title = "Python自动化"
        content = (
            "Python自动化办公真的好用。\n\n"
            "Python自动化能节省时间。\n\n"
            "推荐Python自动化给所有人。\n\n"
            "Python自动化是未来趋势。"
        )
        new_title, new_content = optimizer._enforce_keyword_density(title, content, title)
        assert new_title == title
        # 第一段应完整保留
        assert "Python自动化办公真的好用。" in new_content

    def test_extract_keywords_basic(self):
        """Should extract meaningful keywords from title."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        keywords = optimizer._extract_keywords("Python自动化办公实战分享")
        assert "Python自动化办公实战分享" in keywords

    def test_extract_keywords_with_delimiters(self):
        """Should split on Chinese punctuation."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        keywords = optimizer._extract_keywords("Python自动化，办公实战！分享")
        assert "Python自动化" in keywords
        assert "办公实战" in keywords
        assert "分享" in keywords

    def test_extract_keywords_filters_stop_words(self):
        """Should filter out common stop words."""
        mock_db = MagicMock()
        optimizer = AIOptimizer(mock_db)
        keywords = optimizer._extract_keywords("的，是，在，和，有，也，都")
        assert len(keywords) == 0

    # ── Optimization 7: Link Protection ──
    def test_url_regex_pattern(self):
        """URL pattern should match common URLs."""
        urls = _URL_PATTERN.findall("访问 https://example.com/path 获取更多信息")
        assert "https://example.com/path" in urls

    def test_url_regex_multiple_urls(self):
        """Should extract multiple URLs."""
        text = "链接1: https://a.com/b 链接2: http://c.d/e"
        urls = _URL_PATTERN.findall(text)
        assert len(urls) == 2

    def test_url_regex_no_match_in_plain_text(self):
        """Should not match non-URL text."""
        urls = _URL_PATTERN.findall("这是一段普通文本，没有链接。")
        assert len(urls) == 0

    # ── Long-tail keyword generation ──
    def test_long_tail_keywords_short_title(self):
        """Short titles should get prefix + suffix combinations."""
        candidates = AIOptimizer._generate_long_tail_keywords("Python自动化")
        assert len(candidates) >= 1
        # Each candidate should contain the core word
        for c in candidates:
            assert "Python自动化" in c

    def test_long_tail_keywords_long_title(self):
        """Long titles (>8 chars) should only get suffix appended."""
        candidates = AIOptimizer._generate_long_tail_keywords("Python自动化办公脚本分享")
        assert len(candidates) >= 1
        for c in candidates:
            assert "Python自动化办公脚本分享" in c

    def test_long_tail_keywords_max_candidates(self):
        """Should respect max_candidates limit."""
        candidates = AIOptimizer._generate_long_tail_keywords("测试", max_candidates=2)
        assert len(candidates) <= 2

    def test_long_tail_keywords_no_empty(self):
        """Should not return empty strings."""
        candidates = AIOptimizer._generate_long_tail_keywords("AI工具")
        assert all(len(c) > 0 for c in candidates)

    # ── Optimization 5: Bump Content Candidates ──
    @pytest.mark.asyncio
    async def test_bump_content_parses_three_candidates(self):
        """generate_bump_content should pick from multiple candidates."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(side_effect=lambda k, d: {
            "ai_api_key": "test-key",
            "ai_base_url": "https://api.test.com/v1/",
            "ai_model": "test-model",
        }.get(k, d))

        optimizer = AIOptimizer(mock_db)

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": "看了，不错\nmark一下\n顶一个"
                }
            }]
        })

        mock_post_context = AsyncMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('tieba_mecha.core.ai_optimizer.require_pro', lambda f: f):
            with patch('aiohttp.ClientSession', return_value=mock_session):
                success, content, error = await optimizer.generate_bump_content(
                    "Test Title", "normal"
                )

        assert success is True
        assert content in ["看了，不错", "mark一下", "顶一个"]
        assert error == ""

    @pytest.mark.asyncio
    async def test_bump_content_single_line_fallback(self):
        """generate_bump_content should handle single-line response."""
        mock_db = MagicMock()
        mock_db.get_setting = AsyncMock(side_effect=lambda k, d: {
            "ai_api_key": "test-key",
            "ai_base_url": "https://api.test.com/v1/",
            "ai_model": "test-model",
        }.get(k, d))

        optimizer = AIOptimizer(mock_db)

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": "路过看看"
                }
            }]
        })

        mock_post_context = AsyncMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('tieba_mecha.core.ai_optimizer.require_pro', lambda f: f):
            with patch('aiohttp.ClientSession', return_value=mock_session):
                success, content, error = await optimizer.generate_bump_content(
                    "Test Title", "normal"
                )

        assert success is True
        assert content == "路过看看"
        assert error == ""
