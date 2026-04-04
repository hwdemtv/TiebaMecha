"""Data crawling page with Cyber-Mecha aesthetic"""

import asyncio
import flet as ft
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..components import create_gradient_button, icons
from ..utils import with_opacity
from ...core.crawl import crawl_threads, crawl_user, get_crawl_history, import_threads_to_materials


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
                        icon=icons.ARROW_BACK_IOS_NEW,
                        icon_size=16,
                        on_click=lambda e: self._navigate("dashboard"),
                        style=ft.ButtonStyle(
                            color=ft.colors.PRIMARY,
                            bgcolor={"": with_opacity(0.1, ft.colors.PRIMARY)},
                        ),
                    ),
                    padding=5,
                ),
                ft.Column(
                    controls=[
                        ft.Text("数据探针 / DATA PROBE", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("获取贴吧全量数据或用户信息，支持导入物料库", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # ========== 左侧：探测配置 ==========
        self.crawl_type = ft.SegmentedButton(
            selected={"threads"},
            allow_multiple_selection=False,
            segments=[
                ft.Segment(value="threads", label=ft.Text("贴吧帖子"), icon=ft.Icon(icons.FORUM)),
                ft.Segment(value="user", label=ft.Text("用户信息"), icon=ft.Icon(icons.PERSON_SEARCH)),
            ],
            on_change=self._on_type_change,
        )

        self.forum_name = ft.TextField(label="贴吧名称", hint_text="例如: 百度贴吧", expand=True, text_size=13)
        self.pages_count = ft.TextField(label="爬取页数", value="5", width=80, text_size=13, keyboard_type=ft.KeyboardType.NUMBER)

        self.user_target = ft.TextField(label="用户ID / Portrait", hint_text="输入数字ID或加密Portrait", expand=True, text_size=13, visible=False)
        self.with_posts = ft.Checkbox(label="同步爬取发帖记录", value=True, visible=False)

        self.progress_bar = ft.ProgressBar(value=0, visible=False, color="primary", bar_height=4)
        self.progress_text = ft.Text("", size=12, color="onSurfaceVariant")
        self.retry_indicator = ft.Text("", size=10, color="orange", visible=False)

        # 左侧配置面板
        left_panel = ft.Container(
            content=ft.Column([
                # 配置标题
                ft.Row([
                    ft.Icon(icons.SETTINGS_SUGGEST, color="primary", size=18),
                    ft.Text("探测配置", size=14, weight=ft.FontWeight.BOLD),
                ], spacing=8),
                ft.Divider(height=5),

                # 爬取类型选择
                self.crawl_type,

                # 贴吧帖子配置
                ft.Row([
                    ft.Icon(icons.FORUM, size=16, color="onSurfaceVariant"),
                    self.forum_name,
                    self.pages_count,
                ], spacing=8),

                # 用户信息配置
                ft.Row([
                    ft.Icon(icons.PERSON, size=16, color="onSurfaceVariant", visible=False),
                    self.user_target,
                ], spacing=8),
                self.with_posts,

                ft.Divider(height=10),

                # 进度显示
                ft.Column([
                    self.progress_text,
                    self.retry_indicator,
                    self.progress_bar,
                ], spacing=3),

                # 启动按钮
                ft.Container(
                    content=create_gradient_button("启动探测", icon=icons.SENSORS, on_click=self._start_crawl),
                    alignment=ft.alignment.center,
                ),
            ], spacing=12),
            padding=15,
            bgcolor=with_opacity(0.03, "onSurface"),
            border=ft.border.all(1, with_opacity(0.1, "onSurface")),
            border_radius=12,
            width=280,
        )

        # ========== 右侧：探测历史 ==========
        self.history_list = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

        # 右侧历史面板
        right_panel = ft.Container(
            content=ft.Column([
                # 历史标题栏
                ft.Row([
                    ft.Icon(icons.HISTORY, color="primary", size=18),
                    ft.Text("探测历史", size=14, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.TextButton(
                        text="清理",
                        icon=icons.DELETE_SWEEP,
                        on_click=self._clear_old_records,
                        style=ft.ButtonStyle(color=ft.colors.ERROR),
                    ),
                ], spacing=8),
                ft.Divider(height=5),
                self.history_list,
            ], spacing=8),
            padding=15,
            bgcolor=with_opacity(0.02, "onSurface"),
            border=ft.border.all(1, with_opacity(0.08, "onSurface")),
            border_radius=12,
            expand=True,
        )

        # ========== 主布局：左右结构 ==========
        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                # 左右分栏
                ft.Row([
                    left_panel,
                    right_panel,
                ], spacing=15, expand=True, vertical_alignment=ft.CrossAxisAlignment.START),
            ], spacing=15),
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
        self.retry_indicator.visible = False
        self.page.update()

        try:
            if is_threads:
                try:
                    pages = int(self.pages_count.value)
                except:
                    pages = 5

                async for p in crawl_threads(self.db, target, pages=pages):
                    self.progress_text.value = f"📂 {target} | {p.current} 条帖子"
                    if p.message:
                        self.progress_text.value += f" | {p.message}"

                    # 显示重试信息
                    if p.retries > 0:
                        self.retry_indicator.visible = True
                        self.retry_indicator.value = f"⚠️ 已重试 {p.retries} 次"
                    else:
                        self.retry_indicator.visible = False

                    # 更新进度条
                    if p.total > 0:
                        self.progress_bar.value = p.current / p.total

                    self.page.update()
            else:
                async for p in crawl_user(self.db, target, with_posts=self.with_posts.value):
                    self.progress_text.value = f"👤 用户数据 | {p.message}"
                    self.page.update()

            self._show_snackbar("数据探测任务已完成", "success")
        except Exception as ex:
            self._show_snackbar(f"探测中断: {str(ex)}", "error")

        self._is_crawling = False
        self.progress_bar.visible = False
        self.progress_text.value = ""
        self.retry_indicator.visible = False
        await self.load_data()

    def _build_history_items(self):
        items = []
        for h in self._history:
            # 状态图标和颜色
            status = h.get("status", "")
            if status == "completed":
                status_icon = icons.CHECK_CIRCLE
                status_color = "green"
            elif status == "partial":
                status_icon = icons.WARNING_AMBER
                status_color = "orange"
            else:
                status_icon = icons.ERROR_OUTLINE
                status_color = "error"

            # 类型图标
            type_icon = icons.FORUM if h.get("type") == "threads" else icons.PERSON

            # 操作按钮
            action_buttons = []

            # 查看结果按钮
            if h.get("result_path"):
                action_buttons.append(
                    ft.IconButton(
                        icon=icons.FILE_OPEN,
                        icon_size=18,
                        icon_color="primary",
                        tooltip="查看扫描结果",
                        on_click=lambda e, p=h.get("result_path"): self._open_result(p)
                    )
                )

                # 导入物料库按钮（仅帖子类型）
                if h.get("type") == "threads":
                    action_buttons.append(
                        ft.IconButton(
                            icon=icons.ADD_TO_PHOTOS,
                            icon_size=18,
                            icon_color="teal",
                            tooltip="导入到物料库",
                            on_click=lambda e, p=h.get("result_path"): self.page.run_task(self._import_to_materials, p)
                        )
                    )

            card = ft.Container(
                content=ft.Row([
                    ft.Icon(type_icon, color="onSurfaceVariant", size=18),
                    ft.Icon(status_icon, color=status_color, size=18),
                    ft.Column([
                        ft.Text(
                            f"{h.get('type','').upper()}: {h.get('target','')}",
                            size=13,
                            weight=ft.FontWeight.W_500
                        ),
                        ft.Text(
                            f"{h.get('created_at','')[:16] if h.get('created_at') else '-'} | {h.get('count', 0)} 条数据",
                            size=11,
                            color="onSurfaceVariant"
                        ),
                    ], expand=True, spacing=2),
                    *action_buttons,
                    # 删除按钮
                    ft.IconButton(
                        icon=icons.DELETE_OUTLINE,
                        icon_size=18,
                        icon_color="error",
                        tooltip="删除此记录",
                        on_click=lambda e, tid=h.get("id"): self.page.run_task(self._delete_crawl_task, tid)
                    ),
                ]),
                bgcolor=with_opacity(0.02, "onSurface"),
                border=ft.border.all(1, with_opacity(0.05, "onSurface")),
                border_radius=8,
                padding=10,
            )
            items.append(card)
        return items

    async def _delete_crawl_task(self, task_id: int):
        """删除单条爬取记录"""
        if not self.db:
            return

        success = await self.db.delete_crawl_task(task_id)
        if success:
            self._show_snackbar("记录已删除", "success")
            await self.load_data()
        else:
            self._show_snackbar("删除失败", "error")

    async def _clear_old_records(self, e):
        """清理旧记录"""
        if not self.db:
            return

        async def do_clear(days: int):
            tasks, files = await self.db.clear_old_crawl_tasks(days=days)
            self.page.close(confirm_dialog)
            msg = f"已清理 {tasks} 条记录，{files} 个文件" if days > 0 else f"已清空全部 {tasks} 条记录"
            self._show_snackbar(msg, "success")
            await self.load_data()

        confirm_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.DELETE_SWEEP, color="error"), ft.Text("数据探针清理")]),
            content=ft.Text("请选择清理范围（此操作不可撤销）："),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(confirm_dialog)),
                ft.OutlinedButton(
                    "清理 30 天前", 
                    icon=icons.HISTORY,
                    on_click=lambda _: self.page.run_task(do_clear, 30)
                ),
                ft.FilledButton(
                    "全部清空", 
                    icon=icons.DELETE_FOREVER,
                    style=ft.ButtonStyle(bgcolor=ft.colors.ERROR, color=ft.colors.ON_ERROR),
                    on_click=lambda _: self.page.run_task(do_clear, 0)
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(confirm_dialog)

    async def _import_to_materials(self, result_path: str):
        """打开筛选预览对话框，让用户选择要导入的帖子"""
        from ...core.crawl import load_crawl_result

        # 加载爬取结果
        threads = await load_crawl_result(result_path)
        if not threads:
            self._show_snackbar("无法加载爬取结果", "error")
            return

        # 筛选状态
        selected_indices = set(range(len(threads)))  # 默认全选
        preview_checkboxes = []  # 复选框引用

        # 构建预览列表
        def build_preview_items():
            items = []
            for i, t in enumerate(threads):
                title = t.get("title", "")[:30] or f"无标题 (tid:{t.get('tid')})"
                text_preview = t.get("text", "")[:50].replace("\n", " ")
                agree = t.get("agree", 0)
                reply = t.get("reply_num", 0)

                checkbox = ft.Checkbox(
                    value=i in selected_indices,
                    data=i,
                    on_change=lambda e, idx=i: toggle_select(idx, e.control.value),
                )
                preview_checkboxes.append(checkbox)

                item = ft.Container(
                    content=ft.Row([
                        checkbox,
                        ft.Column([
                            ft.Text(f"📝 {title}{'...' if len(t.get('title', '')) > 30 else ''}",
                                    size=12, weight=ft.FontWeight.W_500),
                            ft.Text(f"{text_preview}{'...' if len(t.get('text', '')) > 50 else ''}",
                                    size=10, color="onSurfaceVariant"),
                        ], spacing=2, expand=True),
                        ft.Column([
                            ft.Text(f"👍 {agree}", size=10, color="primary"),
                            ft.Text(f"💬 {reply}", size=10, color="onSurfaceVariant"),
                        ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END),
                    ], spacing=8),
                    bgcolor=with_opacity(0.02, "onSurface") if i % 2 == 0 else "transparent",
                    border_radius=5,
                    padding=ft.padding.symmetric(horizontal=8, vertical=6),
                )
                items.append(item)
            return items

        def toggle_select(idx: int, value: bool):
            if value:
                selected_indices.add(idx)
            else:
                selected_indices.discard(idx)
            update_count_text()

        def toggle_all(value: bool):
            selected_indices.clear()
            if value:
                selected_indices.update(range(len(threads)))
            for cb in preview_checkboxes:
                cb.value = value
            update_count_text()
            preview_list.update()

        count_text = ft.Text(f"已选择 {len(selected_indices)}/{len(threads)} 条", size=12, color="primary")

        def update_count_text():
            count_text.value = f"已选择 {len(selected_indices)}/{len(threads)} 条"

        # 搜索过滤
        search_field = ft.TextField(
            label="搜索标题/内容",
            hint_text="输入关键词筛选...",
            dense=True,
            text_size=12,
            on_change=lambda e: filter_items(e.control.value),
        )

        preview_container = ft.Column(
            controls=build_preview_items(),
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
            height=300,
        )
        preview_list = preview_container

        def filter_items(keyword: str):
            keyword = keyword.lower()
            filtered_items = []
            preview_checkboxes.clear()

            for i, t in enumerate(threads):
                title = t.get("title", "")
                text = t.get("text", "")
                if keyword and keyword not in title.lower() and keyword not in text.lower():
                    continue

                title_display = title[:30] or f"无标题 (tid:{t.get('tid')})"
                text_preview = text[:50].replace("\n", " ")
                agree = t.get("agree", 0)
                reply = t.get("reply_num", 0)

                checkbox = ft.Checkbox(
                    value=i in selected_indices,
                    data=i,
                    on_change=lambda e, idx=i: toggle_select(idx, e.control.value),
                )
                preview_checkboxes.append(checkbox)

                item = ft.Container(
                    content=ft.Row([
                        checkbox,
                        ft.Column([
                            ft.Text(f"📝 {title_display}{'...' if len(title) > 30 else ''}",
                                    size=12, weight=ft.FontWeight.W_500),
                            ft.Text(f"{text_preview}{'...' if len(text) > 50 else ''}",
                                    size=10, color="onSurfaceVariant"),
                        ], spacing=2, expand=True),
                        ft.Column([
                            ft.Text(f"👍 {agree}", size=10, color="primary"),
                            ft.Text(f"💬 {reply}", size=10, color="onSurfaceVariant"),
                        ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END),
                    ], spacing=8),
                    bgcolor=with_opacity(0.02, "onSurface") if i % 2 == 0 else "transparent",
                    border_radius=5,
                    padding=ft.padding.symmetric(horizontal=8, vertical=6),
                )
                filtered_items.append(item)

            preview_container.controls = filtered_items
            preview_container.update()

        # 执行导入
        async def do_import(_):
            if not selected_indices:
                self._show_snackbar("请至少选择一条帖子", "warning")
                return

            # 收集选中的帖子
            pairs = []
            for i in selected_indices:
                t = threads[i]
                title = t.get("title", "") or f"帖子_{t.get('tid', 'unknown')}"
                text = t.get("text", "")
                content = f"{title}\n\n{text}" if text else title
                pairs.append((title, content.strip()))

            # 批量导入
            added = await self.db.add_materials_bulk(pairs)
            self.page.close(dialog)
            self._show_snackbar(f"成功导入 {added} 条物料（跳过 {len(pairs) - added} 条重复）", "success")

        # 预览对话框
        dialog = ft.AlertDialog(
            title=ft.Row([
                ft.Icon(icons.FILTER_LIST, color="primary"),
                ft.Text("筛选导入物料", size=16),
            ], spacing=10),
            content=ft.Column([
                ft.Row([
                    ft.Text(f"共 {len(threads)} 条帖子", size=11, color="onSurfaceVariant"),
                    ft.Container(expand=True),
                    ft.TextButton("全选", on_click=lambda _: toggle_all(True)),
                    ft.TextButton("全不选", on_click=lambda _: toggle_all(False)),
                ], spacing=5),
                search_field,
                preview_container,
                ft.Divider(height=10),
                count_text,
            ], spacing=10, tight=True, width=550),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton(
                    "导入选中项",
                    icon=icons.ADD_TO_PHOTOS,
                    on_click=do_import,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

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
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
