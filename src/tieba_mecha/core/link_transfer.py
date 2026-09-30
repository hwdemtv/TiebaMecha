"""LinkTransfer - 链接转存后端插槽（预留骨架，本期无行为）

采集物料携带的是别人的网盘链接（material_pool.source_link_url）。
"转存替换"= 把源链资源存到自己网盘、把自有新链写回 material_pool.link_url，
统一走 db.mark_link_transferred()（唯一合法写路径）。

本模块是未来自动转存后端（百度 PCS / 夸克 / 迅雷等 API 客户端）的注册点：
实现 LinkTransferBackend 协议后调用 register_link_transfer_backend() 即接入，
采集→转存→审核的其余链路无需改动。首期 get_link_transfer_backend() 恒返回
None，调用方据此降级为人工转存。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class TransferResult:
    """单次转存结果"""
    ok: bool
    new_link: str = ""   # 自有新链（可含百度盘 ?pwd= 内联码）
    new_note: str = ""   # 自有提取码/备注（追加进 source_link_note）
    error: str = ""


class LinkTransferBackend(Protocol):
    """转存后端协议：source_url → 自有新链"""

    async def transfer(self, source_url: str, note: str = "") -> TransferResult:
        ...


_registered: Optional[LinkTransferBackend] = None


def register_link_transfer_backend(backend: LinkTransferBackend) -> None:
    """注册全局转存后端（后端启动时调用一次）"""
    global _registered
    _registered = backend


def get_link_transfer_backend() -> Optional[LinkTransferBackend]:
    """取当前注册的后端；None 表示未接入，调用方降级人工转存"""
    return _registered
