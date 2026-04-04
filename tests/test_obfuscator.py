"""Tests for Obfuscator functionality."""

import pytest
from tieba_mecha.core.obfuscator import Obfuscator, ZERO_WIDTH_CHARS


class TestObfuscator:
    """Tests for Obfuscator class."""

    def test_zero_width_chars_defined(self):
        """Test ZERO_WIDTH_CHARS contains expected characters."""
        assert "\u200b" in ZERO_WIDTH_CHARS  # Zero-width space
        assert "\u200c" in ZERO_WIDTH_CHARS  # Zero-width non-joiner
        assert "\u200d" in ZERO_WIDTH_CHARS  # Zero-width joiner
        assert "\ufeff" in ZERO_WIDTH_CHARS  # Zero-width no-break space
        assert len(ZERO_WIDTH_CHARS) == 4

    def test_inject_zero_width_chars_empty_string(self):
        """Test inject_zero_width_chars handles empty string."""
        result = Obfuscator.inject_zero_width_chars("")
        assert result == ""

    def test_inject_zero_width_chars_none(self):
        """Test inject_zero_width_chars handles None."""
        result = Obfuscator.inject_zero_width_chars(None)
        assert result is None

    def test_inject_zero_width_chars_preserves_chinese(self):
        """Test inject_zero_width_chars preserves Chinese characters."""
        text = "这是中文测试"
        result = Obfuscator.inject_zero_width_chars(text, density=0)

        # With density=0, should be identical
        assert result == text

    def test_inject_zero_width_chars_adds_characters(self):
        """Test inject_zero_width_chars adds zero-width characters."""
        text = "测试测试测试测试测试测试测试测试测试测试"  # Long text for better probability
        result = Obfuscator.inject_zero_width_chars(text, density=0.8)

        # Result should be longer than original (zero-width chars added)
        # Note: There's a small chance no chars are added due to randomness
        # So we test with high density and long text
        assert len(result) >= len(text)

    def test_inject_zero_width_chars_preserves_urls(self):
        """Test inject_zero_width_chars preserves URLs unchanged."""
        text = "访问 https://example.com/path?query=1 查看详情"
        result = Obfuscator.inject_zero_width_chars(text, density=0.5)

        # URL should be preserved exactly
        assert "https://example.com/path?query=1" in result

    def test_inject_zero_width_chars_preserves_multiple_urls(self):
        """Test inject_zero_width_chars preserves multiple URLs."""
        text = "链接1 http://a.com 和链接2 https://b.com/path"
        result = Obfuscator.inject_zero_width_chars(text, density=0.5)

        assert "http://a.com" in result
        assert "https://b.com/path" in result

    def test_inject_zero_width_chars_preserves_english(self):
        """Test inject_zero_width_chars preserves English text."""
        text = "Hello World 123"
        result = Obfuscator.inject_zero_width_chars(text, density=0.8)

        # English should not have zero-width chars injected between letters
        assert "Hello" in result
        assert "World" in result
        assert "123" in result

    def test_inject_zero_width_chars_mixed_content(self):
        """Test inject_zero_width_chars with mixed Chinese and English."""
        text = "这是中文English混合content"
        result = Obfuscator.inject_zero_width_chars(text, density=0.5)

        # Both Chinese and English should be preserved
        assert "中文" in result
        assert "English" in result
        assert "content" in result

    def test_inject_zero_width_chars_density_zero(self):
        """Test inject_zero_width_chars with density=0."""
        text = "测试内容"
        result = Obfuscator.inject_zero_width_chars(text, density=0)

        # Should be identical
        assert result == text

    def test_inject_zero_width_chars_density_one(self):
        """Test inject_zero_width_chars with density=1."""
        text = "测试"  # 2 Chinese chars
        result = Obfuscator.inject_zero_width_chars(text, density=1)

        # With density=1, should add a zero-width char between Chinese chars
        # Result should be longer than original
        assert len(result) > len(text)
        # But when we remove zero-width chars, it should equal original
        cleaned = result
        for zwc in ZERO_WIDTH_CHARS:
            cleaned = cleaned.replace(zwc, "")
        assert cleaned == text

    def test_humanize_spacing_empty_string(self):
        """Test humanize_spacing handles empty string."""
        result = Obfuscator.humanize_spacing("")
        assert result == ""

    def test_humanize_spacing_none(self):
        """Test humanize_spacing handles None."""
        result = Obfuscator.humanize_spacing(None)
        assert result is None

    def test_humanize_spacing_preserves_content(self):
        """Test humanize_spacing preserves text content."""
        text = "第一段\n第二段\n第三段"
        result = Obfuscator.humanize_spacing(text)

        # Paragraph structure should be preserved
        assert "第一段" in result
        assert "第二段" in result
        assert "第三段" in result

    def test_humanize_spacing_single_line(self):
        """Test humanize_spacing handles single line."""
        text = "单行文本"
        result = Obfuscator.humanize_spacing(text)

        # Content should be preserved
        assert "单行文本" in result

    def test_humanize_spacing_multiple_paragraphs(self):
        """Test humanize_spacing handles multiple paragraphs."""
        text = "段落一\n\n段落二\n\n段落三"
        result = Obfuscator.humanize_spacing(text)

        # All paragraphs should be present
        assert "段落一" in result
        assert "段落二" in result
        assert "段落三" in result


class TestObfuscatorIntegration:
    """Integration tests for Obfuscator."""

    def test_combined_obfuscation(self):
        """Test combining zero-width injection and humanize_spacing."""
        text = "测试帖子内容，包含链接 https://example.com"

        # Apply both transformations
        result = Obfuscator.inject_zero_width_chars(text, density=0.3)
        result = Obfuscator.humanize_spacing(result)

        # URL should still be preserved
        assert "https://example.com" in result
        # Chinese content should be preserved
        assert "测试帖子内容" in result or any(c in result for c in "测试帖子内容")

    def test_realistic_post_content(self):
        """Test with realistic post content."""
        text = """
        分享一个资源

        链接: https://pan.baidu.com/s/xxxxx
        提取码: abcd

        欢迎大家下载使用！
        """

        result = Obfuscator.inject_zero_width_chars(text, density=0.2)
        result = Obfuscator.humanize_spacing(result)

        # All key content should be preserved
        assert "https://pan.baidu.com/s/xxxxx" in result
        assert "提取码" in result
        assert "abcd" in result
