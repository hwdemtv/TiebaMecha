"""实时日志流组件：订阅 core.logger 队列并渲染为日志行。

此前 dashboard / rules / settings 三页各维护一份几乎相同的
“队列监听 + 行渲染 + 上限截断”实现，本组件是其单源替换。
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

import flet as ft

from ...core.logger import get_log_queue, get_recent_logs
from ..utils import with_opacity


class LogStreamView(ft.Container):
    """日志流视图。

    Args:
        filter_fn: 可选过滤函数，返回 True 的日志才渲染（如按 "[AutoRule]" 前缀）。
        max_rows: 保留的最大行数（新日志插入顶部，超限弹出尾部）。
        badge_color_fn: 可选的徽章配色函数 (log_entry) -> color；缺省按级别着色。
        history_count: start() 时回放的历史日志条数，0 表示不回放。
    """

    def __init__(
        self,
        filter_fn: Optional[Callable[[dict], bool]] = None,
        max_rows: int = 100,
        badge_color_fn: Optional[Callable[[dict], str]] = None,
        history_count: int = 0,
        **kwargs,
    ):
        self.filter_fn = filter_fn
        self.max_rows = max_rows
        self.badge_color_fn = badge_color_fn
        self.history_count = history_count
        self.list_view = ft.ListView(expand=True, spacing=5, padding=10)
        self._running = False
        self._task = None
        super().__init__(content=self.list_view, expand=True, **kwargs)

    # ── 生命周期 ──

    async def start(self, page: ft.Page):
        """回放历史（可选）并启动队列监听任务（幂等）。"""
        if self._running:
            return
        self._running = True
        if self.history_count > 0:
            try:
                for entry in await get_recent_logs(self.history_count):
                    self.append(entry)
            except Exception:
                pass
            page.update()
        self._task = page.run_task(self._listen, page)

    def stop(self):
        """停止监听并取消任务。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _listen(self, page: ft.Page):
        queue = get_log_queue()
        try:
            while self._running:
                log_entry = await queue.get()
                if self._running and (self.filter_fn is None or self.filter_fn(log_entry)):
                    self.append(log_entry)
                    page.update()
                queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    # ── 渲染 ──

    @staticmethod
    def _default_badge_color(log_entry: dict) -> str:
        if log_entry.get("level") == "ERROR":
            return "error"
        if log_entry.get("level") == "WARN":
            return "secondary"
        return "primary"

    def append(self, log_entry: dict):
        """渲染一条日志（插入顶部，超限截断）。"""
        color = (
            self.badge_color_fn(log_entry)
            if self.badge_color_fn
            else self._default_badge_color(log_entry)
        )
        row = ft.Row(
            [
                ft.Text(
                    f"[{log_entry.get('time', '')}]",
                    size=10,
                    color="onSurfaceVariant",
                    font_family="Consolas",
                ),
                ft.Container(
                    content=ft.Text(
                        log_entry.get("level", ""),
                        size=9,
                        weight=ft.FontWeight.BOLD,
                        color="black",
                    ),
                    bgcolor=color,
                    padding=ft.padding.symmetric(horizontal=4, vertical=1),
                    border_radius=3,
                ),
                ft.Text(
                    log_entry.get("message", ""),
                    size=11,
                    color="onSurface",
                    expand=True,
                ),
            ],
            spacing=10,
        )
        self.list_view.controls.insert(0, row)
        if len(self.list_view.controls) > self.max_rows:
            self.list_view.controls.pop()
