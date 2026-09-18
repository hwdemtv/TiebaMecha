"""第四批重构新增模块的单元测试：风控分类器 / 代理 URL 单源 / Web 发帖公共层。"""

import pytest

from tieba_mecha.core.risk import (
    ERR_FORUM_BANNED,
    extract_err_code,
    is_already_followed_error,
    is_account_ban_error,
    is_blacklisted_error,
    is_captcha_error,
    is_forum_ban_error,
)
from tieba_mecha.core.proxy import build_proxy_url
from tieba_mecha.core.web_poster import (
    DEFAULT_WEB_UA,
    build_thread_payload,
    build_web_headers,
    normalize_web_content,
)


class TestRiskClassifier:
    def test_captcha_by_keyword_and_code(self):
        assert is_captcha_error("请输入验证码")
        assert is_captcha_error("captcha challenge")
        assert is_captcha_error("操作太频繁")
        assert is_captcha_error("", err_code=100006)
        assert is_captcha_error("", err_code=6)
        assert not is_captcha_error("普通业务错误")
        assert not is_captcha_error("", err_code=999)

    def test_forum_ban(self):
        assert is_forum_ban_error(f"错误 {ERR_FORUM_BANNED} 被吧务封禁")
        assert is_forum_ban_error("", err_code=ERR_FORUM_BANNED)
        assert not is_forum_ban_error("其他错误 3250005")

    def test_account_ban(self):
        assert is_account_ban_error("账号已被封禁")
        assert is_account_ban_error("账号被屏蔽")
        assert not is_account_ban_error("正常")

    def test_blacklist_and_followed(self):
        assert is_blacklisted_error("400013 该账号被拉黑")
        assert is_already_followed_error("已关注该吧")
        assert not is_already_followed_error("关注失败")

    def test_extract_err_code(self):
        assert extract_err_code("abc 3250004 def") == 3250004
        assert extract_err_code("no digits here") == 0
        assert extract_err_code("") == 0
        assert extract_err_code(None) == 0
        assert extract_err_code("code=100006; more") == 100006


class TestProxyUrl:
    def test_anonymous(self):
        assert build_proxy_url("http", "1.2.3.4", 8080) == "http://1.2.3.4:8080"

    def test_credentials_quoted(self):
        url = build_proxy_url("socks5", "h", 1080, "us er", "p@ss:word")
        assert url == "socks5://us%20er:p%40ss%3Aword@h:1080"

    def test_partial_credentials_treated_as_anonymous(self):
        assert build_proxy_url("http", "h", 80, username="only") == "http://h:80"


class TestWebPoster:
    def test_headers_shape(self):
        h = build_web_headers("B", "S", "%E6%B5%8B%E8%AF%95", ua=None)
        assert h["Cookie"] == "BDUSS=B; STOKEN=S"
        assert h["User-Agent"] == DEFAULT_WEB_UA
        assert "Chrome/120" in DEFAULT_WEB_UA  # 统一 UA（曾是 119/120 两套）
        assert h["Referer"].startswith("https://tieba.baidu.com/f?kw=")

    def test_headers_custom_ua(self):
        h = build_web_headers("B", "S", "x", ua="MyUA")
        assert h["User-Agent"] == "MyUA"

    def test_normalize_web_content(self):
        # 贴吧 Web 表单 API 接受并保留原始 LF；CR/CRLF 必须规范化，但不做 [br] 转换
        assert normalize_web_content("a\r\nb\rc\nd") == "a\nb\nc\nd"
        assert normalize_web_content("plain") == "plain"

    def test_payload_roundtrip(self):
        import urllib.parse
        body = build_thread_payload("测试吧", 123, "TBS", "标题", "内容\n二行")
        parsed = urllib.parse.parse_qs(body.decode("utf-8"))
        assert parsed["kw"] == ["测试吧"]
        assert parsed["fid"] == ["123"]
        assert parsed["tbs"] == ["TBS"]
        assert parsed["anonymous"] == ["0"]
        assert parsed["content"] == ["内容\n二行"]
        # rich_text 会走富文本管线丢弃换行，不得携带
        assert "rich_text" not in parsed


@pytest.mark.asyncio
async def test_confirm_async_returns_false_on_cancel():
    """确认对话框：模拟用户点取消，应返回 False 并关闭对话框。"""
    import flet as ft
    from tieba_mecha.web.components.toast import confirm_async

    opened = {}
    closed = []

    class FakePage:
        def open(self, d):
            opened["dialog"] = d
            # 模拟用户点击"取消"按钮（actions[0]）
            ft.TextButton
            d.actions[0].on_click(None)

        def close(self, d):
            closed.append(d)

    result = await confirm_async(FakePage(), "确认？", "说明")
    assert result is False
    assert closed


@pytest.mark.asyncio
async def test_confirm_async_returns_true_on_confirm():
    from tieba_mecha.web.components.toast import confirm_async

    class FakePage:
        def open(self, d):
            d.actions[1].on_click(None)

        def close(self, d):
            pass

    assert await confirm_async(FakePage(), "确认？", "") is True
