"""我的帖子 Tab：筛选、存活四态监控、详情入口、文字+图标行内操作、空状态引导。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import flet as ft

from ...utils import with_opacity
from ...components.icons import (
    COPY_ALL,
    DELETE_OUTLINE,
    DELETE_FOREVER_OUTLINED,
    INFO_OUTLINED,
    MONITOR_HEART_ROUNDED,
    OPEN_IN_NEW,
    POST_ADD,
    REFRESH_ROUNDED,
    SEARCH,
    SEND_ROUNDED,
)
from .helpers import (
    SURVIVAL_DISPLAY,
    classify_survival,
    death_reason_display,
    fmt_time,
    thread_url,
)


class MyPostsTabMixin:
    """我的帖子场景（混入 PostsPage）。"""

    # ---------- 构建 ----------

    def _build_my_posts_tab(self) -> ft.Control:
        # 筛选栏
        self._filter_account = ft.Dropdown(
            label="账号", width=150, text_size=12, options=[],
            on_change=self._on_filter_change,
        )
        self._filter_fname = ft.Dropdown(
            label="贴吧", width=140, text_size=12, options=[],
            on_change=self._on_filter_change,
        )
        self._filter_survival = ft.Dropdown(
            label="存活状态", width=130, text_size=12, value="all",
            options=[
                ft.dropdown.Option("all", "全部"),
                ft.dropdown.Option("alive", "存活"),
                ft.dropdown.Option("dead", "已删除"),
                ft.dropdown.Option("suspected", "疑似删除"),
                ft.dropdown.Option("unknown", "未知"),
            ],
            on_change=self._on_filter_change,
        )
        self._filter_good = ft.Dropdown(
            label="精品", width=110, text_size=12, value="all",
            options=[
                ft.dropdown.Option("all", "全部"),
                ft.dropdown.Option("good", "精品"),
                ft.dropdown.Option("normal", "非精品"),
            ],
            on_change=self._on_filter_change,
        )
        self._filter_date_from = ft.TextField(
            label="起始日期", width=120, text_size=12, hint_text="YYYY-MM-DD",
            on_submit=self._on_filter_change, on_blur=self._on_filter_change,
        )
        self._filter_date_to = ft.TextField(
            label="结束日期", width=120, text_size=12, hint_text="YYYY-MM-DD",
            on_submit=self._on_filter_change, on_blur=self._on_filter_change,
        )
        self._filter_keyword = ft.TextField(
            label="标题/正文关键字", width=260, text_size=12,
            on_submit=self._on_filter_change,
        )

        filter_bar = ft.Row([
            self._filter_account,
            self._filter_fname,
            self._filter_survival,
            self._filter_good,
            self._filter_date_from,
            self._filter_date_to,
            self._filter_keyword,
            ft.IconButton(SEARCH, icon_size=18, tooltip="应用筛选", on_click=self._on_filter_change, icon_color="primary"),
            ft.IconButton(REFRESH_ROUNDED, icon_size=18, tooltip="刷新列表", on_click=self._on_refresh, icon_color="primary"),
        ], spacing=8, wrap=True)

        # 存活统计徽章（存活/疑似/已删/未知）
        self._stats_row = ft.Row(spacing=8)

        # 一键检测（当前筛选结果）
        self._check_progress = ft.ProgressBar(visible=False, bar_height=2, color="primary", expand=True)
        self._check_info = ft.Text("", size=11, color="onSurfaceVariant")
        check_all_btn = ft.OutlinedButton(
            "一键检测存活(当前筛选)",
            icon=MONITOR_HEART_ROUNDED,
            tooltip="逐一探测当前筛选结果的帖子是否仍然存活（限速执行）",
            on_click=lambda e: self.page.run_task(self._bulk_check_rows),
        )

        # 列表与分页
        self._mine_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self._mine_page_info = ft.Text("", size=12, color="onSurfaceVariant")
        mine_pagination = ft.Row([
            ft.IconButton("navigate_before", icon_size=16, on_click=self._on_mine_prev, disabled=True),
            self._mine_page_info,
            ft.IconButton("navigate_next", icon_size=16, on_click=self._on_mine_next, disabled=True),
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=10)
        self._mine_prev_btn = mine_pagination.controls[0]
        self._mine_next_btn = mine_pagination.controls[2]

        # 包 padding 容器：避免下拉框标签被 Tabs 边界裁切（与发布 Tab 一致）
        return ft.Container(
            content=ft.Column([
                filter_bar,
                ft.Row([self._stats_row, ft.Container(expand=True), self._check_info, self._check_progress, check_all_btn], spacing=10),
                self._mine_list,
                mine_pagination,
            ], spacing=10, expand=True),
            padding=ft.padding.only(top=6, left=4, right=4),
            expand=True,
        )

    def _fill_filter_dropdowns(self):
        account_options = [ft.dropdown.Option("all", "全部账号")]
        account_options += [ft.dropdown.Option(str(a.id), a.name or f"账号-{a.id}") for a in self._accounts]
        self._filter_account.options = account_options
        # 默认只看当前账号；但保留用户已设的筛选（仅在失效时回落）
        current = self._filter_account.value
        valid_ids = {str(a.id) for a in self._accounts} | {"all"}
        if current not in valid_ids:
            self._filter_account.value = str(self._active_account.id) if self._active_account else "all"

        fname_options = [ft.dropdown.Option("all", "全部贴吧")]
        seen: set[str] = set()
        for f in self._forums:
            if f.fname not in seen:
                seen.add(f.fname)
                fname_options.append(ft.dropdown.Option(f.fname, f.fname))
        self._filter_fname.options = fname_options
        if self._filter_fname.value not in {f.fname for f in self._forums} | {"all"}:
            self._filter_fname.value = "all"

    def _collect_filters(self) -> dict:
        """读取筛选控件 → get_my_posts 参数。"""
        account_val = self._filter_account.value if hasattr(self, "_filter_account") else "all"
        fname_val = self._filter_fname.value if hasattr(self, "_filter_fname") else "all"
        survival_val = self._filter_survival.value if hasattr(self, "_filter_survival") else "all"
        good_val = self._filter_good.value if hasattr(self, "_filter_good") else "all"
        kw = (self._filter_keyword.value or "").strip() if hasattr(self, "_filter_keyword") else ""

        def _date(s, end_of_day=False):
            if not s:
                return None
            try:
                if end_of_day:
                    return datetime.strptime(s + " 23:59:59", "%Y-%m-%d %H:%M:%S")
                return datetime.strptime(s, "%Y-%m-%d")
            except ValueError:
                return None

        return {
            "account_id": int(account_val) if account_val and account_val != "all" else None,
            "fname": None if (not fname_val or fname_val == "all") else fname_val,
            "survival": None if survival_val == "all" else survival_val,
            "is_good": {"good": True, "normal": False}.get(good_val),
            "date_from": _date(self._filter_date_from.value),
            "date_to": _date(self._filter_date_to.value, end_of_day=True),
            "keyword": kw or None,
        }

    async def _on_filter_change(self, e=None):
        await self._reload_rows(reset_page=True)

    async def _on_refresh(self, e=None):
        await self._reload_rows()

    # ---------- 统计徽章 ----------

    def _update_stats(self):
        counts = {"alive": 0, "suspected": 0, "dead": 0, "unknown": 0}
        for r in self._rows:
            counts[classify_survival(r.survival_status, r.death_reason)] += 1

        def _badge(state: str, n: int):
            icon, label, color = SURVIVAL_DISPLAY[state]
            return ft.Container(
                content=ft.Row([
                    ft.Icon(icon, size=14, color=color),
                    ft.Text(f"{label} {n}", size=12, color=color, weight=ft.FontWeight.W_500),
                ], spacing=4),
                bgcolor=with_opacity(0.08, color),
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=6,
            )

        self._stats_row.controls = [
            _badge("alive", counts["alive"]),
            _badge("suspected", counts["suspected"]),
            _badge("dead", counts["dead"]),
            _badge("unknown", counts["unknown"]),
        ]

    # ---------- 列表 ----------

    def _update_mine_list(self):
        total = len(self._rows)
        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        self._mine_page = min(max(1, self._mine_page), total_pages)
        start = (self._mine_page - 1) * self._page_size
        page_rows = self._rows[start:start + self._page_size]

        if not page_rows:
            self._mine_list.controls = [self._build_empty_state()]
        else:
            self._mine_list.controls = [self._build_post_row(r) for r in page_rows]

        self._mine_page_info.value = f"第 {self._mine_page} / {total_pages} 页 · 共 {total} 条"
        self._mine_prev_btn.disabled = self._mine_page <= 1
        self._mine_next_btn.disabled = self._mine_page >= total_pages

    async def _on_mine_prev(self, e):
        if self._mine_page > 1:
            self._mine_page -= 1
            self._update_mine_list()
            self.page.update()

    async def _on_mine_next(self, e):
        total_pages = max(1, (len(self._rows) + self._page_size - 1) // self._page_size)
        if self._mine_page < total_pages:
            self._mine_page += 1
            self._update_mine_list()
            self.page.update()

    def _survival_badge(self, row) -> ft.Control:
        state = classify_survival(row.survival_status, row.death_reason)
        icon, label, color = SURVIVAL_DISPLAY[state]
        return ft.Container(
            content=ft.Row([
                ft.Icon(icon, size=13, color=color),
                ft.Text(label, size=11, color=color, weight=ft.FontWeight.W_500),
            ], spacing=4),
            bgcolor=with_opacity(0.08, color),
            padding=ft.padding.symmetric(horizontal=8, vertical=3),
            border_radius=6,
        )

    def _build_post_row(self, r) -> ft.Control:
        """单条帖子卡片：状态徽章 + 元信息 + 文字+图标操作（减少误操作）。"""
        is_good = bool(r.is_good)

        title_row = ft.Row([
            ft.Text(
                r.title or "(无标题)",
                size=14, weight=ft.FontWeight.W_500, expand=True,
                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, selectable=True,
            ),
            ft.Container(
                content=ft.Text("精品", size=9, color="secondary", weight=ft.FontWeight.BOLD),
                bgcolor=with_opacity(0.1, "secondary"),
                padding=ft.padding.symmetric(horizontal=4, vertical=1),
                border_radius=3,
                visible=is_good,
            ),
            self._survival_badge(r),
        ], spacing=8)

        meta_items = [
            ft.Text(f"账号: {self._account_display(r.account_id)}", size=11, color="onSurfaceVariant"),
            ft.Text(f"贴吧: {r.fname or '-'}", size=11, color="onSurfaceVariant"),
            ft.Text(f"发布: {fmt_time(r.post_time)}", size=11, color="onSurfaceVariant"),
            ft.Text(f"回复: {r.reply_num}", size=11, color="onSurfaceVariant"),
            ft.Text(f"检测: {fmt_time(r.last_checked_at)}", size=11, color="onSurfaceVariant"),
        ]
        if r.survival_status == "dead" and r.death_reason:
            meta_items.append(ft.Text(
                f"原因: {death_reason_display(r.death_reason)}",
                size=11, color="error", italic=True,
            ))
        meta_row = ft.Row(meta_items, spacing=12, wrap=True)

        # 文字 + 图标操作（避免仅图标造成误操作；危险操作用红色文字按钮）
        can_delete_server = self._can_delete_server(r)
        actions = ft.Row([
            ft.TextButton("查看原帖", icon=OPEN_IN_NEW, on_click=lambda e, row=r: self._view_online(row), style=ft.ButtonStyle(color="primary")),
            ft.TextButton("复制链接", icon=COPY_ALL, on_click=lambda e, row=r: self._copy_link(row)),
            ft.TextButton("详情", icon=INFO_OUTLINED, on_click=lambda e, row=r: self.open_detail(row)),
            ft.TextButton("删除记录", icon=DELETE_OUTLINE, icon_color="onSurfaceVariant", on_click=lambda e, row=r: self.page.run_task(self._remove_row_local, row)),
            ft.TextButton("删除帖子", icon=DELETE_FOREVER_OUTLINED, icon_color="error",
                          disabled=not can_delete_server,
                          tooltip="" if can_delete_server else "贴吧仅允许作者删帖：该记录的作者不是任何本地可用账号（或作者未知），无法删除",
                          on_click=lambda e, row=r: self.page.run_task(self._delete_row_server, row)),
        ], spacing=2, wrap=True)

        return ft.Container(
            content=ft.Column([title_row, meta_row, actions], spacing=4),
            bgcolor=with_opacity(0.03, "onSurface"),
            border=ft.border.all(1, with_opacity(0.1, "onSurface")),
            border_radius=10,
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
        )

    def _build_empty_state(self) -> ft.Control:
        """空状态引导：发布新帖 / 前往矩阵发帖 / 导入历史记录。"""

        def _go_tab0(e):
            self.tabs.selected_index = 0
            self.page.update()

        def _go_batch(e):
            self._navigate("batch_post")

        return ft.Container(
            content=ft.Column([
                ft.Icon(POST_ADD, size=44, color=with_opacity(0.4, "onSurfaceVariant")),
                ft.Text("还没有帖子记录", size=14, color="onSurfaceVariant"),
                ft.Text("从这里开始你的第一步", size=11, color="onSurfaceVariant"),
                ft.Row([
                    ft.FilledButton("发布新帖", icon=POST_ADD, on_click=_go_tab0,
                                    style=ft.ButtonStyle(bgcolor="primary", color="white")),
                    ft.OutlinedButton("前往矩阵发帖", icon=SEND_ROUNDED, on_click=_go_batch),
                    ft.OutlinedButton("导入历史记录", icon=REFRESH_ROUNDED, on_click=lambda e: self._open_import_dialog()),
                ], alignment=ft.MainAxisAlignment.CENTER, spacing=10, wrap=True),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            alignment=ft.alignment.center,
            padding=50,
        )

    # ---------- 行内操作 ----------

    def _can_delete_server(self, row) -> bool:
        """删帖可行性：贴吧仅允许作者删帖，须存在可用的本地作者账号。

        物料行的 account_id 即发帖账号；记录行看 author_id（导入未带作者的不可删）。
        """
        return bool(row.account_id)

    def _view_online(self, row):
        if not row.tid:
            self._show_snackbar("该记录没有有效的帖子链接", "warning")
            return
        self.page.launch_url(thread_url(row.tid))

    def _copy_link(self, row):
        if not row.tid:
            self._show_snackbar("该记录没有有效的帖子链接", "warning")
            return
        self.page.set_clipboard(thread_url(row.tid))
        self._show_snackbar("链接已复制", "success")

    async def _remove_row_local(self, row):
        """仅删除本地记录（物料行删物料，记录行删 ThreadRecord），不碰贴吧服务器。"""
        removed_material = removed_record = False
        if row.src == "material":
            removed_material = await self.db.delete_material(row.material_id)
            # 关联的监控记录一并清理（无 TID 时无记录可删）
            if row.tid:
                removed_record = bool(await self.db.delete_thread_record(row.tid))
            ok = removed_material
        else:
            ok = bool(await self.db.delete_thread_record(row.tid))
        if ok:
            msg = "已删除本地记录"
            if row.src == "material" and row.tid and not removed_record:
                msg += "（关联监控记录不存在或已清理）"
            self._show_snackbar(msg, "info")
        else:
            self._show_snackbar("删除失败：记录不存在", "error")
        await self._reload_rows()

    async def _delete_row_server(self, row):
        """删除贴吧帖子（危险操作）：确认 → 用帖子作者账号删除 → 同步清理本地。"""
        from ...components.toast import confirm_async

        if not self._can_delete_server(row):
            self._show_snackbar("该记录的作者账号不在本地（或未知），无法删除帖子", "warning")
            return
        confirmed = await confirm_async(
            self.page,
            "确认删除贴吧帖子？",
            f"「{(row.title or '')[:40]}」将从贴吧服务器永久删除（贴吧: {row.fname or '-'}，账号: {self._account_display(row.account_id)}），此操作不可逆。",
            confirm_text="确认删除",
        )
        if not confirmed:
            return

        from ....core.post import delete_thread

        # 贴吧仅允许作者删帖：按记录的发布账号传凭证，而非当前活跃账号
        success, msg = await delete_thread(self.db, row.fname, row.tid, account_id=row.account_id)
        if success:
            if row.src == "material":
                await self.db.delete_material(row.material_id)
            await self.db.delete_thread_record(row.tid)
            self._show_snackbar("帖子已从贴吧服务器删除", "success")
        else:
            self._show_snackbar(f"删除失败: {msg}", "error")
        await self._reload_rows()

    # ---------- 一键存活检测（当前筛选结果） ----------

    async def _bulk_check_rows(self, e=None):
        from ....core.post import check_post_survival

        if getattr(self, "_check_running", False):
            return
        targets = [r for r in self._rows if r.src == "material" and r.tid]
        if not targets:
            self._show_snackbar("当前筛选结果中没有可检测的帖子", "warning")
            return

        self._check_running = True
        self._check_progress.visible = True
        self._check_progress.value = 0
        alive = dead = 0
        skipped = len(self._rows) - len(targets)
        try:
            total = len(targets)
            for i, row in enumerate(targets, 1):
                try:
                    status, reason = await check_post_survival(row.tid)
                except Exception:
                    # 异常文本不写入 death_reason（列宽有限且非可确认原因），
                    # 统一归为 error → 展示层归"疑似删除"
                    status, reason = "dead", "error"
                await self.db.update_material_survival_status(row.material_id, status, reason)
                if status == "alive":
                    alive += 1
                else:
                    dead += 1
                self._check_progress.value = i / total
                self._check_info.value = f"检测中 {i}/{total}：存活 {alive} · 阵亡 {dead}"
                self.page.update()
                # 逐条限速，避免高频请求触发风控
                await asyncio.sleep(0.3)

            done_msg = f"检测完成: 存活 {alive} 条, 阵亡 {dead} 条"
            if skipped > 0:
                done_msg += f"（本地导入记录 {skipped} 条不支持检测，已跳过）"
            self._show_snackbar(done_msg, "success")
        finally:
            self._check_running = False
            self._check_progress.visible = False
            self._check_progress.value = 0
            self._check_info.value = ""
            await self._reload_rows()

    # ---------- 导入历史记录 ----------

    def _open_import_dialog(self):
        forum_dd = ft.Dropdown(
            label="选择贴吧", text_size=13, expand=True,
            options=[ft.dropdown.Option(f.fname) for f in self._forums],
        )
        info = ft.Text("", size=11, color="onSurfaceVariant")

        async def do_import(e):
            fname = (forum_dd.value or "").strip()
            if not fname:
                info.value = "请先选择贴吧"
                info.color = "error"
                self.page.update()
                return
            info.value = "正在拉取该吧帖子列表..."
            info.color = "onSurfaceVariant"
            self.page.update()
            try:
                from ....core.post import get_threads

                threads = await get_threads(self.db, fname)
                if not threads:
                    info.value = "未拉取到帖子（检查账号凭证或该吧是否有帖子）"
                    info.color = "warning"
                else:
                    await self.db.upsert_thread_records([{
                        "tid": t.tid,
                        "title": t.title,
                        "author_name": getattr(t, "author_name", ""),
                        "author_id": getattr(t, "author_id", 0),
                        "reply_num": getattr(t, "reply_num", 0),
                        "text": getattr(t, "text", None),
                        "fname": fname,
                        "is_good": getattr(t, "is_good", False),
                    } for t in threads])
                    info.value = f"已导入 {len(threads)} 条记录"
                    info.color = "green"
                self.page.update()
            except Exception as ex:
                info.value = f"导入失败: {ex}"
                info.color = "error"
                self.page.update()
            finally:
                await self._reload_rows()
                self.page.close(dialog)

        dialog = ft.AlertDialog(
            title=ft.Text("导入历史记录"),
            content=ft.Container(
                content=ft.Column([
                    ft.Text("拉取所选贴吧的首页帖子并写入本地记录（当前账号凭证）。", size=12, color="onSurfaceVariant"),
                    forum_dd,
                    info,
                ], tight=True, spacing=10),
                width=380,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("开始导入", on_click=do_import),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)
