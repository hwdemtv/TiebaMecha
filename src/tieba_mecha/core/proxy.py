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
    # ⚠️ 默认关闭（"false"）：防止 IP 关联导致关联封号
    if proxy_id:
        is_fallback_enabled = await db.get_setting("proxy_fallback", "false") == "true"
        if is_fallback_enabled:
            # ⚠️ 警告：自动容灾切换代理可能导致 IP 关联，增加关联封号风险
            # 建议：每个账号绑定固定代理，禁用自动容灾
            from .logger import log_warn
            await log_warn(
                "代理容灾已启用：账号可能切换到其他 IP，与该账号历史 IP 不一致会增加关联封号风险。"
                "建议在「账号管理」中为每个账号绑定固定代理，并关闭「代理自动容灾」。"
            )
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
    import logging
    logger = logging.getLogger(__name__)

    # 解密用户名/密码（独立解密，不要求两者同时存在）
    username = None
    password = None
    if proxy.username:
        try:
            username = decrypt_value(proxy.username)
        except Exception:
            logger.warning("代理 %s:%d 用户名解密失败，可能加密密钥已变更", proxy.host, proxy.port)
            username = proxy.username
    if proxy.password:
        try:
            password = decrypt_value(proxy.password)
        except Exception:
            logger.warning("代理 %s:%d 密码解密失败，可能加密密钥已变更", proxy.host, proxy.port)
            password = proxy.password

    # SOCKS5 协议需要将认证信息嵌入 URL，BasicAuth 对 SOCKS 无效
    if proxy.protocol.startswith("socks"):
        if username and password:
            import urllib.parse
            u = urllib.parse.quote(username, safe="")
            p = urllib.parse.quote(password, safe="")
            proxy_url = f"{proxy.protocol}://{u}:{p}@{proxy.host}:{proxy.port}"
            return ProxyConfig(url=proxy_url, auth=None)
        elif username or password:
            # SOCKS5 认证不完整：有用户名无密码或反之
            # 以无认证模式连接会触发 "NO ACCEPTABLE METHODS" 错误
            logger.error(
                "SOCKS5 代理 %s:%d 认证信息不完整"
                "（用户名=%s，密码=%s），无法连接。"
                "请在代理管理中补全认证信息。",
                proxy.host, proxy.port,
                "已设置" if username else "缺失",
                "已设置" if password else "缺失",
            )
            raise ValueError(
                f"SOCKS5 代理 {proxy.host}:{proxy.port} 认证信息不完整"
                f"（用户名={'已设置' if username else '缺失'}，"
                f"密码={'已设置' if password else '缺失'}），"
                f"请在代理管理中补全用户名和密码"
            )
        # SOCKS5 无认证（匿名模式），允许通过
        proxy_url = f"{proxy.protocol}://{proxy.host}:{proxy.port}"
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

    start_time = time.time()
    try:
        from aiohttp_socks import ProxyConnector
        # SOCKS5 认证必须使用已解密的值拼入 URL
        dec_user = None
        dec_pass = None
        if auth_user:
            try:
                dec_user = decrypt_value(auth_user)
            except Exception:
                dec_user = auth_user
        if auth_pass:
            try:
                dec_pass = decrypt_value(auth_pass)
            except Exception:
                dec_pass = auth_pass
        if dec_user and dec_pass:
            import urllib.parse
            u = urllib.parse.quote(dec_user, safe="")
            p = urllib.parse.quote(dec_pass, safe="")
            proxy_url = proxy_url.replace("://", f"://{u}:{p}@", 1)
        elif dec_user or dec_pass:
            return False, "SOCKS5 认证信息不完整，请补全用户名和密码"
        connector = ProxyConnector.from_url(proxy_url)

        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get("http://www.baidu.com", timeout=aiohttp.ClientTimeout(total=8)) as response:
                if response.status in (200, 302, 403):
                    latency = int((time.time() - start_time) * 1000)
                    return True, f"{latency}ms"
                else:
                    return False, f"HTTP {response.status}"
    except Exception as e:
        return False, f"连接异常: {str(e)[:80]}"
