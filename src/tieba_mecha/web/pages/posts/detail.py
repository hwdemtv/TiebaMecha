"""帖子详情面板（右侧抽屉）：正文、链接、发布参数、AI 原文/优化版本、存活与自顶信息。"""

from __future__ import annotations

import flet as ft

from ...utils import with_opacity
from ...components.icons import (
    AUTO_AWESOME,
    CHECK_CIRCLE,
    CLEAR,
    COPY_ALL,
    DELETE_FOREVER_OUTLINED,
    DELETE_OUTLINE,
    ERROR,
    LINK_ROUNDED,
    MONITOR_HEART_ROUNDED,
    OPEN_IN_NEW,
    STOP_CIRCLE_ROUNDED,
)
from .helpers import (
    SURVIVAL_DISPLAY,
    classify_survival,
    death_reason_display,
    estimate_next_bump,
    extract_links,
    fmt_time,
)

_BUMP_MODE_LABEL = {
    "once": "次数上限模式",
    "scheduled": "定时周期模式",
    "matrix_loop": "矩阵轮换模式",
}


class DetailDrawerMixin:
    """详情面板（混入 PostsPage）。"""

    # ---------- 开合 ----------

    def open_detail(self, row):
        self._detail_row = row
        self._detail_panel.content = self._build_detail_content(row)
        self._detail_panel.visible = True
        self._detail_divider.visible = True
        self.page.update()

    def close_detail(self):
        self._detail_row = None
        if hasattr(self, "_detail_panel"):
            self._detail_panel.visible = False
            self._detail_panel.content = None
        if hasattr(self, "_detail_divider"):
            self._detail_divider.visible = False

    async def _close_and_refresh(self):
        self.close_detail()
        await self._reload_rows()

    # ---------- 内容构建 ----------

    def _build_detail_content(self, r) -> ft.Control:
        state = classify_survival(r.survival_status, r.death_reason)
        icon, state_label, color = SURVIVAL_DISPLAY[state]

        header = ft.Row([
            ft.Icon(icon, color=color, size=20),
            ft.Text("帖子详情", size=15, weight=ft.FontWeight.BOLD, expand=True),
            ft.IconButton(CLEAR, icon_size=16, tooltip="关闭", on_click=lambda e: self.close_detail()),
        ], spacing=6)

        items: list[ft.Control] = []

        # 标题 + 状态
        items.append(ft.Text(r.title or "(无标题)", size=14, weight=ft.FontWeight.W_600, selectable=True))
        items.append(self._survival_section(r, state, state_label, color))

        # 发布参数
        items.append(self._section_title("发布参数"))
        params = [
            ("发帖账号", self._account_display(r.account_id)),
            ("目标贴吧", r.fname or "-"),
            ("发布时间", fmt_time(r.post_time)),
            ("TID", str(r.tid) if r.tid else "-"),
            ("回复数", str(r.reply_num)),
            ("精品", "是" if r.is_good else "否"),
            ("来源任务", r.task_id or "手动/本地导入"),
            ("记录类型", "已发物料" if r.src == "material" else "本地监控记录"),
        ]
        for label, value in params:
            items.append(self._kv_row(label, value))

        # 链接
        links = extract_links(r.content or "")
        if links:
            items.append(self._section_title(f"正文链接 ({len(links)})"))
            for url in links[:10]:
                items.append(ft.Row([
                    ft.Icon(LINK_ROUNDED, size=13, color="primary"),
                    ft.Text(url, size=11, color="primary", expand=True, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS, selectable=True),
                    ft.IconButton(OPEN_IN_NEW, icon_size=13, icon_color="primary",
                                  tooltip="打开链接", on_click=lambda e, u=url: self.page.launch_url(u)),
                ], spacing=4))

        # 正文
        items.append(self._section_title("正文"))
        items.append(ft.Container(
            content=ft.Column([ft.Text(r.content or "(无正文)", size=12, selectable=True)],
                              scroll=ft.ScrollMode.AUTO, expand=True),
            height=150,
            padding=8,
            bgcolor=with_opacity(0.04, "onSurface"),
            border_radius=6,
        ))

        # AI 原文 / 优化版本
        if r.src == "material":
            if r.ai_status == "rewritten" and r.original_title is not None:
                items.append(self._section_title("AI 改写对比"))
                items.append(self._ai_block("AI 原文", r.original_title, r.original_content))
                items.append(self._ai_block("当前版本 (优化后)", r.title, r.content))
            else:
                items.append(self._kv_row("AI 改写", "未改写"))

        # 自顶信息
        if r.src == "material":
            items.append(self._bump_section(r))

        actions = ft.Row([
            ft.TextButton("查看原帖", icon=OPEN_IN_NEW, on_click=lambda e: self._view_online(r)),
            ft.TextButton("复制链接", icon=COPY_ALL, on_click=lambda e: self._copy_link(r)),
            ft.Container(expand=True),
            ft.TextButton("删除记录", icon=DELETE_OUTLINE, icon_color="onSurfaceVariant",
                          on_click=lambda e: self.page.run_task(self._detail_remove_local)),
            ft.TextButton("删除帖子", icon=DELETE_FOREVER_OUTLINED, icon_color="error",
                          on_click=lambda e: self.page.run_task(self._detail_delete_server)),
        ], spacing=2, wrap=True)

        return ft.Column([
            header,
            ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
            ft.Column(items, spacing=6, scroll=ft.ScrollMode.AUTO, expand=True),
            ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
            actions,
        ], spacing=8, expand=True)

    def _section_title(self, text: str) -> ft.Control:
        return ft.Text(text, size=11, weight=ft.FontWeight.W_500, color="onSurfaceVariant")

    def _kv_row(self, label: str, value: str, value_color: str | None = None) -> ft.Control:
        return ft.Row([
            ft.Container(content=ft.Text(f"{label}", size=11, color="onSurfaceVariant"), width=76),
            ft.Text(value or "-", size=12, color=value_color or "onSurface", expand=True, selectable=True),
        ], spacing=8)

    def _survival_section(self, r, state: str, state_label: str, color: str) -> ft.Control:
        rows = [
            self._kv_row("存活状态", state_label, value_color=color),
        ]
        if r.death_reason:
            rows.append(self._kv_row("删除原因", death_reason_display(r.death_reason), value_color="error"))
        if r.last_checked_at:
            rows.append(self._kv_row("最近检测", fmt_time(r.last_checked_at)))
        container = ft.Container(
            content=ft.Column(rows, spacing=4),
            padding=8,
            bgcolor=with_opacity(0.05, color),
            border=ft.border.all(1, with_opacity(0.2, color)),
            border_radius=6,
        )
        # 一键重新检测（仅物料行有存活跟踪）
        if r.src == "material" and r.tid:
            return ft.Column([
                container,
                ft.TextButton(
                    "重新检测存活", icon=MONITOR_HEART_ROUNDED,
                    on_click=lambda e: self.page.run_task(self._detail_recheck),
                ),
            ], spacing=0)
        return container

    def _ai_block(self, header: str, title: str | None, content: str | None) -> ft.Control:
        return ft.Container(
            content=ft.Column([
                ft.Text(header, size=10, weight=ft.FontWeight.W_500, color="primary"),
                ft.Text(title or "-", size=12, weight=ft.FontWeight.W_500, selectable=True),
                ft.Text(content or "-", size=11, color="onSurfaceVariant", max_lines=6,
                        overflow=ft.TextOverflow.ELLIPSIS, selectable=True),
            ], spacing=3),
            padding=8,
            bgcolor=with_opacity(0.05, "primary"),
            border_radius=6,
        )

    def _bump_section(self, r) -> ft.Control:
        """自顶状态：是否开启、已自顶次数、最近自顶、下次执行时间、停止按钮。"""
        if r.is_auto_bump:
            status_text, status_color = "已开启", "green"
        else:
            status_text, status_color = "未开启", "onSurfaceVariant"

        mode_label = _BUMP_MODE_LABEL.get(r.bump_mode, r.bump_mode)

        async def _apply_settings_and_estimate():
            max_bump, cooldown = 20, 45
            try:
                max_bump = int(await self.db.get_setting("max_bump_count", "20"))
                cooldown = int(await self.db.get_setting("bump_cooldown_minutes", "45"))
            except Exception:
                pass
            return max_bump, cooldown

        next_text = ft.Text("", size=12)

        # 下次执行时间在面板打开时异步计算：先给占位，再由 run_task 填充
        async def _fill_next(_=None):
            max_bump, cooldown = await _apply_settings_and_estimate()
            row = self._detail_row
            if not row or row.tid != r.tid:
                return
            next_text.value = estimate_next_bump(
                is_auto_bump=row.is_auto_bump,
                bump_mode=row.bump_mode,
                bump_count=row.bump_count,
                last_bumped_at=row.last_bumped_at,
                bump_hour=row.bump_hour,
                bump_duration_days=row.bump_duration_days,
                bump_start_date=row.bump_start_date,
                max_bump_count=max_bump,
                cooldown_minutes=cooldown,
            )
            try:
                next_text.update()
            except Exception:
                pass

        self.page.run_task(_fill_next)

        rows = [
            self._kv_row("自顶状态", status_text, value_color=status_color),
            self._kv_row("自顶模式", mode_label),
            self._kv_row("已自顶次数", str(r.bump_count)),
            self._kv_row("最近自顶", fmt_time(r.last_bumped_at)),
            ft.Row([
                ft.Container(content=ft.Text("下次执行", size=11, color="onSurfaceVariant"), width=76),
                next_text,
            ], spacing=8),
        ]

        if r.is_auto_bump:
            rows.append(ft.TextButton(
                "停止自顶", icon=STOP_CIRCLE_ROUNDED, icon_color="error",
                tooltip="关闭该帖的自动回帖(自顶)开关",
                on_click=lambda e: self.page.run_task(self._detail_toggle_bump, False),
            ))
        else:
            rows.append(ft.TextButton(
                "开启自顶", icon=AUTO_AWESOME, icon_color="primary",
                tooltip="开启该帖的自动回帖(自顶)开关",
                on_click=lambda e: self.page.run_task(self._detail_toggle_bump, True),
            ))

        # 自顶历史（异步拉取 bump_logs 流水填充）
        self._bump_history_col = ft.Column(spacing=4)
        self.page.run_task(self._fill_bump_history)

        return ft.Column([
            self._section_title("自动回帖 / 自顶"),
            ft.Container(
                content=ft.Column(rows, spacing=4),
                padding=8,
                bgcolor=with_opacity(0.04, "onSurface"),
                border_radius=6,
            ),
            self._section_title("自顶历史"),
            ft.Container(
                content=self._bump_history_col,
                padding=8,
                bgcolor=with_opacity(0.04, "onSurface"),
                border_radius=6,
            ),
        ], spacing=4)

    async def _fill_bump_history(self):
        """拉取当前物料的自顶流水（bump_logs），填充到详情面板历史区。"""
        row = self._detail_row
        if not row or row.src != "material" or not self.db:
            return
        material_id = row.material_id
        try:
            logs = await self.db.get_bump_logs(material_id=material_id, limit=20)
        except Exception:
            logs = []
        # 面板可能已切换/关闭
        if not self._detail_row or self._detail_row.material_id != material_id:
            return

        if not logs:
            self._bump_history_col.controls = [ft.Text(
                "暂无流水记录（自本版本起逐次记录）",
                size=11, color="onSurfaceVariant", italic=True,
            )]
        else:
            controls = []
            for lg in logs:
                ok = bool(lg.success)
                icon, color = (CHECK_CIRCLE, "green") if ok else (ERROR, "error")
                detail_text = (lg.content or "") if ok else (lg.message or "失败")
                controls.append(ft.Column([
                    ft.Row([
                        ft.Icon(icon, size=12, color=color),
                        ft.Text(fmt_time(lg.created_at), size=11, color="onSurfaceVariant"),
                        ft.Text(lg.account_name or "-", size=11, color="onSurfaceVariant",
                                expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=6),
                    ft.Container(
                        content=ft.Text(detail_text, size=11,
                                        color=color if not ok else "onSurface",
                                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        padding=ft.padding.only(left=18),
                    ),
                ], spacing=1))
            self._bump_history_col.controls = controls
        try:
            self._bump_history_col.update()
        except Exception:
            pass  # 面板已卸载等场景

    # ---------- 面板内操作 ----------

    async def _detail_recheck(self, e=None):
        from ....core.post import check_post_survival

        row = self._detail_row
        if not row or row.src != "material" or not row.tid:
            return
        try:
            status, reason = await check_post_survival(row.tid)
        except Exception:
            # 异常文本不写入 death_reason（列宽有限且非可确认原因）
            status, reason = "dead", "error"
        await self.db.update_material_survival_status(row.material_id, status, reason)
        label = "存活" if status == "alive" else ("阵亡(检测异常，疑似删除)" if reason == "error" else f"阵亡({reason or '未知'})")
        self._show_snackbar(f"检测完成: {label}", "success" if status == "alive" else "warning")
        await self._close_and_refresh()

    async def _detail_toggle_bump(self, enabled: bool):
        row = self._detail_row
        if not row or row.src != "material":
            return
        ok = await self.db.set_material_auto_bump(row.material_id, enabled)
        if ok:
            self._show_snackbar("已开启自顶" if enabled else "已停止自顶", "success")
        else:
            self._show_snackbar("操作失败：物料不存在", "error")
        await self._close_and_refresh()

    async def _detail_remove_local(self, e=None):
        row = self._detail_row
        if not row:
            return
        await self._remove_row_local(row)
        self.close_detail()
        self.page.update()

    async def _detail_delete_server(self, e=None):
        row = self._detail_row
        if not row:
            return
        await self._delete_row_server(row)
        self.close_detail()
        self.page.update()
