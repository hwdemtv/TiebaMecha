"""Automation rules management page with Execution Logs"""

import asyncio
import flet as ft
from ..flet_compat import COLORS
from typing import List, Optional

from ..components import create_gradient_button
from ..utils import with_opacity
from ...core.logger import get_recent_logs, get_log_queue
from ..components.icons import (
    ARROW_BACK_IOS_NEW, RULE_ROUNDED, NO_ENCRYPTION_ROUNDED,
    TERMINAL, KEYBOARD, DELETE_OUTLINE, CHECK, LIST_ALT_ROUNDED
)


class RulesPage:
    """自动化规则管理页面 - 增加日志 Tab"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._rules = []
        self._log_task = None
        self._log_task_running = False

    async def load_data(self):
        """加载数据"""
        if self.db:
            self._rules = await self.db.get_auto_rules()
            
            # 加载历史日志
            recent_history = await get_recent_logs(100)
            if hasattr(self, "log_list"):
                self.log_list.controls.clear()
                for log_entry in recent_history:
                    if "[AutoRule]" in log_entry["message"]:
                        self._add_single_log_ui(log_entry)

            # 开启实时日志监听
            if not self._log_task_running:
                self._log_task_running = True
                self._log_task = self.page.run_task(self._listen_logs)

            self.refresh_ui()

    def refresh_ui(self):
        if hasattr(self, "rules_container"):
            self.rules_container.controls = self._build_rule_items()
            self.page.update()

    def build(self) -> ft.Control:
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=ARROW_BACK_IOS_NEW,
                        icon_size=16,
                        on_click=lambda e: self._navigate("dashboard"),
                        style=ft.ButtonStyle(color=COLORS.PRIMARY, bgcolor={"": with_opacity(0.1, COLORS.PRIMARY)}),
                    ),
                    padding=5,
                ),
                ft.Column(
                    controls=[
                        ft.Text("自动化规则 / AUTO RULES", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("基于关键字或正则的自动删帖与监控执行", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
                ft.Container(expand=True),
                create_gradient_button(
                    text="添加新规则",
                    icon=RULE_ROUNDED,
                    on_click=self._show_add_dialog,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # 1. 规则列表 Tab
        self.rules_container = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
        rules_tab = ft.Column([
            ft.Divider(height=10, color="transparent"),
            ft.Text("生效中的防御规则", size=14, weight=ft.FontWeight.W_500),
            self.rules_container,
        ], expand=True)

        # 2. 执行日志 Tab
        self.log_list = ft.ListView(expand=True, spacing=5, padding=10)
        logs_tab = ft.Column([
            ft.Divider(height=10, color="transparent"),
            ft.Text("最近执行记录 / EXECUTION LOGS", size=14, weight=ft.FontWeight.W_500),
            ft.Container(
                content=self.log_list,
                expand=True,
                border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                border_radius=10,
                bgcolor=with_opacity(0.01, "surface"),
            ),
        ], expand=True)

        self.tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            tabs=[
                ft.Tab(text="规则管理", icon=RULE_ROUNDED, content=rules_tab),
                ft.Tab(text="执行日志", icon=LIST_ALT_ROUNDED, content=logs_tab),
            ],
            expand=True,
        )

        return ft.Container(
            content=ft.Column([header, self.tabs], spacing=10),
            padding=20, expand=True,
        )

    def _build_rule_items(self) -> list[ft.Control]:
        items = []
        if not self._rules:
            items.append(ft.Container(content=ft.Column([
                ft.Icon(NO_ENCRYPTION_ROUNDED, size=50, color="onSurfaceVariant"),
                ft.Text("暂无自动化规则", color="onSurfaceVariant"),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER), padding=50, alignment=ft.alignment.center))
            return items

        for r in self._rules:
            is_active = r.is_active
            card = ft.Container(
                content=ft.Row([
                    ft.Container(
                        content=ft.Icon(TERMINAL if r.rule_type == "regex" else KEYBOARD,
                                      color="primary" if is_active else "onSurfaceVariant", size=24),
                        padding=10, bgcolor=with_opacity(0.05, "primary" if is_active else "onSurface"), border_radius=8,
                    ),
                    ft.Column([
                        ft.Row([
                            ft.Text(f"[{r.fname}]", color="primary", size=12, weight=ft.FontWeight.BOLD),
                            ft.Text(r.pattern, color="onSurface", size=14, weight=ft.FontWeight.W_500),
                        ], spacing=8),
                        ft.Row([
                            ft.Text(f"模式: {r.rule_type}", color="onSurfaceVariant", size=11),
                            ft.Container(width=10),
                            ft.Text(f"动作: {r.action}", color="secondary" if r.action == "delete" else "primary", size=11),
                        ], spacing=4),
                    ], spacing=4, expand=True),
                    ft.Switch(value=is_active, active_color="primary",
                              on_change=lambda e, rid=r.id: self.page.run_task(self._toggle_rule, rid, e.control.value)),
                    ft.IconButton(icon=DELETE_OUTLINE, icon_color="error",
                                  on_click=lambda e, rid=r.id: self.page.run_task(self._delete_rule, rid)),
                ]),
                bgcolor=with_opacity(0.02, "primary" if is_active else "onSurface"),
                border=ft.border.all(1, with_opacity(0.1, "primary" if is_active else "onSurface")),
                border_radius=10, padding=10,
            )
            items.append(card)
        return items

    def _add_single_log_ui(self, log_entry):
        color = COLORS.GREEN if "已删除" in log_entry["message"] or "命中" in log_entry["message"] else "primary"
        log_row = ft.Row([
            ft.Text(f"[{log_entry['time']}]", size=10, color="onSurfaceVariant", font_family="Consolas"),
            ft.Container(content=ft.Text(log_entry["level"], size=9, weight=ft.FontWeight.BOLD, color="black"),
                         bgcolor=color, padding=ft.padding.symmetric(horizontal=4, vertical=1), border_radius=3),
            ft.Text(log_entry["message"], size=11, color="onSurface", expand=True),
        ], spacing=10)
        self.log_list.controls.insert(0, log_row)
        if len(self.log_list.controls) > 100: self.log_list.controls.pop()

    async def _listen_logs(self):
        queue = get_log_queue()
        try:
            while self._log_task_running:
                log_entry = await queue.get()
                if self._log_task_running and "[AutoRule]" in log_entry["message"]:
                    if hasattr(self, "log_list"):
                        self._add_single_log_ui(log_entry)
                        self.page.update()
                queue.task_done()
        except asyncio.CancelledError: pass
        finally: self._log_task_running = False

    def _show_add_dialog(self, e):
        fname_f = ft.TextField(label="作用贴吧 (支持多个以逗号分隔)", hint_text="例如: c++吧,python吧")
        rule_type_f = ft.Dropdown(label="匹配类型", options=[ft.dropdown.Option("keyword", "关键字匹配"), ft.dropdown.Option("regex", "正则表达式")], value="keyword")
        pattern_f = ft.TextField(label="匹配内容/正则模式", hint_text="输入您想拦截的内容")
        action_f = ft.Dropdown(label="触发动作", options=[ft.dropdown.Option("delete", "直接删除"), ft.dropdown.Option("notify", "仅通知不处理")], value="delete")

        async def on_submit(e):
            if self.db and fname_f.value and pattern_f.value:
                fnames = [f.strip() for f in fname_f.value.split(",") if f.strip()]
                for fn in fnames:
                    await self.db.add_auto_rule(fn, rule_type_f.value, pattern_f.value, action_f.value)
                self.page.close(dialog)
                await self.load_data()
                self._show_snackbar(f"已添加 {len(fnames)} 条自动化规则", "success")

        dialog = ft.AlertDialog(title=ft.Row([ft.Icon(RULE_ROUNDED, color="primary"), ft.Text("新建防御规则")]),
                                content=ft.Column([ft.Text("配置自动化执行脚本:", size=12, color="onSurfaceVariant"), fname_f, rule_type_f, pattern_f, action_f], tight=True, spacing=15, width=450),
                                actions=[ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)), ft.FilledButton("创建规则", icon=CHECK, on_click=on_submit)])
        self.page.open(dialog)

    async def _toggle_rule(self, rid: int, val: bool):
        if self.db: await self.db.toggle_rule(rid, val); await self.load_data()

    async def _delete_rule(self, rid: int):
        if self.db: await self.db.delete_auto_rule(rid); await self.load_data(); self._show_snackbar("规则已移除", "info")

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = COLORS.GREEN if type=="success" else "error" if type=="error" else "primary"
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))

    def cleanup(self):
        if self._log_task and not self._log_task.done(): self._log_task.cancel()
        self._log_task_running = False
