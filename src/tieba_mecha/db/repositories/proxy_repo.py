"""代理池 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select, text, update

from ..models import (
    Account,
    AutoRule,
    Base,
    BatchPostLog,
    BatchPostTask,
    CaptchaEvent,
    Forum,
    MaterialPool,
    Notification,
    Proxy,
    Setting,
    SignLog,
    TargetPool,
    ThreadRecord,
    WeightHistory,
)

logger = logging.getLogger(__name__)

# 代理失败阈值,超过此值自动禁用（原 crud.py 模块级常量，随代理域迁移至此）
PROXY_FAIL_THRESHOLD = 10



class ProxyRepository:
    """代理池（依赖宿主类提供 async_session/engine）。"""

    async def add_proxy(
        self,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        protocol: str = "http",
    ) -> Proxy:
        """添加代理（自动加密凭证）"""
        from ...core.account import encrypt_value
        
        # 对非空密码执行保护倒灌
        enc_username = encrypt_value(username) if username else ""
        enc_password = encrypt_value(password) if password else ""
        
        async with self.async_session() as session:
            proxy = Proxy(
                host=host, port=port, username=enc_username, password=enc_password, protocol=protocol
            )
            session.add(proxy)
            await session.commit()
            await session.refresh(proxy)
            return proxy
    async def get_proxy(self, proxy_id: int) -> Proxy | None:
        """根据 ID 获取代理"""
        async with self.async_session() as session:
            return await session.get(Proxy, proxy_id)
    async def get_active_proxies(self) -> list[Proxy]:
        """获取所有可用代理"""
        async with self.async_session() as session:
            result = await session.execute(
                select(Proxy).where(Proxy.is_active == True).order_by(Proxy.fail_count)
            )
            return list(result.scalars().all())
    async def mark_proxy_fail(self, proxy_id: int) -> None:
        """根据 ID 标记代理失败"""
        async with self.async_session() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy:
                proxy.fail_count += 1
                if proxy.fail_count >= PROXY_FAIL_THRESHOLD:  # 使用常量
                    proxy.is_active = False
                await session.commit()
    async def mark_proxy_fail_by_url(self, url: str) -> None:
        """根据 URL 标记代理失败"""
        import re
        match = re.search(r'//([^:/]+):(\d+)', url)
        if not match: return
        
        host, port = match.groups()
        async with self.async_session() as session:
            result = await session.execute(
                select(Proxy).where(Proxy.host == host, Proxy.port == int(port))
            )
            proxy = result.scalar_one_or_none()
            if proxy:
                proxy.fail_count += 1
                if proxy.fail_count >= PROXY_FAIL_THRESHOLD:  # 使用常量
                    proxy.is_active = False
                await session.commit()
    async def delete_proxy(self, proxy_id: int) -> bool:
        """删除代理，并在同一事务里解绑仍指向它的账号。

        界面文案承诺「绑定该节点的账号将回退为直连」，但此前只删了代理行：
        accounts.proxy_id 悬空指向一个不存在的 ID——签到总览把这些账号显示成「代理挂起」；
        且 proxies 主键不是 AUTOINCREMENT，删掉最大 ID 后再新增会复用同一个 ID，
        批量删再批量加时，原账号会被静默绑到新代理上（出口 IP 突变）（2026-11-24 体检）。
        """
        async with self.async_session() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy:
                await session.execute(
                    update(Account)
                    .where(Account.proxy_id == proxy_id)
                    .values(proxy_id=None, proxy_bind_time=None)
                )
                await session.delete(proxy)
                await session.commit()
                return True
            return False
    async def update_proxy(self, proxy_id: int, **kwargs) -> Proxy | None:
        """更新代理信息"""
        from ...core.account import encrypt_value
        async with self.async_session() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy:
                for key, value in kwargs.items():
                    if hasattr(proxy, key):
                        # 对于认证信息执行加密
                        if key in ("username", "password") and value:
                            value = encrypt_value(value)
                        setattr(proxy, key, value)
                await session.commit()
                await session.refresh(proxy)
                return proxy
            return None
