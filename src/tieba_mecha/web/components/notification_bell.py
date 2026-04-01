"""通知铃铛组件 - 显示在导航栏"""

import asyncio
from typing import TYPE_CHECKING, Optional, Callable

import flet as ft

if TYPE_CHECKING:
    from ...core.notification import NotificationManager


class NotificationBell(ft.Container):
    """通知铃铛组件 - 显示在导航栏顶部"""

    def __init__(
        self,
        page: ft.Page,
        on_click: Optional[Callable] = None,
        on_panel_open: Optional[Callable] = None,
    ):
        super().__init__()
        self.page = page
        self._on_click = on_click
        self._on_panel_open = on_panel_open
        self._unread_count = 0
        self._notification_manager = None
        self._panel_visible = False

        # 创建铃铛图标（带徽章）
        self._badge = ft.Badge(
            content=ft.IconButton(
                icon=ft.icons.NOTIFICATIONS_OUTLINED,
                selected_icon=ft.icons.NOTIFICATIONS,
                icon_color="white",
                tooltip="通知中心",
                on_click=self._on_bell_click,
            ),
            text="0",
            visible=False,
            text_color="white",
            bgcolor=ft.colors.RED,
        )

        self.content = self._badge

    def set_notification_manager(self, manager: "NotificationManager"):
        """设置通知管理器"""
        self._notification_manager = manager

    async def update_count(self, count: int):
        """更新未读数量"""
        self._unread_count = count
        self._badge.text = str(count) if count < 100 else "99+"
        self._badge.visible = count > 0
        try:
            await self.page.update_async()
        except Exception:
            pass

    async def _on_bell_click(self, e):
        """点击铃铛显示通知面板"""
        if self._on_click:
            await self._on_click(e)
        else:
            await self._toggle_panel()

    async def _toggle_panel(self):
        """切换通知面板显示状态"""
        self._panel_visible = not self._panel_visible
        if self._panel_visible and self._on_panel_open:
            await self._on_panel_open()

    async def refresh(self):
        """刷新未读数量"""
        if not self._notification_manager:
            return

        count = await self._notification_manager.get_unread_count()
        await self.update_count(count)


class NotificationPanel(ft.AlertDialog):
    """通知面板 - 显示通知列表"""

    def __init__(
        self,
        page: ft.Page,
        notification_manager: "NotificationManager" = None,
    ):
        self.page = page
        self._notification_manager = notification_manager
        self._notifications = []

        # 通知列表容器
        self._list_container = ft.Column(
            controls=[],
            scroll=ft.ScrollMode.AUTO,
            height=400,
            width=450,
        )

        # 标题栏
        self._title_row = ft.Row(
            controls=[
                ft.Text("通知中心", size=18, weight=ft.FontWeight.BOLD, expand=True),
                ft.TextButton(
                    "全部已读",
                    on_click=self._mark_all_read,
                    visible=False,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        super().__init__(
            title=self._title_row,
            content=self._list_container,
            actions=[
                ft.TextButton("关闭", on_click=self._close),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def set_notification_manager(self, manager: "NotificationManager"):
        """设置通知管理器"""
        self._notification_manager = manager

    async def load_notifications(self):
        """加载通知列表"""
        if not self._notification_manager:
            self._list_container.controls = [
                ft.Text("通知服务未初始化", color="grey", text_align=ft.TextAlign.CENTER),
            ]
            return

        self._notifications = await self._notification_manager.get_all(limit=50)

        if not self._notifications:
            self._list_container.controls = [
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.icons.NOTIFICATIONS_NONE, size=48, color="grey"),
                        ft.Text("暂无通知", color="grey"),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=40,
                    alignment=ft.alignment.center,
                ),
            ]
            self._title_row.controls[1].visible = False
            return

        # 构建 UI
        unread_count = sum(1 for n in self._notifications if not n.is_read)
        self._title_row.controls[1].visible = unread_count > 0

        controls = []
        for n in self._notifications:
            controls.append(self._build_notification_item(n))

        self._list_container.controls = controls

    def _build_notification_item(self, notification) -> ft.Control:
        """构建单条通知项"""
        # 根据类型选择图标和颜色
        icon_map = {
            "post_success": (ft.icons.CHECK_CIRCLE, "green"),
            "post_failed": (ft.icons.ERROR, "red"),
            "update_available": (ft.icons.NEW_RELEASES, "blue"),
            "account_expired": (ft.icons.WARNING, "red"),
            "proxy_failed": (ft.icons.WARNING_AMBER, "orange"),
            "warning": (ft.icons.WARNING_AMBER, "orange"),
            "error": (ft.icons.ERROR, "red"),
            "info": (ft.icons.INFO, "blue"),
        }

        icon, color = icon_map.get(notification.type, (ft.icons.NOTIFICATIONS, "grey"))

        # 未读标记
        unread_indicator = ft.Container(
            bgcolor=ft.colors.BLUE if not notification.is_read else "transparent",
            width=4,
            height=40,
            border_radius=2,
        )

        # 时间格式化
        time_str = ""
        if notification.created_at:
            time_str = notification.created_at.strftime("%m-%d %H:%M")

        return ft.Container(
            content=ft.Row(
                controls=[
                    unread_indicator,
                    ft.Icon(icon, color=color, size=20),
                    ft.Column(
                        controls=[
                            ft.Text(
                                notification.title,
                                size=14,
                                weight=ft.FontWeight.BOLD if not notification.is_read else ft.FontWeight.NORMAL,
                                color="white" if not notification.is_read else "grey",
                            ),
                            ft.Text(
                                notification.message[:100] + ("..." if len(notification.message) > 100 else ""),
                                size=12,
                                color="grey",
                            ),
                            ft.Text(time_str, size=10, color="grey"),
                        ],
                        expand=True,
                        spacing=4,
                    ),
                    ft.IconButton(
                        icon=ft.icons.OPEN_IN_NEW if notification.action_url else ft.icons.CHECK,
                        icon_size=16,
                        visible=bool(notification.action_url or not notification.is_read),
                        on_click=lambda e, n=notification: self._on_item_click(n),
                    ),
                ],
                spacing=10,
            ),
            padding=ft.padding.symmetric(horizontal=8, vertical=8),
            border=ft.border.all(1, ft.colors.with_opacity(0.1, "grey")) if not notification.is_read else None,
            border_radius=8,
            on_click=lambda e, n=notification: self._on_item_click(n),
        )

    async def _on_item_click(self, notification):
        """点击通知项"""
        # 标记已读
        if not notification.is_read and self._notification_manager:
            await self._notification_manager.mark_read(notification.id)

        # 打开链接
        if notification.action_url:
            self.page.launch_url(notification.action_url)

        # 刷新列表
        await self.load_notifications()
        await self.page.update_async()

    async def _mark_all_read(self, e):
        """全部标记已读"""
        if self._notification_manager:
            await self._notification_manager.mark_all_read()
        await self.load_notifications()
        await self.page.update_async()

    async def _close(self, e):
        """关闭面板"""
        self.page.close(self)


async def show_notification_dialog(page: ft.Page, notification_manager: "NotificationManager"):
    """显示通知对话框"""
    panel = NotificationPanel(page, notification_manager)
    await panel.load_notifications()
    page.open(panel)
    await page.update_async()
