"""aiotieba Client factory with proxy support"""
import aiotieba
from .proxy import get_best_proxy_config
from ..db.crud import Database

async def create_client(db: Database, bduss: str, stoken: str = "", proxy_id: int | None = None, cuid: str = "", ua: str = "") -> aiotieba.Client:
    """
    创建一个带有独立指纹和代理支持的 aiotieba 客户端对象。
    """
    proxy = await get_best_proxy_config(db, proxy_id=proxy_id)
    # 如果 proxy 对象存在，直接将其作为 ProxyConfig 实例传递给 aiotieba
    proxy_arg = False
    if proxy:
        # aiotieba.Client 接受 ProxyConfig 实例作为参数
        proxy_arg = proxy
            
    # aiotieba 4.6+ 不支持在初始化阶段直接传 cuid kwargs, UA 也在 Client 内部分析配置
    return aiotieba.Client(BDUSS=bduss, STOKEN=stoken, proxy=proxy_arg)





