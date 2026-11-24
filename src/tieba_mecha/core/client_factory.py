"""aiotieba Client factory with proxy support

账号绑定代理的两条生效通道（2026-11-24 重写；旧实现在已安装的 aiotieba 4.6.4 下是死代码）：

- HTTP/HTTPS 代理：aiotieba 原生 ``proxy=ProxyConfig``。aiotieba 构造 aiohttp 请求时写入
  ``proxy`` / ``proxy_auth``，aiohttp 据此连到代理服务器并发绝对形式的请求。
- SOCKS 代理：aiohttp 不原生支持，需在连接器层生效。aiotieba 的 HTTP / WebSocket / 长连接
  三个核心共用同一个 NetCore，进入上下文后把 NetCore 的连接器换成 ProxyConnector。

旧实现试图替换各 HttpContainer 的 ``session`` 属性来注入 ProxyConnector，但 4.6.4 的
HttpContainer 只有 headers 和 cookie_jar 两个字段——注入条件恒为假，替换数恒为 0（运行时
实测）。结果是签到、取号信息、关注/取关、首评、自顶等全部 aiotieba 请求都从服务器 IP 直连，
只有提交主帖的 httpx 请求走了代理：同一 BDUSS 几秒内出现两个出口 IP。
"""

import time

import aiohttp
import aiotieba
from aiotieba.core import Account

from .proxy import get_best_proxy_config
from ..db.crud import Database

# 「绑定的代理不可用」告警的节流：同一代理 ID 至多 10 分钟提醒一次（create_client 调用频繁）
_PROXY_WARN_INTERVAL_SEC = 600
_proxy_warned_at: dict[int, float] = {}


async def _warn_bound_proxy_unavailable(proxy_id: int) -> None:
    """账号绑定了代理但该代理已停用/被删除（且未启用容灾）：本次请求只能直连。

    直连意味着该账号的出口 IP 与它走代理时不一致——正是绑定代理想避免的事。
    是否改为「代理不可用就跳过该账号」属于账号可用性策略，这里不擅自改变既有行为，
    只把原先的静默直连变成日志里可见。
    """
    now = time.monotonic()
    if now - _proxy_warned_at.get(proxy_id, 0.0) < _PROXY_WARN_INTERVAL_SEC:
        return
    _proxy_warned_at[proxy_id] = now
    from .logger import log_warn
    await log_warn(
        f"账号绑定的代理 (ID:{proxy_id}) 已停用或不存在，本次 aiotieba 请求将以直连发出"
        "（出口 IP 与走代理时不一致，存在关联风险）"
    )


def _proxy_not_in_effect(client: "MechaClient", http_core) -> str | None:
    """自检：配置了代理，它是否真的挂在 aiotieba 的网络核心上。返回未生效的原因，None 表示正常。"""
    net_core = getattr(http_core, "net_core", None)
    if client._proxy.url is not None and getattr(net_core, "proxy", None) is not client._proxy:
        return "HTTP 代理配置没有进入 aiotieba 的网络核心"
    if client.custom_connector is not None and getattr(net_core, "connector", None) is not client.custom_connector:
        return "SOCKS 代理连接器没有挂到 aiotieba 的网络核心"
    return None


class MechaClient(aiotieba.Client):
    """定制化贴吧客户端：自定义 User-Agent + 按协议选择生效通道的代理支持"""

    def __init__(self, *args, ua: str = "", proxy_connector=None, **kwargs):
        # 兼容旧调用方的 connector 关键字
        if proxy_connector is None:
            proxy_connector = kwargs.pop("connector", None)
        else:
            kwargs.pop("connector", None)
        # SOCKS 代理连接器：进入上下文后替换 NetCore 的连接器（见 __aenter__）
        self.custom_connector = proxy_connector
        super().__init__(*args, **kwargs)
        self.custom_ua = ua

    async def __aenter__(self) -> "MechaClient":
        await super().__aenter__()
        try:
            http_core = getattr(self, "_http_core", None)

            # 注入自定义 User-Agent（headers 是普通 dict，直接改即生效）
            if http_core and self.custom_ua:
                for name in ("web", "app", "app_proto"):
                    headers = getattr(getattr(http_core, name, None), "headers", None)
                    if isinstance(headers, dict):
                        headers[aiohttp.hdrs.USER_AGENT] = self.custom_ua

            # SOCKS：换掉共用 NetCore 的连接器即对 HTTP / WebSocket / 长连接全部生效；
            # _connector 同时指向新连接器，保证父类 __aexit__ 关闭的是它
            if self.custom_connector is not None:
                net_core = getattr(http_core, "net_core", None)
                default_connector = getattr(self, "_connector", None)
                if net_core is not None and default_connector is not None:
                    net_core.connector = self.custom_connector
                    self._connector = self.custom_connector
                    try:
                        await default_connector.close()
                    except Exception:
                        pass

            # 自检：配置了代理却没生效时显式失败。旧实现正是因为「静默直连」才两个多月
            # 无人察觉；aiotieba 升级后若内部结构变化，这里会第一时间报错而不是放行
            reason = _proxy_not_in_effect(self, http_core)
            if reason:
                raise RuntimeError(
                    f"aiotieba {aiotieba.__version__} 的代理注入点已变化：{reason}。"
                    "为避免账号以服务器真实出口 IP 直连，已中止本次请求；"
                    "请将本项目升级到与该 aiotieba 版本匹配的实现，或回退 aiotieba"
                )
        except BaseException:
            # 上下文没建立成功：把父类已经建出的资源收干净，再把错误交给调用方
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, exc_type=None, exc_val=None, exc_tb=None) -> None:
        try:
            await super().__aexit__(exc_type, exc_val, exc_tb)
        finally:
            # 连接器未被换上（例如 aiotieba 内部结构变化）时，父类不会关闭它
            if self.custom_connector is not None and self.custom_connector is not getattr(self, "_connector", None):
                try:
                    await self.custom_connector.close()
                except Exception:
                    pass


async def create_client(db: Database, bduss: str, stoken: str = "", proxy_id: int | None = None, cuid: str = "", ua: str = "") -> aiotieba.Client:
    """
    创建一个带有独立指纹和代理支持的 aiotieba 客户端对象。
    """
    client_kwargs: dict = {"ua": ua}

    # 只有在明确指定 proxy_id 时才获取代理配置
    if proxy_id:
        try:
            proxy = await get_best_proxy_config(db, proxy_id=proxy_id)
        except ValueError as e:
            # SOCKS5 代理认证信息不完整等配置错误
            from .logger import log_error
            await log_error(f"代理配置错误: {e}")
            proxy = None

        if proxy is None:
            await _warn_bound_proxy_unavailable(proxy_id)
        else:
            proxy_url = str(proxy.url) if proxy.url is not None else ""
            if proxy_url.lower().startswith("socks"):
                # SOCKS：aiohttp 不原生支持，走连接器层；认证信息已由 _build_proxy_config 嵌入 URL
                from aiohttp_socks import ProxyConnector

                client_kwargs["proxy_connector"] = ProxyConnector.from_url(proxy_url, rdns=False)
            else:
                # HTTP/HTTPS：aiotieba 原生 ProxyConfig（请求级 proxy + BasicAuth），无需替换连接器
                client_kwargs["proxy"] = proxy

    # 提前构造 Account 对象并注入 cuid，确保独立指纹
    account = Account(BDUSS=bduss, STOKEN=stoken)
    if cuid:
        account.cuid = cuid

    # 使用定制的 MechaClient 实例化
    return MechaClient(account=account, **client_kwargs)
