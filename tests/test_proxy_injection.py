"""账号绑定代理对 aiotieba 请求的生效回归（2026-11-24 全项目体检整改）

旧实现在已安装的 aiotieba 4.6.4 下是死代码：注入条件 hasattr(container, 'session') 恒为假，
运行时实测替换的 session 数为 0。签到、取号信息、关注/取关、首评、自顶全部直连，只有提交
主帖的 httpx 请求走代理——同一 BDUSS 几秒内两个出口 IP。

这里的 HTTP 功能测试起一个本地假代理：aiotieba 的真实请求必须先到达它才算代理生效。
假代理不向任何外部地址转发，全程不触网。
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp_socks import ProxyConnector
from yarl import URL

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tieba_mecha.core.proxy as proxy_mod  # noqa: E402
from tieba_mecha.core import client_factory  # noqa: E402

BDUSS, STOKEN = "b" * 192, "s" * 64


async def _add_proxy(db, protocol="http", *, active=True, username="proxy_user", password="proxy_pass"):
    proxy = await db.add_proxy("127.0.0.1", 18888, username=username, password=password, protocol=protocol)
    if not active:
        await db.update_proxy(proxy.id, is_active=False)
    return proxy.id


# ---------- HTTP / HTTPS：aiotieba 原生 ProxyConfig ----------

async def test_http_proxy_is_passed_as_native_proxy_config(db):
    pid = await _add_proxy(db, "http")

    client = client_factory.create_client  # 引用，防止后面被替身覆盖
    me = await client(db, BDUSS, STOKEN, proxy_id=pid, ua="ua/1.0")

    assert me._proxy.url == URL("http://127.0.0.1:18888")      # 请求级代理：aiohttp 据此连到代理服务器
    assert me._proxy.auth.login == "proxy_user" and me._proxy.auth.password == "proxy_pass"
    assert me.custom_connector is None                          # 不再走替换连接器的旧路
    assert me.custom_ua == "ua/1.0"


class _FakeHttpProxy:
    """最小 HTTP 代理：记录到达的请求（绝对形式请求行 + 头），回一个空响应，不向外转发"""

    def __init__(self):
        self.requests: list[dict] = []
        self.server = None
        self.port = 0

    async def _handle(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            record = {"request_line": lines[0], "headers": lines[1:-1]}
            for h in lines[1:]:
                if h.lower().startswith("content-length:"):
                    record["body"] = await reader.readexactly(int(h.split(":", 1)[1]))
            self.requests.append(record)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-protobuf\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError) as e:
            self.requests.append({"error": repr(e)})
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()


def _header(record: dict, name: str) -> str:
    for h in record["headers"]:
        if h.lower().startswith(name.lower() + ":"):
            return h.split(":", 1)[1].strip()
    return ""


async def test_aiotieba_requests_actually_go_through_http_proxy(db):
    """真实 aiotieba 请求必须先到达本地假代理：绝对形式请求行 + Proxy-Authorization"""
    async with _FakeHttpProxy() as fake:
        proxy = await db.add_proxy("127.0.0.1", fake.port, username="u", password="p", protocol="http")

        me = await client_factory.create_client(db, BDUSS, STOKEN, proxy_id=proxy.id)
        async with me:
            info = await me.get_self_info()
            err = getattr(info, "err", None)

    assert fake.requests, "aiotieba 的请求没有经过代理（代理注入未生效）"
    first = fake.requests[0]
    assert "error" not in first, first
    # 代理服务器收到的是绝对形式请求行（含目标 scheme://host），而不是发往目标主机的直连请求
    assert first["request_line"].split(" ")[1].lower().startswith("http://")
    assert "baidu.com" in first["request_line"]
    expected = "Basic " + base64.b64encode(b"u:p").decode()
    assert _header(first, "Proxy-Authorization") == expected
    # 旧注释称「HTTP 代理直接传 proxy 参数会触发 Can not write request body」：带请求体的真实请求已走通
    assert "Can not write request body" not in str(err)
    assert err is not None and not isinstance(err, TypeError)   # 假代理回的是空响应，出错是预期


async def test_ua_injection_still_works(db):
    me = await client_factory.create_client(db, BDUSS, STOKEN, ua="custom-ua/2.0")
    try:
        await me.__aenter__()
        import aiohttp

        for name in ("web", "app", "app_proto"):
            assert getattr(me._http_core, name).headers[aiohttp.hdrs.USER_AGENT] == "custom-ua/2.0"
    finally:
        await me.__aexit__(None, None, None)


# ---------- SOCKS：连接器层 ----------

async def test_socks_proxy_replaces_the_shared_connector(db):
    pid = await _add_proxy(db, "socks5")

    me = await client_factory.create_client(db, BDUSS, STOKEN, proxy_id=pid)
    assert isinstance(me.custom_connector, ProxyConnector)
    assert me._proxy.url is None                       # SOCKS 不走请求级 proxy
    try:
        await me.__aenter__()
        net_core = me._http_core.net_core
        assert net_core.connector is me.custom_connector          # HTTP/WebSocket/长连接共用的 NetCore
        assert me._connector is me.custom_connector               # __aexit__ 关闭的就是它
        assert me.custom_connector._loop is not None
        assert me.custom_connector._proxy_host == "127.0.0.1" and me.custom_connector._proxy_port == 18888
    finally:
        await me.__aexit__(None, None, None)
    assert me.custom_connector.closed


async def test_socks_proxy_not_in_effect_fails_loudly_and_leaks_nothing(db):
    """aiotieba 内部结构变化导致连接器挂不上：必须显式报错（绝不静默直连），且代理连接器不泄漏"""
    pid = await _add_proxy(db, "socks5")
    me = await client_factory.create_client(db, BDUSS, STOKEN, proxy_id=pid)

    async def _no_http_core(self):
        return self                                   # 模拟父类没建出 _http_core

    with patch.object(type(me).__mro__[1], "__aenter__", _no_http_core), \
         patch.object(type(me).__mro__[1], "__aexit__", AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError, match="SOCKS 代理连接器没有挂到"):
            await me.__aenter__()

    assert me.custom_connector.closed                 # 失败路径上资源被收干净


async def test_http_proxy_dropped_from_net_core_fails_loudly(db):
    """HTTP 代理配置没有进入 aiotieba 网络核心（模拟升级后注入点变化）：显式报错"""
    from aiotieba.config import ProxyConfig

    pid = await _add_proxy(db, "http")
    me = await client_factory.create_client(db, BDUSS, STOKEN, proxy_id=pid)

    async def _enter_then_drop(self):
        await real_aenter(self)
        self._http_core.net_core.proxy = ProxyConfig()   # 网络核心里的代理被换成了空配置
        return self

    real_aenter = type(me).__mro__[1].__aenter__
    with patch.object(type(me).__mro__[1], "__aenter__", _enter_then_drop):
        with pytest.raises(RuntimeError, match="HTTP 代理配置没有进入"):
            await me.__aenter__()
    assert me._connector.closed


# ---------- 不走代理 / 代理不可用 ----------

async def test_account_without_proxy_stays_direct(db):
    me = await client_factory.create_client(db, BDUSS, STOKEN)
    assert me._proxy.url is None and me.custom_connector is None
    assert me._account.BDUSS == BDUSS and me._account.STOKEN == STOKEN


async def test_bound_proxy_inactive_warns_and_keeps_direct(db, monkeypatch):
    """绑定的代理已停用：保持既有的直连行为，但不再静默"""
    client_factory._proxy_warned_at.clear()
    warn = AsyncMock()
    monkeypatch.setattr(proxy_mod, "get_best_proxy_config", AsyncMock(return_value=None))
    with patch("tieba_mecha.core.logger.log_warn", warn):
        pid = await _add_proxy(db, "http", active=False)
        me = await client_factory.create_client(db, BDUSS, STOKEN, proxy_id=pid)
        me2 = await client_factory.create_client(db, BDUSS, STOKEN, proxy_id=pid)
        await client_factory.create_client(db, BDUSS, STOKEN, proxy_id=pid + 999)

    assert me._proxy.url is None and me2._proxy.url is None
    messages = [str(c.args[0]) for c in warn.await_args_list]
    assert len(messages) == 2                           # 同一代理节流为 1 次；另一个 ID 另记 1 次
    assert any(f"ID:{pid}" in m and "直连" in m for m in messages)


async def test_incomplete_socks_credentials_log_error_and_stay_direct(db):
    """SOCKS5 认证信息不完整：记录错误，不抛异常打断调用方（既有行为）"""
    proxy = await db.add_proxy("127.0.0.1", 18888, username="only_user", password="", protocol="socks5")
    log_error = AsyncMock()
    with patch("tieba_mecha.core.logger.log_error", log_error):
        me = await client_factory.create_client(db, BDUSS, STOKEN, proxy_id=proxy.id)
    assert me._proxy.url is None and me.custom_connector is None
    assert "认证信息不完整" in str(log_error.await_args.args[0])
