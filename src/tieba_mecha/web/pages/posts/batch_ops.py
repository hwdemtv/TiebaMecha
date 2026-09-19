"""批量操作与分析 Tab：按风险分组操作栏、影响预览+二次确认、分析卡片、按筛选导出。"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime
from pathlib import Path

import flet as ft

from ...utils import with_opacity
from ...components.icons import (
    AUTO_AWESOME,
    COPY_ALL,
    DELETE_FOREVER_OUTLINED,
    DELETE_OUTLINE,
    INFO_OUTLINED,
    MONITOR_HEART_ROUNDED,
    SAVE_ROUNDED,
)
from .helpers import (
    SURVIVAL_DISPLAY,
    classify_survival,
    death_reason_display,
    fmt_time,
    thread_url,
)

_PAGE_SIZE = 25


class BatchOpsTabMixin:
    """批量操作与分析场景（混入 PostsPage）。"""

    # ---------- 构建 ----------

    def _build_batch_tab(self) -> ft.Control:
        # 数据范围提示（与"我的帖子"筛选联动）
        self._batch_hint = ft.Text("", size=11, color="onSurfaceVariant")

        # 分析卡片：存活分布 + 删除原因分布
        self._analysis_area = ft.Column(spacing=8)

        # 列表与选择
        self._batch_list = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
        self._batch_page_info = ft.Text("", size=12, color="onSurfaceVariant")
        # 进度条置于 Row 内横向撑满（Column 内 expand 会变成纵向弹性）
        self._batch_progress_bar = ft.ProgressBar(visible=False, bar_height=2, color="primary", expand=True)
        self._batch_progress = ft.Row([self._batch_progress_bar])
        self._batch_progress_info = ft.Text("", size=11, color="onSurfaceVariant")

        # 风险分组操作栏（选中后出现）
        self.select_all_btn = ft.TextButton("全选/取消", icon="select_all", on_click=self._on_select_all)
        self.export_btn = ft.TextButton("导出 CSV", icon=SAVE_ROUNDED, on_click=self._on_export,
                                        tooltip="导出选中项；未选中时导出当前筛选结果")
        self.copy_links_btn = ft.TextButton("复制链接", icon=COPY_ALL, on_click=self._on_copy_links, visible=False)
        self.bulk_ai_btn = ft.TextButton("批量 AI 优化", icon=AUTO_AWESOME, icon_color="teal",
                                         on_click=self._on_bulk_ai, visible=False)
        self.recheck_btn = ft.TextButton("重新检测存活", icon=MONITOR_HEART_ROUNDED, on_click=self._on_recheck, visible=False)
        self.remove_local_btn = ft.TextButton("移除本地记录", icon=DELETE_OUTLINE, icon_color="onSurfaceVariant",
                                              on_click=self._on_remove_local, visible=False)
        self.delete_server_btn = ft.TextButton("删除贴吧帖子", icon=DELETE_FOREVER_OUTLINED, icon_color="error",
                                               on_click=self._on_delete_server, visible=False)

        def _group(label: str, color: str, buttons: list) -> ft.Control:
            return ft.Row([
                ft.Container(
                    content=ft.Text(label, size=10, weight=ft.FontWeight.BOLD, color=color),
                    bgcolor=with_opacity(0.1, color),
                    padding=ft.padding.symmetric(horizontal=6, vertical=2),
                    border_radius=4,
                ),
                *buttons,
            ], spacing=2)

        self.action_bar = ft.Column([
            ft.Row([
                ft.Text("已选 0 项", size=12, weight=ft.FontWeight.W_500),
                ft.Container(expand=True),
                self.select_all_btn,
            ], spacing=8),
            ft.Row([
                _group("常规", "primary", [self.export_btn, self.copy_links_btn, self.bulk_ai_btn]),
                ft.Container(width=10),
                _group("数据", "onSurfaceVariant", [self.recheck_btn, self.remove_local_btn]),
                ft.Container(width=10),
                _group("危险", "error", [self.delete_server_btn]),
            ], spacing=4, wrap=True),
        ], spacing=4, visible=False)

        pagination = ft.Row([
            ft.IconButton("navigate_before", icon_size=16, on_click=self._on_batch_prev, disabled=True),
            self._batch_page_info,
            ft.IconButton("navigate_next", icon_size=16, on_click=self._on_batch_next, disabled=True),
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=10)
        self._batch_prev_btn = pagination.controls[0]
        self._batch_next_btn = pagination.controls[2]

        self._batch_selection_label = self.action_bar.controls[0].controls[0]

        # 包 padding 容器：避免顶部控件被 Tabs 边界裁切（与发布/我的帖子 Tab 一致）
        return ft.Container(
            content=ft.Column([
                self._batch_hint,
                self._analysis_area,
                self.action_bar,
                self._batch_progress,
                self._batch_progress_info,
                self._batch_list,
                pagination,
            ], spacing=8, expand=True),
            padding=ft.padding.only(top=6, left=4, right=4),
            expand=True,
        )

    def _update_batch_hint(self):
        n = len(self._rows)
        filters = self._collect_filters()
        scope = "全部账号" if not filters["account_id"] else self._account_display(filters["account_id"])
        self._batch_hint.value = (
            f"数据范围：与「我的帖子」筛选一致（{scope}，共 {n} 条）。"
            "勾选后按 风险分组 执行操作；危险操作有影响预览与二次确认。"
        )

    # ---------- 分析卡片 ----------

    def _update_batch_analysis(self):
        """存活分布 + 删除原因分布（均基于当前筛选结果）。"""
        counts = {"alive": 0, "suspected": 0, "dead": 0, "unknown": 0}
        reason_counts: dict[str, int] = {}
        for r in self._rows:
            state = classify_survival(r.survival_status, r.death_reason)
            counts[state] += 1
            if state in ("dead", "suspected") and r.death_reason:
                label = death_reason_display(r.death_reason)
                reason_counts[label] = reason_counts.get(label, 0) + 1

        def _card(label, value, color):
            return ft.Container(
                content=ft.Column([
                    ft.Text(label, size=10, color="onSurfaceVariant"),
                    ft.Text(str(value), size=20, weight=ft.FontWeight.BOLD, color=color),
                ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.symmetric(horizontal=16, vertical=8),
                bgcolor=with_opacity(0.05, color),
                border=ft.border.all(1, with_opacity(0.2, color)),
                border_radius=8,
            )

        cards = ft.Row([
            _card("存活", counts["alive"], "green"),
            _card("疑似删除", counts["suspected"], "#FF9800"),
            _card("已删除", counts["dead"], "error"),
            _card("未知", counts["unknown"], "onSurfaceVariant"),
        ], spacing=8)

        # 删除原因横向条形（Top 5）
        reason_bar_controls: list[ft.Control] = []
        total_dead = sum(reason_counts.values())
        if total_dead:
            top = sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
            for label, cnt in top:
                ratio = cnt / total_dead
                reason_bar_controls.append(ft.Row([
                    ft.Container(
                        content=ft.Text(label, size=11, color="onSurfaceVariant"),
                        width=150,
                    ),
                    ft.Row([
                        ft.Container(width=max(8, int(220 * ratio)), height=8, bgcolor="error", border_radius=4),
                        ft.Container(expand=True, height=8, bgcolor=with_opacity(0.15, "error"), border_radius=4),
                    ], expand=True, spacing=0),
                    ft.Text(f"{cnt}", size=11, weight=ft.FontWeight.W_500),
                ], spacing=8))
        reason_section = ft.Column(
            ([ft.Text("删除原因分布", size=11, color="onSurfaceVariant")] + reason_bar_controls)
            if reason_bar_controls else
            [ft.Text("删除原因分布：暂无阵亡记录", size=11, color="onSurfaceVariant")],
            spacing=4,
        )

        self._analysis_area.controls = [
            ft.Row([cards, ft.Container(expand=True)], spacing=10),
            reason_section,
        ]

    # ---------- 列表与选择 ----------

    def _update_batch_list(self):
        total = len(self._rows)
        total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self._batch_page = min(max(1, self._batch_page), total_pages)
        start = (self._batch_page - 1) * _PAGE_SIZE
        page_rows = self._rows[start:start + _PAGE_SIZE]

        if not page_rows:
            self._batch_list.controls = [
                ft.Container(
                    content=ft.Text("当前筛选范围内没有帖子", size=12, color="onSurfaceVariant"),
                    alignment=ft.alignment.center,
                    padding=30,
                )
            ]
        else:
            self._batch_list.controls = [self._build_batch_row(r) for r in page_rows]

        self._batch_page_info.value = f"第 {self._batch_page} / {total_pages} 页 · 共 {total} 条"
        self._batch_prev_btn.disabled = self._batch_page <= 1
        self._batch_next_btn.disabled = self._batch_page >= total_pages
        self._update_action_bar()

    def _build_batch_row(self, r) -> ft.Control:
        key = self.row_key(r)
        checked = key in self._selected
        state = classify_survival(r.survival_status, r.death_reason)
        _, state_label, color = SURVIVAL_DISPLAY[state]

        # 中间内容区承担"点击选中"；详情按钮独立放置避免点击冒泡双重触发
        content_area = ft.Container(
            content=ft.Column([
                ft.Text(
                    r.title or "(无标题)",
                    size=13, weight=ft.FontWeight.W_500, expand=True,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, selectable=True,
                ),
                ft.Row([
                    ft.Text(f"{r.fname or '-'}", size=11, color="onSurfaceVariant"),
                    ft.Text(f"· {self._account_display(r.account_id)}", size=11, color="onSurfaceVariant"),
                    ft.Text(f"· {fmt_time(r.post_time)}", size=11, color="onSurfaceVariant"),
                    ft.Text(f"· {state_label}", size=11, color=color),
                ], spacing=2),
            ], spacing=2),
            expand=True,
            on_click=lambda e, k=key: self._toggle_select(k),
            ink=True,
        )

        return ft.Container(
            content=ft.Row([
                ft.Checkbox(value=checked, on_change=lambda e, k=key: self._toggle_select(k)),
                content_area,
                ft.IconButton(
                    INFO_OUTLINED, icon_size=16, icon_color="primary",
                    tooltip="查看详情",
                    on_click=lambda e, row=r: self.open_detail(row),
                ),
            ]),
            bgcolor=with_opacity(0.08, "primary") if checked else with_opacity(0.03, "onSurface"),
            border=ft.border.all(1, with_opacity(0.2, "primary") if checked else with_opacity(0.1, "onSurface")),
            border_radius=8,
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
        )

    def _toggle_select(self, key: str):
        if key in self._selected:
            self._selected.remove(key)
        else:
            self._selected.add(key)
        self._update_batch_list()
        self.page.update()

    async def _on_select_all(self, e):
        if len(self._selected) == len(self._rows) and self._rows:
            self._selected.clear()
        else:
            self._selected = {self.row_key(r) for r in self._rows}
        self._update_batch_list()
        self.page.update()

    def _selected_rows(self) -> list:
        key_set = self._selected
        return [r for r in self._rows if self.row_key(r) in key_set]

    def _update_action_bar(self):
        n = len(self._selected)
        self.action_bar.visible = n > 0
        self._batch_selection_label.value = f"已选 {n} 项"
        for btn in (self.copy_links_btn, self.bulk_ai_btn, self.recheck_btn,
                    self.remove_local_btn, self.delete_server_btn):
            btn.visible = n > 0

    async def _on_batch_prev(self, e):
        if self._batch_page > 1:
            self._batch_page -= 1
            self._update_batch_list()
            self.page.update()

    async def _on_batch_next(self, e):
        total_pages = max(1, (len(self._rows) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        if self._batch_page < total_pages:
            self._batch_page += 1
            self._update_batch_list()
            self.page.update()

    # ---------- 常规：导出 / 复制链接 / 批量 AI ----------

    @staticmethod
    def _csv_safe(value) -> str:
        """CSV 公式注入防护：以 = + - @ 开头的单元格前置单引号。"""
        s = "" if value is None else str(value)
        if s[:1] in ("=", "+", "-", "@"):
            return "'" + s
        return s

    async def _on_export(self, e):
        """导出选中项；未选中时导出当前筛选结果（而非全库）。

        桌面端弹出保存对话框；Web 端无法弹窗时回落写入 data/exports/。
        """
        rows = self._selected_rows() or self._rows
        if not rows:
            self._show_snackbar("没有可导出的数据", "warning")
            return
        self._export_rows = rows

        filename = f"posts_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            if not hasattr(self, "_export_picker"):
                self._export_picker = ft.FilePicker(on_result=self._on_export_result)
                self.page.overlay.append(self._export_picker)
                try:
                    self.page.update()
                except Exception:
                    pass
            self._export_picker.save_file(file_name=filename, allowed_extensions=["csv"])
        except Exception:
            # Web 端等不支持保存对话框的场景：直接写 data/exports/
            await self._write_export(rows, Path("data") / "exports" / filename)

    async def _on_export_result(self, e: ft.FilePickerResultEvent):
        if not e.path:
            return  # 用户取消
        await self._write_export(self._export_rows, Path(e.path))

    async def _write_export(self, rows: list, path):
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["TID", "标题", "账号", "贴吧", "回复数", "发布时间",
                                 "存活状态", "删除原因", "最近检测时间", "是否精品", "正文摘要"])
                for r in rows:
                    state = classify_survival(r.survival_status, r.death_reason)
                    state_label = SURVIVAL_DISPLAY[state][1]
                    writer.writerow([
                        self._csv_safe(r.tid),
                        self._csv_safe(r.title),
                        self._csv_safe(self._account_display(r.account_id)),
                        self._csv_safe(r.fname),
                        self._csv_safe(r.reply_num),
                        self._csv_safe(fmt_time(r.post_time)),
                        state_label,
                        self._csv_safe(death_reason_display(r.death_reason) if r.death_reason else ""),
                        self._csv_safe(fmt_time(r.last_checked_at)),
                        "是" if r.is_good else "否",
                        self._csv_safe((r.content or "")[:120]),
                    ])
            self._show_snackbar(f"已导出 {len(rows)} 条: {path}", "success")
        except Exception as ex:
            self._show_snackbar(f"导出失败: {ex}", "error")

    async def _on_copy_links(self, e):
        rows = self._selected_rows()
        urls = [thread_url(r.tid) for r in rows if r.tid]
        if not urls:
            self._show_snackbar("选中项中没有有效链接", "warning")
            return
        self.page.set_clipboard("\n".join(urls))
        self._show_snackbar(f"已复制 {len(urls)} 条链接", "success")

    async def _on_bulk_ai(self, e):
        """批量 AI 优化：生成建议报告，采纳后写入物料池。"""
        rows = [r for r in self._selected_rows() if r.title]
        if not rows:
            self._show_snackbar("没有可优化的选中项", "warning")
            return

        from ....core.ai_optimizer import AIOptimizer

        self._batch_progress_bar.visible = True
        self._batch_progress_bar.value = 0
        self.page.update()

        optimizer = AIOptimizer(self.db)
        results = []
        try:
            for i, r in enumerate(rows):
                self._batch_progress_bar.value = (i + 1) / len(rows)
                self._batch_progress_info.value = f"AI 优化中 {i + 1}/{len(rows)}"
                self.page.update()
                try:
                    success, opt_t, opt_c, err = await optimizer.optimize_post(r.title, r.content or "")
                except Exception:
                    success, opt_t, opt_c = False, "", ""
                if success:
                    results.append((r.title, opt_t, opt_c))
                # 节流：避免密集调用触发 AI API 限流
                if i < len(rows) - 1:
                    await asyncio.sleep(0.5)
        finally:
            await optimizer.close()
            self._batch_progress_bar.visible = False
            self._batch_progress_info.value = ""

        if not results:
            self._show_snackbar("AI 未产出可用建议（检查全局设置中的 AI 配置）", "warning")
            return

        result_view = ft.ListView(height=380, spacing=14)
        for old_t, new_t, new_c in results:
            async def adopt_item(e, nt=new_t, nc=new_c):
                await self.db.add_materials_bulk([(nt, nc)])
                self._show_snackbar("已存入物料库待发池", "success")

            result_view.controls.append(ft.Column([
                ft.Text(f"原帖: {old_t}", size=11, color="onSurfaceVariant"),
                ft.Text(f"建议标题: {new_t}", weight="bold", color="primary"),
                ft.Text(new_c, size=12),
                ft.Row([
                    ft.FilledButton("采纳到物料库", icon=AUTO_AWESOME,
                                    on_click=lambda e, nt=new_t, nc=new_c: self.page.run_task(adopt_item, e, nt, nc),
                                    style=ft.ButtonStyle(bgcolor="primary", color="white")),
                    ft.TextButton("复制文案", icon=COPY_ALL,
                                  on_click=lambda e, nc=new_c: self.page.set_clipboard(nc)),
                ], spacing=10),
                ft.Divider(),
            ]))

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(AUTO_AWESOME), ft.Text("批量 AI 优化报告")]),
            content=ft.Container(result_view, width=520),
            actions=[ft.TextButton("关闭", on_click=lambda _: self.page.close(dialog))],
        )
        self.page.open(dialog)

    # ---------- 数据：重新检测 / 移除本地记录 ----------

    async def _on_recheck(self, e):
        """重新检测选中项的存活状态。"""
        from ....core.post import check_post_survival

        targets = [r for r in self._selected_rows() if r.src == "material" and r.tid]
        skipped = len(self._selected) - len(targets)
        if not targets:
            self._show_snackbar("选中项中没有可检测的帖子", "warning")
            return

        self._batch_progress_bar.visible = True
        self._batch_progress_bar.value = 0
        alive = dead = 0
        try:
            total = len(targets)
            for i, row in enumerate(targets, 1):
                try:
                    status, reason = await check_post_survival(row.tid)
                except Exception:
                    # 异常文本不写入 death_reason（列宽有限且非可确认原因）
                    status, reason = "dead", "error"
                await self.db.update_material_survival_status(row.material_id, status, reason)
                if status == "alive":
                    alive += 1
                else:
                    dead += 1
                self._batch_progress_bar.value = i / total
                self._batch_progress_info.value = f"存活检测 {i}/{total}"
                self.page.update()
                await asyncio.sleep(0.3)
        finally:
            self._batch_progress_bar.visible = False
            self._batch_progress_info.value = ""

        msg = f"检测完成: 存活 {alive}, 阵亡 {dead}"
        if skipped > 0:
            msg += f"（跳过 {skipped} 条本地记录）"
        self._show_snackbar(msg, "success")
        self._selected.clear()
        await self._reload_rows()

    async def _on_remove_local(self, e):
        """批量移除本地记录（不触碰贴吧服务器）。"""
        rows = self._selected_rows()
        if not rows:
            return
        from ...components.toast import confirm_async

        confirmed = await confirm_async(
            self.page, "确认移除本地记录？",
            f"将移除 {len(rows)} 条本地记录（不影响贴吧服务器上的帖子）。",
            confirm_text="移除",
        )
        if not confirmed:
            return

        count = 0
        for r in rows:
            if r.src == "material":
                if await self.db.delete_material(r.material_id):
                    count += 1
                await self.db.delete_thread_record(r.tid)
            else:
                if await self.db.delete_thread_record(r.tid):
                    count += 1

        self._show_snackbar(f"已移除 {count} 条本地记录", "info")
        self._selected.clear()
        await self._reload_rows()

    # ---------- 危险：删除贴吧帖子（影响预览 + 二次确认） ----------

    def _build_impact_summary(self, rows: list) -> tuple[str, str]:
        """返回 (标题, 影响说明)。"""
        by_forum: dict[str, int] = {}
        for r in rows:
            fname = r.fname or "-"
            by_forum[fname] = by_forum.get(fname, 0) + 1
        detail = "、".join(f"{k}({v})" for k, v in sorted(by_forum.items(), key=lambda kv: -kv[1]))
        text = (
            f"将删除 {len(rows)} 个帖子，来自 {len(by_forum)} 个贴吧：{detail}。\n"
            "帖子将从贴吧服务器永久删除并同步清理本地记录，此操作不可逆。"
        )
        return f"确认删除 {len(rows)} 个贴吧帖子？", text

    async def _on_delete_server(self, e):
        # 贴吧仅允许作者删帖：只收可执行行（有 TID 且作者账号在本地），其余计入跳过
        rows = [r for r in self._selected_rows() if r.tid and r.account_id]
        skipped = sum(1 for r in self._selected_rows() if not (r.tid and r.account_id))
        if not rows:
            self._show_snackbar("选中项中没有可删除的帖子（作者账号不在本地的记录无法删除）", "warning")
            return

        title, impact = self._build_impact_summary(rows)
        from ...components.toast import confirm_async

        confirmed = await confirm_async(self.page, title, impact, confirm_text="确认删除")
        if not confirmed:
            return

        from ....core.post import delete_thread

        self._batch_progress_bar.visible = True
        self._batch_progress_bar.value = 0
        success_tids: list[int] = []
        failed: list[str] = []
        try:
            total = len(rows)
            for i, r in enumerate(rows, 1):
                try:
                    # 按帖子作者账号传凭证（活跃账号删不了别人发的帖子）
                    ok, msg = await delete_thread(self.db, r.fname, r.tid, account_id=r.account_id)
                except Exception as ex:
                    ok, msg = False, str(ex)
                if ok:
                    success_tids.append(r.tid)
                    if r.src == "material":
                        await self.db.delete_material(r.material_id)
                    await self.db.delete_thread_record(r.tid)
                else:
                    failed.append(f"{r.tid}({msg})")
                self._batch_progress_bar.value = i / total
                self._batch_progress_info.value = f"删除中 {i}/{total}"
                self.page.update()
                await asyncio.sleep(0.3)
        finally:
            self._batch_progress_bar.visible = False
            self._batch_progress_info.value = ""

        msg = f"删除完成：成功 {len(success_tids)}，失败 {len(failed)}"
        if failed:
            msg += f"（{'；'.join(failed[:3])}）"
        if skipped > 0:
            msg += f"；另有 {skipped} 条作者不明的记录已跳过"
        self._show_snackbar(msg, "success" if not failed and not skipped else "warning")
        self._selected.clear()
        await self._reload_rows()
