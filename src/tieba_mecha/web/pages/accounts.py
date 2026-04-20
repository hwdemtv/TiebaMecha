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
        
        # 吧库管理相关状态
        self._matrix_stats = []
        self._matrix_search_text = ""
        self._matrix_selected_fnames: set[str] = set()
        self._matrix_banned_filter = False  # 封禁筛选开关
        self._matrix_deleted_filter = False  # 被删筛选开关
        self._active_tab_index = 0
        
        # 存活分析数据
        self._survival_stats = {"total": 0, "alive": 0, "dead": 0, "unknown": 0}
        self._survival_by_account = []
        self._survival_search_text = ""

    async def load_data(self):
        """加载数据"""
        if not self.db:
            return
        
        # 加载账号
        self._accounts = await list_accounts(self.db)
        active_acc = await self.db.get_active_account()
        self._active_id = active_acc.id if active_acc else None
        
        # 加载代理列表用于下拉框
        self._proxies = await self.db.get_active_proxies()

        # 加载全吧库统计
        await self._refresh_matrix_stats()
        
        # 加载存活统计数据
        self._survival_stats = await self.db.get_survival_stats()
        self._survival_by_account = await self.db.get_survival_by_account()
        
        self.refresh_ui()

    async def _refresh_matrix_stats(self):
        """统一刷新矩阵统计数据（含封禁详情），替代散落各处的单独刷新"""
        # 自动同步 is_post_target（根据封禁状态和删帖记录自动判定）
        await self.db.auto_sync_post_target()
        # 回填历史击穿数（仅补 success_count=0 的记录，幂等）
        await self.db.backfill_success_count()
        self._matrix_stats = await self.db.get_forum_matrix_stats()
        self._banned_forum_details = await self.db.get_banned_forums_detail()
        self._banned_forum_map: dict[str, list[dict]] = {}
        for item in self._banned_forum_details:
            self._banned_forum_map.setdefault(item['fname'], []).append(item)

    def refresh_ui(self):
        """刷新 UI"""
        # 始终刷新当前 tab 的内容（确保数据更新时 UI 也更新）
        current_tab = self._active_tab_index
        
        # 账号档案中心
        if hasattr(self, "account_list"):
            self.account_list.controls = self._build_account_items()
            # 统计封禁损耗
            banned_count = sum(1 for a in getattr(self, "_accounts", []) if getattr(a, "status", "") == "banned")
            if hasattr(self, "account_stats_info"):
                if banned_count > 0:
                    self.account_stats_info.text = f"🚨 战损报警：检测到 {banned_count} 个已封禁账号"
                    self.account_stats_info.visible = True
                else:
                    self.account_stats_info.visible = False
            # 只有在当前 tab 是账号中心时才更新 page
            if current_tab == 0:
                self.page.update()
        
        # 全域战略吧库
        if hasattr(self, "matrix_list"):
            self.matrix_list.controls = self._build_matrix_items()
            self._update_matrix_header()
            if current_tab == 1:
                self.page.update()
        
        # 存活分析
        if hasattr(self, "survival_list"):
            self.survival_list.controls = self._build_survival_items()
            self._update_survival_header()
            if current_tab == 2:
                self.page.update()
        
        # 异常记录
        if hasattr(self, "exception_list") and current_tab == 3:
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
                    text="存活分析",
                    icon=icons.MONITOR_HEART_ROUNDED,
                    content=self._build_survival_tab(),
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
                ft.dropdown.Option("error", "🟡 异常"),
                ft.dropdown.Option("banned", "💔 已封禁"),
            ],
            value=self._filter_status,
            on_change=self._on_filter_change,
            width=120,
            height=45,
            content_padding=10,
            text_size=13,
            border_radius=10,
        )

        self.account_stats_info = ft.Text("", size=12, color="error", visible=False)

        self.bulk_bar = ft.Row([
            ft.Checkbox(label="全选", on_change=self._toggle_select_all),
            ft.TextButton("一键智能权重", icon=icons.AUTO_AWESOME, on_click=lambda e: self.page.run_task(self._auto_calculate_weights, e), tooltip="根据账号等级/签到成功率/状态等自动计算推荐权重"),
            ft.TextButton("批量验证", icon=icons.VERIFIED_USER, on_click=lambda e: self.page.run_task(self._bulk_verify_accounts, e), visible=False),
            ft.TextButton("批量删除", icon=icons.DELETE_SWEEP, on_click=lambda e: self.page.run_task(self._bulk_delete_accounts, e), style=ft.ButtonStyle(color="error"), visible=False),
            ft.Container(expand=True),
            self.account_stats_info
        ], spacing=10, visible=True)

        # 账号列表容器
        self.account_list = ft.Column(
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        return ft.Column(
            controls=[
                ft.Row([search_field, status_filter, add_btn], spacing=10),
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
        search_field = ft.TextField(
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
        self.matrix_bulk_clear_target_btn = ft.TextButton(
            "清理靶场", icon=icons.REMOVE_CIRCLE_OUTLINED,
            on_click=lambda e: self.page.run_task(self._bulk_matrix_clear_target),
            tooltip="从靶场池中移除（不取消关注）",
            visible=False,
        )
        self.matrix_bulk_bar = ft.Row([
            self.matrix_select_all_cb,
            self.matrix_bulk_toggle_target_btn,
            self.matrix_bulk_follow_btn,
            self.matrix_bulk_unfollow_btn,
            self.matrix_bulk_clear_target_btn,
            self.matrix_bulk_tag_btn,
        ], spacing=5, wrap=True)

        # 列表容器
        self.matrix_list = ft.ListView(
            expand=True,
            spacing=10,
            padding=10,
        )

        return ft.Column(
            controls=[
                ft.Row([search_field, self.banned_filter_btn, self.deleted_filter_btn, sync_btn, clear_search_btn, follow_btn], spacing=10),
                self.matrix_bulk_bar,
                self.matrix_header_info,
                ft.Divider(color=with_opacity(0.1, "primary"), height=1),
                ft.Container(
                    content=self.matrix_list,
                    expand=True,
                ),
            ],
            spacing=10,
        )

    def _build_survival_tab(self) -> ft.Control:
        """存活分析标签页"""
        # 存活率概览卡片
        self.survival_rate_display = ft.Text("存活率: --%", size=14, weight=ft.FontWeight.BOLD, color="primary")
        
        self.survival_header_info = ft.Text(
            "总发帖数: 0 | 存活: 0 | 阵亡: 0 | 未知: 0",
            size=12,
            color="onSurfaceVariant"
        )
        
        self.survival_check_btn = ft.TextButton(
            "🔍 批量检测存活",
            icon=icons.REFRESH_ROUNDED,
            on_click=lambda e: self.page.run_task(self._bulk_check_survival, e),
            tooltip="检测所有帖子的存活状态",
        )
        
        self.survival_list = ft.ListView(
            expand=True,
            spacing=10,
            padding=10,
        )
        
        return ft.Column(
            controls=[
                ft.Row([
                    ft.Container(
                        content=ft.Column([
                            ft.Text("存活率", size=11, color="onSurfaceVariant"),
                            self.survival_rate_display,
                        ], spacing=2),
                        padding=10,
                        bgcolor=with_opacity(0.1, "primary"),
                        border_radius=8,
                    ),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("总统计", size=11, color="onSurfaceVariant"),
                            self.survival_header_info,
                        ], spacing=2),
                        padding=10,
                        expand=True,
                    ),
                    self.survival_check_btn,
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(color=with_opacity(0.1, "primary"), height=1),
                ft.Container(
                    content=self.survival_list,
                    expand=True,
                ),
            ],
            spacing=10,
        )

    def _update_survival_header(self):
        """更新存活分析头部信息"""
        stats = self._survival_stats
        total = stats.get("total", 0)
        alive = stats.get("alive", 0)
        dead = stats.get("dead", 0)
        unknown = stats.get("unknown", 0)
        
        rate = (alive / total * 100) if total > 0 else 0
        self.survival_rate_display.value = f"存活率: {rate:.1f}%"
        self.survival_rate_display.color = "#4CAF50" if rate >= 80 else "#FF9800" if rate >= 50 else "#F44336"
        
        self.survival_header_info.value = f"总发帖数: {total} | 存活: {alive} | 阵亡: {dead} | 未知: {unknown}"

    def _build_survival_items(self) -> list:
        """构建存活分析列表项"""
        items = []
        
        # 过滤
        filtered = self._survival_by_account
        if self._survival_search_text:
            search = self._survival_search_text.lower()
            filtered = [a for a in filtered if search in a.get("account_name", "").lower()]
        
        if not filtered:
            items.append(ft.Container(
                content=ft.Text("暂无发帖记录或存活数据", size=13, color="onSurfaceVariant"),
                padding=20,
                alignment=ft.alignment.center,
            ))
            return items
        
        for acc_stat in filtered:
            total = acc_stat.get("total", 0)
            alive = acc_stat.get("alive", 0)
            dead = acc_stat.get("dead", 0)
            unknown = acc_stat.get("unknown", 0)
            rate = (alive / total * 100) if total > 0 else 0
            
            # 状态颜色
            if rate >= 80:
                status_color = "#4CAF50"
                status_icon = icons.CHECK_CIRCLE_ROUNDED
            elif rate >= 50:
                status_color = "#FF9800"
                status_icon = icons.WARNING_AMBER_ROUNDED
            else:
                status_color = "#F44336"
                status_icon = icons.ERROR_OUTLINE
            
            card = ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text(acc_stat.get("account_name", "未知账号"), size=14, weight=ft.FontWeight.BOLD),
                        ft.Container(expand=True),
                        ft.Icon(status_icon, color=status_color, size=18),
                        ft.Text(f"{rate:.0f}%", size=13, weight=ft.FontWeight.BOLD, color=status_color),
                    ]),
                    ft.Row([
                        ft.Text(f"总发帖: {total}", size=12, color="onSurfaceVariant"),
                        ft.Container(expand=True),
                        ft.Text(f"✅存活: {alive}", size=12, color="#4CAF50"),
                        ft.Text(f"❌阵亡: {dead}", size=12, color="#F44336"),
                        ft.Text(f"❓未知: {unknown}", size=12, color="#9E9E9E"),
                    ]),
                    ft.Container(
                        content=ft.ProgressBar(
                            value=rate / 100,
                            color=status_color,
                            bgcolor=with_opacity(0.2, status_color),
                            height=6,
                        ),
                        margin=ft.margin.only(top=5),
                    ),
                ], spacing=5),
                padding=15,
                bgcolor=with_opacity(0.05, "onSurface"),
                border_radius=8,
                border=ft.border.all(1, with_opacity(0.1, "primary")),
            )
            items.append(card)
        
        return items

    async def _bulk_check_survival(self, e):
        """批量检测所有帖子的存活状态"""
        from ...core.post import check_post_survival
        
        self._show_snackbar("🚀 正在启动存活检测任务...", "info")
        
        try:
            # 获取所有有 posted_tid 的物料
            materials = await self.db.get_materials(status="success")
            targets = [m for m in materials if m.posted_tid and m.posted_tid != 0]
            
            if not targets:
                self._show_snackbar("没有需要检测的帖子", "warning")
                return
            
            alive_count = 0
            dead_count = 0
            total = len(targets)
            
            for i, m in enumerate(targets):
                try:
                    status, reason = await check_post_survival(m.posted_tid)
                    await self.db.update_material_survival_status(m.id, status, reason)
                    
                    if status == "alive":
                        alive_count += 1
                    else:
                        dead_count += 1
                except Exception as ex:
                    await self.db.update_material_survival_status(m.id, "dead", str(ex))
                    dead_count += 1
                
                # 每 10 条刷新一次
                if (i + 1) % 10 == 0:
                    self._survival_stats = await self.db.get_survival_stats()
                    self._survival_by_account = await self.db.get_survival_by_account()
                    self.refresh_ui()
                
                import asyncio
                await asyncio.sleep(0.3)
            
            # 重新加载数据
            self._survival_stats = await self.db.get_survival_stats()
            self._survival_by_account = await self.db.get_survival_by_account()
            self.refresh_ui()
            
            self._show_snackbar(f"✅ 检测完成: 存活 {alive_count} 条, 阵亡 {dead_count} 条", "success")
            
        except Exception as ex:
            self._show_snackbar(f"❌ 检测失败: {str(ex)}", "error")

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
                        ft.Text(f"账号ID: {event['account_id'] or '-'}", size=12, color="onSurfaceVariant"),
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
        self.refresh_ui()

    def _on_toggle_banned_filter(self, e):
        """切换封禁贴吧筛选"""
        self._matrix_banned_filter = not self._matrix_banned_filter
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
        # 找到搜索框并清空
        for ctrl in self.tabs.tabs[1].content.controls:
            if isinstance(ctrl, ft.Row) and len(ctrl.controls) > 0:
                for c in ctrl.controls:
                    if isinstance(c, ft.TextField):
                        c.value = ""
                        break
        self.refresh_ui()

    async def _on_sync_matrix(self, e):
        """全域同步关注列表"""
        from ...core.sign import sync_forums_to_db
        
        self._show_snackbar("🚀 指令下达：正在启动全域矩阵关注同步...", "info")
        
        try:
            # 标记按钮状态或显示进度 (可选)
            added = await sync_forums_to_db(self.db)
            
            # 重新加载统计数据
            await self._refresh_matrix_stats()
            self.refresh_ui()
            
            self._show_snackbar(f"✅ 全域同步完成！矩阵新增 {added} 个战略支点", "success")
        except Exception as ex:
            self._show_snackbar(f"❌ 同步失败: {str(ex)}", "error")

    def _show_follow_forum_dialog(self, e):
        """显示关注贴吧弹窗"""
        forum_input = ft.TextField(
            hint_text="输入要关注的贴吧名称",
            border_radius=10,
            text_size=13,
            autofocus=True,
            on_submit=lambda ev: self.page.run_task(self._on_follow_forum, ev),
        )

        hint_text = ft.Text(
            "💡 支持批量关注，多个贴吧用逗号或换行分隔",
            size=11,
            color="onSurfaceVariant"
        )

        async def on_follow(ev):
            forum_input.disabled = True
            submit_btn.disabled = True
            submit_btn.text = "关注中..."
            self.page.update()

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
                result = await pm.follow_forums_bulk(fnames)

                # 关闭弹窗
                self.page.close(dialog)

                # 显示结果
                success_count = len(result["success"])
                failed_count = len(result["failed"])
                skipped_count = len(result["skipped"])

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

    async def _on_unfollow_forum(self, fname: str):
        """取消关注：所有账号取关该贴吧"""
        async def do_unfollow(e):
            try:
                self.page.close(dialog)
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                await pm.unfollow_forums_bulk([fname])
                self._show_snackbar(f"✅ 已取消关注 '{fname}'", "success")
                await self._refresh_matrix_stats()
                self.refresh_ui()
            except Exception as ex:
                self._show_snackbar(f"❌ 取消关注失败: {str(ex)}", "error")

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
        self.matrix_bulk_clear_target_btn.visible = has_sel
        self.matrix_bulk_tag_btn.visible = has_sel
        if has_sel:
            self.matrix_bulk_toggle_target_btn.text = f"批量切换火力 ({count})"
            self.matrix_bulk_follow_btn.text = f"批量补齐关注 ({count})"
            self.matrix_bulk_unfollow_btn.text = f"批量取消关注 ({count})"
            self.matrix_bulk_clear_target_btn.text = f"清理靶场 ({count})"
            self.matrix_bulk_tag_btn.text = f"批量修改标签 ({count})"
        self.page.update()

    async def _bulk_matrix_toggle_target(self):
        """批量切换 Target（已投放→移除，未投放→投放）"""
        if not self._matrix_selected_fnames: return
        fnames = list(self._matrix_selected_fnames)
        count = len(fnames)
        try:
            # 从 _matrix_stats 构建状态映射
            stats_map = {s['fname']: s for s in self._matrix_stats}
            target_fnames = set()
            for f in fnames:
                stat = stats_map.get(f, {})
                if stat.get('is_target'):
                    target_fnames.add(f)
            non_target_fnames = set(fnames) - target_fnames

            # 投放未投放的
            for f in non_target_fnames:
                await self.db.upsert_target_pools([f], "未分类")
            # 移除已投放的
            if target_fnames:
                await self.db.delete_target_pool_by_fnames(list(target_fnames))

            # 重新加载数据并刷新 UI
            await self._refresh_matrix_stats()
            self._show_snackbar(f"✅ 已切换 {count} 个贴吧的火力标记（投放 {len(non_target_fnames)}，移除 {len(target_fnames)}）", "success")
        except Exception as e:
            self._show_snackbar(f"❌ 批量操作失败: {str(e)}", "error")
        self._matrix_selected_fnames.clear()
        self.matrix_select_all_cb.value = False
        self._update_matrix_bulk_bar()
        self.refresh_ui()
        self.page.update()

    async def _bulk_matrix_complement_follow(self):
        """批量补齐关注"""
        if not self._matrix_selected_fnames: return
        fnames = list(self._matrix_selected_fnames)
        total_success = 0
        total_failed = 0
        try:
            for f in fnames:
                missing_accounts = await self.db.get_accounts_not_following_forum(f)
                if not missing_accounts:
                    continue
                missing_ids = [acc.id for acc in missing_accounts]
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                result = await pm.follow_forums_bulk([f], account_ids=missing_ids)
                total_success += len(result["success"])
                total_failed += len(result["failed"])
            self._show_snackbar(f"✅ 补齐关注完成: 成功 {total_success}, 失败 {total_failed}", "success")
        except Exception as e:
            self._show_snackbar(f"❌ 批量补齐失败: {str(e)}", "error")
        self._matrix_selected_fnames.clear()
        await self._refresh_matrix_stats()
        self._update_matrix_bulk_bar()
        self.refresh_ui()

    async def _bulk_matrix_unfollow(self):
        """批量取消关注"""
        if not self._matrix_selected_fnames: return
        fnames = list(self._matrix_selected_fnames)

        async def do_unfollow(e):
            try:
                self.page.close(dialog)
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                await pm.unfollow_forums_bulk(fnames)
                self._show_snackbar(f"✅ 已批量取消关注 {len(fnames)} 个贴吧", "success")
            except Exception as ex:
                self._show_snackbar(f"❌ 批量取关失败: {str(ex)}", "error")
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

    async def _bulk_matrix_clear_target(self):
        """从靶场池中移除选中的贴吧（不取消关注），同时清理Forum表残留"""
        if not self._matrix_selected_fnames: return
        fnames = list(self._matrix_selected_fnames)

        async def do_clear(e):
            try:
                self.page.close(dialog)
                # 1. 从靶场池移除
                removed = await self.db.delete_target_pool_by_fnames(fnames)
                # 2. 清理 Forum 表中残留的关注记录（0账号部署的贴吧）
                forum_removed = await self.db.delete_forum_memberships_globally(fnames)
                self._show_snackbar(f"✅ 已从靶场移除 {removed} 条，清理关注记录 {forum_removed} 条", "success")
            except Exception as ex:
                self._show_snackbar(f"❌ 清理靶场失败: {str(ex)}", "error")
            self._matrix_selected_fnames.clear()
            await self._refresh_matrix_stats()
            self._update_matrix_bulk_bar()
            self.refresh_ui()

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.REMOVE_CIRCLE_OUTLINED, color="error"), ft.Text("确认清理靶场？")]),
            content=ft.Text(f"确定要从靶场池中移除以下 {len(fnames)} 个贴吧吗？\n此操作不影响账号的关注状态，仅清理历史战绩数据。\n\n{', '.join(fnames[:10])}{'...' if len(fnames) > 10 else ''}"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("确认移除", icon=icons.REMOVE_CIRCLE_OUTLINED, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=do_clear),
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
        """构建战略贴吧列表项"""
        items = []
        search_lower = self._matrix_search_text.lower()
        
        for stat in self._matrix_stats:
            fname = stat['fname']
            if search_lower and search_lower not in fname.lower() and search_lower not in stat['post_group'].lower():
                continue
            # 封禁筛选：仅显示被封禁的贴吧
            if self._matrix_banned_filter and not stat.get('is_banned', False):
                continue
            # 被删筛选：仅显示有删帖记录的贴吧
            if self._matrix_deleted_filter and stat.get('deleted_count', 0) == 0:
                continue
                
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
                        # 操作按钮组
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
                            ft.IconButton(
                                icon=icons.LABEL_ROUNDED,
                                tooltip="修改分组/标签",
                                icon_color="primary",
                                on_click=lambda e, s=stat: self._show_tag_edit_dialog(s)
                            ),
                            ft.IconButton(
                                icon=icons.PERSON_ADD_ALT_1_ROUNDED,
                                tooltip="补齐关注（让未关注账号也关注）",
                                icon_color="primary",
                                on_click=lambda e, f=fname: self.page.run_task(self._on_complement_follow, f)
                            ),
                            ft.IconButton(
                                icon=icons.HEART_BROKEN,
                                tooltip="取消关注（所有账号取关该贴吧）",
                                icon_color="error",
                                on_click=lambda e, f=fname: self.page.run_task(self._on_unfollow_forum, f)
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
        for acc in self._accounts:
            # 状态过滤
            status = getattr(acc, "status", "unknown")
            if self._filter_status != "all" and status != self._filter_status:
                continue

            # 搜索过滤
            if search_lower:
                match = (
                    search_lower in (acc.name or "").lower() or
                    search_lower in (acc.user_name or "").lower() or
                    search_lower in str(acc.user_id)
                )
                if not match:
                    continue

            is_active = acc.id == self._active_id
            is_selected = acc.id in self._selected_ids
            
            # 状态灯
            status = getattr(acc, "status", "unknown")
            last_v = getattr(acc, "last_verified", None)
            
            status_color = COLORS.GREY_400
            if status == "active": status_color = COLORS.GREEN_ACCENT_400
            elif status == "expired": status_color = COLORS.ERROR
            elif status == "error": status_color = COLORS.AMBER
            elif status == "banned": status_color = COLORS.RED_ACCENT_400
            
            # 查找关联代理名称
            proxy_info = "直连"
            if acc.proxy_id:
                p = next((p for p in self._proxies if p.id == acc.proxy_id), None)
                if p: proxy_info = f"{p.protocol}://{p.host}"
            
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
                                        visible=is_active and status != "banned",
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
                                    ft.Text(f"UID: {acc.user_id}", color="onSurfaceVariant", size=11),
                                    ft.Container(width=10),
                                    ft.Icon(icons.PHONELINK_LOCK_ROUNDED, size=12, color="onSurfaceVariant"),
                                    ft.Text(f"标识: {getattr(acc, 'cuid', '')[:8]}...", color="onSurfaceVariant", size=11, tooltip=f"完整指纹: {getattr(acc, 'cuid', '')}"),
                                    ft.Container(width=10),
                                    ft.Icon(icons.LANGUAGE, size=12, color="onSurfaceVariant"),
                                    ft.Text(f"代理: {proxy_info}", color="onSurfaceVariant", size=11),
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
                                    icon=icons.EDIT_DOCUMENT,
                                    tooltip="编辑账号信息",
                                    icon_color="primary",
                                    on_click=lambda e, a=acc: self.page.run_task(self._show_edit_dialog, a)
                                ),
                                ft.IconButton(
                                    icon=icons.DELETE_OUTLINE,
                                    tooltip="删除账号",
                                    icon_color="error",
                                    on_click=lambda e, aid=acc.id: self.page.run_task(self._show_delete_confirm, aid)
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
            )
            items.append(card)
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
        e.control.update()

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
            ft.Text("发帖权重:", size=13),
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
        full_account = await self.db.get_accounts()
        full_account = next((a for a in full_account if a.id == account.id), None)
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
            ft.Text("发帖权重:", size=13),
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
        acc = await refresh_account(self.db, account_id)
        if acc:
            await self.load_data()
            if acc.status.startswith("invalid"):
                self._show_snackbar(f"账号 '{acc.name}' 已失效", "error")
            else:
                self._show_snackbar(f"账号 '{acc.user_name}' 刷新成功", "success")
        else:
            self._show_snackbar("刷新失败，账号不存在", "error")

    async def _show_delete_confirm(self, account_id: int):
        """显示删除确认框"""
        async def do_delete(e):
            await remove_account(self.db, account_id)
            await log_warn(f"账号凭据已被用户手动移除: ID {account_id}")
            self.page.close(dialog)
            await self.load_data()
            self._show_snackbar("账号已从本地移除", "info")

        dialog = ft.AlertDialog(
            title=ft.Text("确认移除账号?"),
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
                if self._filter_status != "all" and status != self._filter_status:
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
        self.bulk_bar.controls[2].visible = has_sel
        self.bulk_bar.controls[3].visible = has_sel
        self.bulk_bar.controls[2].text = f"批量验证 ({len(self._selected_ids)})"
        self.bulk_bar.controls[3].text = f"批量删除 ({len(self._selected_ids)})"
        self.page.update()

    async def _bulk_verify_accounts(self, e):
        if not self._selected_ids: return
        count = len(self._selected_ids)
        self._show_snackbar(f"开始批量验证 {count} 个账号...", "info")
        for aid in list(self._selected_ids):
            await refresh_account(self.db, aid)
        self._selected_ids.clear()
        self._update_bulk_bar()
        await self.load_data()
        self._show_snackbar(f"成功完成 {count} 个账号的批量效验", "success")

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

    async def _auto_calculate_weights(self, e):
        """一键自动计算所有账号的推荐权重"""
        from ...core.batch_post import AutoWeightCalculator
        
        self._show_snackbar("正在分析账号数据，计算智能权重...", "info")
        
        # 获取所有账号及其关联的贴吧
        accounts_with_forums = await self.db.get_accounts_with_forums()
        
        if not accounts_with_forums:
            self._show_snackbar("未找到账号数据", "error")
            return
        
        weight_updates = []
        results = []
        
        for account, forums in accounts_with_forums:
            recommended_weight, details = AutoWeightCalculator.calculate(account, forums)
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
        update_result = await self.db.batch_update_weights(weight_updates)
        
        # 构建结果展示
        result_lines = [f"✅ 权重智能计算完成！共分析 {len(results)} 个账号"]
        result_lines.append(f"更新成功: {update_result['updated']} | 失败: {update_result['failed']}")
        result_lines.append("")
        result_lines.append("📊 计算依据：")
        result_lines.append("• 平均贴吧等级 (30%)")
        result_lines.append("• 签到成功率 (25%)")
        result_lines.append("• 账号状态 (20%)")
        result_lines.append("• 代理绑定 (15%)")
        result_lines.append("• 验证时效 (10%)")
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
                ft.TextButton("确定", on_click=lambda _: self.page.close(dialog)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)
        
        # 刷新账号列表显示新权重
        await self.load_data()

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
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = COLORS.GREEN
        self.page.show_snack_bar(
            ft.SnackBar(
                content=ft.Text(message),
                bgcolor=with_opacity(0.8, color),
                behavior=ft.SnackBarBehavior.FLOATING,
            )
        )

