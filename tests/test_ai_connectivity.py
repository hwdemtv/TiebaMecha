"""AI 连通性检查（check_connectivity）回归测试。

2026-09-22 网关证书不匹配导致改写静默不可用三天，设置页增加一键检查；
此处验证各失败分支转化为可读文案，以及表单值优先于库内配置。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tieba_mecha.core.ai_optimizer import AIOptimizer


class _FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._exc:
            raise self._exc
        return self._resp


@pytest.fixture
def optimizer(db):
    return AIOptimizer(db)


async def _check_with(optimizer, session, **overrides):
    optimizer._get_session = AsyncMock(return_value=session)
    kwargs = {"api_key": "test-key", "base_url": "http://ai.test/v1/", "model": "test-model"}
    kwargs.update(overrides)
    return await optimizer.check_connectivity(**kwargs)


async def test_missing_key_fails_fast(db, optimizer):
    result = await optimizer.check_connectivity()
    assert result["ok"] is False
    assert "未配置 API Key" in result["detail"]


async def test_form_overrides_win_over_db(db, optimizer):
    # 库内配置与表单值并存时，表单值（overrides）优先
    await db.set_setting("ai_base_url", "http://db-gateway/v1/")
    session = _FakeSession(_FakeResp(200, {"choices": [{"message": {"content": "OK"}}]}))
    result = await _check_with(optimizer, session)
    assert result["ok"] is True
    assert result["model"] == "test-model"
    url, _ = session.calls[0]
    assert url.startswith("http://ai.test/v1/")


async def test_success_flow(db, optimizer):
    session = _FakeSession(_FakeResp(200, {"choices": [{"message": {"content": "OK"}}]}))
    result = await _check_with(optimizer, session)
    assert result["ok"] is True
    assert "连通正常" in result["detail"]
    assert "OK" in result["detail"]
    url, kwargs = session.calls[0]
    assert url.endswith("/chat/completions")
    assert kwargs["headers"]["Authorization"].startswith("Bearer test-key")
    assert kwargs["json"]["model"] == "test-model"
    assert kwargs["json"]["max_tokens"] == 8


async def test_http_401_maps_to_auth_message(db, optimizer):
    session = _FakeSession(_FakeResp(401, text='{"error":"bad key"}'))
    result = await _check_with(optimizer, session)
    assert result["ok"] is False
    assert "认证失败" in result["detail"]
    assert "401" in result["detail"]


async def test_http_500_surfaces_body(db, optimizer):
    session = _FakeSession(_FakeResp(500, text="upstream broken"))
    result = await _check_with(optimizer, session)
    assert result["ok"] is False
    assert "500" in result["detail"]
    assert "upstream broken" in result["detail"]


async def test_ssl_error_maps_to_cert_hint(db, optimizer):
    session = _FakeSession(exc=Exception(
        "Cannot connect to host api.example.com:443 ssl:True "
        "[SSLCertVerificationError: certificate verify failed: Hostname mismatch]"
    ))
    result = await _check_with(optimizer, session)
    assert result["ok"] is False
    assert "SSL 证书" in result["detail"]
    assert "反代证书" in result["detail"]


async def test_dns_error_maps_to_unreachable(db, optimizer):
    session = _FakeSession(exc=Exception("Cannot connect to host api.example.com:443 [Name or service not known]"))
    result = await _check_with(optimizer, session)
    assert result["ok"] is False
    assert "无法连接" in result["detail"]


async def test_timeout_maps_to_gateway_silent(db, optimizer):
    session = _FakeSession(exc=asyncio.TimeoutError())
    result = await _check_with(optimizer, session)
    assert result["ok"] is False
    assert "连接超时" in result["detail"]
