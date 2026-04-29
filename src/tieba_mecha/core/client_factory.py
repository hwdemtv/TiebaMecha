"""aiotieba Client factory with proxy support"""
import aiotieba
from aiotieba.core import Account
from .proxy import get_best_proxy_config
from ..db.crud import Database

class MechaClient(aiotieba.Client):
    """
    定制化贴吧客户端，支持指纹注入与 SOCKS5 代理
    """
    def __init__(self, *args, ua: str = "", **kwargs):
        # 拦截并在调用父类前移除 connector，防止 TypeError
        self.custom_connector = kwargs.pop("connector", None)
        self._replacement_sessions: list[aiohttp.ClientSession] = []
        super().__init__(*args, **kwargs)
        self.custom_ua = ua

    async def __aenter__(self) -> "MechaClient":
        # 记录原始 session 状态
        await super().__aenter__()
        
        # 注入自定义 User-Agent 和处理 SOCKS5 Connector
        nw = getattr(self, '_http_core', None) or getattr(self, '_nw', None)
        if nw:
            import aiohttp
            for name in ['web', 'app', 'app_proto']:
                container = getattr(nw, name, None)
                if not container:
                    continue
                
                # 处理 User-Agent
                if self.custom_ua and hasattr(container, 'headers'):
                    container.headers[aiohttp.hdrs.USER_AGENT] = self.custom_ua
                
                # 处理 SOCKS5: 如果由于使用了 SOCKS5 需要替换 Session
                # 注意：这是一种高级注入，通过替换内部 session 来强制启用 SOCKS5 支持
                if self.custom_connector and hasattr(container, 'session'):
                    # 保存旧 session 的关键信息
                    old_session = container.session
                    # 创建新 session 并合并配置
                    new_session = aiohttp.ClientSession(
                        connector=self.custom_connector,
                        headers=old_session.headers,
                        timeout=old_session.timeout,
                        connector_owner=True
                    )
                    # 替换
                    container.session = new_session
                    self._replacement_sessions.append(new_session)
                    # 关闭旧 session 释放连接池，防止 "Unclosed client session" 警告
                    try:
                        await old_session.close()
                    except Exception:
                        pass
                    
        return self

    async def __aexit__(self, exc_type=None, exc_val=None, exc_tb=None) -> None:
        # 先关闭自定义替换的 session，避免其连接池泄漏
        while self._replacement_sessions:
            session = self._replacement_sessions.pop()
            try:
                await session.close()
            except Exception:
                pass

        await super().__aexit__(exc_type, exc_val, exc_tb)

async def create_client(db: Database, bduss: str, stoken: str = "", proxy_id: int | None = None, cuid: str = "", ua: str = "") -> aiotieba.Client:
    """
    创建一个带有独立指纹和代理支持的 aiotieba 客户端对象。
    """
    # 只有在明确指定 proxy_id 时才获取代理配置
    client_kwargs = {"ua": ua}
    
    if proxy_id:
        try:
            proxy = await get_best_proxy_config(db, proxy_id=proxy_id)
        except ValueError as e:
            # SOCKS5 代理认证信息不完整等配置错误
            from .logger import log_error
            await log_error(f"代理配置错误: {e}")
            proxy = None
        if proxy:
            # 统一使用 ProxyConnector：实测 HTTP/HTTPS 代理直接传 proxy 参数会触发
            # "Can not write request body" 错误，改用 connector 模式可解决
            from aiohttp_socks import ProxyConnector
            
            proxy_url = str(proxy.url)
            # 如果 proxy.auth 存在（HTTP/HTTPS 代理带认证），将认证信息拼入 URL
            # 否则 ProxyConnector.from_url 会以无认证模式连接，导致
            # "NO ACCEPTABLE METHODS" 错误
            if proxy.auth and not ("@" in proxy_url):
                import urllib.parse
                u = urllib.parse.quote(proxy.auth.login, safe="")
                p = urllib.parse.quote(proxy.auth.password, safe="")
                # 在 :// 后面插入 user:pass@
                proxy_url = proxy_url.replace("://", f"://{u}:{p}@", 1)
            
            client_kwargs["connector"] = ProxyConnector.from_url(proxy_url, rdns=False)
            
    # 提前构造 Account 对象并注入 cuid，确保独立指纹
    account = Account(BDUSS=bduss, STOKEN=stoken)
    if cuid:
        account.cuid = cuid
        
    # 使用定制的 MechaClient 实例化
    client = MechaClient(account=account, **client_kwargs)
    
    return client





