"""通知铃铛组件 - 显示在导航栏"""

import asyncio
from typing import TYPE_CHECKING, Optional, Callable

import flet as ft
from ..utils import with_opacity
from ..flet_compat import COLORS
from .icons import (
    NOTIFICATIONS_OUTLINED, NOTIFICATIONS, REFRESH_ROUNDED,
    DELETE_SWEEP_OUTLINED, NOTIFICATIONS_NONE, CHECK_CIRCLE,
    ERROR, NEW_RELEASES, WARNING, WARNING_AMBER, INFO,
    OPEN_IN_NEW, CHECK, DELETE_OUTLINE
)

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
        super().__init__(visible=False)
        self._page = page  # 使用私有变量存储
        self._on_click = on_click
        self._on_panel_open = on_panel_open
        self._unread_count = 0
        self._notification_manager = None
        self._panel_visible = False

        # 创建铃铛图标按钮
        self._icon_button = ft.IconButton(
            icon=NOTIFICATIONS_OUTLINED,
            selected_icon=NOTIFICATIONS,
            icon_color="white",
            tooltip="通知中心",
            on_click=self._on_bell_click,
        )

        # 徽章标签（显示未读数）
        self._badge_label = ft.Badge(
            text=None,
            bgcolor=COLORS.RED,
            text_color="white",
            visible=False,
        )

        # 用 Stack 叠加徽章和图标
        self.content = ft.Stack(
            controls=[
                self._icon_button,
                self._badge_label,
            ],
            width=48,
            height=48,
        )

    def set_notification_manager(self, manager: "NotificationManager"):
        """设置通知管理器并订阅更新"""
        self._notification_manager = manager
        if manager:
            # 订阅状态变更（新消息、已读、删除都会触发）
            manager.add_listener(lambda _: self._page.run_task(self.refresh))

    async def update_count(self, count: int):
        """更新未读数量"""
        self._unread_count = count
        label_text = str(count) if count > 0 else None
        # 如果 count >= 100，使用 99+
        if count >= 100:
            label_text = "99+"

        # 更新徽章标签 - 用 visible 测试控制红点显示与隐藏
        self._badge_label.text = label_text
        self._badge_label.visible = count > 0
        
        # 没有新消息时彻底隐藏整个铃铛入口
        self.visible = count > 0

        try:
            # 仅在控件已挂载到页面控件树后才调用 update，
            # 防止监听器在铃铛渲染前触发导致 AssertionError
            if self.page:
                await self._page.update_async()
        except Exception:
            pass

    async def _on_bell_click(self, e):
        """点击铃铛显示通知面板"""
        if self._on_click:
            # 如果是 lambda 直接调用，或者是普通函数
            res = self._on_click(e)
            # 只有当结果本身是 coroutine/awaitable 时才 await
            if asyncio.iscoroutine(res):
                await res
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
        self._page = page  # 使用私有变量存储
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
                ft.IconButton(
                    icon=REFRESH_ROUNDED,
                    tooltip="同步系统远程通知",
                    on_click=self._on_refresh_remote,
                    icon_size=18,
                ),
                ft.IconButton(
                    icon=DELETE_SWEEP_OUTLINED,
                    tooltip="清理所有已读通知",
                    on_click=self._on_clear_read,
                    icon_size=18,
                    icon_color=COLORS.ON_SURFACE_VARIANT,
                ),
                ft.TextButton(
                    "全部已读",
                    on_click=self._mark_all_read_btn,
                    style=ft.ButtonStyle(color=COLORS.PRIMARY),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
                        ft.Icon(NOTIFICATIONS_NONE, size=48, color="grey"),
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
        self._title_row.controls[2].visible = unread_count > 0

        controls = []
        for n in self._notifications:
            controls.append(self._build_notification_item(n))

        self._list_container.controls = controls

    def _build_notification_item(self, notification) -> ft.Control:
        """构建单条通知项"""
        # 根据类型选择图标和颜色
        icon_map = {
            "post_success": (CHECK_CIRCLE, "green"),
            "post_failed": (ERROR, "red"),
            "update_available": (NEW_RELEASES, "blue"),
            "account_expired": (WARNING, "red"),
            "proxy_failed": (WARNING_AMBER, "orange"),
            "warning": (WARNING_AMBER, "orange"),
            "error": (ERROR, "red"),
            "info": (INFO, "blue"),
        }

        icon, color = icon_map.get(notification.type, (NOTIFICATIONS, "grey"))

        # 未读标记
        unread_indicator = ft.Container(
            bgcolor=COLORS.BLUE if not notification.is_read else "transparent",
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
                    ft.Row([
                        ft.IconButton(
                            icon=OPEN_IN_NEW if notification.action_url else CHECK,
                            icon_size=16,
                            tooltip="查看/标记已读",
                            visible=bool(notification.action_url or not notification.is_read),
                            on_click=lambda e, n=notification: self._page.run_task(self._on_item_click, n),
                        ),
                        ft.IconButton(
                            icon=DELETE_OUTLINE,
                            icon_size=16,
                            icon_color=COLORS.ERROR,
                            tooltip="删除通知",
                            on_click=lambda e, n=notification: self._page.run_task(self._on_delete_item, n),
                        ),
                    ], spacing=0),
                ],
                spacing=10,
            ),
            padding=ft.padding.symmetric(horizontal=8, vertical=8),
            border=ft.border.all(1, with_opacity(0.1, "grey")) if not notification.is_read else None,
            border_radius=8,
            on_click=lambda e, n=notification: self._page.run_task(self._on_item_click, n),
        )

    async def _on_item_click(self, notification):
        """点击通知项"""
        # 标记已读
        if not notification.is_read and self._notification_manager:
            await self._notification_manager.mark_read(notification.id)

        # 打开链接
        if notification.action_url:
            self._page.launch_url(notification.action_url)

        # 刷新列表
        await self.load_notifications()
        await self._page.update_async()

    async def _mark_all_read_btn(self, e):
        """全部标记已读"""
        if self._notification_manager:
            await self._notification_manager.mark_all_read()
        await self.load_notifications()
        await self._page.update_async()

    async def _on_delete_item(self, notification):
        """删除单条通知"""
        if self._notification_manager:
            await self._notification_manager.delete_notification(notification.id)
        await self.load_notifications()
        await self._page.update_async()

    async def _on_clear_read(self, e):
        """清理所有已读通知"""
        if self._notification_manager:
            count = await self._notification_manager.clear_all_read()
            if count > 0:
                self._page.open(ft.SnackBar(content=ft.Text(f"已清理 {count} 条已读通知")))
        await self.load_notifications()
        await self._page.update_async()

    async def _on_refresh_remote(self, e):
        """手动刷新远程通知"""
        if not self._notification_manager:
            return

        e.control.disabled = True
        await self._page.update_async()

        try:
            added = await self._notification_manager.sync_remote_notifications()
            # 刷新列表和计数
            await self.load_notifications()
            # 如果有新通知，显示提示
            if added > 0:
                self._page.open(ft.SnackBar(content=ft.Text(f"同步完成，新增 {added} 条广播通知")))
        except Exception as ex:
            from ...core.logger import log_error
            await log_error(f"[NotificationPanel] Refresh error: {ex}")
        finally:
            e.control.disabled = False
            await self._page.update_async()

    async def _close(self, e):
        """关闭面板"""
        self._page.close(self)


async def show_notification_dialog(page: ft.Page, notification_manager: "NotificationManager"):
    """显示通知对话框"""
    panel = NotificationPanel(page, notification_manager)
    await panel.load_notifications()
    page.open(panel)
    await page.update_async()
