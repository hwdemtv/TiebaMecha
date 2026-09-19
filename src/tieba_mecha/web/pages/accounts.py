"""Accounts management page with Cyber-Mecha aesthetic"""

import asyncio
import flet as ft
from ..flet_compat import COLORS
from typing import List, Optional

from ..components import create_gradient_button, icons
from ..utils import with_opacity
from ...core.account import add_account, list_accounts, switch_account, remove_account, parse_cookie, verify_account, refresh_account
from ...core.logger import log_info, log_warn, log_error


class AccountsPage:
    """账号管理页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._accounts = []
        self._active_id = None
        self._proxies = []
        self._search_text = ""
        self._filter_status = "all"
        self._selected_ids = set()
        self._sort_mode = "default"
        self._busy = False  # 长耗时操作互斥锁，防止重复点击叠加执行

        # 账号排序：危险状态(封禁/失效) > 异常 > 未知 > 正常
        self._STATUS_SORT_ORDER = {"banned": 0, "invalid": 1, "expired": 2, "error": 3, "unknown": 4, "active": 5}
        
        # 吧库管理相关状态
        self._matrix_stats = []
        self._matrix_search_text = ""
        self._matrix_selected_fnames: set[str] = set()
        self._matrix_banned_filter = False  # 封禁筛选开关
        self._matrix_deleted_filter = False  # 被删筛选开关
        self._banned_forum_details = []
        self._banned_forum_map: dict[str, list[dict]] = {}
        self._active_tab_index = 0

        # 吧库分页
        self._matrix_current_page = 1
        self._matrix_page_size = 20
        self._matrix_filtered_count = 0
        

    async def load_data(self):
        """加载数据（相互独立的查询并行执行）"""
        if not self.db:
            return

        # 并行加载互不依赖的数据
        (
            self._accounts,
            active_acc,
            self._proxies,
        ) = await asyncio.gather(
            list_accounts(self.db),
            self.db.get_active_account(),
            self.db.get_active_proxies(),
        )
        self._active_id = active_acc.id if active_acc else None

        # 加载全吧库统计
        await self._refresh_matrix_stats()

        # [Fix 2] 预加载异常事件数据，避免首次进入 tab 时空白
        if hasattr(self, "exception_list"):
            await self._load_exception_events()

        self.refresh_ui()

    async def _refresh_matrix_stats(self):
        """统一刷新矩阵统计数据（含封禁详情），替代散落各处的单独刷新"""
        # 注：auto_sync_post_target / backfill_success_count 属于全库写操作，
        # 已移至应用启动（app._full_initialize）执行，不再随每次刷新重复写入。
        self._matrix_stats = await self.db.get_forum_matrix_stats()
        self._banned_forum_details = await self.db.get_banned_forums_detail()
        self._banned_forum_map: dict[str, list[dict]] = {}
        for item in self._banned_forum_details:
            self._banned_forum_map.setdefault(item['fname'], []).append(item)

    # ── 长耗时操作互斥与进度反馈 ──

    def _begin_op(self) -> bool:
        """开始一个长耗时操作；已有操作进行中时返回 False，避免叠加执行。"""
        if self._busy:
            self._show_snackbar("已有操作正在执行，请稍候...", "warning")
            return False
        self._busy = True
        return True

    def _end_op(self):
        self._busy = False

    def _open_progress_dialog(self, title: str, determinate: bool = False):
        self._progress_text = ft.Text(title, size=13)
        self._progress_bar = ft.ProgressBar(width=300, value=0 if determinate else None)
        self._progress_dialog = ft.AlertDialog(
            content=ft.Container(
                content=ft.Column([self._progress_text, self._progress_bar], tight=True, spacing=12),
                padding=ft.padding.only(left=10, right=10, top=5, bottom=5),
                width=360,
            ),
            modal=True,
        )
        self.page.open(self._progress_dialog)

    def _update_progress(self, text: str, value: float | None = None):
        if not getattr(self, "_progress_dialog", None):
            return
        self._progress_text.value = text
        if value is not None:
            self._progress_bar.value = value
        try:
            self.page.update()
        except Exception:
            pass

    def _close_progress_dialog(self):
        dialog = getattr(self, "_progress_dialog", None)
        if dialog:
            try:
                self.page.close(dialog)
            except Exception:
                pass
            self._progress_dialog = None

    def refresh_ui(self):
        """刷新 UI（仅重建当前活动 tab 的列表，切换 tab 时由 _on_tab_change 触发重建）"""
        current_tab = self._active_tab_index

        # 账号档案中心
        if hasattr(self, "account_list") and current_tab == 0:
            self.account_list.controls = self._build_account_items()
            # 统计封禁损耗
            banned_count = sum(1 for a in getattr(self, "_accounts", []) if getattr(a, "status", "") == "banned")
            if hasattr(self, "account_stats_info"):
                if banned_count > 0:
                    self.account_stats_info.content.value = f"🚨 战损报警：检测到 {banned_count} 个已封禁账号，点击筛选"
                    self.account_stats_info.visible = True
                else:
                    self.account_stats_info.visible = False
            self.page.update()

        # 全域战略吧库
        if hasattr(self, "matrix_list") and current_tab == 1:
            self.matrix_list.controls = self._build_matrix_items()
            self._update_matrix_header()
            self.page.update()

        # 异常记录
        if hasattr(self, "exception_list") and current_tab == 2:
            self.page.run_task(self._load_exception_events)

    def build(self) -> ft.Control:
        # 主标签页切换逻辑
        self.tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            tabs=[
                ft.Tab(
                    text="账号档案中心",
                    icon=icons.PEOPLE_OUTLINE_ROUNDED,
                    content=self._build_accounts_tab(),
                ),
                ft.Tab(
                    text="全域战略吧库",
                    icon=icons.HUB_ROUNDED,
                    content=self._build_strategic_tab(),
                ),
                ft.Tab(
                    text="异常记录",
                    icon=icons.WARNING_ROUNDED,
                    content=self._build_exception_tab(),
                ),
            ],
            expand=True,
            on_change=self._on_tab_change,
        )

        return ft.Container(
            content=ft.Column(
                controls=[
                    self._build_main_header(),
                    self.tabs,
                ],
                spacing=10,
            ),
            padding=ft.padding.only(left=20, right=20, top=10, bottom=10),
            expand=True,
        )

    def _build_main_header(self):
        """主页面顶部导航栏"""
        return ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=icons.ARROW_BACK_IOS_NEW,
                        icon_size=16,
                        on_click=lambda e: self._navigate("dashboard"),
                        style=ft.ButtonStyle(
                            color=COLORS.PRIMARY,
                            bgcolor={"": with_opacity(0.1, COLORS.PRIMARY)},
                        ),
                    ),
                    padding=5,
                ),
                ft.Column(
                    controls=[
                        ft.Text("矩阵资源管理 / MATRIX HUB", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("指挥中心：账号兵力部署与战略靶场调度", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
                ft.Container(expand=True),
                ft.OutlinedButton(
                    "存活分析",
                    icon=icons.MONITOR_HEART_ROUNDED,
                    tooltip="在分析与风控中心查看账号存活与行为审计",
                    on_click=lambda e: self._navigate("survival"),
                ),
                ft.Row([
                    ft.Text("Antigravity AI 矩阵指挥模块", size=10, color=with_opacity(0.3, "onSurface")),
                    ft.Icon(icons.SHIELD_ROUNDED, size=16, color=with_opacity(0.3, "onSurface")),
                ]),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

    def _build_accounts_tab(self) -> ft.Control:
        """账号管理标签页"""
        # 添加账号按钮
        add_btn = create_gradient_button(
            text="接入账号",
            icon=icons.PERSON_ADD_ROUNDED,
            on_click=self._show_add_dialog,
        )

        # 搜索与过滤栏
        search_field = ft.TextField(
            hint_text="搜索账号、用户名或UID...",
            prefix_icon=icons.SEARCH,
            border_radius=10,
            text_size=13,
            on_change=self._on_search_change,
            bgcolor=with_opacity(0.05, "onSurface"),
            border_color=with_opacity(0.1, "primary"),
            expand=True,
            height=45,
        )

        status_filter = ft.Dropdown(
            options=[
                ft.dropdown.Option("all", "全部状态"),
                ft.dropdown.Option("active", "🟢 正常"),
                ft.dropdown.Option("expired", "🔴 已失效"),
                ft.dropdown.Option("invalid", "⛔ 验证失败"),
                ft.dropdown.Option("error", "🟡 异常"),
                ft.dropdown.Option("banned", "💔 已封禁"),
                ft.dropdown.Option("unknown", "⚪ 未知"),
            ],
            value=self._filter_status,
            on_change=self._on_filter_change,
            width=140,
            height=45,
            content_padding=10,
            text_size=13,
            border_radius=10,
        )

        self._status_filter_dropdown = status_filter

        self._sort_dropdown = ft.Dropdown(
            options=[
                ft.dropdown.Option("default", "默认排序"),
                ft.dropdown.Option("weight", "按权重"),
                ft.dropdown.Option("status", "按状态"),
                ft.dropdown.Option("verified", "按最近验证"),
            ],
            value=self._sort_mode,
            on_change=self._on_sort_change,
            width=120,
            height=45,
            content_padding=10,
            text_size=13,
            border_radius=10,
        )

        # 战损报警横幅（点击一键筛选已封禁账号）
        self.account_stats_info = ft.GestureDetector(
            content=ft.Text("", size=12, color="error"),
            visible=False,
            on_tap=self._on_banned_banner_click,
            mouse_cursor=ft.MouseCursor.CLICK,
        )

        self._bulk_verify_btn = ft.TextButton("批量验证", icon=icons.VERIFIED_USER, on_click=lambda e: self.page.run_task(self._bulk_verify_accounts, e), visible=False)
        self._bulk_delete_btn = ft.TextButton("批量删除", icon=icons.DELETE_SWEEP, on_click=lambda e: self.page.run_task(self._bulk_delete_accounts, e), style=ft.ButtonStyle(color="error"), visible=False)
        self.bulk_bar = ft.Row([
            ft.Checkbox(label="全选", on_change=self._toggle_select_all),
            ft.PopupMenuButton(
                items=[
                    ft.PopupMenuItem(text="全量重算 (所有账号)", on_click=lambda e: self.page.run_task(self._auto_calculate_weights, e, False)),
                    ft.PopupMenuItem(text="增量计算 (仅变更账号)", on_click=lambda e: self.page.run_task(self._auto_calculate_weights, e, True)),
                ],
                icon=icons.AUTO_AWESOME,
                tooltip="智能权重计算",
            ),
            ft.IconButton(icon=icons.SETTINGS, tooltip="评分模型配置", on_click=lambda e: self.page.run_task(self._show_weight_config_dialog), icon_size=18),
            ft.IconButton(icon=icons.HISTORY, tooltip="权重变更历史", on_click=lambda e: self.page.run_task(self._show_weight_history), icon_size=18),
            self._bulk_verify_btn,
            self._bulk_delete_btn,
            ft.Container(expand=True),
            self.account_stats_info
        ], spacing=10, visible=True)

        # 账号列表容器：动态填充必须用 ListView——Tabs 内对 scrollable Column
        # 动态添加的子控件不会被渲染（Flet web 缺陷，见 docs/flet_web_layout_pitfalls.md 规则4）
        self.account_list = ft.ListView(
            spacing=10,
            expand=True,
        )

        return ft.Column(
            controls=[
                ft.Row([search_field, status_filter, self._sort_dropdown, add_btn], spacing=10),
                self.bulk_bar,
                ft.Divider(color=with_opacity(0.1, "primary"), height=1),
                ft.Container(
                    content=self.account_list,
                    expand=True,
                ),
            ],
            spacing=10,
        )

    def _build_strategic_tab(self) -> ft.Control:
        """全域战略吧库标签页"""
        # 工具栏
        self._matrix_search_field = ft.TextField(
            hint_text="搜索吧名或标签...",
            prefix_icon=icons.SEARCH,
            border_radius=10,
            text_size=13,
            on_change=self._on_matrix_search_change,
            bgcolor=with_opacity(0.05, "onSurface"),
            border_color=with_opacity(0.1, "primary"),
            expand=True,
            height=45,
        )

        sync_btn = ft.IconButton(
            icon=icons.SYNC_ROUNDED,
            tooltip="全域同步关注列表",
            icon_color="primary",
            on_click=self._on_sync_matrix,
        )

        clear_search_btn = ft.IconButton(
            icon=icons.CLEAR,
            tooltip="清除搜索",
            icon_color="onSurfaceVariant",
            on_click=self._on_clear_matrix_search,
        )

        follow_btn = ft.IconButton(
            icon=icons.ADD,
            tooltip="关注贴吧",
            icon_color="primary",
            on_click=lambda e: self._show_follow_forum_dialog(e),
        )

        self.banned_filter_btn = ft.IconButton(
            icon=icons.FILTER_LIST,
            tooltip="筛选封禁贴吧",
            icon_color="onSurfaceVariant",
            on_click=self._on_toggle_banned_filter,
        )

        self.deleted_filter_btn = ft.IconButton(
            icon=icons.DELETE_SWEEP_OUTLINED,
            tooltip="筛选有删帖的贴吧",
            icon_color="onSurfaceVariant",
            on_click=self._on_toggle_deleted_filter,
        )

        self.matrix_header_info = ft.Text("战略贴吧总数: 0 | 矩阵覆盖率: 0%", size=12, color="onSurfaceVariant")

        # 批量操作栏
        self.matrix_select_all_cb = ft.Checkbox(label="全选", on_change=self._on_matrix_select_all)
        self.matrix_bulk_toggle_target_btn = ft.TextButton(
            "批量切换火力", icon=icons.SWAP_HORIZ,
            on_click=lambda e: self.page.run_task(self._bulk_matrix_toggle_target),
            visible=False,
        )
        self.matrix_bulk_follow_btn = ft.TextButton(
            "批量补齐关注", icon=icons.PERSON_ADD_ALT_1_ROUNDED,
            on_click=lambda e: self.page.run_task(self._bulk_matrix_complement_follow),
            visible=False,
        )
        self.matrix_bulk_unfollow_btn = ft.TextButton(
            "批量取消关注", icon=icons.HEART_BROKEN,
            on_click=lambda e: self.page.run_task(self._bulk_matrix_unfollow),
            style=ft.ButtonStyle(color="error"),
            visible=False,
        )
        self.matrix_bulk_tag_btn = ft.TextButton(
            "批量修改标签", icon=icons.LABEL_ROUNDED,
            on_click=self._bulk_matrix_edit_tag,
            visible=False,
        )
        self.matrix_bulk_bar = ft.Row([
            self.matrix_select_all_cb,
            self.matrix_bulk_toggle_target_btn,
            self.matrix_bulk_follow_btn,
            self.matrix_bulk_unfollow_btn,
            self.matrix_bulk_tag_btn,
        ], spacing=5, wrap=True)

        # 列表容器
        self.matrix_list = ft.ListView(
            expand=True,
            spacing=10,
            padding=10,
        )

        # 分页控件
        self._matrix_page_info = ft.Text("", size=12, color="onSurfaceVariant")
        self._matrix_prev_btn = ft.IconButton(
            icon=icons.ARROW_BACK_IOS_NEW, icon_size=16,
            tooltip="上一页",
            on_click=lambda e: self.page.run_task(self._on_matrix_prev_page),
            disabled=True,
        )
        self._matrix_next_btn = ft.IconButton(
            icon=icons.NAVIGATE_NEXT, icon_size=16,
            tooltip="下一页",
            on_click=lambda e: self.page.run_task(self._on_matrix_next_page),
            disabled=True,
        )
        matrix_pagination = ft.Row(
            [self._matrix_prev_btn, self._matrix_page_info, self._matrix_next_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
        )

        return ft.Column(
            controls=[
                ft.Row([self._matrix_search_field, self.banned_filter_btn, self.deleted_filter_btn, sync_btn, clear_search_btn, follow_btn], spacing=10),
                self.matrix_bulk_bar,
                self.matrix_header_info,
                ft.Divider(color=with_opacity(0.1, "primary"), height=1),
                ft.Container(
                    content=self.matrix_list,
                    expand=True,
                ),
                matrix_pagination,
            ],
            spacing=10,
        )

    def _build_exception_tab(self) -> ft.Control:
        """异常记录标签页"""
        self.exception_pending_count = ft.Text("待处理: 0", size=12, color="error")
        self.exception_list = ft.ListView(
            expand=True,
            spacing=10,
            padding=10,
        )
        self.exception_clear_btn = ft.TextButton(
            "清除已解决记录",
            icon=icons.DELETE_SWEEP,
            on_click=lambda e: self.page.run_task(self._clear_resolved_events, e),
        )
        
        return ft.Column(
            controls=[
                ft.Row([
                    ft.Container(
                        content=ft.Column([
                            ft.Text("验证码/异常事件", size=11, color="onSurfaceVariant"),
                            self.exception_pending_count,
                        ], spacing=2),
                        padding=10,
                        bgcolor=with_opacity(0.1, "error"),
                        border_radius=8,
                    ),
                    ft.Container(expand=True),
                    self.exception_clear_btn,
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(color=with_opacity(0.1, "error"), height=1),
                ft.Container(
                    content=self.exception_list,
                    expand=True,
                ),
            ],
            spacing=10,
        )

    async def _load_exception_events(self):
        """加载异常事件列表"""
        events = await self.db.get_captcha_events(limit=100)
        items = []
        
        # 构建账号 ID -> 显示名映射，用于将数字 ID 解析为可读名称
        acc_name_map = {a.id: (a.name or a.user_name or f"账号-{a.id}") for a in self._accounts}
        
        pending_count = sum(1 for e in events if e["status"] == "pending")
        self.exception_pending_count.value = f"待处理: {pending_count}"
        self.exception_pending_count.color = "#F44336" if pending_count > 0 else "#4CAF50"
        
        if not events:
            items.append(ft.Container(
                content=ft.Text("暂无异常事件记录", size=13, color="onSurfaceVariant"),
                padding=20,
                alignment=ft.alignment.center,
            ))
            self.exception_list.controls = items
            return
        
        for event in events:
            status_color = "#F44336" if event["status"] == "pending" else "#4CAF50"
            status_icon = icons.WARNING_ROUNDED if event["status"] == "pending" else icons.CHECK_CIRCLE
            status_text = "待处理" if event["status"] == "pending" else "已解决"
            
            created_at = event["created_at"].strftime("%y-%m-%d %H:%M") if event["created_at"] else "-"
            resolved_at = event["resolved_at"].strftime("%y-%m-%d %H:%M") if event["resolved_at"] else "-"
            
            card = ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Icon(status_icon, color=status_color, size=20),
                        ft.Text(f"验证码事件 #{event['id']}", size=14, weight=ft.FontWeight.BOLD),
                        ft.Container(expand=True),
                        ft.Container(
                            content=ft.Text(status_text, size=11, color="white"),
                            padding=ft.padding.only(left=8, right=8, top=2, bottom=2),
                            bgcolor=status_color,
                            border_radius=10,
                        ),
                    ]),
                    ft.Container(height=5),
                    ft.Row([
                        ft.Text(f"触发时间: {created_at}", size=12, color="onSurfaceVariant"),
                        ft.Container(expand=True),
                        ft.Text(f"原因: {event['reason'] or '未知'}", size=12, color="onSurfaceVariant"),
                    ]),
                    ft.Row([
                        ft.Text(f"账号: {acc_name_map.get(event['account_id'], event['account_id'] or '-')}", size=12, color="onSurfaceVariant"),
                        ft.Container(expand=True),
                        ft.Text(f"任务ID: {event['task_id'] or '-'}", size=12, color="onSurfaceVariant"),
                    ]),
                    ft.Container(height=5),
                    ft.Row([
                        ft.Text(f"解决时间: {resolved_at}", size=11, color="onSurfaceVariant"),
                        ft.Container(expand=True),
                        ft.TextButton(
                            "手动解决",
                            icon=icons.CHECK,
                            on_click=lambda e, event_id=event['id']: self.page.run_task(self._resolve_event, e, event_id),
                            visible=event["status"] == "pending",
                        ),
                    ]),
                ], spacing=3),
                padding=15,
                bgcolor=with_opacity(0.05, "onSurface"),
                border_radius=8,
                border=ft.border.all(1, with_opacity(0.2, status_color)),
            )
            items.append(card)
        
        self.exception_list.controls = items

    async def _resolve_event(self, e, event_id: int):
        """手动解决异常事件"""
        success = await self.db.resolve_captcha_event(event_id, resolved_by="manual", notes="用户手动确认")
        if success:
            self._show_snackbar(f"✅ 事件 #{event_id} 已标记为已解决", "success")
            await self._load_exception_events()
        else:
            self._show_snackbar(f"❌ 解决失败", "error")

    async def _clear_resolved_events(self, e):
        """清除已解决的异常事件记录"""
        count = await self.db.clear_resolved_captcha_events()
        self._show_snackbar(f"✅ 已清除 {count} 条已解决记录", "success")
        await self._load_exception_events()

    def _on_tab_change(self, e):
        self._active_tab_index = e.control.selected_index
        self.refresh_ui()

    def _on_matrix_search_change(self, e):
        self._matrix_search_text = e.control.value
        self._matrix_current_page = 1
        self.refresh_ui()

    def _on_toggle_banned_filter(self, e):
        """切换封禁贴吧筛选"""
        self._matrix_banned_filter = not self._matrix_banned_filter
        self._matrix_current_page = 1
        if self._matrix_banned_filter:
            self.banned_filter_btn.icon = icons.FILTER_LIST
            self.banned_filter_btn.icon_color = "error"
            self.banned_filter_btn.tooltip = "显示全部贴吧"
        else:
            self.banned_filter_btn.icon = icons.FILTER_LIST
            self.banned_filter_btn.icon_color = "onSurfaceVariant"
            self.banned_filter_btn.tooltip = "筛选封禁贴吧"
        self.refresh_ui()

    def _on_toggle_deleted_filter(self, e):
        """切换被删帖贴吧筛选"""
        self._matrix_deleted_filter = not self._matrix_deleted_filter
        self._matrix_current_page = 1
        if self._matrix_deleted_filter:
            self.deleted_filter_btn.icon_color = "error"
            self.deleted_filter_btn.tooltip = "显示全部贴吧"
        else:
            self.deleted_filter_btn.icon_color = "onSurfaceVariant"
            self.deleted_filter_btn.tooltip = "筛选有删帖的贴吧"
        self.refresh_ui()

    def _on_clear_matrix_search(self, e):
        """清除全域战略吧库搜索"""
        self._matrix_search_text = ""
        self._matrix_current_page = 1
        if hasattr(self, "_matrix_search_field"):
            self._matrix_search_field.value = ""
        self.refresh_ui()

    async def _on_matrix_prev_page(self, e=None):
        """吧库分页 - 上一页"""
        if self._matrix_current_page > 1:
            self._matrix_current_page -= 1
            self.refresh_ui()

    async def _on_matrix_next_page(self, e=None):
        """吧库分页 - 下一页"""
        total_pages = max(1, (self._matrix_filtered_count + self._matrix_page_size - 1) // self._matrix_page_size)
        if self._matrix_current_page < total_pages:
            self._matrix_current_page += 1
            self.refresh_ui()

    def _update_matrix_pagination(self):
        """更新吧库分页信息"""
        if not hasattr(self, "_matrix_page_info"):
            return
        total_pages = max(1, (self._matrix_filtered_count + self._matrix_page_size - 1) // self._matrix_page_size)
        self._matrix_page_info.value = f"第 {self._matrix_current_page}/{total_pages} 页，共 {self._matrix_filtered_count} 个贴吧"
        self._matrix_prev_btn.disabled = self._matrix_current_page <= 1
        self._matrix_next_btn.disabled = self._matrix_current_page >= total_pages

    async def _on_sync_matrix(self, e):
        """全域同步关注列表"""
        from ...core.sign import sync_forums_to_db

        if not self._begin_op():
            return
        try:
            self._open_progress_dialog("正在启动全域矩阵关注同步...")
            added = await sync_forums_to_db(self.db)
            await self._refresh_matrix_stats()
            self._show_snackbar(f"✅ 全域同步完成！矩阵新增 {added} 个战略支点", "success")
            self.refresh_ui()
        except Exception as ex:
            self._show_snackbar(f"❌ 同步失败: {str(ex)}", "error")
        finally:
            self._close_progress_dialog()
            self._end_op()

    def _show_follow_forum_dialog(self, e):
        """显示关注贴吧弹窗"""
        async def on_follow(ev):
            if not self._begin_op():
                return
            forum_input.disabled = True
            submit_btn.disabled = True
            submit_btn.text = "关注中..."
            self.page.update()
            self._open_progress_dialog("批量关注执行中...", determinate=True)

            async def report_progress(done, total):
                self._update_progress(f"关注进度 {done}/{total}", (done / total) if total else None)

            try:
                # 解析输入：支持逗号分隔、换行分隔、空格分隔
                raw = forum_input.value.strip()
                if not raw:
                    self._show_snackbar("请输入贴吧名称", "warning")
                    forum_input.disabled = False
                    submit_btn.disabled = False
                    submit_btn.text = "确认关注"
                    self.page.update()
                    return

                # 分割并清理输入
                import re
                fnames = re.split(r'[,\n，\s]+', raw)
                fnames = [f.strip() for f in fnames if f.strip()]

                if not fnames:
                    self._show_snackbar("未识别到有效贴吧名称", "warning")
                    forum_input.disabled = False
                    submit_btn.disabled = False
                    submit_btn.text = "确认关注"
                    self.page.update()
                    return

                # 调用关注 API
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                result = await pm.follow_forums_bulk(fnames, progress_callback=report_progress)

                self._close_progress_dialog()
                # 关闭弹窗
                self.page.close(dialog)

                # 显示结果
                success_count = len(result["success"])
                failed_count = len(result["failed"])
                skipped_count = len(result["skipped"])

                if any(f.get("reason") == "已有批量关注/取关任务在执行" for f in result["failed"]):
                    self._show_snackbar("ℹ️ 已有批量关注/取关任务在执行，本次未执行", "info")
                    return

                if success_count > 0:
                    self._show_snackbar(f"✅ 成功关注 {success_count} 个贴吧", "success")
                if failed_count > 0:
                    self._show_snackbar(f"⚠️ {failed_count} 个关注失败（可能被拉黑或已关注）", "warning")
                if skipped_count > 0:
                    self._show_snackbar(f"ℹ️ {skipped_count} 个已跳过（无需重复关注）", "info")

                # 刷新列表
                await self._refresh_matrix_stats()
                self.refresh_ui()

            except Exception as ex:
                self._show_snackbar(f"❌ 关注失败: {str(ex)}", "error")
                forum_input.disabled = False
                submit_btn.disabled = False
                submit_btn.text = "确认关注"
                self.page.update()
            finally:
                self._close_progress_dialog()
                self._end_op()

        forum_input = ft.TextField(
            hint_text="输入要关注的贴吧名称",
            border_radius=10,
            text_size=13,
            autofocus=True,
            on_submit=lambda ev: self.page.run_task(on_follow, ev),
        )

        hint_text = ft.Text(
            "💡 支持批量关注，多个贴吧用逗号或换行分隔",
            size=11,
            color="onSurfaceVariant"
        )

        submit_btn = ft.FilledButton("确认关注", icon=icons.CHECK, on_click=lambda ev: self.page.run_task(on_follow, ev))

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.FAVORITE_ROUNDED, color="primary"), ft.Text("关注贴吧")]),
            content=ft.Container(
                content=ft.Column(
                    controls=[forum_input, hint_text],
                    spacing=10,
                ),
                padding=10,
                width=400,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                submit_btn,
            ]
        )
        self.page.open(dialog)

    async def _on_toggle_target(self, fname: str, is_currently_target: bool):
        """一键标记/取消战略目标
        
        Args:
            fname: 贴吧名称
            is_currently_target: 当前是否已是火力打击目标（True=已在靶场，False=不在靶场）
        """
        await log_info(f"一键切换火力: 贴吧={fname}, 当前状态={is_currently_target}")
        try:
            if is_currently_target:
                # 已在靶场中，点击要移除
                removed = await self.db.delete_target_pool_by_fnames([fname])
                await log_info(f"已从靶场移除: {removed}")
                self._show_snackbar(f"🏳️ 已从打击名单中移除 '{fname}'", "info")
            else:
                # 不在靶场中，点击要添加
                added = await self.db.upsert_target_pools([fname], "未分类")
                await log_info(f"已添加到靶场: added={added}")
                self._show_snackbar(f"🎯 已将 '{fname}' 锁定为火力打击目标", "success")
            
            # 刷新列表
            await self._refresh_matrix_stats()
            self.refresh_ui()
            self.page.update()
        except Exception as e:
            await log_error(f"火力切换异常: {e}")
            self._show_snackbar(f"操作失败: {str(e)}", "error")

    async def _on_complement_follow(self, fname: str):
        """补齐关注：让未关注的账号也关注该贴吧"""
        if not self._begin_op():
            return
        try:
            # 获取未关注的账号
            missing_accounts = await self.db.get_accounts_not_following_forum(fname)

            if not missing_accounts:
                self._show_snackbar(f"✅ '{fname}' 已被所有账号关注，无需补齐", "success")
                return

            missing_names = [acc.name for acc in missing_accounts]
            self._show_snackbar(f"🔄 正在让 {len(missing_accounts)} 个账号关注 '{fname}'...", "info")

            # 只让未关注的账号关注
            missing_ids = [acc.id for acc in missing_accounts]
            from ...core.batch_post import BatchPostManager
            pm = BatchPostManager(self.db)
            result = await pm.follow_forums_bulk([fname], account_ids=missing_ids)

            success_count = len(result["success"])
            failed_count = len(result["failed"])

            if success_count > 0:
                self._show_snackbar(f"✅ {success_count}/{len(missing_accounts)} 个账号成功关注 '{fname}'", "success")
            if failed_count > 0:
                self._show_snackbar(f"⚠️ {failed_count} 个账号关注失败", "warning")

            # 刷新列表
            await self._refresh_matrix_stats()
            self.refresh_ui()

        except Exception as e:
            self._show_snackbar(f"❌ 补齐失败: {str(e)}", "error")
        finally:
            self._end_op()

    async def _on_unfollow_forum(self, fname: str):
        """取消关注：所有账号取关该贴吧"""
        async def do_unfollow(e):
            if not self._begin_op():
                return
            try:
                self.page.close(dialog)
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                res = await pm.unfollow_forums_bulk([fname])
                ok, bad = len(res["success"]), len(res["failed"])
                skipped = len(res.get("skipped", []))
                if any(f.get("reason") == "已有批量关注/取关任务在执行" for f in res["failed"]):
                    self._show_snackbar("ℹ️ 已有批量关注/取关任务在执行，本次未执行", "info")
                    return
                if ok:
                    self._show_snackbar(f"✅ 已取消关注 '{fname}'（{ok} 个账号）", "success")
                if bad:
                    self._show_snackbar(f"⚠️ {bad} 个账号取关失败，记录已保留", "warning")
                if skipped:
                    self._show_snackbar(f"ℹ️ {skipped} 个账号被跳过（熔断/无凭证）", "info")
                if ok + bad + skipped == 0:
                    self._show_snackbar(f"ℹ️ 没有账号关注 '{fname}'，本地记录已清理", "info")
                await self._refresh_matrix_stats()
                self.refresh_ui()
            except Exception as ex:
                self._show_snackbar(f"❌ 取消关注失败: {str(ex)}", "error")
            finally:
                self._end_op()

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.HEART_BROKEN, color="error"), ft.Text("确认取消关注？")]),
            content=ft.Text(f"确定要取消关注 '{fname}' 吗？此操作将让所有账号取关该贴吧，并从战略吧库中移除。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("确认取消", icon=icons.HEART_BROKEN, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=do_unfollow),
            ]
        )
        self.page.open(dialog)

    # ── 批量操作 ──

    def _on_matrix_item_select(self, e):
        fname = e.control.data
        if e.control.value:
            self._matrix_selected_fnames.add(fname)
        else:
            self._matrix_selected_fnames.discard(fname)
        self._update_matrix_bulk_bar()

    def _on_matrix_select_all(self, e):
        search_lower = self._matrix_search_text.lower()
        if e.control.value:
            for stat in self._matrix_stats:
                fname = stat['fname']
                if search_lower and search_lower not in fname.lower() and search_lower not in stat['post_group'].lower():
                    continue
                # 封禁筛选：仅选中被封禁的贴吧
                if self._matrix_banned_filter and not stat.get('is_banned', False):
                    continue
                # 被删筛选：仅选中有删帖记录的贴吧
                if self._matrix_deleted_filter and stat.get('deleted_count', 0) == 0:
                    continue
                self._matrix_selected_fnames.add(fname)
        else:
            self._matrix_selected_fnames.clear()
        self.refresh_ui()
        self._update_matrix_bulk_bar()

    def _update_matrix_bulk_bar(self):
        count = len(self._matrix_selected_fnames)
        has_sel = count > 0
        self.matrix_bulk_toggle_target_btn.visible = has_sel
        self.matrix_bulk_follow_btn.visible = has_sel
        self.matrix_bulk_unfollow_btn.visible = has_sel
        self.matrix_bulk_tag_btn.visible = has_sel
        if has_sel:
            self.matrix_bulk_toggle_target_btn.text = f"批量切换火力 ({count})"
            self.matrix_bulk_follow_btn.text = f"批量补齐关注 ({count})"
            self.matrix_bulk_unfollow_btn.text = f"批量取消关注 ({count})"
            self.matrix_bulk_tag_btn.text = f"批量修改标签 ({count})"
        self.page.update()

    async def _bulk_matrix_toggle_target(self):
        """批量切换 Target（未投放→投放；已投放→移出靶场，可选清理关注记录）"""
        if not self._matrix_selected_fnames: return
        fnames = list(self._matrix_selected_fnames)
        try:
            # 从 _matrix_stats 构建状态映射
            stats_map = {s['fname']: s for s in self._matrix_stats}
            target_fnames = [f for f in fnames if stats_map.get(f, {}).get('is_target')]
            non_target_fnames = [f for f in fnames if f not in set(target_fnames)]

            # 投放未投放的（无风险，直接执行）
            for f in non_target_fnames:
                await self.db.upsert_target_pools([f], "未分类")

            if target_fnames:
                # 已投放的 → 弹出"移出靶场"确认框（可选清理关注记录）
                self._show_remove_target_dialog(target_fnames)
            else:
                self._show_snackbar(f"✅ 已投放 {len(non_target_fnames)} 个贴吧至靶场", "success")
                await self._finish_matrix_bulk_change()
        except Exception as e:
            self._show_snackbar(f"❌ 批量操作失败: {str(e)}", "error")
            await self._finish_matrix_bulk_change()

    async def _finish_matrix_bulk_change(self):
        """批量操作收尾：清空选择并刷新数据与 UI"""
        self._matrix_selected_fnames.clear()
        self.matrix_select_all_cb.value = False
        self._update_matrix_bulk_bar()
        await self._refresh_matrix_stats()
        self.refresh_ui()
        self.page.update()

    def _show_remove_target_dialog(self, target_fnames: list[str]):
        """移出靶场确认框，可选同时清理全部账号对这些贴吧的关注记录"""
        cleanup_cb = ft.Checkbox(
            label="同时清理这些贴吧的全部关注记录（影响所有账号，不可恢复）",
            value=False,
        )
        preview = ', '.join(target_fnames[:8]) + ('...' if len(target_fnames) > 8 else '')

        async def do_remove(_):
            try:
                removed = await self.db.delete_target_pool_by_fnames(target_fnames)
                cleaned = 0
                if cleanup_cb.value:
                    cleaned = await self.db.delete_forum_memberships_globally(target_fnames)
                self.page.close(dialog)
                msg = f"✅ 已将 {len(target_fnames)} 个贴吧移出靶场（移除 {removed} 条标记）"
                if cleaned:
                    msg += f"，清理关注记录 {cleaned} 条"
                self._show_snackbar(msg, "success")
                await self._finish_matrix_bulk_change()
            except Exception as ex:
                self._show_snackbar(f"❌ 移出失败: {str(ex)}", "error")

        def _cancel(_):
            self.page.close(dialog)
            self.page.run_task(self._finish_matrix_bulk_change)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.GPS_OFF_ROUNDED, color="error"), ft.Text(f"移出靶场（{len(target_fnames)} 个贴吧）")]),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(f"将移出：{preview}", size=12),
                    ft.Text("移出后不再作为火力打击目标，默认不影响账号的关注状态。", size=11, color="onSurfaceVariant"),
                    cleanup_cb,
                ], tight=True, spacing=10),
                width=420,
                padding=10,
            ),
            actions=[
                ft.TextButton("取消", on_click=_cancel),
                ft.FilledButton("确认移出", icon=icons.GPS_OFF_ROUNDED, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=lambda e: self.page.run_task(do_remove, e)),
            ],
        )
        self.page.open(dialog)

    async def _bulk_matrix_complement_follow(self):
        """批量补齐关注（N+1 优化：单次查询 + 单次批量操作）"""
        if not self._matrix_selected_fnames: return
        if not self._begin_op():
            return
        fnames = list(self._matrix_selected_fnames)
        try:
            # 单次查询获取所有缺失关注的账号（替代逐吧循环查询）
            missing_accounts = await self.db.get_accounts_not_following_any_forums(fnames)
            if not missing_accounts:
                self._show_snackbar("✅ 所有账号已关注选中的贴吧", "info")
            else:
                missing_ids = [acc.id for acc in missing_accounts]
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                # 单次调用处理所有贴吧
                result = await pm.follow_forums_bulk(fnames, account_ids=missing_ids)
                total_success = len(result["success"])
                total_failed = len(result["failed"])
                total_skipped = len(result.get("skipped", []))
                msg = f"✅ 补齐关注完成: 成功 {total_success}, 失败 {total_failed}"
                if total_skipped > 0:
                    msg += f", 跳过 {total_skipped}"
                self._show_snackbar(msg, "success")
        except Exception as e:
            self._show_snackbar(f"❌ 批量补齐失败: {str(e)}", "error")
        finally:
            self._end_op()
        self._matrix_selected_fnames.clear()
        await self._refresh_matrix_stats()
        self._update_matrix_bulk_bar()
        self.refresh_ui()

    async def _bulk_matrix_unfollow(self):
        """批量取消关注"""
        if not self._matrix_selected_fnames: return
        fnames = list(self._matrix_selected_fnames)

        async def do_unfollow(e):
            if not self._begin_op():
                return
            try:
                self.page.close(dialog)
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                res = await pm.unfollow_forums_bulk(fnames)
                ok, bad = len(res["success"]), len(res["failed"])
                if any(f.get("reason") == "已有批量关注/取关任务在执行" for f in res["failed"]):
                    self._show_snackbar("ℹ️ 已有批量关注/取关任务在执行，本次未执行", "info")
                elif bad:
                    self._show_snackbar(f"⚠️ 批量取关完成：成功 {ok} 项，失败 {bad} 项（失败记录已保留）", "warning")
                else:
                    self._show_snackbar(f"✅ 已批量取消关注 {len(fnames)} 个贴吧（{ok} 项）", "success")
            except Exception as ex:
                self._show_snackbar(f"❌ 批量取关失败: {str(ex)}", "error")
            finally:
                self._end_op()
            self._matrix_selected_fnames.clear()
            await self._refresh_matrix_stats()
            self._update_matrix_bulk_bar()
            self.refresh_ui()

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.HEART_BROKEN, color="error"), ft.Text("确认批量取消关注？")]),
            content=ft.Text(f"确定要取消关注以下 {len(fnames)} 个贴吧吗？所有账号将取关这些贴吧，并从战略吧库中移除。\n\n{', '.join(fnames[:10])}{'...' if len(fnames) > 10 else ''}"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("确认取消", icon=icons.HEART_BROKEN, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=do_unfollow),
            ]
        )
        self.page.open(dialog)

    def _bulk_matrix_edit_tag(self, e):
        """批量修改标签"""
        if not self._matrix_selected_fnames: return
        fnames = list(self._matrix_selected_fnames)
        tag_input = ft.TextField(
            label="所属吧组 / 标签",
            hint_text="使用英文逗号分隔多个标签 (如: IT,资源,北京)",
            text_size=13,
            autofocus=True,
        )

        async def on_save(_):
            group = tag_input.value.strip() if tag_input.value else ""
            await self.db.bulk_update_target_group(fnames, group)
            self.page.close(dialog)
            self._matrix_selected_fnames.clear()
            await self._refresh_matrix_stats()
            self._update_matrix_bulk_bar()
            self.refresh_ui()
            self._show_snackbar(f"🏷️ 已批量更新 {len(fnames)} 个贴吧的标签", "success")

        dialog = ft.AlertDialog(
            title=ft.Text(f"批量修改标签 ({len(fnames)} 个贴吧)"),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(f"将修改: {', '.join(fnames[:8])}{'...' if len(fnames) > 8 else ''}", size=11, color="onSurfaceVariant"),
                    tag_input,
                ], tight=True, spacing=10),
                padding=10,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("保存", on_click=lambda e: self.page.run_task(on_save, e))
            ]
        )
        self.page.open(dialog)

    def _show_safety_detail(self, stat: dict):
        """显示本土作战自动判定详情"""
        fname = stat['fname']
        is_post_target = stat.get('is_post_target', False)
        is_banned = stat.get('is_banned', False)
        deleted_count = stat.get('deleted_count', 0)
        acc_count = stat.get('account_count', 0)

        # 构建判定原因
        reasons = []
        if is_banned:
            # 查找封禁详情
            ban_items = self._banned_forum_map.get(fname, [])
            if ban_items:
                for b in ban_items:
                    reasons.append(f"🚫 账号 {b['account_name']} 被封禁: {b['ban_reason']}")
            else:
                reasons.append("🚫 该贴吧存在被封禁的账号")
        if deleted_count > 0:
            reasons.append(f"⚠️ 存在 {deleted_count} 条被删除的帖子记录（含吧务/系统删除）")

        if is_post_target:
            result_text = "✅ 本土作战已开启"
            result_color = "green"
            detail = "判定依据：该贴吧未被封禁且无被吧务删帖记录，判定为安全。"
            detail += f"\n\n当前有 {acc_count} 个账号部署在该贴吧。" if acc_count > 0 else "\n\n暂无账号部署。"
        else:
            result_text = "❌ 本土作战未开启"
            result_color = "error"
            detail = "判定依据：\n" + "\n".join(reasons)
            detail += "\n\n💡 当封禁解除且删帖记录清除后，将自动恢复为安全状态。"

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.SHIELD_ROUNDED if is_post_target else icons.SHIELD_OUTLINED, color=result_color), ft.Text(f"{fname} — 安全判定")]),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(result_text, size=16, weight="bold", color=result_color),
                    ft.Divider(height=5),
                    ft.Text(detail, size=13),
                ], tight=True, spacing=10),
                padding=5,
            ),
            actions=[
                ft.TextButton("关闭", on_click=lambda _: self.page.close(dialog)),
            ]
        )
        self.page.open(dialog)

    def _show_tag_edit_dialog(self, stat: dict):
        """显示修改吧组标签对话框"""
        fname = stat['fname']
        tag_input = ft.TextField(
            label="所属吧组 / 标签",
            hint_text="使用英文逗号分隔多个标签 (如: IT,资源,北京)",
            value=stat['post_group'],
            text_size=13,
            autofocus=True,
        )

        async def on_save(_):
            group = tag_input.value.strip() if tag_input.value else ""
            await self.db.bulk_update_target_group([fname], group)
            self.page.close(dialog)
            await self._refresh_matrix_stats()
            self.refresh_ui()
            self._show_snackbar(f"🏷️ '{fname}' 标签已更新", "success")

        dialog = ft.AlertDialog(
            title=ft.Text(f"修改吧组分类: {fname}"),
            content=ft.Container(content=tag_input, padding=10),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("保存", on_click=lambda e: self.page.run_task(on_save, e))
            ]
        )
        self.page.open(dialog)

    def _update_matrix_header(self):
        """更新吧库头部统计信息"""
        total = len(self._matrix_stats)
        covered = sum(1 for s in self._matrix_stats if s['account_count'] > 0)
        banned_count = sum(1 for s in self._matrix_stats if s.get('is_banned'))
        deleted_count = sum(1 for s in self._matrix_stats if s.get('deleted_count', 0) > 0)
        percent = (covered / total * 100) if total > 0 else 0
        base_info = f"战略资源: {total} 个贴吧 | 矩阵实存火力涵盖: {covered} 个 (覆盖率 {percent:.1f}%)"
        if banned_count > 0:
            base_info += f" | 🚫 封禁: {banned_count} 个"
        if deleted_count > 0:
            base_info += f" | 🗑️ 有删帖: {deleted_count} 个"
        if self._matrix_banned_filter:
            base_info = f"🚫 封禁筛选模式 | 显示 {banned_count} 个被封禁贴吧"
        elif self._matrix_deleted_filter:
            base_info = f"🗑️ 删帖筛选模式 | 显示 {deleted_count} 个有删帖的贴吧"
        self.matrix_header_info.value = base_info

    def _build_matrix_items(self) -> list[ft.Control]:
        """构建战略贴吧列表项（含分页）"""
        items = []
        search_lower = self._matrix_search_text.lower()

        # 第一步：筛选
        filtered = []
        for stat in self._matrix_stats:
            fname = stat['fname']
            if search_lower and search_lower not in fname.lower() and search_lower not in stat['post_group'].lower():
                continue
            if self._matrix_banned_filter and not stat.get('is_banned', False):
                continue
            if self._matrix_deleted_filter and stat.get('deleted_count', 0) == 0:
                continue
            filtered.append(stat)

        # 记录筛选后总数
        self._matrix_filtered_count = len(filtered)

        # 页码越界保护
        total_pages = max(1, (len(filtered) + self._matrix_page_size - 1) // self._matrix_page_size)
        if self._matrix_current_page > total_pages:
            self._matrix_current_page = total_pages

        # 分页切片
        start = (self._matrix_current_page - 1) * self._matrix_page_size
        end = start + self._matrix_page_size
        page_items = filtered[start:end]

        # 第二步：构建卡片（仅当前页）
        for stat in page_items:
            fname = stat['fname']
                
            acc_count = stat['account_count']
            groups = stat['post_group']
            is_target = stat['is_target']
            is_post_target = stat.get('is_post_target', False)
            is_banned = stat.get('is_banned', False)
            is_selected = fname in self._matrix_selected_fnames
            
            # 分组标签 chips
            group_chips = []
            if groups:
                for g in groups.split(","):
                    group_chips.append(
                        ft.Container(
                            content=ft.Text(g.strip(), size=10, color=COLORS.PRIMARY),
                            bgcolor=with_opacity(0.1, COLORS.PRIMARY),
                            padding=ft.padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                        )
                    )
            
            if is_target:
                group_chips.insert(0, 
                    ft.Container(
                        content=ft.Text("TARGET", size=9, weight="bold", color="white"),
                        bgcolor="error",
                        padding=ft.padding.symmetric(horizontal=6, vertical=2),
                        border_radius=4,
                        tooltip="火力打击组标的"
                    )
                )

            if is_banned:
                # 构建封禁详情 tooltip
                banned_items = self._banned_forum_map.get(fname, [])
                if banned_items:
                    ban_lines = [f"· {b['account_name']}: {b['ban_reason']}" for b in banned_items]
                    ban_tooltip = "封禁详情:\n" + "\n".join(ban_lines)
                else:
                    ban_tooltip = "该吧已被吧务封禁，禁止发帖"
                group_chips.insert(0,
                    ft.Container(
                        content=ft.Text("封禁", size=9, weight="bold", color="white"),
                        bgcolor="error",
                        padding=ft.padding.symmetric(horizontal=6, vertical=2),
                        border_radius=4,
                        tooltip=ban_tooltip,
                    )
                )

            # 封禁详情行（仅封禁筛选模式下直接展示）
            ban_detail_row = None
            if is_banned and self._matrix_banned_filter and banned_items:
                ban_detail_row = ft.Column([
                    ft.Row([
                        ft.Icon(icons.BLOCK, size=12, color="error"),
                        ft.Text(f"{b['account_name']}", size=11, weight="bold", color="error"),
                        ft.Text(f"— {b['ban_reason']}", size=11, color="error", italic=True),
                    ], spacing=4)
                    for b in banned_items
                ], spacing=2)

            if is_post_target and acc_count > 0:
                group_chips.insert(0,
                    ft.Container(
                        content=ft.Text("本土作战", size=9, weight="bold", color="white"),
                        bgcolor="green",
                        padding=ft.padding.symmetric(horizontal=6, vertical=2),
                        border_radius=4,
                        tooltip="自动判定安全：未封禁且无删帖记录，优先派遣本吧原生号出战"
                    )
                )

            # 卡片主体行
            main_row = ft.Row(
                    controls=[
                        # 选择框
                        ft.Checkbox(value=is_selected, data=fname, on_change=self._on_matrix_item_select),
                        # 吧名
                        ft.Column([
                            ft.Text(fname, size=16, weight="bold"),
                            ft.Row(group_chips, spacing=5) if group_chips else ft.Text("未分类", size=10, color="onSurfaceVariant")
                        ], spacing=4, expand=True),
                        
                        # 覆盖详情
                        ft.Column([
                            ft.Row([
                                ft.Icon(icons.GROUPS_ROUNDED, size=16, color="primary"),
                                ft.Text(f"{acc_count} 账号部署", size=12, weight="bold"),
                            ], spacing=4),
                            ft.Text(
                                    stat.get('account_names') or "暂无兵力驻守", 
                                    size=10, 
                                    color="onSurfaceVariant", 
                                    italic=True,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    width=200,
                                    text_align=ft.TextAlign.RIGHT
                            )
                        ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END),
                        
                        # 成功率统计
                        ft.Container(
                            content=ft.Column([
                                ft.Text(f"{stat['success_count']}", size=14, weight="bold", color="primary"),
                                ft.Text("击穿数", size=9, color="onSurfaceVariant")
                            ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                            width=60,
                            padding=5,
                            border=ft.border.only(left=ft.border.BorderSide(1, with_opacity(0.1, "primary"))),
                        ),
                        # 被删帖数统计
                        ft.Container(
                            content=ft.Column([
                                ft.Text(f"{stat.get('deleted_count', 0)}", size=14, weight="bold",
                                        color="error" if stat.get('deleted_count', 0) > 0 else "onSurfaceVariant"),
                                ft.Text("被删", size=9, color="onSurfaceVariant")
                            ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                            width=50,
                            padding=5,
                            border=ft.border.only(left=ft.border.BorderSide(1, with_opacity(0.1, "error") if stat.get('deleted_count', 0) > 0 else with_opacity(0.1, "primary"))),
                        ),
                        # 操作按钮组：高频操作用图标，低频操作收进"更多"菜单
                        ft.Row([
                            ft.IconButton(
                                icon=icons.GPS_FIXED_ROUNDED if not is_target else icons.GPS_OFF_ROUNDED,
                                tooltip="已锁定为火力目标 (点击移除)" if is_target else "投放火力 (设为 Target)",
                                icon_color="primary" if is_target else "onSurfaceVariant",
                                on_click=lambda e, f=fname, t=is_target: self.page.run_task(self._on_toggle_target, f, t)
                            ),
                            ft.IconButton(
                                icon=icons.SHIELD_ROUNDED if is_post_target else icons.SHIELD_OUTLINED,
                                tooltip="本土作战已开启 (自动判定)" if is_post_target else "本土作战未开启 (点击查看原因)",
                                icon_color="green" if is_post_target else "error",
                                on_click=lambda e, s=stat: self._show_safety_detail(s),
                            ),
                            ft.PopupMenuButton(
                                icon=icons.MORE_VERT_ROUNDED,
                                tooltip="更多操作",
                                items=[
                                    ft.PopupMenuItem(text="修改分组/标签", icon=icons.LABEL_ROUNDED, on_click=lambda e, s=stat: self._show_tag_edit_dialog(s)),
                                    ft.PopupMenuItem(text="补齐关注（让未关注账号也关注）", icon=icons.PERSON_ADD_ALT_1_ROUNDED, on_click=lambda e, f=fname: self.page.run_task(self._on_complement_follow, f)),
                                    ft.PopupMenuItem(),
                                    ft.PopupMenuItem(text="取消关注（所有账号取关）", icon=icons.HEART_BROKEN, on_click=lambda e, f=fname: self.page.run_task(self._on_unfollow_forum, f)),
                                ],
                            ),
                        ], spacing=0),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )

            # 组装卡片内容（主行 + 封禁详情行）
            card_children = [main_row]
            if ban_detail_row:
                card_children.append(ban_detail_row)

            item = ft.Container(
                content=ft.Column(card_children, spacing=4),
                padding=12,
                border_radius=10,
                border=ft.border.all(1, with_opacity(0.1, "primary")),
                bgcolor=with_opacity(0.02, "primary") if is_target else with_opacity(0.01, "onSurface"),
            )
            items.append(item)
            
        if not items:
            items.append(ft.Container(
                content=ft.Text("没有找到符合条件的战略资源", color="onSurfaceVariant"),
                padding=50,
                alignment=ft.alignment.center,
            ))

        # 更新分页控件
        self._update_matrix_pagination()

        return items

    def _build_account_items(self) -> list[ft.Control]:
        items = []
        if not self._accounts:
            items.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(icons.PERSON_OFF, size=50, color="onSurfaceVariant"),
                            ft.Text("暂无账号，请点击右上角添加", color="onSurfaceVariant"),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=50,
                    alignment=ft.alignment.center,
                )
            )
            return items
        search_lower = self._search_text.lower()

        # 第一步：状态 + 搜索过滤
        filtered = []
        for acc in self._accounts:
            status = getattr(acc, "status", "unknown")
            if not self._status_matches(status, self._filter_status):
                continue
            if search_lower:
                match = (
                    search_lower in (acc.name or "").lower() or
                    search_lower in (acc.user_name or "").lower() or
                    search_lower in str(acc.user_id)
                )
                if not match:
                    continue
            filtered.append(acc)

        # 第二步：排序
        filtered.sort(key=self._account_sort_key)

        for acc in filtered:
            is_active = acc.id == self._active_id
            is_selected = acc.id in self._selected_ids

            # 状态灯
            status = getattr(acc, "status", "unknown")
            last_v = getattr(acc, "last_verified", None)

            status_color = COLORS.GREY_400
            if status == "active": status_color = COLORS.GREEN_ACCENT_400
            elif status == "expired" or status.startswith("invalid"): status_color = COLORS.ERROR
            elif status == "error": status_color = COLORS.AMBER
            elif status == "banned": status_color = COLORS.RED_ACCENT_400
            
            # 查找关联代理名称；未绑定代理的账号以裸连模式运行，存在关联风险
            proxy_info = "裸连 (存在关联风险)"
            proxy_risk = True
            if acc.proxy_id:
                p = next((p for p in self._proxies if p.id == acc.proxy_id), None)
                proxy_risk = False
                if p:
                    proxy_info = f"{p.protocol}://{p.host}"
                else:
                    proxy_info = f"代理#{acc.proxy_id} (已停用)"
            
            card = ft.Container(
                content=ft.Row(
                    controls=[
                        # 选择框
                        ft.Checkbox(value=is_selected, data=acc.id, on_change=self._on_item_select),
                        # 状态核心
                        ft.Container(
                            width=10, height=10, 
                            bgcolor=status_color, 
                            border_radius=5,
                            tooltip=f"状态: {status} | 最后检测: {last_v.strftime('%m-%d %H:%M') if last_v else '从未'}"
                        ),
                        ft.Container(width=5),
                        # 头像/图标
                        ft.Container(
                            content=ft.Icon(
                                icons.ACCOUNT_CIRCLE,
                                color="primary" if is_active else "onSurfaceVariant",
                                size=36,
                            ),
                            padding=5,
                        ),
                        # 信息
                        ft.Column(
                            controls=[
                                ft.Row([
                                    ft.Text(
                                        f"{acc.name} [{acc.user_name}]" if acc.user_name and acc.user_name != acc.name else (acc.name or acc.user_name),
                                        color="onSurface",
                                        size=15,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Container(
                                        content=ft.Text("ACTIVE", size=9, weight=ft.FontWeight.BOLD, color="black"),
                                        bgcolor="primary",
                                        padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                        border_radius=4,
                                        visible=is_active and status == "active",
                                    ),
                                    ft.Container(
                                        content=ft.Row([
                                            ft.Icon(icons.HEART_BROKEN, size=10, color="white"),
                                            ft.Text("已封禁", size=9, weight=ft.FontWeight.BOLD, color="white")
                                        ], spacing=2),
                                        bgcolor="error",
                                        padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                        border_radius=4,
                                        visible=status == "banned",
                                    ),
                                ], spacing=8),
                                ft.Row([
                                    ft.Icon(icons.FINGERPRINT, size=12, color="onSurfaceVariant"),
                                    ft.Text(
                                        f"UID: {acc.user_id or '待验证'}",
                                        color="onSurfaceVariant" if acc.user_id else "error",
                                        size=11,
                                        tooltip=f"设备指纹: {getattr(acc, 'cuid', '')}",
                                    ),
                                    ft.Container(width=10),
                                    ft.Icon(icons.LANGUAGE, size=12, color="amber" if proxy_risk else "onSurfaceVariant"),
                                    ft.Text(
                                        f"代理: {proxy_info}",
                                        color="amber" if proxy_risk else "onSurfaceVariant",
                                        size=11,
                                        weight=ft.FontWeight.BOLD if proxy_risk else None,
                                        tooltip="未绑定代理，多账号同 IP 出口存在关联风控风险，建议尽快绑定代理" if proxy_risk else None,
                                    ),
                                    ft.Container(width=10),
                                    ft.Icon(icons.STAR_HALF_ROUNDED, size=12, color="primary"),
                                    ft.Text(
                                        "权重: " + "●" * ((acc.post_weight or 5) // 2) + "○" * (5 - (acc.post_weight or 5) // 2), 
                                        color="primary", 
                                        size=11, 
                                        tooltip=f"当前权重值: {acc.post_weight or 5}/10"
                                    ),
                                ], spacing=4),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        # 养号开关 (BioWarming)
                        ft.Column(
                            controls=[
                                ft.Switch(
                                    label="养号",
                                    label_style=ft.TextStyle(size=11, color="primary" if getattr(acc, 'is_maint_enabled', False) else "onSurfaceVariant"),
                                    value=getattr(acc, 'is_maint_enabled', False),
                                    on_change=lambda e, aid=acc.id: self.page.run_task(self._on_maint_toggle, aid, e.control.value),
                                    scale=0.7,
                                    tooltip="开启后，机甲将定期模拟真人浏览与点赞以提升账号权重",
                                ),
                                ft.Text(
                                    f"上次: {acc.last_maint_at.strftime('%m-%d %H:%M')}" if getattr(acc, 'last_maint_at', None) else "待维护",
                                    size=9,
                                    color="onSurfaceVariant",
                                )
                            ],
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=0,
                        ),
                        ft.Container(width=10),
                        # 动作按钮
                        ft.Row(
                            controls=[
                                ft.IconButton(
                                    icon=icons.CHECK if not is_active else icons.RADIO_BUTTON_CHECKED,
                                    tooltip="切换为此账号",
                                    icon_color="primary" if is_active else "onSurfaceVariant",
                                    disabled=is_active,
                                    on_click=lambda e, aid=acc.id: self.page.run_task(self._switch_account, aid)
                                ),
                                ft.IconButton(
                                    icon=icons.REFRESH_ROUNDED,
                                    tooltip="刷新账号信息",
                                    icon_color="primary",
                                    on_click=lambda e, aid=acc.id: self.page.run_task(self._refresh_account_info, aid)
                                ),
                                ft.IconButton(
                                    icon=icons.INFO_OUTLINED,
                                    tooltip="查看账号概览与关注贴吧",
                                    icon_color="primary",
                                    on_click=lambda e, a=acc: self.page.run_task(self._show_account_detail, a)
                                ),
                                ft.IconButton(
                                    icon=icons.EDIT_DOCUMENT,
                                    tooltip="编辑账号信息",
                                    icon_color="primary",
                                    on_click=lambda e, a=acc: self.page.run_task(self._show_edit_dialog, a)
                                ),
                                ft.IconButton(
                                    icon=icons.DELETE_OUTLINE,
                                    tooltip="删除账号",
                                    icon_color="error",
                                    on_click=lambda e, a=acc: self.page.run_task(self._show_delete_confirm, a)
                                ),
                            ],
                            spacing=0,
                        ),
                    ],
                ),
                bgcolor=with_opacity(0.03, "primary") if is_active else with_opacity(0.02, "onSurface"),
                border=ft.border.all(1, with_opacity(0.2, "primary") if is_active else with_opacity(0.1, "onSurface")),
                border_radius=10,
                padding=10,
                on_hover=self._on_item_hover,
                tooltip="点击账号信息可查看概览、关注贴吧与删帖风险",
                on_click=lambda e, a=acc: self.page.run_task(self._show_account_detail, a),
            )
            items.append(card)

        if not items and self._accounts:
            items.append(
                ft.Container(
                    content=ft.Text("未找到匹配的账号，请调整搜索关键词或状态筛选条件", color="onSurfaceVariant", size=13),
                    padding=50,
                    alignment=ft.alignment.center,
                )
            )
        return items

    def _on_search_change(self, e):
        self._search_text = e.control.value
        self.refresh_ui()

    def _on_item_hover(self, e):
        """账号卡片悬停高亮"""
        is_hovered = e.data == "true"
        e.control.bgcolor = (
            with_opacity(0.08, "primary") if is_hovered
            else (with_opacity(0.03, "primary") if e.control.border else with_opacity(0.02, "onSurface"))
        )
        try:
            e.control.update()
        except Exception:
            pass

    async def _show_account_detail(self, account):
        """打开账号概览与关注贴吧详情。"""
        if not self.db:
            return
        from datetime import datetime

        try:
            forums, overview = await asyncio.gather(
                self.db.get_forums(account.id),
                self.db.get_account_overview(account.id),
            )
        except Exception as ex:
            self._show_snackbar(f"加载账号详情失败: {ex}", "error")
            return

        display_name = account.user_name or account.name or f"账号-{account.id}"
        total = overview["total"]
        alive = overview["alive"]
        dead = overview["dead"]
        unknown = overview["unknown"]
        deleted_by_forum = overview["deleted_by_forum"]
        survival_rate = (alive / total * 100) if total else 0

        def metric(label: str, value: str, color="onSurface"):
            return ft.Container(
                content=ft.Column([
                    ft.Text(label, size=10, color="onSurfaceVariant"),
                    ft.Text(value, size=18, weight=ft.FontWeight.BOLD, color=color),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=10,
                border_radius=8,
                bgcolor=with_opacity(0.04, "onSurface"),
                expand=True,
            )

        forum_rows = []
        for forum in forums:
            deleted_count = deleted_by_forum.get(forum.fname, 0)
            # “当天”判定需双条件：is_sign_today 可能因未触发每日重置而残留昨日状态
            signed_today = bool(forum.is_sign_today) and forum.last_sign_date == datetime.now().date()
            if signed_today:
                status_label = {"success": "今日已签", "failure": "今日签到失败"}.get(forum.last_sign_status or "", "今日已签")
            else:
                status_label = "待签到"
            row_controls = [
                ft.Column([
                    ft.Text(forum.fname, size=14, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        f"等级 Lv.{forum.level or 0}  · 连续签到 {forum.sign_count or 0} 天  · {status_label}",
                        size=11,
                        color="onSurfaceVariant",
                    ),
                ], expand=True, spacing=2),
            ]
            if deleted_count:
                row_controls.extend([
                    ft.Container(
                        content=ft.Text(f"删帖 {deleted_count}", size=10, color="white"),
                        bgcolor="error",
                        border_radius=4,
                        padding=ft.padding.symmetric(horizontal=6, vertical=3),
                    ),
                    ft.OutlinedButton(
                        "取关",
                        icon=icons.HEART_BROKEN,
                        tooltip="仅让当前账号取消关注此贴吧",
                        style=ft.ButtonStyle(color="error"),
                        on_click=lambda e, fname=forum.fname: self.page.run_task(
                            self._confirm_account_forum_unfollow, account.id, fname, detail_dialog
                        ),
                    ),
                ])
            forum_rows.append(
                ft.Container(
                    content=ft.Row(row_controls, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=10,
                    border_radius=8,
                    bgcolor=with_opacity(0.06 if deleted_count else 0.02, "error" if deleted_count else "onSurface"),
                )
            )

        forum_content = ft.Column(
            forum_rows or [ft.Text("该账号暂无关注贴吧", color="onSurfaceVariant")],
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            height=330,
        )
        detail_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.ACCOUNT_CIRCLE, color="primary"), ft.Text(f"{display_name} · 账号详情")]),
            content=ft.Container(
                width=720,
                content=ft.Column([
                    ft.Text(f"UID: {account.user_id or '待验证'}  ·  状态: {account.status or 'unknown'}", size=12, color="onSurfaceVariant"),
                    ft.Row([
                        metric("成功发帖", str(total), "primary"),
                        metric("存活", str(alive), "#4CAF50"),
                        metric("被删", str(dead), "error"),
                        metric("存活率", f"{survival_rate:.0f}%", "primary"),
                        metric("关注贴吧", str(len(forums)), "secondary"),
                    ], spacing=8),
                    ft.Divider(height=20),
                    ft.Row([
                        ft.Text("关注贴吧", size=15, weight=ft.FontWeight.BOLD),
                        ft.Text("仅对有删帖记录的贴吧显示取关按钮", size=11, color="onSurfaceVariant"),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    forum_content,
                ], tight=True, spacing=10),
            ),
            actions=[ft.TextButton("关闭", on_click=lambda _: self.page.close(detail_dialog))],
        )
        self.page.open(detail_dialog)

    async def _confirm_account_forum_unfollow(self, account_id: int, fname: str, detail_dialog):
        """确认后仅对当前账号执行取关，保留其他账号的关注关系。"""
        async def do_unfollow(_):
            if not self._begin_op():
                return
            try:
                self.page.close(confirm_dialog)
                self.page.close(detail_dialog)
                from ...core.batch_post import BatchPostManager
                res = await BatchPostManager(self.db).unfollow_forums_bulk([fname], account_ids=[account_id])
                if any(f.get("reason") == "已有批量关注/取关任务在执行" for f in res["failed"]):
                    self._show_snackbar("ℹ️ 已有批量关注/取关任务在执行，本次未执行", "info")
                elif res["success"]:
                    self._show_snackbar(f"已让当前账号取消关注 '{fname}'", "success")
                elif res["skipped"]:
                    self._show_snackbar(f"当前账号被跳过：{res['skipped'][0].get('reason', '未知原因')}", "warning")
                elif res["failed"]:
                    self._show_snackbar(f"取关失败，记录已保留：{res['failed'][0].get('reason', '')}", "error")
                else:
                    self._show_snackbar(f"当前账号未关注 '{fname}'，无需取关", "info")
                await self.load_data()
            except Exception as ex:
                self._show_snackbar(f"取消关注失败: {ex}", "error")
            finally:
                self._end_op()

        confirm_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.HEART_BROKEN, color="error"), ft.Text("确认对当前账号取关？")]),
            content=ft.Text(f"将仅让当前账号取消关注“{fname}”。其他账号的关注关系不会改变。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(confirm_dialog)),
                ft.FilledButton("确认取关", icon=icons.HEART_BROKEN, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=do_unfollow),
            ],
        )
        self.page.open(confirm_dialog)

    async def _show_add_dialog(self, e):
        """显示添加账号对话框"""
        cookie_input = ft.TextField(
            label="从 Cookie 导入 (推荐)",
            hint_text="粘贴完整的 Cookie 字符串，我们将自动为您提取 BDUSS 和 STOKEN",
            multiline=True,
            min_lines=2,
            max_lines=4,
            text_size=12,
            border_color="primary",
        )
        
        bduss_field = ft.TextField(label="BDUSS", password=True, can_reveal_password=True, text_size=13, expand=True)
        stoken_field = ft.TextField(label="STOKEN (可选)", password=True, can_reveal_password=True, text_size=13, expand=True)
        name_field = ft.TextField(label="账号备注", hint_text="用于区分不同账号", text_size=13)
        
        proxy_dropdown = ft.Dropdown(
            label="关联代理",
            hint_text="为该账号指定固定出站代理",
            options=[ft.dropdown.Option("0", "不使用代理 / 直连")] + 
                    [ft.dropdown.Option(str(p.id), f"{p.protocol}://{p.host}:{p.port}") for p in self._proxies],
            value="0",
            text_size=13,
        )

        weight_slider = ft.Slider(
            min=1, max=10, divisions=9, value=5,
            label="{value}",
        )
        
        weight_row = ft.Row([
            ft.Icon(icons.STAR_HALF_ROUNDED, size=20, color="primary"),
            ft.Text("发帖权重 (智能计算可覆盖):", size=13),
            weight_slider,
            ft.Text("5", size=13, weight="bold")
        ], spacing=10)
        
        # 联动更新权重文本
        weight_slider.on_change = lambda e: (
            setattr(weight_row.controls[3], "value", str(int(e.control.value))),
            self.page.update()
        )

        def on_cookie_change(e):
            if not cookie_input.value: return
            bduss, stoken = parse_cookie(cookie_input.value)
            if bduss:
                bduss_field.value = bduss
                stoken_field.value = stoken
                self.page.update()
                self._show_snackbar("已从 Cookie 中提取凭证", "success")

        cookie_input.on_change = on_cookie_change

        async def on_submit(e):
            if not bduss_field.value:
                self._show_snackbar("BDUSS 不能为空", "error")
                return
            
            submit_btn.disabled = True
            submit_btn.text = "验证中..."
            self.page.update()
            
            # 验证账号 (增加 15 秒硬超时防护，防止底层阻塞)
            import asyncio
            try:
                success, uid, uname, err = await asyncio.wait_for(
                    verify_account(bduss_field.value, stoken_field.value),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                self._show_snackbar("网络验证超时: 请检查本地网络或是否在海外", "error")
                submit_btn.disabled = False
                submit_btn.text = "验证并添加"
                self.page.update()
                return
            except Exception as e:
                self._show_snackbar(f"验证过程发生异常: {str(e)}", "error")
                submit_btn.disabled = False
                submit_btn.text = "验证并添加"
                self.page.update()
                return
                
            if not success:
                self._show_snackbar(f"账号验证失败: {err}", "error")
                submit_btn.disabled = False
                submit_btn.text = "验证并添加"
                self.page.update()
                return

            proxy_id = int(proxy_dropdown.value) if proxy_dropdown.value != "0" else None
            
            try:
                # 修复传递参数缺失：将 uid 和 uname 传递进去
                from ...core.account import encrypt_value
                await self.db.add_account(
                    name=name_field.value or uname,
                    bduss=encrypt_value(bduss_field.value),
                    stoken=encrypt_value(stoken_field.value) if stoken_field.value else "",
                    user_id=uid,
                    user_name=uname,
                    proxy_id=proxy_id,
                    post_weight=int(weight_slider.value)
                )
                
                await log_info(f"账号库录入成功: {uname} (关联代理: {proxy_id or '无'})")
                
                self.page.close(dialog)
                await self.load_data()
                self._show_snackbar(f"账号 '{uname}' 添加成功", "success")
                
            except Exception as ex:
                self._show_snackbar(f"写入数据库失败: {str(ex)}", "error")
                submit_btn.disabled = False
                submit_btn.text = "验证并添加"
                self.page.update()

        submit_btn = ft.FilledButton("验证并添加", icon=icons.CHECK, on_click=on_submit)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.PERSON_ADD_ROUNDED, color="primary"), ft.Text("添加百度账号")]),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row([
                            ft.Text("通过 Cookie 自动填充或手动输入凭据:", size=12, color="onSurfaceVariant", expand=True),
                            ft.TextButton("《手把手：教程》", icon=icons.HELP_OUTLINE, on_click=self._show_tutorial, style=ft.ButtonStyle(padding=0))
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        cookie_input,
                        ft.Divider(height=10, color="transparent"),
                        ft.Row([bduss_field, stoken_field], spacing=10),
                        name_field,
                        proxy_dropdown,
                        weight_row,
                    ],
                    tight=True,
                    spacing=15,
                    width=500,
                ),
                padding=10,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                submit_btn,
            ],
        )

        self.page.open(dialog)

    async def _show_edit_dialog(self, account):
        """显示修改账号对话框"""
        from ...core.account import decrypt_value, encrypt_value
        
        # 从数据库获取完整账号对象（含加密凭据）
        full_account = await self.db.get_account(account.id)
        if not full_account:
            self._show_snackbar("无法获取账号信息", "error")
            return
        
        # 解密现有凭据
        bduss_val = ""
        stoken_val = ""
        try:
            bduss_val = decrypt_value(full_account.bduss)
        except Exception:
            bduss_val = ""
        if full_account.stoken:
            try:
                stoken_val = decrypt_value(full_account.stoken)
            except Exception:
                stoken_val = ""

        name_field = ft.TextField(label="账号备注", value=account.name or account.user_name, text_size=13)
        bduss_field = ft.TextField(label="BDUSS", value=bduss_val, password=True, can_reveal_password=True, text_size=13, expand=True)
        stoken_field = ft.TextField(label="STOKEN (可选)", value=stoken_val, password=True, can_reveal_password=True, text_size=13, expand=True)
        
        cookie_input = ft.TextField(
            label="从 Cookie 更新凭据 (可选)",
            hint_text="粘贴新的 Cookie 字符串以快速更新 BDUSS 和 STOKEN",
            multiline=True,
            min_lines=2,
            max_lines=3,
            text_size=11,
            border_color="primary",
        )

        def on_cookie_change(e):
            if not cookie_input.value: return
            bduss, stoken = parse_cookie(cookie_input.value)
            if bduss:
                bduss_field.value = bduss
                stoken_field.value = stoken
                self.page.update()
                self._show_snackbar("凭据已从 Cookie 提取", "success")

        cookie_input.on_change = on_cookie_change

        proxy_dropdown = ft.Dropdown(
            label="关联代理",
            options=[ft.dropdown.Option("0", "不使用代理 / 直连")] + 
                    [ft.dropdown.Option(str(p.id), f"{p.protocol}://{p.host}:{p.port}") for p in self._proxies],
            value=str(account.proxy_id or "0"),
            text_size=13,
        )

        edit_weight_slider = ft.Slider(
            min=1, max=10, divisions=9, value=float(account.post_weight or 5),
            label="{value}",
        )
        
        edit_weight_row = ft.Row([
            ft.Icon(icons.STAR_HALF_ROUNDED, size=20, color="primary"),
            ft.Text("发帖权重 (智能计算可覆盖):", size=13),
            edit_weight_slider,
            ft.Text(str(account.post_weight or 5), size=13, weight="bold")
        ], spacing=10)
        
        # 联动更新权重文本
        edit_weight_slider.on_change = lambda e: (
            setattr(edit_weight_row.controls[3], "value", str(int(e.control.value))),
            self.page.update()
        )

        async def on_save(e):
            if not bduss_field.value:
                self._show_snackbar("BDUSS 不能为空", "error")
                return
            
            save_btn.disabled = True
            save_btn.text = "保存中..."
            self.page.update()
            
            try:
                # 如果修改了凭据，则重新验证
                if bduss_field.value != bduss_val or stoken_field.value != stoken_val:
                    success, uid, uname, err = await asyncio.wait_for(
                        verify_account(bduss_field.value, stoken_field.value),
                        timeout=15.0
                    )
                    if not success:
                        self._show_snackbar(f"新凭据验证失败: {err}", "error")
                        save_btn.disabled = False
                        save_btn.text = "保存修改"
                        self.page.update()
                        return
                
                proxy_id = int(proxy_dropdown.value) if proxy_dropdown.value != "0" else None
                
                await self.db.update_account(
                    account.id,
                    name=name_field.value,
                    bduss=encrypt_value(bduss_field.value),
                    stoken=encrypt_value(stoken_field.value) if stoken_field.value else "",
                    proxy_id=proxy_id,
                    post_weight=int(edit_weight_slider.value)
                )
                
                self.page.close(dialog)
                await self.load_data()
                self._show_snackbar(f"账号 '{account.user_name}' 信息已更新", "success")
                
            except Exception as ex:
                self._show_snackbar(f"更新失败: {str(ex)}", "error")
                save_btn.disabled = False
                save_btn.text = "保存修改"
                self.page.update()

        save_btn = ft.FilledButton("保存修改", icon=icons.SAVE_ROUNDED, on_click=on_save)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.EDIT_DOCUMENT, color="primary"), ft.Text("修改账号信息")]),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row([
                            ft.Text(f"正在编辑账号: {account.user_name} (UID: {account.user_id})", size=12, color="onSurfaceVariant", expand=True),
                            ft.TextButton("《手把手：教程》", icon=icons.HELP_OUTLINE, on_click=self._show_tutorial, style=ft.ButtonStyle(padding=0))
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        name_field,
                        cookie_input,
                        ft.Row([bduss_field, stoken_field], spacing=10),
                        proxy_dropdown,
                        edit_weight_row,
                    ],
                    tight=True,
                    spacing=15,
                    width=500,
                ),
                padding=10,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                save_btn,
            ],
        )
        self.page.open(dialog)
    async def _switch_account(self, account_id: int):
        """切换账号"""
        await switch_account(self.db, account_id)
        self._active_id = account_id
        self.refresh_ui()
        self._show_snackbar("活跃账号已切换", "success")

    async def _refresh_account_info(self, account_id: int):
        """刷新账号信息"""
        if not self._begin_op():
            return
        try:
            acc = await refresh_account(self.db, account_id)
            if acc:
                await self.load_data()
                if acc.status.startswith("invalid"):
                    self._show_snackbar(f"账号 '{acc.name}' 已失效", "error")
                else:
                    self._show_snackbar(f"账号 '{acc.user_name}' 刷新成功", "success")
            else:
                self._show_snackbar("刷新失败，账号不存在", "error")
        finally:
            self._end_op()

    async def _show_delete_confirm(self, account):
        """显示删除确认框"""
        account_id = account.id
        account_name = account.user_name or account.name or str(account_id)

        async def do_delete(e):
            await remove_account(self.db, account_id)
            await log_warn(f"账号凭据已被用户手动移除: {account_name}")
            self.page.close(dialog)
            await self.load_data()
            self._show_snackbar(f"账号 '{account_name}' 已从本地移除", "info")

        dialog = ft.AlertDialog(
            title=ft.Text(f"确认移除账号: {account_name}?"),
            content=ft.Text("此操作仅从本地数据库移除凭据，不会影响贴吧账号本身状态。"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                ft.TextButton("确认移除", icon=icons.DELETE_FOREVER, icon_color="error", on_click=do_delete),
            ],
        )
        self.page.open(dialog)

    def _on_filter_change(self, e):
        self._filter_status = e.control.value
        self.refresh_ui()

    def _on_sort_change(self, e):
        self._sort_mode = e.control.value
        self.refresh_ui()

    def _on_banned_banner_click(self, e=None):
        """点击战损报警横幅：一键筛选已封禁账号"""
        self._filter_status = "banned"
        if hasattr(self, "_status_filter_dropdown"):
            self._status_filter_dropdown.value = "banned"
        self.refresh_ui()

    @staticmethod
    def _status_matches(status: str, filter_value: str) -> bool:
        """状态筛选匹配。`invalid: xxx` 这类带错误详情的状态按前缀归入 invalid。"""
        if filter_value == "all":
            return True
        if filter_value == "invalid":
            return status == "invalid" or status.startswith("invalid:")
        return status == filter_value

    def _account_sort_key(self, acc):
        """账号列表排序键。"""
        if self._sort_mode == "weight":
            return -(getattr(acc, "post_weight", None) or 5)
        if self._sort_mode == "status":
            status = getattr(acc, "status", None) or "unknown"
            if status.startswith("invalid"):
                status = "invalid"
            return self._STATUS_SORT_ORDER.get(status, 4)
        if self._sort_mode == "verified":
            lv = getattr(acc, "last_verified", None)
            # 最近验证的排前面；从未验证的排最后
            return (0, -lv.timestamp()) if lv else (1, 0)
        return acc.id

    async def _on_maint_toggle(self, account_id: int, value: bool):
        """开启或关闭养号维护功能"""
        await self.db.update_account(account_id, is_maint_enabled=value)
        # 局部更新内存中的状态
        for acc in self._accounts:
            if acc.id == account_id:
                acc.is_maint_enabled = value
                break
        self._show_snackbar(f"账号养号维护已{'开启' if value else '关闭'}", "success")
        self.refresh_ui()

    def _on_item_select(self, e):
        aid = e.control.data
        if e.control.value:
            self._selected_ids.add(aid)
        else:
            self._selected_ids.discard(aid)
        self._update_bulk_bar()

    def _toggle_select_all(self, e):
        # 仅选择当前过滤后的账号
        search_lower = self._search_text.lower()
        if e.control.value:
            for acc in self._accounts:
                status = getattr(acc, "status", "unknown")
                if not self._status_matches(status, self._filter_status):
                    continue
                if search_lower:
                    match = (search_lower in (acc.name or "").lower() or 
                             search_lower in (acc.user_name or "").lower() or 
                             search_lower in str(acc.user_id))
                    if not match: continue
                self._selected_ids.add(acc.id)
        else:
            self._selected_ids.clear()
        self.refresh_ui()
        self._update_bulk_bar()

    def _update_bulk_bar(self):
        has_sel = len(self._selected_ids) > 0
        self._bulk_verify_btn.visible = has_sel
        self._bulk_delete_btn.visible = has_sel
        self._bulk_verify_btn.text = f"批量验证 ({len(self._selected_ids)})"
        self._bulk_delete_btn.text = f"批量删除 ({len(self._selected_ids)})"
        self.page.update()

    async def _bulk_verify_accounts(self, e):
        if not self._selected_ids:
            return
        if not self._begin_op():
            return
        ids = list(self._selected_ids)
        try:
            self._open_progress_dialog("批量验证中...", determinate=True)
            results = []  # (显示名, 结果标签)
            for i, aid in enumerate(ids, 1):
                try:
                    acc = await refresh_account(self.db, aid)
                except Exception as ex:
                    results.append((f"账号#{aid}", f"❌ 网络异常"))
                    self._update_progress(f"正在验证 {i}/{len(ids)}：账号#{aid}（异常）", i / len(ids))
                    await log_warn(f"批量验证账号 #{aid} 异常: {ex}")
                    continue
                display = (acc.user_name or acc.name) if acc else f"账号#{aid}"
                if acc is None:
                    results.append((display, "❌ 读取失败"))
                elif acc.status == "active":
                    results.append((display, "✅ 有效"))
                elif acc.status == "banned":
                    results.append((display, "💔 已封禁"))
                elif acc.status.startswith("invalid") or acc.status == "expired":
                    results.append((display, "⛔ 已失效"))
                else:
                    results.append((display, f"⚠️ {acc.status}"))
                self._update_progress(f"正在验证 {i}/{len(ids)}：{display}", i / len(ids))

            self._selected_ids.clear()
            self._update_bulk_bar()
            await self.load_data()

            ok_count = sum(1 for _, s in results if s == "✅ 有效")
            fail_lines = "\n".join(f"{n}：{s}" for n, s in results if s != "✅ 有效") or "无"
            detail_lines = "\n".join(f"{n}：{s}" for n, s in results[:20])
            if len(results) > 20:
                detail_lines += f"\n... 其余 {len(results) - 20} 个账号"
            dialog = ft.AlertDialog(
                title=ft.Text(f"批量验证完成：{ok_count}/{len(results)} 有效"),
                content=ft.Container(
                    content=ft.Text(f"失效/异常清单：\n{fail_lines}\n\n全部结果：\n{detail_lines}", size=12, selectable=True),
                    width=380,
                    height=280,
                ),
                actions=[ft.TextButton("确定", on_click=lambda _: self.page.close(dialog))],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.open(dialog)
        finally:
            self._close_progress_dialog()
            self._end_op()

    async def _bulk_delete_accounts(self, e):
        if not self._selected_ids: return
        
        async def do_delete(_):
            count = len(self._selected_ids)
            for aid in list(self._selected_ids):
                await remove_account(self.db, aid)
            self._selected_ids.clear()
            self._update_bulk_bar()
            await self.load_data()
            self._show_snackbar(f"已批量注销 {count} 个账号", "success")
            self.page.close(dialog)

        dialog = ft.AlertDialog(
            title=ft.Text("确认批量从本机注销？"),
            content=ft.Text(f"将注销锁定的 {len(self._selected_ids)} 个账号及其所有的登录凭据。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("确认注销", icon=icons.DELETE_FOREVER, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=do_delete),
            ]
        )
        self.page.open(dialog)

    async def _auto_calculate_weights(self, e, incremental: bool = False):
        """一键自动计算所有账号的推荐权重"""
        from ...core.batch_post import AutoWeightCalculator

        if not self._begin_op():
            return
        try:
            await self._auto_calculate_weights_impl(e, incremental)
        finally:
            self._end_op()

    async def _auto_calculate_weights_impl(self, e, incremental: bool = False):
        self._show_snackbar("正在分析账号数据，计算智能权重...", "info")

        # 加载自定义权重比例
        ratios = await AutoWeightCalculator.get_weight_ratios(self.db)

        if incremental:
            accounts_with_forums = await self.db.get_accounts_with_forums()
            calc_times = [a.last_weight_calc_at for a, _ in accounts_with_forums if a.last_weight_calc_at]
            if calc_times:
                from datetime import datetime as _dt
                since = min(calc_times)
                accounts_with_forums = await self.db.get_accounts_needing_weight_recalc(since)
                if not accounts_with_forums:
                    self._show_snackbar("所有账号权重均为最新，无需重新计算", "info")
                    return
        else:
            accounts_with_forums = await self.db.get_accounts_with_forums()

        if not accounts_with_forums:
            self._show_snackbar("未找到账号数据", "error")
            return

        weight_updates = []
        results = []

        for account, forums in accounts_with_forums:
            recommended_weight, details = await AutoWeightCalculator.calculate(account, forums, db=self.db)
            weight_updates.append((account.id, recommended_weight))

            old_weight = account.post_weight or 5
            change = ""
            if recommended_weight > old_weight:
                change = "↑"
            elif recommended_weight < old_weight:
                change = "↓"

            results.append({
                "name": account.name or f"账号-{account.id}",
                "old": old_weight,
                "new": recommended_weight,
                "change": change,
                "details": details,
            })

        # 批量更新权重
        update_result = await self.db.batch_update_weights(weight_updates, source="auto_calculate")

        # 更新增量计算时间戳
        updated_ids = [account.id for account, _ in accounts_with_forums]
        await self.db.update_weight_calc_timestamp(updated_ids)

        # 构建结果展示
        result_lines = [f"✅ 权重智能计算完成！共分析 {len(results)} 个账号"]
        result_lines.append(f"更新成功: {update_result['updated']} | 失败: {update_result['failed']}")
        result_lines.append("")
        result_lines.append("📊 计算依据：")
        for key, label in AutoWeightCalculator._WEIGHT_LABELS.items():
            pct = int(ratios[key] * 100)
            result_lines.append(f"• {label} ({pct}%)")
        result_lines.append("")
        result_lines.append("📋 权重变化详情：")

        # 按变化排序：降权优先 > 不变 > 升权
        results.sort(key=lambda x: (x["change"] == "↑", x["change"] == "↓", -x["old"]))

        for r in results[:10]:  # 只显示前10个
            emoji = "🟢" if r["change"] == "↑" else ("🔴" if r["change"] == "↓" else "⚪")
            result_lines.append(f"{emoji} {r['name']}: {r['old']} → {r['new']} {r['change']}")

        if len(results) > 10:
            result_lines.append(f"... 还有 {len(results) - 10} 个账号")

        # 显示结果对话框
        dialog = ft.AlertDialog(
            title=ft.Text("🧠 智能权重计算报告"),
            content=ft.Container(
                content=ft.Column([
                    ft.Text("\n".join(result_lines), size=11, selectable=True),
                    ft.Container(height=10),
                    ft.Text("💡 提示：权重越高，该账号在批量发帖时被选中的概率越大。建议定期执行此功能以保持权重与账号状态同步。",
                           size=10, color="onSurfaceVariant"),
                ], tight=True),
                width=400,
                height=400,
            ),
            actions=[
                ft.TextButton("查看历史", on_click=lambda _: (self.page.close(dialog), self.page.run_task(self._show_weight_history))),
                ft.TextButton("确定", on_click=lambda _: self.page.close(dialog)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

        # 刷新账号列表显示新权重
        await self.load_data()

    async def _show_weight_config_dialog(self):
        """显示评分模型权重配置对话框"""
        import json
        from ...core.batch_post import AutoWeightCalculator

        current = await AutoWeightCalculator.get_weight_ratios(self.db)
        labels = AutoWeightCalculator._WEIGHT_LABELS

        sliders: dict[str, ft.Slider] = {}
        pct_texts: dict[str, ft.Text] = {}
        rows = []

        for key, default_val in current.items():
            slider = ft.Slider(
                min=0, max=100, divisions=20,
                value=int(default_val * 100),
                label="{value}%",
                expand=True,
            )
            pct_text = ft.Text(f"{int(default_val * 100)}%", width=50, text_align="right")

            def _make_on_change(t):
                def _handler(e):
                    t.value = f"{int(e.control.value)}%"
                    self.page.update()
                return _handler

            slider.on_change = _make_on_change(pct_text)
            sliders[key] = slider
            pct_texts[key] = pct_text
            rows.append(ft.Row([
                ft.Text(labels.get(key, key), width=100, size=13),
                slider,
                pct_text,
            ], spacing=10))

        async def _on_save(e):
            ratios = {k: s.value / 100.0 for k, s in sliders.items()}
            total = sum(ratios.values())
            if abs(total - 1.0) > 0.05:
                self._show_snackbar(f"权重总和为 {total:.0%}，需接近 100%", "error")
                return
            await self.db.set_setting("auto_weight_ratios", json.dumps(ratios))
            self.page.close(dialog)
            self._show_snackbar("评分模型权重已保存", "success")

        def _on_reset(e):
            for key, s in sliders.items():
                s.value = int(AutoWeightCalculator.DEFAULT_WEIGHTS[key] * 100)
                pct_texts[key].value = f"{int(AutoWeightCalculator.DEFAULT_WEIGHTS[key] * 100)}%"
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("⚙️ 评分模型权重配置"),
            content=ft.Container(
                content=ft.Column(rows + [
                    ft.Text("提示：各项权重总和应为 100%。调整后点击「智能权重计算」即使用新比例。",
                           size=10, color="onSurfaceVariant"),
                ], spacing=8),
                width=420,
            ),
            actions=[
                ft.TextButton("恢复默认", on_click=_on_reset),
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("保存", on_click=_on_save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

    async def _show_weight_history(self, account_id: int | None = None):
        """显示权重变更历史"""
        history = await self.db.get_weight_history(account_id=account_id, limit=50)

        if not history:
            self._show_snackbar("暂无权重变更记录", "info")
            return

        source_labels = {"manual": "手动", "auto_calculate": "智能计算", "batch": "批量"}
        rows = []
        for h in history:
            change = "↑" if h.new_weight > h.old_weight else ("↓" if h.new_weight < h.old_weight else "=")
            source_label = source_labels.get(h.source, h.source)
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(h.account_name or str(h.account_id), size=11)),
                ft.DataCell(ft.Text(f"{h.old_weight} → {h.new_weight} {change}", size=11)),
                ft.DataCell(ft.Text(source_label, size=11)),
                ft.DataCell(ft.Text(h.created_at.strftime("%m-%d %H:%M") if h.created_at else "", size=10)),
            ]))

        table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("账号", size=11, weight="bold")),
                ft.DataColumn(ft.Text("权重变化", size=11, weight="bold")),
                ft.DataColumn(ft.Text("来源", size=11, weight="bold")),
                ft.DataColumn(ft.Text("时间", size=11, weight="bold")),
            ],
            rows=rows,
            border=ft.border.all(1, "outline"),
            heading_row_height=30,
            column_spacing=20,
        )

        dialog = ft.AlertDialog(
            title=ft.Text("📜 权重变更历史"),
            content=ft.Container(
                content=ft.Column([table], scroll=ft.ScrollMode.AUTO),
                width=550,
                height=400,
            ),
            actions=[ft.TextButton("关闭", on_click=lambda _: self.page.close(dialog))],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)

    def _show_tutorial(self, e):
        """显示分步引导教程"""
        content = ft.Column([
            ft.Text("如何获取您的贴吧凭据 (Cookie)", size=18, weight=ft.FontWeight.BOLD),
            ft.Divider(),
            ft.Text("1. 打开电脑浏览器 (推荐 Chrome/Edge)，访问 tieba.baidu.com并登录。", size=13),
            ft.Text("2. 按下 F12 或 Ctrl+Shift+I 打开开发者工具。", size=13),
            ft.Text("3. 切换到 Application (应用程序) 选项卡 (如果没看到，点击 >> 展开)。", size=13),
            ft.Text("4. 在左侧选择 Storage -> Cookies -> https://tieba.baidu.com。", size=13),
            ft.Text("5. 在右侧列表中寻找 BDUSS 和 STOKEN 项，双击 Value 选中后按 Ctrl+C 复制。", size=13),
            ft.Container(height=10),
            ft.Container(
                content=ft.Text("💡 提示：您可以直接复制开发者工具中 Network -> Headers 下的完整 'Cookie:' 文本，并在输入框粘贴，程序会自动尝试提取。", 
                               size=12, color="primary"),
                padding=10,
                bgcolor=with_opacity(0.1, "primary"),
                border_radius=5,
            ),
        ], tight=True, spacing=12, width=500)

        dialog = ft.AlertDialog(
            content=content,
            actions=[
                ft.TextButton("了解，去获取", on_click=lambda _: self.page.close(dialog))
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

    def _show_snackbar(self, message: str, type="info"):
        from ..components.toast import show_toast
        show_toast(self.page, message, type)
