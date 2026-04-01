"""Plugins management page with Cyber-Mecha aesthetic"""

import asyncio
import flet as ft
from typing import List, Optional

from ..components import create_gradient_button
from ...core.plugin_loader import get_plugin_manager


class PluginsPage:
    """插件中心页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._plugin_names = []

    async def load_data(self):
        """加载插件数据"""
        manager = get_plugin_manager()
        self._plugin_names = list(manager.plugins.keys())
        self.refresh_ui()

    def refresh_ui(self):
        if hasattr(self, "plugins_list"):
            self.plugins_list.controls = self._build_plugin_items()
            self.page.update()

    def build(self) -> ft.Control:
        # 标题区域
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=ft.icons.ARROW_BACK_IOS_NEW,
                        icon_size=16,
                        on_click=lambda e: self._navigate("dashboard"),
                        style=ft.ButtonStyle(
                            color=ft.colors.PRIMARY,
                            bgcolor={"": ft.colors.with_opacity(0.1, ft.colors.PRIMARY)},
                        ),
                    ),
                    padding=5,
                ),
                ft.Column(
                    controls=[
                        ft.Text("插件中心 / PLUGIN HUB", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("增强核心功能，挂载第三方自动化脚本", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
                ft.Container(expand=True),
                create_gradient_button(
                    text="刷新插件库",
                    icon=ft.icons.REPLAY_ROUNDED,
                    on_click=self._scan_plugins,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # 插件列表容器
        self.plugins_list = ft.Column(
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # 主布局
        return ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Divider(color=ft.colors.with_opacity(0.1, "primary"), height=20),
                    ft.Text("已加载的增强模块", size=14, weight=ft.FontWeight.W_500),
                    ft.Container(
                        content=self.plugins_list,
                        expand=True,
                    ),
                ],
                spacing=10,
            ),
            padding=20,
            expand=True,
        )

    def _build_plugin_items(self) -> list[ft.Control]:
        items = []
        if not self._plugin_names:
            items.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.icons.EXTENSION_OFF, size=50, color="onSurfaceVariant"),
                            ft.Text("未发现可用插件 / plugins 目录为空", color="onSurfaceVariant"),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=50,
                    alignment=ft.alignment.center,
                )
            )
            return items

        for name in self._plugin_names:
            card = ft.Container(
                content=ft.Row(
                    controls=[
                        # 插件图标
                        ft.Container(
                            content=ft.Icon(ft.icons.EXTENSION, color="primary", size=24),
                            padding=10,
                            bgcolor=ft.colors.with_opacity(0.05, "primary"),
                            border_radius=8,
                        ),
                        # 插件信息
                        ft.Column(
                            controls=[
                                ft.Text(name, color="onSurface", size=14, weight=ft.FontWeight.BOLD),
                                ft.Text("内核增强扩展模块", color="onSurfaceVariant", size=11),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        # 执行按钮
                        create_gradient_button(
                            text="运行", 
                            icon=ft.icons.PLAY_ARROW_ROUNDED,
                            on_click=lambda e, n=name: self._run_plugin(n),
                            height=36,
                        ),
                    ],
                ),
                bgcolor=ft.colors.with_opacity(0.02, "onSurface"),
                border=ft.border.all(1, ft.colors.with_opacity(0.05, "onSurface")),
                border_radius=10,
                padding=10,
            )
            items.append(card)
        return items

    async def _scan_plugins(self, e):
        manager = get_plugin_manager()
        self._plugin_names = manager.load_plugins()
        self.refresh_ui()
        self._show_snackbar("多维空间扫描完成，插件库已更新", "success")

    async def _run_plugin(self, name: str):
        self._show_snackbar(f"正在启动插件: {name}", "info")
        manager = get_plugin_manager()
        try:
            result = await manager.run_plugin(name, db=self.db)
            self._show_snackbar(f"插件执行成功: {result}", "success")
        except Exception as ex:
            self._show_snackbar(f"插件执行异常: {str(ex)}", "error")

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=ft.colors.with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
