"""系统通知中心 - 支持本地通知和远程通知同步"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

import aiohttp

from ..db.crud import Database


class NotificationType(Enum):
    """通知类型枚举"""
    # 任务相关
    POST_SUCCESS = "post_success"
    POST_FAILED = "post_failed"
    BATCH_COMPLETE = "batch_complete"
    SIGN_COMPLETE = "sign_complete"

    # 账号相关
    ACCOUNT_EXPIRED = "account_expired"
    ACCOUNT_WARNING = "account_warning"

    # 系统相关
    UPDATE_AVAILABLE = "update_available"
    SYSTEM_ALERT = "system_alert"
    PROXY_FAILED = "proxy_failed"

    # 养号相关
    MAINT_COMPLETE = "maint_complete"

    # 远程广播
    REMOTE_INFO = "info"
    REMOTE_WARNING = "warning"
    REMOTE_ERROR = "error"


@dataclass
class NotificationData:
    """通知数据结构"""
    id: Optional[int] = None
    type: str = "system_alert"
    title: str = ""
    message: str = ""
    is_read: bool = False
    action_url: Optional[str] = None
    extra: dict = field(default_factory=dict)
    source: str = "local"
    remote_id: Optional[str] = None
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()


class NotificationManager:
    """系统通知管理器 - 单例模式"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db: Database = None, page=None, license_config: dict = None):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self.db = db
            self.page = page
            self._listeners: list[Callable] = []
            self._license_config = license_config or {}
            self._last_remote_check: Optional[datetime] = None

    def set_page(self, page):
        """设置 Flet 页面引用"""
        self.page = page

    def set_db(self, db: Database):
        """设置数据库引用"""
        self.db = db

    def set_license_config(self, license_key: str, device_id: str, server_url: str, product_id: str = "tieba_mecha"):
        """设置许可证配置（用于获取远程通知）"""
        self._license_config = {
            "license_key": license_key,
            "device_id": device_id,
            "server_url": server_url,
            "product_id": product_id,
        }

    async def push(
        self,
        type: NotificationType | str,
        title: str,
        message: str,
        action_url: str = None,
        extra: dict = None,
        show_snackbar: bool = True,
    ):
        """
        推送本地通知

        Args:
            type: 通知类型
            title: 标题
            message: 详细消息
            action_url: 操作链接
            extra: 扩展数据
            show_snackbar: 是否显示即时 SnackBar
        """
        if not self.db:
            return

        type_str = type.value if isinstance(type, NotificationType) else type

        # 持久化到数据库
        notification = await self.db.add_notification(
            type=type_str,
            title=title,
            message=message,
            action_url=action_url,
            extra=extra,
            source="local",
        )

        # 触发监听器
        for listener in self._listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(notification)
                else:
                    listener(notification)
            except Exception as e:
                print(f"[NotificationManager] Listener error: {e}")

        # 显示即时通知
        if show_snackbar and self.page:
            await self._show_snackbar(type_str, title, message)

    async def _show_snackbar(self, type: str, title: str, message: str):
        """显示 Flet SnackBar"""
        import flet as ft

        color_map = {
            "post_success": "green",
            "update_available": "blue",
            "account_expired": "red",
            "post_failed": "red",
            "proxy_failed": "red",
            "warning": "orange",
            "error": "red",
            "info": "blue",
        }

        icon_map = {
            "post_success": ft.icons.CHECK_CIRCLE,
            "post_failed": ft.icons.ERROR,
            "update_available": ft.icons.NEW_RELEASES,
            "account_expired": ft.icons.WARNING,
            "info": ft.icons.INFO,
            "warning": ft.icons.WARNING_AMBER,
            "error": ft.icons.ERROR,
        }

        bg_color = ft.colors.with_opacity(0.9, color_map.get(type, "grey"))
        icon = icon_map.get(type, ft.icons.NOTIFICATIONS)

        try:
            snackbar = ft.SnackBar(
                content=ft.Row([
                    ft.Icon(icon, color="white"),
                    ft.Text(f"{title}: {message}", color="white", expand=True),
                ]),
                bgcolor=bg_color,
                duration=4000,
            )
            self.page.open(snackbar)
            await self.page.update_async()
        except Exception as e:
            print(f"[NotificationManager] SnackBar error: {e}")

    async def fetch_remote_notifications(self) -> list[dict]:
        """
        从 hw-license-center 服务获取远程通知

        Returns:
            远程通知列表
        """
        if not self._license_config:
            return []

        if not self._license_config.get("license_key") or not self._license_config.get("server_url"):
            return []

        try:
            server_url = self._license_config["server_url"].rstrip("/")
            payload = {
                "license_key": self._license_config["license_key"],
                "device_id": self._license_config["device_id"],
                "product_id": self._license_config.get("product_id", "tieba_mecha"),
                "mode": "silent",  # 静默模式，不触发设备绑定
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{server_url}/api/v1/auth/verify",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return []

                    data = await resp.json()
                    if not data.get("success"):
                        return []

                    # 提取通知
                    notification = data.get("notification")
                    if notification:
                        return [notification]

                    return []

        except Exception as e:
            print(f"[NotificationManager] Failed to fetch remote notifications: {e}")
            return []

    async def sync_remote_notifications(self):
        """
        同步远程通知到本地数据库

        Returns:
            新增的通知数量
        """
        if not self.db:
            return 0

        remote_notifications = await self.fetch_remote_notifications()
        added_count = 0

        for rn in remote_notifications:
            remote_id = rn.get("id")
            if not remote_id:
                continue

            # 检查是否已存在
            if await self.db.notification_exists(remote_id):
                continue

            # 入库
            await self.db.add_notification(
                type=rn.get("type", "info"),
                title=rn.get("title", ""),
                message=rn.get("content", ""),
                action_url=rn.get("action_url"),
                extra={"is_force": rn.get("is_force", False)},
                source="remote",
                remote_id=remote_id,
            )
            added_count += 1

            # 如果是强制通知，立即显示
            if rn.get("is_force") and self.page:
                await self._show_snackbar(
                    rn.get("type", "info"),
                    rn.get("title", ""),
                    rn.get("content", ""),
                )

        self._last_remote_check = datetime.now()
        return added_count

    async def get_unread(self, limit: int = 50) -> list[NotificationData]:
        """获取未读通知列表"""
        if not self.db:
            return []

        notifications = await self.db.get_unread_notifications(limit)
        return [
            NotificationData(
                id=n.id,
                type=n.type,
                title=n.title,
                message=n.message,
                is_read=n.is_read,
                action_url=n.action_url,
                extra=json.loads(n.extra_json) if n.extra_json else {},
                source=n.source,
                remote_id=n.remote_id,
                created_at=n.created_at,
            )
            for n in notifications
        ]

    async def get_all(self, limit: int = 100) -> list[NotificationData]:
        """获取所有通知列表"""
        if not self.db:
            return []

        notifications = await self.db.get_all_notifications(limit)
        return [
            NotificationData(
                id=n.id,
                type=n.type,
                title=n.title,
                message=n.message,
                is_read=n.is_read,
                action_url=n.action_url,
                extra=json.loads(n.extra_json) if n.extra_json else {},
                source=n.source,
                remote_id=n.remote_id,
                created_at=n.created_at,
            )
            for n in notifications
        ]

    async def mark_read(self, notification_id: int) -> bool:
        """标记为已读"""
        if not self.db:
            return False
        return await self.db.mark_notification_read(notification_id)

    async def mark_all_read(self) -> int:
        """全部标记已读"""
        if not self.db:
            return 0
        return await self.db.mark_all_notifications_read()

    async def get_unread_count(self) -> int:
        """获取未读数量"""
        if not self.db:
            return 0
        return await self.db.get_unread_count()

    def add_listener(self, callback: Callable):
        """添加事件监听器"""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable):
        """移除事件监听器"""
        if callback in self._listeners:
            self._listeners.remove(callback)


# 全局实例
_notification_manager: Optional[NotificationManager] = None


def init_notification_manager(db: Database = None, page=None) -> NotificationManager:
    """初始化通知管理器"""
    global _notification_manager
    _notification_manager = NotificationManager(db=db, page=page)
    return _notification_manager


def get_notification_manager() -> Optional[NotificationManager]:
    """获取通知管理器实例"""
    return _notification_manager
