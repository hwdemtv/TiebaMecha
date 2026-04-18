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
                    # 我们不在这里关闭 old_session，通常交给 aiotieba 的退出逻辑或让它自动释放
                    
        return self

async def create_client(db: Database, bduss: str, stoken: str = "", proxy_id: int | None = None, cuid: str = "", ua: str = "") -> aiotieba.Client:
    """
    创建一个带有独立指纹和代理支持的 aiotieba 客户端对象。
    """
    # 只有在明确指定 proxy_id 时才获取代理配置
    client_kwargs = {"ua": ua}
    
    if proxy_id:
        proxy = await get_best_proxy_config(db, proxy_id=proxy_id)
        if proxy:
            # 这里的 proxy 是 ProxyConfig 对象
            # ⚠️ 统一使用 ProxyConnector：实测 HTTP/HTTPS 代理直接传 proxy 参数会触发
            # "Can not write request body" 错误，改用 connector 模式可解决
            from aiohttp_socks import ProxyConnector
            # 如果是 socks 协议，从 proxy.url 获取；否则直接构造 HTTP 代理 URL
            if str(proxy.url).startswith("socks"):
                client_kwargs["connector"] = ProxyConnector.from_url(str(proxy.url))
            else:
                # HTTP/HTTPS 代理使用 ProxyConnector 构造器（from_url 可处理 http:// 协议）
                # 注意：aiohttp_socks 从 0.8+ 版本开始支持 http:// 协议
                client_kwargs["connector"] = ProxyConnector.from_url(str(proxy.url))
            
    # 提前构造 Account 对象并注入 cuid，确保独立指纹
    account = Account(BDUSS=bduss, STOKEN=stoken)
    if cuid:
        account.cuid = cuid
        
    # 使用定制的 MechaClient 实例化
    client = MechaClient(account=account, **client_kwargs)
    
    return client





