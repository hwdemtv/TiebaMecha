"""Posts management page with Cyber-Mecha aesthetic and AI SEO integration"""

import asyncio
import flet as ft
import csv
import os
from datetime import datetime
from typing import List, Set, Optional

from ..components import create_gradient_button
from ..utils import with_opacity
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
        self._is_from_db = False  # 标记当前列表来源是否为数据库缓存

    async def load_data(self):
        """同步数据库中的贴吧列表到下拉框，并加载已有监控记录"""
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

        # 加载本地缓存的帖子记录
        await self._load_cached_threads()

        self.page.update()

    async def _load_cached_threads(self):
        """从数据库加载已缓存的帖子记录"""
        from ...db.models import ThreadRecord

        records = await self.db.get_thread_records(limit=200)
        if records:
            # 将 ThreadRecord 转换为类似 Threads 对象的结构
            self._threads = []
            for r in records:
                # 创建简单的数据类来存储帖子信息
                class ThreadData:
                    pass
                t = ThreadData()
                t.tid = r.tid
                t.title = r.title
                t.author_name = r.author_name
                t.reply_num = r.reply_num
                t.text = r.text
                t.fname = r.fname
                t.is_good = r.is_good
                self._threads.append(t)

            self._is_from_db = True
            self.thread_list.controls = self._build_thread_items()
            self.toolbar.visible = True
            self._update_toolbar_label("监控列表 (本地缓存)")
        else:
            self._is_from_db = False
            self._update_toolbar_label("搜索结果")

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
                            bgcolor={"": with_opacity(0.1, ft.colors.PRIMARY)},
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

        # --- 发帖区域 (Cyber Card) - 左侧固定宽度 ---
        # 字数统计标签
        self.title_counter = ft.Text("0/31 字 (最少5字)", size=10, color="onSurfaceVariant")
        self.content_counter = ft.Text("0/2000 字", size=10, color="onSurfaceVariant")

        self.post_title = ft.TextField(
            label="帖子标题",
            text_size=13,
            border_color=with_opacity(0.2, "primary"),
            on_change=self._on_title_change,
        )
        self.post_forum = ft.Dropdown(
            label="选择贴吧",
            text_size=13,
            border_color=with_opacity(0.2, "primary"),
            options=[], # 由 load_data 填充
        )
        self.post_content = ft.TextField(
            label="内容",
            multiline=True,
            min_lines=4,
            max_lines=8,
            text_size=13,
            border_color=with_opacity(0.2, "primary"),
            on_change=self._on_content_change,
        )
        self.post_status = ft.Text("", size=11)

        post_area = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.icons.POST_ADD, color="primary", size=18),
                    ft.Text("发布新帖", size=14, weight=ft.FontWeight.BOLD),
                ]),
                self.post_forum,
                self.post_title,
                ft.Row([ft.Container(expand=True), self.title_counter]),
                self.post_content,
                ft.Row([ft.Container(expand=True), self.content_counter]),
                ft.TextButton(
                    "AI SEO 优化",
                    icon=ft.icons.AUTO_AWESOME,
                    on_click=self._ai_optimize_post,
                    style=ft.ButtonStyle(color="primary")
                ),
                create_gradient_button("立即发布", icon=ft.icons.SEND, on_click=self._do_post),
                self.post_status,
            ], spacing=10, scroll=ft.ScrollMode.AUTO),
            padding=15,
            width=300,
            bgcolor=with_opacity(0.03, "primary"),
            border=ft.border.all(1, with_opacity(0.1, "primary")),
            border_radius=12,
        )

        # --- 列表区域 ---
        self.search_forum = ft.Dropdown(
            label="筛选贴吧", 
            width=200, 
            text_size=13,
            border_color=with_opacity(0.2, "primary"),
            options=[],
        )
        self.search_keyword = ft.TextField(label="内容关键字", expand=True, text_size=13, on_submit=self._do_search)
        
        self.thread_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self.loading_indicator = ft.ProgressBar(visible=False, bar_height=2, color="primary")

        # 工具栏按钮 (保存为成员变量以避免索引 Bug)
        self.download_btn = ft.IconButton(ft.icons.DOWNLOAD_ROUNDED, tooltip="导出当前结果", on_click=self._export_threads, icon_color="primary")
        self.select_all_btn = ft.TextButton("全选/取消", icon=ft.icons.SELECT_ALL, on_click=self._select_all)
        self.ai_bulk_btn = ft.TextButton("批量 AI SEO", icon=ft.icons.AUTO_AWESOME, icon_color="teal", on_click=self._bulk_ai_optimize, visible=False)
        self.remove_local_btn = ft.TextButton("移除记录", icon=ft.icons.DELETE_OUTLINE, icon_color="onSurfaceVariant", on_click=self._remove_selected_local, visible=False)
        self.delete_server_btn = ft.TextButton("物理抹除", icon=ft.icons.DELETE_FOREVER_OUTLINED, icon_color="error", on_click=self._delete_selected, visible=False)

        self.toolbar_label = ft.Text("监控列表", size=14, weight=ft.FontWeight.W_500)

        self.toolbar = ft.Row([
            self.toolbar_label,
            ft.Container(expand=True),
            self.download_btn,
            self.ai_bulk_btn,
            self.remove_local_btn,
            self.select_all_btn,
            self.delete_server_btn,
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

        # 主内容布局 - 左右结构
        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                ft.Row([
                    post_area,
                    ft.VerticalDivider(width=1, color=with_opacity(0.1, "onSurface")),
                    ft.Container(
                        content=list_area,
                        expand=True,
                        padding=ft.padding.only(left=10),
                    ),
                ], expand=True, vertical_alignment=ft.CrossAxisAlignment.START),
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
                # 更新字数统计
                self._update_title_counter(len(opt_title))
                self._update_content_counter(len(opt_content))
                self.page.close(dialog)
                self.page.update()
                self._show_snackbar("SEO 方案已采纳", "success")

            dialog = ft.AlertDialog(
                title=ft.Row([ft.Icon(ft.icons.AUTO_AWESOME, color="primary"), ft.Text("AI SEO 优化建议")]),
                content=ft.Column([
                    ft.Text("优化后的标题:", weight=ft.FontWeight.BOLD, size=12, color="primary"),
                    ft.Container(
                        content=ft.Text(opt_title, selectable=True),
                        padding=10, bgcolor=with_opacity(0.05, "onSurface"), border_radius=5
                    ),
                    ft.Container(height=10),
                    ft.Text("优化后的内容:", weight=ft.FontWeight.BOLD, size=12, color="primary"),
                    ft.Container(
                        content=ft.Column(
                            controls=[ft.Text(opt_content, selectable=True)],
                            scroll=ft.ScrollMode.AUTO,
                            expand=True
                        ),
                        padding=10, bgcolor=with_opacity(0.05, "onSurface"), border_radius=5,
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
            # 获取贴吧名（优先使用当前搜索的贴吧，否则使用记录中的贴吧名）
            t_fname = getattr(t, 'fname', None) or self._current_fname

            card = ft.Container(
                content=ft.Row([
                    ft.Checkbox(value=is_selected, on_change=lambda e, tid=t.tid: self._toggle_select(tid)),
                    ft.Column([
                        ft.Text(t.title, size=14, weight=ft.FontWeight.W_500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, selectable=True),
                        ft.Text(t.text, size=12, color="onSurfaceVariant", max_lines=2, overflow=ft.TextOverflow.ELLIPSIS, selectable=True) if t.text else ft.Container(),
                        ft.Row([
                            ft.Text(f"作者: {t.author_name}", size=11, color="onSurfaceVariant"),
                            ft.Text(f"回复: {t.reply_num}", size=11, color="onSurfaceVariant"),
                            ft.Text(f"贴吧: {t_fname}", size=11, color="onSurfaceVariant") if t_fname else ft.Container(),
                            ft.Container(
                                content=ft.Text("精品", size=9, color="secondary", weight=ft.FontWeight.BOLD),
                                bgcolor=with_opacity(0.1, "secondary"),
                                padding=ft.padding.symmetric(horizontal=4, vertical=1),
                                border_radius=3,
                                visible=t.is_good
                            ),
                        ], spacing=10),
                    ], expand=True, spacing=2),
                    # 本地移除按钮 (垃圾桶)
                    ft.IconButton(
                        ft.icons.DELETE_OUTLINE,
                        icon_color="onSurfaceVariant",
                        icon_size=18,
                        tooltip="移除记录 (仅本地)",
                        on_click=lambda e, tid=t.tid: self.page.run_task(self._remove_local_record, tid)
                    ),
                    # 物理抹除按钮 (原子图标)
                    ft.IconButton(
                        ft.icons.DELETE_FOREVER_OUTLINED,
                        icon_color="error",
                        icon_size=18,
                        tooltip="物理抹除 (服务器删除)",
                        on_click=lambda e, tid=t.tid, fname=t_fname: self.page.run_task(self._delete_thread_server, tid, fname)
                    ),
                ]),
                bgcolor=with_opacity(0.08, "primary") if is_selected else with_opacity(0.03, "onSurface"),
                border=ft.border.all(1, with_opacity(0.2, "primary") if is_selected else with_opacity(0.1, "onSurface")),
                border_radius=10,
                padding=10,
                on_click=lambda e, tid=t.tid: self._toggle_select(tid),
            )
            items.append(card)
        return items

    async def _bulk_ai_optimize(self, e):
        """对选中的帖子进行批量 AI SEO 优化建议展示"""
        if not self._selected: return
        tids = list(self._selected)
        threads_to_opt = [t for t in self._threads if t.tid in tids]
        
        self.loading_indicator.visible = True
        self.loading_indicator.value = 0
        self.page.update()
        
        optimizer = AIOptimizer(self.db)
        results = []
        
        for i, t in enumerate(threads_to_opt):
            self.loading_indicator.value = (i + 1) / len(threads_to_opt)
            self.page.update()
            success, opt_t, opt_c, err = await optimizer.optimize_post(t.title, t.text or "")
            if success:
                results.append((t.title, opt_t, opt_c))
            await asyncio.sleep(0.5)

        self.loading_indicator.visible = False
        
        # 显示结果弹窗
        result_view = ft.ListView(height=400, spacing=15)
        for old_t, new_t, new_c in results:
            async def adopt_item(e, nt=new_t, nc=new_c):
                await self.db.add_materials_bulk([(nt, nc)])
                self._show_snackbar("已成功存入物料库待发池", "success")

            result_view.controls.append(ft.Column([
                ft.Text(f"原帖: {old_t}", size=11, color="onSurfaceVariant"),
                ft.Text(f"建议标题: {new_t}", weight="bold", color="primary"),
                ft.Text(new_c, size=12),
                ft.Row([
                    ft.FilledButton(
                        "采纳到物料库", 
                        icon=ft.icons.POST_ADD, 
                        on_click=lambda e, nt=new_t, nc=new_c: self.page.run_task(adopt_item, e, nt, nc),
                        style=ft.ButtonStyle(bgcolor="primary", color="white")
                    ),
                    ft.TextButton("复制文案", icon=ft.icons.COPY_ALL, on_click=lambda e, nc=new_c: self.page.set_clipboard(nc)),
                ], spacing=10),
                ft.Divider(),
            ]))

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.icons.AUTO_AWESOME), ft.Text("批量 AI 优化报告")]),
            content=ft.Container(result_view, width=500),
            actions=[ft.TextButton("关闭", on_click=lambda _: self.page.close(dialog))],
        )
        self.page.open(dialog)
        self.page.update()

    async def _export_threads(self, e):
        """导出当前搜索或选中的帖子数据"""
        data_to_export = []
        if self._selected:
            data_to_export = [t for t in self._threads if t.tid in self._selected]
        else:
            data_to_export = self._threads

        if not data_to_export:
            self._show_snackbar("没有可导出的数据", "warning")
            return

        try:
            filename = f"threads_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            with open(filename, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["TID", "Title", "Author", "Replies", "Content Snippet"])
                for t in data_to_export:
                    writer.writerow([t.tid, t.title, t.author_name, t.reply_num, t.text])
            
            self._show_snackbar(f"导出成功: {filename}", "success")
        except Exception as ex:
            self._show_snackbar(f"导出失败: {str(ex)}", "error")

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
            self._is_from_db = False

            # 搜索结果入库 (UPSERT)
            if results:
                threads_data = []
                for t in results:
                    threads_data.append({
                        "tid": t.tid,
                        "title": t.title,
                        "author_name": getattr(t, 'author_name', ''),
                        "author_id": getattr(t, 'author_id', 0),
                        "reply_num": getattr(t, 'reply_num', 0),
                        "text": getattr(t, 'text', None),
                        "fname": self._current_fname,
                        "is_good": getattr(t, 'is_good', False),
                    })
                await self.db.upsert_thread_records(threads_data)

            self.thread_list.controls = self._build_thread_items()
            self._update_toolbar_label("搜索结果")
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

        # 字数校验
        title_len = len(title)
        content_len = len(content)
        if title_len < 5 or title_len > 31:
            self._show_snackbar(f"标题需5-31字，当前{title_len}字", "error")
            return
        if content_len > 2000:
            self._show_snackbar(f"内容超出限制，当前{content_len}字", "error")
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
            # 重置字数统计
            self.title_counter.value = "0/31 字 (最少5字)"
            self.title_counter.color = "onSurfaceVariant"
            self.content_counter.value = "0/2000 字"
            self.content_counter.color = "onSurfaceVariant"
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

    async def _remove_local_record(self, tid):
        """仅从本地数据库移除记录"""
        success = await self.db.delete_thread_record(tid)
        if success:
            self._show_snackbar("已从本地记录中移除", "info")
            # 从当前列表中移除
            self._threads = [t for t in self._threads if t.tid != tid]
            self._selected.discard(tid)
            self.thread_list.controls = self._build_thread_items()
            self._update_toolbar()
            self.page.update()
        else:
            self._show_snackbar("移除失败：记录不存在", "error")

    async def _delete_thread_server(self, tid, fname):
        """从贴吧服务器删除帖子，并同步移除本地记录"""
        fname = fname or self._current_fname
        if not fname:
            self._show_snackbar("无法确定贴吧名称", "error")
            return

        success, msg = await delete_thread(self.db, fname, tid)
        if success:
            # 同时删除本地记录
            await self.db.delete_thread_record(tid)
            self._show_snackbar("帖子已从服务器物理抹除", "info")
            # 从当前列表中移除
            self._threads = [t for t in self._threads if t.tid != tid]
            self._selected.discard(tid)
            self.thread_list.controls = self._build_thread_items()
            self._update_toolbar()
            self.page.update()
        else:
            self._show_snackbar(msg, "error")

    def _toggle_select(self, tid):
        if tid in self._selected: self._selected.remove(tid)
        else: self._selected.add(tid)
        
        # 核心修复：必须重绘列表以使复选框组件获得最新的 value 状态
        self.thread_list.controls = self._build_thread_items()
        
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
        self.ai_bulk_btn.visible = has_sel
        self.remove_local_btn.visible = has_sel
        self.delete_server_btn.visible = has_sel
        self.remove_local_btn.text = f"移除记录 ({len(self._selected)})"
        self.delete_server_btn.text = f"物理抹除 ({len(self._selected)})"

    def _update_toolbar_label(self, text: str):
        """更新工具栏标签文本"""
        self.toolbar_label.value = text

    async def _remove_selected_local(self, e):
        """批量移除本地记录"""
        if not self._selected:
            return

        tids = list(self._selected)
        count = await self.db.delete_thread_records_bulk(tids)

        self._show_snackbar(f"已从本地移除 {count} 条记录", "info")
        self._selected.clear()

        # 重新加载本地缓存
        await self._load_cached_threads()
        self._update_toolbar()
        self.page.update()

    async def _delete_selected(self, e):
        if not self._selected: return

        # 收集选中帖子对应的贴吧名
        tids = list(self._selected)

        def close_dlg(e):
            self.page.close(dialog)
            self.page.update()

        async def start_batch_delete(e):
            self.page.close(dialog)

            self.loading_indicator.visible = True
            self.loading_indicator.value = 0
            self.page.update()

            total = len(tids)
            count = 0
            success_tids = []

            # 按贴吧分组处理
            for t in self._threads:
                if t.tid in tids:
                    fname = getattr(t, 'fname', None) or self._current_fname
                    if not fname:
                        continue

                    success, msg = await delete_thread(self.db, fname, t.tid)
                    count += 1
                    self.loading_indicator.value = count / total
                    self.page.update()

                    if success:
                        success_tids.append(t.tid)
                    else:
                        self._show_snackbar(f"TID {t.tid} 删除异常: {msg}", "error")

            # 批量删除本地记录
            if success_tids:
                await self.db.delete_thread_records_bulk(success_tids)

            self._show_snackbar(f"批量清理完成，成功抹除 {len(success_tids)} 个目标", "success")
            self._selected.clear()
            self.loading_indicator.visible = False

            # 重新加载本地缓存
            await self._load_cached_threads()
            self._update_toolbar()
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.icons.WARNING_AMBER_ROUNDED, color="error"), ft.Text("确认批量物理抹除？")]),
            content=ft.Text(f"您已标记 {len(self._selected)} 个帖子。此操作将从贴吧服务器物理删除相关内容，由机甲控制核心执行，具有不可逆性。", size=13),
            actions=[
                ft.TextButton("取消", on_click=close_dlg),
                ft.FilledButton(
                    "确认执行", 
                    icon=ft.icons.DELETE_FOREVER, 
                    on_click=start_batch_delete, 
                    style=ft.ButtonStyle(bgcolor=ft.colors.ERROR, color=ft.colors.WHITE)
                ),
            ],
        )
        self.page.open(dialog)

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _on_title_change(self, e):
        """标题输入变化时更新字数统计"""
        length = len(e.control.value or "")
        self._update_title_counter(length)

    def _update_title_counter(self, length: int):
        """更新标题字数统计显示"""
        # 标题限制: 5-31 字
        if length < 5:
            color = "error"
            hint = f"{length}/31 字 (还需{5-length}字)"
        elif length > 31:
            color = "error"
            hint = f"{length}/31 字 (超出{length-31}字)"
        else:
            color = "onSurfaceVariant"
            hint = f"{length}/31 字"
        self.title_counter.value = hint
        self.title_counter.color = color
        self.page.update()

    def _on_content_change(self, e):
        """内容输入变化时更新字数统计"""
        length = len(e.control.value or "")
        self._update_content_counter(length)

    def _update_content_counter(self, length: int):
        """更新内容字数统计显示"""
        # 内容限制: 最多 2000 字
        if length > 2000:
            color = "error"
            hint = f"{length}/2000 字 (超出{length-2000}字)"
        else:
            color = "onSurfaceVariant"
            hint = f"{length}/2000 字"
        self.content_counter.value = hint
        self.content_counter.color = color
        self.page.update()

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
