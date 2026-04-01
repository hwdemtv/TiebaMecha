"""Data crawling page with Cyber-Mecha aesthetic"""

import asyncio
import flet as ft
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..components import create_gradient_button
from ...core.crawl import crawl_threads, crawl_user, get_crawl_history


class CrawlPage:
    """数据爬取页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._history = []
        self._is_crawling = False

    async def load_data(self):
        """加载历史数据"""
        if self.db:
            self._history = await get_crawl_history(self.db)
            self.refresh_ui()

    def refresh_ui(self):
        if hasattr(self, "history_list"):
            self.history_list.controls = self._build_history_items()
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
                        ft.Text("数据探针 / DATA PROBE", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("获取贴吧全量数据或用户信息", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # 爬取配置卡片
        self.crawl_type = ft.SegmentedButton(
            selected={"threads"},
            allow_multiple_selection=False,
            segments=[
                ft.Segment(value="threads", label=ft.Text("贴吧帖子"), icon=ft.Icon(ft.icons.FORUM)),
                ft.Segment(value="user", label=ft.Text("用户信息"), icon=ft.Icon(ft.icons.PERSON_SEARCH)),
            ],
            on_change=self._on_type_change,
        )

        self.forum_name = ft.TextField(label="贴吧名称", hint_text="例如: 百度贴吧", expand=True, text_size=13)
        self.pages_count = ft.TextField(label="爬取页数", value="5", width=100, text_size=13, keyboard_type=ft.KeyboardType.NUMBER)
        
        self.user_target = ft.TextField(label="用户ID / Portrait", hint_text="输入数字ID或加密Portrait", expand=True, text_size=13, visible=False)
        self.with_posts = ft.Checkbox(label="同步爬取发帖记录", value=True, visible=False)

        self.progress_bar = ft.ProgressBar(value=0, visible=False, color="primary", bar_height=3)
        self.progress_text = ft.Text("", size=11, color="onSurfaceVariant")

        config_area = ft.Container(
            content=ft.Column([
                ft.Text("探测配置", size=14, weight=ft.FontWeight.BOLD),
                self.crawl_type,
                ft.Row([self.forum_name, self.pages_count], spacing=10),
                ft.Row([self.user_target, self.with_posts], spacing=10),
                ft.Row([
                    create_gradient_button("启动探测", icon=ft.icons.SENSORS, on_click=self._start_crawl),
                    ft.Column([self.progress_text, self.progress_bar], spacing=5, expand=True),
                ], spacing=20),
            ], spacing=15),
            padding=20,
            bgcolor=ft.colors.with_opacity(0.03, "onSurface"),
            border=ft.border.all(1, ft.colors.with_opacity(0.1, "onSurface")),
            border_radius=12,
        )

        # 历史记录
        self.history_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

        # 主布局
        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=ft.colors.with_opacity(0.1, "onSurface")),
                config_area,
                ft.Text("探测历史 / SCAN HISTORY", size=14, weight=ft.FontWeight.W_500),
                self.history_list,
            ], spacing=20),
            padding=20,
            expand=True,
        )

    def _on_type_change(self, e):
        is_threads = "threads" in self.crawl_type.selected
        self.forum_name.visible = is_threads
        self.pages_count.visible = is_threads
        self.user_target.visible = not is_threads
        self.with_posts.visible = not is_threads
        self.page.update()

    async def _start_crawl(self, e):
        if self._is_crawling: return
        
        is_threads = "threads" in self.crawl_type.selected
        target = self.forum_name.value.strip() if is_threads else self.user_target.value.strip()
        
        if not target:
            self._show_snackbar("检索目标不能为空", "error")
            return

        self._is_crawling = True
        self.progress_bar.visible = True
        self.progress_bar.value = None
        self.progress_text.value = "正在初始化探测引擎..."
        self.page.update()

        try:
            if is_threads:
                try:
                    pages = int(self.pages_count.value)
                except:
                    pages = 5
                
                async for p in crawl_threads(self.db, target, pages=pages):
                    self.progress_text.value = f"正在从 {target} 获取数据: {p.current} 条"
                    self.page.update()
            else:
                async for p in crawl_user(self.db, target, with_posts=self.with_posts.value):
                    self.progress_text.value = f"正在获取用户 {target} 数据: {p.message}"
                    self.page.update()
            
            self._show_snackbar("数据探测任务已完成", "success")
        except Exception as ex:
            self._show_snackbar(f"探测中断: {str(ex)}", "error")
        
        self._is_crawling = False
        self.progress_bar.visible = False
        self.progress_text.value = ""
        await self.load_data()

    def _build_history_items(self):
        items = []
        for h in self._history:
            status_icon = ft.icons.CHECK_CIRCLE if h.get("status") == "completed" else ft.icons.ERROR_OUTLINE
            status_color = "primary" if h.get("status") == "completed" else "error"
            
            card = ft.Container(
                content=ft.Row([
                    ft.Icon(status_icon, color=status_color, size=20),
                    ft.Column([
                        ft.Text(f"{h.get('type','').upper()} SCAN: {h.get('target','')}", size=13, weight=ft.FontWeight.W_500),
                        ft.Text(f"完成时间: {h.get('created_at','')[:16]} | 捕获数据: {h.get('count', 0)} 条", size=11, color="onSurfaceVariant"),
                    ], expand=True, spacing=2),
                    ft.IconButton(
                        icon=ft.icons.FILE_OPEN, 
                        icon_size=18, 
                        tooltip="查看扫描结果",
                        on_click=lambda e, p=h.get("result_path"): self._open_result(p)
                    ),
                ]),
                bgcolor=ft.colors.with_opacity(0.02, "onSurface"),
                border=ft.border.all(1, ft.colors.with_opacity(0.05, "onSurface")),
                border_radius=8,
                padding=10,
            )
            items.append(card)
        return items

    def _open_result(self, path):
        """安全打开结果文件(避免命令注入)"""
        if not path:
            return
        
        import subprocess
        import sys
        from pathlib import Path
        
        try:
            # 验证路径存在且为文件
            path_obj = Path(path)
            if not path_obj.exists():
                self._show_snackbar("结果文件不存在", "error")
                return
            
            # 使用 subprocess 安全打开文件(避免命令注入)
            if sys.platform == "win32":
                import os
                os.startfile(str(path_obj.absolute()))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path_obj.absolute())], check=False)
            else:
                subprocess.run(["xdg-open", str(path_obj.absolute())], check=False)
        except Exception as e:
            self._show_snackbar(f"无法打开结果文件: {str(e)}", "error")

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=ft.colors.with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
