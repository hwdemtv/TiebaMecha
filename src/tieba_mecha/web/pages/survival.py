"""存活分析页面 - 查看物料存活状态，支持筛选和分页"""

import flet as ft
from ..flet_compat import COLORS
from ..utils import with_opacity
from ..components.icons import (
    ARROW_BACK_IOS_NEW, ANALYTICS_OUTLINED, CHECK_CIRCLE,
    ERROR, REMOVE_CIRCLE_OUTLINED, OPEN_IN_NEW, INFO_OUTLINED,
    DELETE_OUTLINE
)


class SurvivalPage:
    """存活分析页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._stats = {"total": 0, "alive": 0, "dead": 0, "unknown": 0}
        self._account_options = []
        self._fname_options = []  # 贴吧名选项
        self._death_reason_options = []  # 阵亡原因选项
        self._account_name_map = {}  # account_id -> name 映射
        self._current_page = 1
        self._stat_cards_container = None
        self._page_size = 15
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
            # 构建账号 ID -> 名称映射
            self._account_name_map = {a.id: a.name or f"账号{a.id}" for a in accounts}
            # 获取贴吧名列表
            fnames = await self.db.get_distinct_fnames()
            self._fname_options = [ft.dropdown.Option(f, f) for f in fnames]
            # 获取阵亡原因列表
            reasons = await self.db.get_distinct_death_reasons()
            self._death_reason_options = [ft.dropdown.Option(r, r) for r in reasons]
            await self._load_page(1)
        except Exception as e:
            import traceback
            traceback.print_exc()

    async def _load_page(self, page: int):
        """加载指定页"""
        self._current_page = page
        status_filter = self._status_filter.value if hasattr(self, "_status_filter") else None
        account_filter = self._account_filter.value if hasattr(self, "_account_filter") else None
        fname_filter = self._fname_filter.value if hasattr(self, "_fname_filter") else None
        death_reason_filter = self._death_reason_filter.value if hasattr(self, "_death_reason_filter") else None
        date_from_filter = self._date_from.value if hasattr(self, "_date_from") else None
        date_to_filter = self._date_to.value if hasattr(self, "_date_to") else None
        account_id = int(account_filter) if account_filter and account_filter != "all" else None
        fname = fname_filter if fname_filter and fname_filter != "all" else None
        death_reason = death_reason_filter if death_reason_filter and death_reason_filter != "all" else None
        status = status_filter if status_filter and status_filter != "all" else None
        # 解析日期
        date_from = None
        date_to = None
        if date_from_filter:
            try:
                from datetime import datetime
                date_from = datetime.strptime(date_from_filter, "%Y-%m-%d")
            except ValueError:
                pass
        if date_to_filter:
            try:
                from datetime import datetime
                date_to = datetime.strptime(date_to_filter + " 23:59:59", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        materials, total = await self.db.get_materials_paginated(
            survival_status=status,
            account_id=account_id,
            fname=fname,
            death_reason=death_reason,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=self._page_size,
        )
        self._materials = materials
        self._total = total
        self._update_card_list()
        self._update_pagination()

    async def _on_status_change(self, e):
        await self._load_page(1)

    async def _on_account_change(self, e):
        await self._load_page(1)

    async def _on_fname_change(self, e):
        await self._load_page(1)

    async def _on_death_reason_change(self, e):
        await self._load_page(1)

    async def _on_date_change(self, e):
        await self._load_page(1)

    async def _on_prev_page(self, e):
        if self._current_page > 1:
            await self._load_page(self._current_page - 1)

    async def _on_next_page(self, e):
        total_pages = (self._total + self._page_size - 1) // self._page_size
        if self._current_page < total_pages:
            await self._load_page(self._current_page + 1)

    # ========== 统计卡片 ==========

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

    # ========== 筛选栏 ==========

    def _build_filter_bar(self) -> ft.Control:
        """构建筛选栏"""
        self._status_filter = ft.Dropdown(
            label="存活状态",
            value="all",
            width=130,
            text_size=13,
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
            text_size=13,
            options=[ft.dropdown.Option("all", "全部账号")] + self._account_options,
            on_change=self._on_account_change,
        )
        self._fname_filter = ft.Dropdown(
            label="贴吧",
            value="all",
            width=160,
            text_size=13,
            options=[ft.dropdown.Option("all", "全部贴吧")] + self._fname_options,
            on_change=self._on_fname_change,
        )
        self._death_reason_filter = ft.Dropdown(
            label="阵亡原因",
            value="all",
            width=180,
            text_size=13,
            options=[ft.dropdown.Option("all", "全部原因")] + self._death_reason_options,
            on_change=self._on_death_reason_change,
        )
        self._date_from = ft.TextField(
            label="起始日期",
            width=120,
            text_size=12,
            hint_text="YYYY-MM-DD",
            on_submit=self._on_date_change,
        )
        self._date_to = ft.TextField(
            label="结束日期",
            width=120,
            text_size=12,
            hint_text="YYYY-MM-DD",
            on_submit=self._on_date_change,
        )
        return ft.Row(
            [self._status_filter, self._account_filter, self._fname_filter, self._death_reason_filter, self._date_from, self._date_to, ft.IconButton(
                icon="search",
                icon_size=18,
                tooltip="搜索",
                on_click=self._on_date_change,
            )],
            spacing=10,
            wrap=True,
        )

    # ========== 数据回调 ==========

    def on_data_loaded(self):
        """数据加载完成后的回调 - 更新统计卡片和筛选下拉框选项"""
        if self._stat_cards_container:
            self._stat_cards_container.content = ft.Row(self._build_stat_cards(), spacing=10)
        if hasattr(self, "_account_filter") and self._account_options:
            self._account_filter.options = [ft.dropdown.Option("all", "全部账号")] + self._account_options
        if hasattr(self, "_fname_filter") and self._fname_options:
            self._fname_filter.options = [ft.dropdown.Option("all", "全部贴吧")] + self._fname_options
        if hasattr(self, "_death_reason_filter") and self._death_reason_options:
            self._death_reason_filter.options = [ft.dropdown.Option("all", "全部原因")] + self._death_reason_options
        self.page.update()

    # ========== 卡片列表 ==========

    def _build_card_list(self) -> ft.Control:
        """构建卡片列表区域"""
        self._card_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        return ft.Container(
            content=self._card_list,
            expand=True,
            border=ft.border.all(1, with_opacity(0.1, "onSurface")),
            border_radius=8,
            padding=10,
        )

    def _build_material_card(self, m) -> ft.Control:
        """构建单个物料卡片"""
        status_map = {
            "alive": (CHECK_CIRCLE, "存活", COLORS.GREEN),
            "dead": (ERROR, "阵亡", "error"),
            "unknown": (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"),
        }
        icon, label, color = status_map.get(m.survival_status, (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"))

        # 账号名
        account_name = self._account_name_map.get(m.posted_account_id, str(m.posted_account_id or "-"))

        # 标题（优先 title，其次 content 截取）
        title = m.title or ""
        if not title and m.content:
            title = m.content[:40] + ("..." if len(m.content) > 40 else "")
        if not title:
            title = "无标题"

        # 时间
        posted_time = m.posted_time.strftime("%m-%d %H:%M") if m.posted_time else "-"

        # 状态标签
        status_badge = ft.Container(
            content=ft.Row([ft.Icon(icon, size=14, color=color), ft.Text(label, size=12, color=color, weight=ft.FontWeight.W_500)], spacing=4),
            bgcolor=with_opacity(0.08, color),
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
        )

        # 顶行：标题 + 状态标签
        top_row = ft.Row([
            ft.Text(title, size=14, weight=ft.FontWeight.W_500, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            status_badge,
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        # 底行：元信息
        meta_items = []
        if m.posted_fname:
            meta_items.append(ft.Row([ft.Icon(INFO_OUTLINED, size=12, color="onSurfaceVariant"), ft.Text(m.posted_fname, size=11, color="onSurfaceVariant")], spacing=3))
        meta_items.append(ft.Row([ft.Icon(INFO_OUTLINED, size=12, color="onSurfaceVariant"), ft.Text(account_name, size=11, color="onSurfaceVariant")], spacing=3))
        meta_items.append(ft.Text(posted_time, size=11, color="onSurfaceVariant"))

        # 阵亡原因
        if m.survival_status == "dead" and m.death_reason:
            meta_items.append(ft.Text(f"原因: {m.death_reason}", size=11, color="error", italic=True))

        bottom_row = ft.Row(meta_items, spacing=12, wrap=True)

        card = ft.Container(
            content=ft.Column([top_row, bottom_row], spacing=6),
            bgcolor=with_opacity(0.02, color) if m.survival_status != "unknown" else with_opacity(0.02, "onSurface"),
            border=ft.border.all(1, with_opacity(0.15, color) if m.survival_status != "unknown" else with_opacity(0.1, "onSurface")),
            border_radius=8,
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
            on_click=lambda e, mat=m: self._show_detail(mat),
            ink=True,
        )
        return card

    def _update_card_list(self):
        """更新卡片列表"""
        if not hasattr(self, "_card_list"):
            return
        cards = [self._build_material_card(m) for m in self._materials]
        if not cards:
            cards = [ft.Container(
                content=ft.Text("暂无数据", size=13, color="onSurfaceVariant", text_align=ft.TextAlign.CENTER),
                alignment=ft.alignment.center,
                padding=30,
            )]
        self._card_list.controls = cards
        self.page.update()

    def _show_detail(self, m):
        """点击卡片弹出详情"""
        status_map = {
            "alive": (CHECK_CIRCLE, "存活", COLORS.GREEN),
            "dead": (ERROR, "阵亡", "error"),
            "unknown": (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"),
        }
        icon, label, color = status_map.get(m.survival_status, (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"))
        account_name = self._account_name_map.get(m.posted_account_id, str(m.posted_account_id or "-"))

        # 帖子链接
        tid_row = ft.Row([])
        if m.posted_tid and m.posted_fname:
            tid_row = ft.Row([
                ft.Text(f"TID: {m.posted_tid}", size=12, color="onSurfaceVariant"),
                ft.IconButton(
                    icon=OPEN_IN_NEW,
                    icon_size=14,
                    icon_color=COLORS.PRIMARY,
                    tooltip="在浏览器中打开",
                    on_click=lambda e: self.page.launch_url(f"https://tieba.baidu.com/p/{m.posted_tid}"),
                ),
            ])

        content_text = m.content or "无内容"
        # 限制详情弹窗中的内容长度
        if len(content_text) > 500:
            content_text = content_text[:500] + f"\n\n... (共 {len(m.content)} 字)"

        detail_items = [
            ft.Row([ft.Icon(icon, color=color, size=18), ft.Text(f"状态: {label}", size=14, color=color, weight=ft.FontWeight.W_500)]),
            ft.Divider(height=1),
            ft.Row([ft.Text("标题:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(m.title or "无", size=12, expand=True, selectable=True)]),
            ft.Row([ft.Text("贴吧:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(m.posted_fname or "-", size=12)]),
            ft.Row([ft.Text("账号:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(account_name, size=12)]),
            tid_row,
            ft.Row([ft.Text("发布时间:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(m.posted_time.strftime("%Y-%m-%d %H:%M") if m.posted_time else "-", size=12)]),
        ]

        # 阵亡原因
        if m.survival_status == "dead" and m.death_reason:
            detail_items.append(ft.Row([ft.Text("阵亡原因:", size=12, weight=ft.FontWeight.BOLD, color="error"), ft.Text(m.death_reason, size=12, color="error", expand=True, selectable=True)]))

        # 最后检测时间
        if m.last_checked_at:
            detail_items.append(ft.Row([ft.Text("最后检测:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(m.last_checked_at.strftime("%Y-%m-%d %H:%M"), size=12)]))

        detail_items.append(ft.Divider(height=1))
        detail_items.append(ft.Text("内容:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"))
        detail_items.append(ft.Container(
            content=ft.Column([ft.Text(content_text, size=12, selectable=True)], scroll=ft.ScrollMode.AUTO, height=180),
            bgcolor=with_opacity(0.03, "onSurface"),
            border_radius=6,
            padding=10,
        ))

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icon, color=color), ft.Text(f"物料详情 #{m.id}")]),
            content=ft.Container(
                content=ft.Column(detail_items, spacing=8, scroll=ft.ScrollMode.AUTO),
                width=500,
                height=500,
            ),
            actions=[
                ft.TextButton("删除", icon=DELETE_OUTLINE, on_click=lambda e: self._confirm_delete(m, dialog)),
                ft.TextButton("关闭", on_click=lambda e: self.page.close(dialog)),
            ],
        )
        self.page.open(dialog)

    def _confirm_delete(self, m, parent_dialog):
        """显示删除确认弹窗"""
        self.page.close(parent_dialog)
        confirm = ft.AlertDialog(
            title=ft.Text("确认删除"),
            content=ft.Text(f"确定要删除物料 #{m.id} 吗？此操作不可撤销。"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(confirm)),
                ft.TextButton("删除", style=ft.ButtonStyle(color="error"), on_click=lambda e: self._do_delete(m.id, confirm)),
            ],
        )
        self.page.open(confirm)

    async def _do_delete(self, material_id: int, confirm_dialog):
        """执行删除"""
        self.page.close(confirm_dialog)
        if self.db:
            await self.db.delete_material(material_id)
            # 重新加载统计数据和当前页
            self._stats = await self.db.get_survival_stats()
            if self._stat_cards_container:
                self._stat_cards_container.content = ft.Row(self._build_stat_cards(), spacing=10)
            await self._load_page(self._current_page)

    # ========== 分页控件 ==========

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

    def _update_pagination(self):
        """更新分页信息"""
        if not hasattr(self, "_page_info"):
            return
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._page_info.value = f"第 {self._current_page} / {total_pages} 页，共 {self._total} 条"
        self._prev_btn.disabled = self._current_page <= 1
        self._next_btn.disabled = self._current_page >= total_pages
        self.page.update()

    # ========== 页面构建 ==========

    def build(self) -> ft.Control:
        """构建页面"""
        # 创建统计卡片容器（保存引用以便后续更新）
        self._stat_cards_container = ft.Container(
            content=ft.Row(self._build_stat_cards(), spacing=10),
            padding=ft.padding.only(top=15, left=20, right=20),
        )
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
                self._stat_cards_container,
                ft.Divider(height=10, color="transparent"),
                ft.Container(
                    content=self._build_filter_bar(),
                    padding=ft.padding.only(left=20, right=20),
                ),
                ft.Container(
                    content=self._build_card_list(),
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
