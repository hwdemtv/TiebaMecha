"""Dashboard page with Cyber-Mecha aesthetic"""

import flet as ft
from typing import List, Optional

from ..components import (
    CoreButtonWithLabel,
    HUDPanel,
    TileGrid,
    StreamList,
    create_gradient_button,
    DualHUD,
)
from ..utils import with_opacity
from ...core.sign import get_sign_stats
from ...core.logger import get_log_queue
from ..components.icons import (
    ACCOUNT_CIRCLE, VPN_LOCK, FORUM, SETTINGS, 
    EDIT_NOTE_ROUNDED, OPEN_IN_NEW, AUTO_AWESOME
)


class DashboardPage:
    """仪表盘页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._stats = {"total": 0, "success": 0, "failure": 0}
        self._sys_stats = {"accounts": 0, "active_proxies": 0, "pending_batch": 0}
        self._recent_forums = []
        self._ai_api_key_set = False  # AI API Key 是否已配置

    async def load_data(self):
        """同步数据库数据"""
        if not self.db: return
        
        # 加载签到统计
        self._stats = await get_sign_stats(self.db)
        
        # 加载全系统统计
        accounts = await self.db.get_accounts()
        self._sys_stats["accounts"] = len(accounts)
        
        proxies = await self.db.get_active_proxies()
        self._sys_stats["active_proxies"] = len(proxies)
        
        batch_tasks = await self.db.get_pending_batch_tasks()
        self._sys_stats["pending_batch"] = len(batch_tasks)
        
        # 加载最近贴吧
        account = await self.db.get_active_account()
        if account:
            forums = await self.db.get_forums(account.id)
            self._recent_forums = forums[:10]
        
        # 检查 AI API Key 是否已配置
        ai_key = await self.db.get_setting("ai_api_key", "")
        self._ai_api_key_set = bool(ai_key and ai_key.strip())
            
        # 页面挂载时主动补齐过去的历史
        from ...core.logger import get_recent_logs
        recent_history = await get_recent_logs(50)
        if hasattr(self, "log_list") and hasattr(self, "_add_single_log_ui"):
            self.log_list.controls.clear()
            for log_entry in recent_history:
                self._add_single_log_ui(log_entry)
        
        # 开启日志监听任务
        if not hasattr(self, "_log_task_running") or not self._log_task_running:
            self._log_task_running = True
            self.page.run_task(self._listen_logs)
            
        self.refresh_ui()

    def _add_single_log_ui(self, log_entry):
        color = "primary"
        if log_entry["level"] == "ERROR": color = "error"
        elif log_entry["level"] == "WARN": color = "secondary"
        
        log_row = ft.Row([
            ft.Text(f"[{log_entry['time']}]", size=10, color="onSurfaceVariant", font_family="Consolas"),
            ft.Container(
                content=ft.Text(log_entry["level"], size=9, weight=ft.FontWeight.BOLD, color="black"),
                bgcolor=color,
                padding=ft.padding.symmetric(horizontal=4, vertical=1),
                border_radius=3,
            ),
            ft.Text(log_entry["message"], size=11, color="onSurface", expand=True),
        ], spacing=10)
        
        self.log_list.controls.insert(0, log_row)
        if len(self.log_list.controls) > 30:
            self.log_list.controls.pop()

    async def _listen_logs(self):
        """监听日志队列并更新 UI"""
        queue = get_log_queue()
        while True:
            log_entry = await queue.get()
            if hasattr(self, "log_list"):
                self._add_single_log_ui(log_entry)
                self.page.update()
            queue.task_done()

    def refresh_ui(self):
        if hasattr(self, "hud"):
            self.hud.left_value = str(self._stats["success"])
            self.hud.right_value = str(self._stats.get("failure", 0))
        
        if hasattr(self, "sys_hud"):
            self.sys_hud.left_value = str(self._sys_stats["accounts"])
            self.sys_hud.right_value = str(self._sys_stats["pending_batch"])
            
        if hasattr(self, "forum_list"):
            self.forum_list.items = [
                {
                    "title": f.fname,
                    "subtitle": f"连续签到 {f.sign_count} 天",
                    "icon": ft.icons.VERIFIED_ROUNDED if f.is_sign_today else ft.icons.RADIO_BUTTON_UNCHECKED,
                }
                for f in self._recent_forums
            ]
        
        # 更新 AI 状态横幅
        if hasattr(self, "ai_status_icon"):
            ai_configured = bool(self._ai_api_key_set)
            self.ai_status_icon.name = ft.icons.MEMORY_ROUNDED if ai_configured else ft.icons.WARNING_AMBER_ROUNDED
            self.ai_status_icon.color = "#4CAF50" if ai_configured else "#FF6B35"
            self.ai_status_text_val.value = "ONLINE · API Key 已配置" if ai_configured else "OFFLINE · 请前往设置配置 API Key"
            self.ai_status_text_val.color = "#4CAF50" if ai_configured else "#FF6B35"
            self.ai_status_container.bgcolor = with_opacity(0.1, "#4CAF50" if ai_configured else "#FF6B35")
            self.ai_banner_outer.bgcolor = with_opacity(0.05, "#4CAF50" if ai_configured else "#FF6B35")
            self.ai_banner_outer.border = ft.border.all(1, with_opacity(0.2, "#4CAF50" if ai_configured else "#FF6B35"))
            self.ai_nav_btn.visible = not ai_configured
            
        self.page.update()

    def build(self) -> ft.Control:
        # --- 初始化组件 (避免在控件列表中直接赋值引起语法错误) ---
        self.log_list = ft.ListView(
            expand=True,
            spacing=5,
            padding=10,
        )

        # --- 标题区域 ---
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.Icon(ft.icons.TERMINAL_ROUNDED, color="primary", size=28),
                    padding=10,
                    bgcolor=with_opacity(0.1, "primary"),
                    border_radius=10,
                ),
                ft.Column(
                    controls=[
                        ft.Text("SYSTEM OVERVIEW", size=24, weight=ft.FontWeight.BOLD, color="primary", font_family="Consolas"),
                        ft.Text("TIEBA MECHA CONTROL PANEL v1.1.0", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
                ft.Container(expand=True),
                ft.Row([
                    ft.Icon(ft.icons.SIGNAL_CELLULAR_ALT, size=16, color="primary"),
                    ft.Text("ONLINE", size=11, weight=ft.FontWeight.BOLD, color="primary"),
                ], spacing=5),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # --- HUD 监控区 (分为左右两个部分) ---
        self.hud = DualHUD(
            left_title="SUCCESS / 签到成功",
            left_value=str(self._stats["success"]),
            left_icon=ft.icons.CHECK_CIRCLE_ROUNDED,
            right_title="FAILED / 签到失败",
            right_value=str(self._stats.get("failure", 0)),
            right_icon=ft.icons.SYNC_PROBLEM_ROUNDED,
        )
        
        self.sys_hud = DualHUD(
            left_title="UNITS / 账号矩阵",
            left_value=str(self._sys_stats["accounts"]),
            left_icon=ft.icons.AUTO_AWESOME_MOTION_ROUNDED,
            right_title="JOBS / 待执行批量",
            right_value=str(self._sys_stats["pending_batch"]),
            right_icon=ft.icons.SCHEDULE_ROUNDED,
        )

        hud_section = ft.Row([
            ft.Container(content=self.hud, expand=True),
            ft.Container(content=self.sys_hud, expand=True),
        ], spacing=20)

        # --- AI 神经元状态横幅 ---
        ai_configured = bool(self._ai_api_key_set)
        ai_status_color = "#4CAF50" if ai_configured else "#FF6B35"
        ai_status_text = "ONLINE · API Key 已配置" if ai_configured else "OFFLINE · 请前往设置配置 API Key"
        ai_status_icon_name = ft.icons.MEMORY_ROUNDED if ai_configured else ft.icons.WARNING_AMBER_ROUNDED

        self.ai_status_icon = ft.Icon(ai_status_icon_name, color=ai_status_color, size=18)
        self.ai_status_text_val = ft.Text(ai_status_text, size=12, weight=ft.FontWeight.BOLD, color=ai_status_color)
        self.ai_status_container = ft.Container(
            content=self.ai_status_text_val,
            padding=ft.padding.symmetric(horizontal=8, vertical=3),
            bgcolor=with_opacity(0.1, ai_status_color),
            border_radius=5,
        )
        self.ai_nav_btn = ft.TextButton(
            "前往配置 →",
            style=ft.ButtonStyle(color="primary"),
            on_click=lambda _: self._navigate("settings"),
            visible=not ai_configured,
        )

        self.ai_banner_outer = ft.Container(
            content=ft.Row([
                self.ai_status_icon,
                ft.Text("AI 神经元 / NEURAL CORE:", size=12, color="onSurfaceVariant", weight=ft.FontWeight.W_500),
                self.ai_status_container,
                ft.Container(expand=True),
                self.ai_nav_btn,
                ft.TextButton(
                    "注册智谱 AI",
                    icon=ACCOUNT_CIRCLE,
                    style=ft.ButtonStyle(color="secondary"),
                    on_click=lambda _: self.page.launch_url("https://www.bigmodel.cn/invite?icode=SQx7axFjnOGhwGsnmgUpHGczbXFgPRGIalpycrEwJ28%3D"),
                ),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=with_opacity(0.05, ai_status_color),
            border=ft.border.all(1, with_opacity(0.2, ai_status_color)),
            border_radius=8,
            padding=ft.padding.symmetric(horizontal=15, vertical=8),
        )
        ai_banner = self.ai_banner_outer


        tiles = TileGrid(
            columns=2,
            tiles=[
                {
                    "title": "账号列表",
                    "icon": ft.icons.GROUP,
                    "subtitle": "MULTI-ACCOUNT",
                    "tooltip": "管理矩阵账号资产，配置凭证与会话属性。",
                    "on_click": lambda e: self._navigate("accounts"),
                },
                {
                    "title": "全域签到",
                    "icon": ft.icons.FLASH_ON_ROUNDED,
                    "subtitle": "GLOBAL SIGN",
                    "tooltip": "执行全贴吧自动化签到，维持账号稳健度。",
                    "on_click": lambda e: self._navigate("sign"),
                },
                {
                    "title": "贴子管理",
                    "icon": EDIT_NOTE_ROUNDED,
                    "subtitle": "CONTENT OPS",
                    "tooltip": "管理云端贴子、历史发布的引流软文等物料储备。",
                    "on_click": lambda e: self._navigate("posts"),
                },
                {
                    "title": "数据爬取",
                    "icon": ft.icons.RADAR,
                    "subtitle": "DATA PROBE",
                    "tooltip": "采集目标贴吧数据，探测竞品或提取目标用户。",
                    "on_click": lambda e: self._navigate("crawl"),
                },
            ],
        )

        # --- 中央控制按钮 ---
        self.core_btn = CoreButtonWithLabel(
            label="开始同步全域签到",
            icon=ft.icons.POWER_SETTINGS_NEW_ROUNDED,
            on_click=lambda e: self._navigate("sign"),
            size=100,
        )

        # --- 实时动态列表 ---
        self.forum_list = StreamList(
            items=[
                {
                    "title": f.fname,
                    "subtitle": f"连续签到 {f.sign_count} 天",
                    "icon": ft.icons.VERIFIED_ROUNDED if f.is_sign_today else ft.icons.RADIO_BUTTON_UNCHECKED,
                }
                for f in self._recent_forums
            ],
            on_item_click=None,
        )

        # --- 主框架布局 ---
        return ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Divider(height=20, color="transparent"),
                    hud_section,
                    ft.Divider(height=10, color="transparent"),
                    # AI 神经元状态横幅
                    ai_banner,
                    ft.Divider(height=10, color="transparent"),
                    
                    # 第 1.5 层：关键状态条
                    ft.Row([
                        ft.Icon(ft.icons.NETWORK_CHECK_ROUNDED, size=14, color="secondary"),
                        ft.Text(f"当前活跃代理节点: {self._sys_stats['active_proxies']} UNIT(S)", size=12, weight=ft.FontWeight.W_500),
                        ft.Container(expand=True),
                        ft.Text("SYSTEM CLOCK: ACTIVE", size=10, font_family="Consolas"),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    
                    # 中间层：功能磁贴 + 大按钮
                    ft.Row(
                        controls=[
                            ft.Column([
                                ft.Text("模块索引 / MODULE INDEX", size=14, weight=ft.FontWeight.W_500, color="primary"),
                                ft.Container(content=tiles, padding=5),
                            ], expand=3, spacing=15),
                            
                            ft.Column([
                                ft.Text("全局指令 / GLOBAL COMMAND", size=14, weight=ft.FontWeight.W_500, color="secondary", text_align=ft.TextAlign.CENTER),
                                self.core_btn,
                            ], expand=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=15),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    
                    ft.Container(height=30),
                    
                    # 底层：最近动态
                    ft.Text("实时状态流 / SYSTEM STREAM", size=14, weight=ft.FontWeight.W_500, color="primary"),
                    ft.Container(
                        content=self.log_list,
                        height=200,
                        border=ft.border.all(1, with_opacity(0.1, "#00BFA5")),
                        border_radius=10,
                        bgcolor=with_opacity(0.01, "#F2F5F9"),
                    ),
                ],
                spacing=0,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=30,
            expand=True,
        )

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)
