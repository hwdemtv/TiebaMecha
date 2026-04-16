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
        self._active_tab_index = 0

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
        self._matrix_stats = await self.db.get_forum_matrix_stats()
        
        self.refresh_ui()

    def refresh_ui(self):
        """刷新 UI"""
        if self._active_tab_index == 0:
            if hasattr(self, "account_list"):
                self.account_list.controls = self._build_account_items()
                # 统计封禁损耗
                banned_count = sum(1 for a in getattr(self, "_accounts", []) if getattr(a, "status", "") == "banned")
                if banned_count > 0:
                    self.account_stats_info.text = f"🚨 战损报警：检测到 {banned_count} 个已封禁账号"
                    self.account_stats_info.visible = True
                else:
                    self.account_stats_info.visible = False
                self.page.update()
        else:
            if hasattr(self, "matrix_list"):
                self.matrix_list.controls = self._build_matrix_items()
                self._update_matrix_header()
                self.page.update()

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
            ft.TextButton("批量验证", icon=icons.VERIFIED_USER, on_click=lambda e: self.page.run_task(self._bulk_verify_accounts, e), visible=False),
            ft.TextButton("一键智能权重", icon=icons.AUTO_AWESOME, on_click=lambda e: self.page.run_task(self._auto_calculate_weights, e), tooltip="根据账号等级/签到成功率/状态等自动计算推荐权重"),
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

        self.matrix_header_info = ft.Text("战略贴吧总数: 0 | 矩阵覆盖率: 0%", size=12, color="onSurfaceVariant")

        # 列表容器
        self.matrix_list = ft.ListView(
            expand=True,
            spacing=10,
            padding=10,
        )

        return ft.Column(
            controls=[
                ft.Row([search_field, sync_btn], spacing=10),
                self.matrix_header_info,
                ft.Divider(color=with_opacity(0.1, "primary"), height=1),
                ft.Container(
                    content=self.matrix_list,
                    expand=True,
                ),
            ],
            spacing=10,
        )

    def _on_tab_change(self, e):
        self._active_tab_index = e.control.selected_index
        self.refresh_ui()

    def _on_matrix_search_change(self, e):
        self._matrix_search_text = e.control.value
        self.refresh_ui()

    async def _on_sync_matrix(self, e):
        """全域同步关注列表"""
        from ...core.sign import sync_forums_to_db
        
        self._show_snackbar("🚀 指令下达：正在启动全域矩阵关注同步...", "info")
        
        try:
            # 标记按钮状态或显示进度 (可选)
            added = await sync_forums_to_db(self.db)
            
            # 重新加载统计数据
            self._matrix_stats = await self.db.get_forum_matrix_stats()
            self.refresh_ui()
            
            self._show_snackbar(f"✅ 全域同步完成！矩阵新增 {added} 个战略支点", "success")
        except Exception as ex:
            self._show_snackbar(f"❌ 同步失败: {str(ex)}", "error")

    async def _on_toggle_target(self, fname: str, is_target: bool):
        """一键标记/取消战略目标"""
        try:
            if is_target:
                await self.db.upsert_target_pools([fname], "未分类")
                self._show_snackbar(f"🎯 已将 '{fname}' 锁定为火力打击目标", "success")
            else:
                await self.db.delete_target_pool_by_fnames([fname])
                self._show_snackbar(f"🏳️ 已从打击名单中移除 '{fname}'", "info")
            
            # 重新加载数据
            self._matrix_stats = await self.db.get_forum_matrix_stats()
            self.refresh_ui()
        except Exception as e:
            self._show_snackbar(f"操作失败: {str(e)}", "error")

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
            self._matrix_stats = await self.db.get_forum_matrix_stats()
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
        percent = (covered / total * 100) if total > 0 else 0
        self.matrix_header_info.value = f"战略资源: {total} 个贴吧 | 矩阵实存火力涵盖: {covered} 个 (覆盖率 {percent:.1f}%)"

    def _build_matrix_items(self) -> list[ft.Control]:
        """构建战略贴吧列表项"""
        items = []
        search_lower = self._matrix_search_text.lower()
        
        for stat in self._matrix_stats:
            fname = stat['fname']
            if search_lower and search_lower not in fname.lower() and search_lower not in stat['post_group'].lower():
                continue
                
            acc_count = stat['account_count']
            groups = stat['post_group']
            is_target = stat['is_target']
            
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

            item = ft.Container(
                content=ft.Row(
                    controls=[
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
                        # 操作菜单
                        ft.PopupMenuButton(
                            items=[
                                ft.PopupMenuItem(
                                    text="投放火力 (设为 Target)",
                                    icon=icons.GPS_FIXED_ROUNDED,
                                    on_click=lambda e, f=fname: self.page.run_task(self._on_toggle_target, f, True)
                                ),
                                ft.PopupMenuItem(
                                    text="移除火力 (取消 Target)",
                                    icon=icons.GPS_OFF_ROUNDED,
                                    on_click=lambda e, f=fname: self.page.run_task(self._on_toggle_target, f, False)
                                ),
                                ft.PopupMenuItem(
                                    text="修改分组/标签",
                                    icon=icons.LABEL_ROUNDED,
                                    on_click=lambda e, s=stat: self._show_tag_edit_dialog(s)
                                ),
                                ft.PopupMenuItem(
                                    text="全环境同步关注",
                                    icon=icons.PERSON_ADD_ALT_1_ROUNDED,
                                    # on_click=...
                                ),
                            ],
                            icon=icons.MORE_VERT_ROUNDED,
                        )
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
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
        self.bulk_bar.controls[1].visible = has_sel
        self.bulk_bar.controls[2].visible = has_sel
        self.bulk_bar.controls[1].text = f"批量验证 ({len(self._selected_ids)})"
        self.bulk_bar.controls[2].text = f"批量删除 ({len(self._selected_ids)})"
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

