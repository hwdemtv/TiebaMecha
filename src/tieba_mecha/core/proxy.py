import random
from typing import Optional
from aiotieba.config import ProxyConfig
from ..db.crud import Database

async def get_best_proxy_config(db: Database, proxy_id: int | None = None) -> Optional[ProxyConfig]:
    """
    获取推荐的代理配置。
    """
    # 优先尝试指定 ID 的代理
    if proxy_id:
        proxy = await db.get_proxy(proxy_id)
        if proxy and proxy.is_active:
            return _build_proxy_config(proxy)

    # 获取可用的代理列表
    proxies = await db.get_active_proxies()
    if not proxies:
        return None

    # 如果指定了代理 ID 但失效，且启用了自动容灾，才尝试 Fallback
    if proxy_id:
        is_fallback_enabled = await db.get_setting("proxy_fallback", "true") == "true"
        if is_fallback_enabled:
            # 随机选择一个较稳定的代理（前 3 个）
            top_proxies = sorted(proxies, key=lambda p: p.fail_count)[:3]
            proxy = random.choice(top_proxies)
            return _build_proxy_config(proxy)
        return None

    # 未指定 proxy_id 时，返回第一个可用代理
    return _build_proxy_config(proxies[0])

def _build_proxy_config(proxy) -> ProxyConfig:
    """内部工具函数：从模型构建 ProxyConfig，含解密逻辑"""
    from .account import decrypt_value
    
    # 解密用户名/密码
    username = None
    password = None
    if proxy.username and proxy.password:
        try:
            username = decrypt_value(proxy.username)
        except Exception:
            username = proxy.username
        try:
            password = decrypt_value(proxy.password)
        except Exception:
            password = proxy.password

    # SOCKS5 协议需要将认证信息嵌入 URL，BasicAuth 对 SOCKS 无效
    if proxy.protocol.startswith("socks") and username and password:
        import urllib.parse
        u = urllib.parse.quote(username, safe="")
        p = urllib.parse.quote(password, safe="")
        proxy_url = f"{proxy.protocol}://{u}:{p}@{proxy.host}:{proxy.port}"
        return ProxyConfig(url=proxy_url, auth=None)

    # HTTP/HTTPS 代理使用标准 BasicAuth
    proxy_url = f"{proxy.protocol}://{proxy.host}:{proxy.port}"
    auth = None
    if username and password:
        from aiohttp import BasicAuth
        auth = BasicAuth(username, password)
    return ProxyConfig(url=proxy_url, auth=auth)

async def mark_proxy_failure(db: Database, proxy_url: str):
    """标记指定 URL 的代理失败次数"""
    if db and proxy_url:
        await db.mark_proxy_fail_by_url(proxy_url)

async def test_proxy(proxy_url: str, auth_user: str | None = None, auth_pass: str | None = None) -> tuple[bool, str]:
    """
    测试代理连通性（带智能解密处理）
    
    Returns:
        (是否成功, 消息/延迟)
    """
    import aiohttp
    import time
    from .account import decrypt_value
    
    # 构造 aiohttp 认证对象
    proxy_auth = None
    if auth_user and auth_pass:
        # 解密处理
        try:
            username = decrypt_value(auth_user)
        except Exception:
            username = auth_user
        
        try:
            password = decrypt_value(auth_pass)
        except Exception:
            password = auth_pass
            
        proxy_auth = aiohttp.BasicAuth(username, password)
        
    start_time = time.time()
    try:
        connector = None
        get_kwargs = {"timeout": aiohttp.ClientTimeout(total=8)}
        
        if proxy_url.startswith("socks"):
            from aiohttp_socks import ProxyConnector
            # SOCKS5 认证必须使用已解密的值拼入 URL
            if auth_user and auth_pass:
                try:
                    dec_user = decrypt_value(auth_user)
                except Exception:
                    dec_user = auth_user
                try:
                    dec_pass = decrypt_value(auth_pass)
                except Exception:
                    dec_pass = auth_pass
                import urllib.parse
                u = urllib.parse.quote(dec_user, safe="")
                p = urllib.parse.quote(dec_pass, safe="")
                proxy_url = proxy_url.replace("://", f"://{u}:{p}@", 1)
            connector = ProxyConnector.from_url(proxy_url)
        else:
            get_kwargs["proxy"] = proxy_url
            if proxy_auth:
                get_kwargs["proxy_auth"] = proxy_auth

        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get("http://www.baidu.com", **get_kwargs) as response:
                if response.status in (200, 302, 403):
                    latency = int((time.time() - start_time) * 1000)
                    return True, f"{latency}ms"
                else:
                    return False, f"HTTP {response.status}"
    except Exception as e:
        return False, f"连接异常: {str(e)[:80]}"
