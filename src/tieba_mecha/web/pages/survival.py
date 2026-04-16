"""存活分析页面 - 查看物料存活状态，支持筛选和分页"""

import flet as ft
from ..flet_compat import COLORS
from ..utils import with_opacity
from ..components.icons import (
    ARROW_BACK_IOS_NEW, ANALYTICS_OUTLINED, CHECK_CIRCLE,
    ERROR, REMOVE_CIRCLE_OUTLINED
)


class SurvivalPage:
    """存活分析页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._stats = {"total": 0, "alive": 0, "dead": 0, "unknown": 0}
        self._account_options = []
        self._current_page = 1
        self._page_size = 20
        self._total = 0
        self._materials = []

    async def load_data(self):
        """加载数据"""
        if not self.db:
            return
        try:
            self._stats = await self.db.get_survival_stats()
            accounts = await self.db.get_accounts()
            self._account_options = [
                ft.dropdown.Option(str(a.id), a.name or f"账号{a.id}")
                for a in accounts
            ]
            await self._load_page(1)
        except Exception as e:
            import traceback
            traceback.print_exc()

    async def _load_page(self, page: int):
        """加载指定页"""
        self._current_page = page
        status_filter = self._status_filter.value if hasattr(self, "_status_filter") else None
        account_filter = self._account_filter.value if hasattr(self, "_account_filter") else None
        account_id = int(account_filter) if account_filter and account_filter != "all" else None
        status = status_filter if status_filter and status_filter != "all" else None
        materials, total = await self.db.get_materials_paginated(
            survival_status=status,
            account_id=account_id,
            page=page,
            page_size=self._page_size,
        )
        self._materials = materials
        self._total = total
        self._update_table()
        self._update_pagination()

    async def _on_status_change(self, e):
        await self._load_page(1)

    async def _on_account_change(self, e):
        await self._load_page(1)

    async def _on_prev_page(self, e):
        if self._current_page > 1:
            await self._load_page(self._current_page - 1)

    async def _on_next_page(self, e):
        total_pages = (self._total + self._page_size - 1) // self._page_size
        if self._current_page < total_pages:
            await self._load_page(self._current_page + 1)

    def _build_stat_cards(self) -> list[ft.Control]:
        """构建统计卡片"""
        stats = self._stats
        total = stats["total"] or 1
        rate = stats["alive"] / total * 100

        def card(icon, label, value, color):
            return ft.Container(
                content=ft.Column([
                    ft.Row([ft.Icon(icon, color=color, size=22), ft.Text(label, size=11, color="onSurfaceVariant")], spacing=5),
                    ft.Text(str(value), size=28, weight=ft.FontWeight.BOLD, color=color),
                ], spacing=2),
                padding=15,
                bgcolor=with_opacity(0.03, color),
                border=ft.border.all(1, with_opacity(0.2, color)),
                border_radius=10,
                expand=1,
            )

        cards = [
            card(CHECK_CIRCLE, "存活 / ALIVE", stats["alive"], COLORS.GREEN),
            card(ERROR, "阵亡 / DEAD", stats["dead"], "error"),
            card(REMOVE_CIRCLE_OUTLINED, "未知 / UNKNOWN", stats["unknown"], "onSurfaceVariant"),
        ]

        # 存活率进度条卡片
        progress_card = ft.Container(
            content=ft.Column([
                ft.Row([ft.Text("存活率 / SURVIVAL", size=11, color="onSurfaceVariant")], spacing=5),
                ft.Text(f"{rate:.1f}%", size=28, weight=ft.FontWeight.BOLD, color=COLORS.PRIMARY),
                ft.Container(
                    content=ft.Stack([
                        ft.Container(
                            width=max(4, min(280, rate / 100 * 280)),
                            height=8,
                            bgcolor=COLORS.PRIMARY,
                            border_radius=4,
                        ),
                        ft.Container(
                            height=8,
                            bgcolor=with_opacity(0.15, "onSurface"),
                            border_radius=4,
                        ),
                    ]),
                    width=280,
                ),
            ], spacing=4),
            padding=15,
            bgcolor=with_opacity(0.03, COLORS.PRIMARY),
            border=ft.border.all(1, with_opacity(0.2, COLORS.PRIMARY)),
            border_radius=10,
            expand=1,
        )
        cards.append(progress_card)
        return cards

    def _build_filter_bar(self) -> ft.Control:
        """构建筛选栏"""
        self._status_filter = ft.Dropdown(
            label="存活状态",
            value="all",
            width=130,
            options=[
                ft.dropdown.Option("all", "全部"),
                ft.dropdown.Option("alive", "存活"),
                ft.dropdown.Option("dead", "阵亡"),
                ft.dropdown.Option("unknown", "未知"),
            ],
            on_change=self._on_status_change,
        )
        self._account_filter = ft.Dropdown(
            label="账号",
            value="all",
            width=160,
            options=[ft.dropdown.Option("all", "全部账号")] + self._account_options,
            on_change=self._on_account_change,
        )
        return ft.Row(
            [self._status_filter, self._account_filter],
            spacing=15,
        )

    def _build_table(self) -> ft.Control:
        """构建数据表格"""
        self._table = ft.DataTable(
            column_spacing=20,
            heading_row_height=36,
            data_row_min_height=40,
            columns=[
                ft.DataColumn(ft.Text("账号", size=12, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("贴吧", size=12, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("标题", size=12, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("状态", size=12, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("发布时间", size=12, weight=ft.FontWeight.BOLD)),
            ],
            rows=[],
        )
        return ft.Container(
            content=ft.Column([self._table], scroll=ft.ScrollMode.AUTO),
            expand=True,
            border=ft.border.all(1, with_opacity(0.1, "onSurface")),
            border_radius=8,
            padding=5,
        )

    def _build_pagination(self) -> ft.Control:
        """构建分页控件"""
        self._page_info = ft.Text("", size=12, color="onSurfaceVariant")
        self._prev_btn = ft.IconButton(
            icon=ARROW_BACK_IOS_NEW,
            icon_size=16,
            on_click=self._on_prev_page,
            disabled=True,
        )
        self._next_btn = ft.IconButton(
            icon=ARROW_BACK_IOS_NEW,
            icon_size=16,
            rotate=3.14159,
            on_click=self._on_next_page,
            disabled=True,
        )
        return ft.Row(
            [self._prev_btn, self._page_info, self._next_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
        )

    def _update_table(self):
        """更新表格数据"""
        if not hasattr(self, "_table"):
            return
        rows = []
        for m in self._materials:
            status_map = {
                "alive": (CHECK_CIRCLE, "存活", COLORS.GREEN),
                "dead": (ERROR, "阵亡", "error"),
                "unknown": (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"),
            }
            icon, label, color = status_map.get(m.survival_status, ("", "未知", "onSurfaceVariant"))
            posted_time = m.posted_time.strftime("%m-%d %H:%M") if m.posted_time else "-"
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(str(m.posted_account_id or "-"), size=11)),
                ft.DataCell(ft.Text(m.posted_fname or "-", size=11)),
                ft.DataCell(ft.Text((m.content[:20] + "...") if m.content and len(m.content) > 20 else (m.content or "-"), size=11)),
                ft.DataCell(ft.Row([ft.Icon(icon, size=14, color=color), ft.Text(label, size=11, color=color)], spacing=4)),
                ft.DataCell(ft.Text(posted_time, size=11)),
            ]))
        self._table.rows = rows
        self.page.update()

    def _update_pagination(self):
        """更新分页信息"""
        if not hasattr(self, "_page_info"):
            return
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._page_info.value = f"第 {self._current_page} / {total_pages} 页，共 {self._total} 条"
        self._prev_btn.disabled = self._current_page <= 1
        self._next_btn.disabled = self._current_page >= total_pages
        self.page.update()

    def build(self) -> ft.Control:
        """构建页面"""
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=ARROW_BACK_IOS_NEW,
                        icon_size=16,
                        on_click=lambda e: self._navigate("dashboard"),
                        style=ft.ButtonStyle(
                            color=COLORS.PRIMARY,
                            bgcolor={"": with_opacity(0.1, COLORS.PRIMARY)},
                        ),
                    ),
                    padding=5,
                ),
                ft.Row([ft.Icon(ANALYTICS_OUTLINED, color=COLORS.PRIMARY, size=22), ft.Text("存活分析", size=18, weight=ft.FontWeight.BOLD, color=COLORS.PRIMARY)], spacing=8),
            ],
        )

        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                ft.Container(
                    content=ft.Column(self._build_stat_cards(), spacing=10, expand=True),
                    padding=ft.padding.only(top=15, left=20, right=20),
                ),
                ft.Divider(height=10, color="transparent"),
                ft.Container(
                    content=self._build_filter_bar(),
                    padding=ft.padding.only(left=20, right=20),
                ),
                ft.Container(
                    content=self._build_table(),
                    padding=ft.padding.only(left=20, right=20, top=10),
                    expand=True,
                ),
                ft.Container(
                    content=self._build_pagination(),
                    padding=15,
                ),
            ], spacing=10, expand=True),
            padding=15,
            expand=True,
        )

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)
