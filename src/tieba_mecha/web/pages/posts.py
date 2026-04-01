"""Posts management page with Cyber-Mecha aesthetic and AI SEO integration"""

import asyncio
import flet as ft
from typing import List, Set, Optional

from ..components import create_gradient_button
from ...core.post import (
    get_threads, 
    add_thread, 
    add_post, 
    delete_thread, 
    delete_threads, 
    set_good, 
    set_top, 
    search_threads
)
from ...core.ai_optimizer import AIOptimizer


class PostsPage:
    """帖子管理页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._threads = []
        self._selected: Set[int] = set()
        self._current_fname = ""
        self._current_page = 1
        self._is_loading = False

    async def load_data(self):
        """同步数据库中的贴吧列表到下拉框"""
        if not self.db: return
        
        account = await self.db.get_active_account()
        if not account:
            return

        # 获取当前账号关注的所有贴吧
        forums = await self.db.get_forums(account.id)
        
        options = [ft.dropdown.Option(f.fname) for f in forums]
        
        # 更新下拉框选项
        self.post_forum.options = options
        self.search_forum.options = options
        
        if forums:
            # 默认选中第一个
            self.post_forum.value = forums[0].fname
            self.search_forum.value = forums[0].fname
            
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
                        ft.Text("帖子管理 / POST CONTROL", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("发布新帖、回复监控及内容优化", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # --- 发帖区域 (Cyber Card) ---
        self.post_title = ft.TextField(
            label="帖子标题", 
            expand=True, 
            text_size=13,
            border_color=ft.colors.with_opacity(0.2, "primary")
        )
        self.post_forum = ft.Dropdown(
            label="选择贴吧", 
            width=200, 
            text_size=13,
            border_color=ft.colors.with_opacity(0.2, "primary"),
            options=[], # 由 load_data 填充
        )
        self.post_content = ft.TextField(
            label="内容", 
            multiline=True, 
            min_lines=3, 
            max_lines=6, 
            text_size=13,
            border_color=ft.colors.with_opacity(0.2, "primary")
        )
        self.post_status = ft.Text("", size=11)

        post_area = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.icons.POST_ADD, color="primary", size=18),
                    ft.Text("发布新建议/帖子", size=14, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.TextButton(
                        "AI SEO 优化", 
                        icon=ft.icons.AUTO_AWESOME, 
                        on_click=self._ai_optimize_post,
                        style=ft.ButtonStyle(color="primary")
                    ),
                ]),
                ft.Row([self.post_forum, self.post_title], spacing=10),
                self.post_content,
                ft.Row([
                    create_gradient_button("立即发布", icon=ft.icons.SEND, on_click=self._do_post),
                    self.post_status,
                ], alignment=ft.MainAxisAlignment.START),
            ], spacing=10),
            padding=15,
            bgcolor=ft.colors.with_opacity(0.03, "primary"),
            border=ft.border.all(1, ft.colors.with_opacity(0.1, "primary")),
            border_radius=12,
        )

        # --- 列表区域 ---
        self.search_forum = ft.Dropdown(
            label="筛选贴吧", 
            width=200, 
            text_size=13,
            border_color=ft.colors.with_opacity(0.2, "primary"),
            options=[],
        )
        self.search_keyword = ft.TextField(label="内容关键字", expand=True, text_size=13)
        
        self.thread_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self.loading_indicator = ft.ProgressBar(visible=False, bar_height=2, color="primary")

        # 工具栏
        self.toolbar = ft.Row([
            ft.Text("搜索结果", size=14, weight=ft.FontWeight.W_500),
            ft.Container(expand=True),
            ft.TextButton("全选/取消", icon=ft.icons.SELECT_ALL, on_click=self._select_all, visible=True),
            ft.TextButton("删除选中", icon=ft.icons.DELETE, icon_color="error", on_click=self._delete_selected, visible=False),
        ], visible=True)

        list_area = ft.Column([
            ft.Row([
                self.search_forum,
                self.search_keyword,
                ft.IconButton(ft.icons.SEARCH, on_click=self._do_search, icon_color="primary"),
            ], spacing=10),
            self.loading_indicator,
            self.toolbar,
            self.thread_list,
        ], expand=True, spacing=10)

        # 主内容布局
        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=ft.colors.with_opacity(0.1, "onSurface")),
                post_area,
                ft.Container(height=10),
                list_area,
            ], spacing=20),
            padding=20,
            expand=True,
        )

    async def _ai_optimize_post(self, e):
        """调用 AI 优化帖子"""
        title = self.post_title.value.strip()
        content = self.post_content.value.strip()

        if not title or not content:
            self._show_snackbar("请先填写标题和内容再进行优化", "error")
            return

        self._show_snackbar("AI 神经元正在计算优化方案...", "info")
        
        async def optimize():
            optimizer = AIOptimizer(self.db)
            success, opt_title, opt_content, err = await optimizer.optimize_post(title, content)
            
            if not success:
                self._show_snackbar(err, "error")
                return

            def apply_opt(e):
                self.post_title.value = opt_title
                self.post_content.value = opt_content
                self.page.close(dialog)
                self.page.update()
                self._show_snackbar("SEO 方案已采纳", "success")

            dialog = ft.AlertDialog(
                title=ft.Row([ft.Icon(ft.icons.AUTO_AWESOME, color="primary"), ft.Text("AI SEO 优化建议")]),
                content=ft.Column([
                    ft.Text("优化后的标题:", weight=ft.FontWeight.BOLD, size=12, color="primary"),
                    ft.Container(
                        content=ft.Text(opt_title, selectable=True),
                        padding=10, bgcolor=ft.colors.with_opacity(0.05, "onSurface"), border_radius=5
                    ),
                    ft.Container(height=10),
                    ft.Text("优化后的内容:", weight=ft.FontWeight.BOLD, size=12, color="primary"),
                    ft.Container(
                        content=ft.Column(
                            controls=[ft.Text(opt_content, selectable=True)],
                            scroll=ft.ScrollMode.AUTO,
                            expand=True
                        ),
                        padding=10, bgcolor=ft.colors.with_opacity(0.05, "onSurface"), border_radius=5,
                        height=200
                    ),
                ], tight=True, width=500),
                actions=[
                    ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                    ft.FilledButton("采纳建议", icon=ft.icons.CHECK, on_click=apply_opt),
                ],
            )
            self.page.open(dialog)

        self.page.run_task(optimize)

    def _build_thread_items(self):
        items = []
        for t in self._threads:
            is_selected = t.tid in self._selected
            card = ft.Container(
                content=ft.Row([
                    ft.Checkbox(value=is_selected, on_change=lambda e, tid=t.tid: self._toggle_select(tid)),
                    ft.Column([
                        ft.Text(t.title, size=14, weight=ft.FontWeight.W_500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Row([
                            ft.Text(f"作者: {t.author_name}", size=11, color="onSurfaceVariant"),
                            ft.Text(f"回复: {t.reply_num}", size=11, color="onSurfaceVariant"),
                            ft.Container(
                                content=ft.Text("精品", size=9, color="secondary", weight=ft.FontWeight.BOLD),
                                bgcolor=ft.colors.with_opacity(0.1, "secondary"),
                                padding=ft.padding.symmetric(horizontal=4, vertical=1),
                                border_radius=3,
                                visible=t.is_good
                            ),
                        ], spacing=10),
                    ], expand=True, spacing=2),
                    ft.IconButton(ft.icons.DELETE_OUTLINE, icon_color="error", icon_size=18, on_click=lambda e, tid=t.tid: self.page.run_task(self._delete_thread, tid)),
                ]),
                bgcolor=ft.colors.with_opacity(0.03, "onSurface"),
                border=ft.border.all(1, ft.colors.with_opacity(0.1, "onSurface")),
                border_radius=8,
                padding=10,
            )
            items.append(card)
        return items

    async def _do_search(self, e):
        fname_val = self.search_forum.value
        self._current_fname = fname_val.strip() if fname_val else ""
        if not self._current_fname:
            self._show_snackbar("请选择贴吧名称", "error")
            return
        
        self.loading_indicator.visible = True
        self.thread_list.controls.clear()
        self.page.update()

        try:
            results = await get_threads(self.db, self._current_fname)
            self._threads = results
            self.thread_list.controls = self._build_thread_items()
            if not results:
                self.thread_list.controls.append(ft.Text("未找到相关帖子", color="onSurfaceVariant", text_align=ft.TextAlign.CENTER))
        except Exception as ex:
            self._show_snackbar(f"搜索失败: {str(ex)}", "error")
        
        self.loading_indicator.visible = False
        self.page.update()

    async def _do_post(self, e):
        fname_val = self.post_forum.value
        fname = fname_val.strip() if fname_val else ""
        title = self.post_title.value.strip()
        content = self.post_content.value.strip()

        if not fname or not title or not content:
            self._show_snackbar("请填写完整发帖信息", "error")
            return

        self.post_status.value = "正在通过加密信道传输数据..."
        self.post_status.color = "primary"
        self.page.update()

        success, msg, tid = await add_thread(self.db, fname, title, content)
        if success:
            self._show_snackbar(f"发帖成功! TID: {tid}", "success")
            self.post_title.value = ""
            self.post_content.value = ""
            self.post_status.value = ""
        else:
            self._show_snackbar(msg, "error")
            self.post_status.value = "传输失败"
            self.post_status.color = "error"
        
        self.page.update()

    async def _delete_thread(self, tid):
        # 简单处理，直接调用
        success, msg = await delete_thread(self.db, self._current_fname, tid)
        if success:
            self._show_snackbar("帖子已从服务器抹除", "info")
            await self._do_search(None)
        else:
            self._show_snackbar(msg, "error")

    def _toggle_select(self, tid):
        if tid in self._selected: self._selected.remove(tid)
        else: self._selected.add(tid)
        self._update_toolbar()
        self.page.update()

    def _select_all(self, e):
        if len(self._selected) == len(self._threads):
            self._selected.clear()
        else:
            self._selected = {t.tid for t in self._threads}
        
        # 刷新列表项状态
        self.thread_list.controls = self._build_thread_items()
        self._update_toolbar()
        self.page.update()

    def _update_toolbar(self):
        has_sel = len(self._selected) > 0
        self.toolbar.controls[2].visible = True # 全选始终可见
        self.toolbar.controls[3].visible = has_sel
        self.toolbar.controls[3].text = f"删除选中 ({len(self._selected)})"

    async def _delete_selected(self, e):
        if not self._selected: return

        def close_dlg(e):
            self.page.close(dialog)
            self.page.update()

        async def start_batch_delete(e):
            tids = list(self._selected)
            self.page.close(dialog)
            
            self.loading_indicator.visible = True
            self.loading_indicator.value = 0
            self.page.update()

            total = len(tids)
            count = 0
            
            async for tid, success, msg in delete_threads(self.db, self._current_fname, tids):
                count += 1
                self.loading_indicator.value = count / total
                if not success:
                    self._show_snackbar(f"TID {tid} 删除异常: {msg}", "error")
                self.page.update()

            self._show_snackbar(f"批量清理完成，成功处理 {count} 个目标", "success")
            self._selected.clear()
            self.loading_indicator.visible = False
            await self._do_search(None)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.icons.WARNING_AMBER_ROUNDED, color="error"), ft.Text("确认批量物理抹除？")]),
            content=ft.Text(f"您已标记 {len(self._selected)} 个帖子。此操作将从贴吧服务器物理删除相关内容，由机甲控制核心执行，具有不可逆性。", size=13),
            actions=[
                ft.TextButton("取消", on_click=close_dlg),
                ft.FilledButton("确认执行", icon=ft.icons.DELETE_FOREVER, on_click=start_batch_delete, bgcolor="error"),
            ],
        )
        self.page.open(dialog)

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=ft.colors.with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
