"""aiotieba Client factory with proxy support"""
import aiotieba
from aiotieba.core import Account
from .proxy import get_best_proxy_config
from ..db.crud import Database

class MechaClient(aiotieba.Client):
    """
    定制化贴吧客户端，支持指纹注入
    """
    def __init__(self, *args, ua: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_ua = ua

    async def __aenter__(self) -> "MechaClient":
        await super().__aenter__()
        # 在进入异步上下文后，_http_core 已经初始化
        if self.custom_ua:
            import aiohttp
            nw = getattr(self, '_http_core', None) or getattr(self, '_nw', None)
            if nw:
                for name in ['web', 'app', 'app_proto']:
                    container = getattr(nw, name, None)
                    if container and hasattr(container, 'headers'):
                        container.headers[aiohttp.hdrs.USER_AGENT] = self.custom_ua
        return self

async def create_client(db: Database, bduss: str, stoken: str = "", proxy_id: int | None = None, cuid: str = "", ua: str = "") -> aiotieba.Client:
    """
    创建一个带有独立指纹和代理支持的 aiotieba 客户端对象。
    """
    # 只有在明确指定 proxy_id 时才获取代理配置
    proxy_arg = False
    if proxy_id:
        proxy = await get_best_proxy_config(db, proxy_id=proxy_id)
        if proxy:
            proxy_arg = proxy
            
    # 提前构造 Account 对象并注入 cuid，确保独立指纹
    account = Account(BDUSS=bduss, STOKEN=stoken)
    if cuid:
        account.cuid = cuid
        
    # 使用定制的 MechaClient 实例化
    client = MechaClient(account=account, proxy=proxy_arg, ua=ua)
    
    return client





