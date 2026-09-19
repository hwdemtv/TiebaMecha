"""LogStream：批量发帖流水（运行日志）单源组件。

此前流水渲染/筛选/统计/清除/DB 回放逻辑内嵌在批量发帖页，
配置页与运行中心都需要它：配置页仅作即时执行监视（无工具栏），
运行中心承载完整流水视图（筛选/统计/清除/刷新/拦截详情）。
本模块是两者的单源实现，保持既有内部 API（_add_log/_log_raw_items 等）语义。
"""

from __future__ import annotations

from datetime import datetime

import flet as ft

from ...flet_compat import COLORS
from ...utils import with_opacity
from ...components import icons


def format_log_timestamp(dt_or_str) -> str:
    """格式化流水时间戳：当天显示 HH:MM:SS，跨天显示 MM-DD HH:MM"""
    if isinstance(dt_or_str, str):
        return dt_or_str
    now = datetime.now()
    if dt_or_str.date() == now.date():
        return dt_or_str.strftime("%H:%M:%S")
    return dt_or_str.strftime("%m-%d %H:%M")


class LogStream:
    """结构化流水：卡片渲染 + 筛选缓存 + 统计 + DB 回放。"""

    def __init__(self, page, db=None, show_snackbar=None, resolve_account=None,
                 with_toolbar: bool = True):
        self.page = page
        self.db = db
        self._show_snackbar = show_snackbar or (lambda *a, **k: None)
        self._resolve_account = resolve_account or (lambda aid: f"账号-{aid}")
        self.raw_items: list = []          # (log_item_control, status_str)

        self.log_list = ft.ListView(expand=True, spacing=5, padding=10)
        self.stats_text = ft.Text("✅0  ❌0  ⏭0", size=11, color="onSurfaceVariant",
                                  weight=ft.FontWeight.W_500)
        self.filter_dropdown = ft.Dropdown(
            width=120, height=48, text_size=13,
            options=[
                ft.dropdown.Option("key", "⚠️ 异常/关键"),
                ft.dropdown.Option("all", "全部"),
                ft.dropdown.Option("success", "✅ 成功"),
                ft.dropdown.Option("error", "❌ 失败"),
                ft.dropdown.Option("skipped", "⏭ 跳过"),
            ],
            value="key",
            tooltip="默认只展示异常与关键节点，切到“全部”查看完整流水",
            on_change=self.on_filter_change,
        )
        self.clear_btn = ft.OutlinedButton("清除流水", icon=icons.DELETE_SWEEP,
                                           on_click=self.clear_logs,
                                           style=ft.ButtonStyle(color="error"))
        self.refresh_btn = ft.IconButton(icons.REFRESH, icon_size=18, tooltip="刷新流水",
                                         on_click=self.refresh)
        self.with_toolbar = with_toolbar

    # ------------------------------------------------------------------
    @property
    def filter_value(self) -> str:
        """当前筛选值，以下拉框为单一数据源（默认只看异常/关键节点）。"""
        value = getattr(self.filter_dropdown, "value", None)
        return value if value else "key"

    @staticmethod
    def matches_filter(status_str: str, filter_val: str) -> bool:
        """流水条目与筛选值匹配。"key"=异常+关键节点（默认视图）。"""
        if filter_val == "all":
            return True
        if filter_val == "key":
            return status_str not in ("success", "info")
        if filter_val == "error":
            return status_str not in ("success", "skipped")
        return status_str == filter_val

    def add(self, data, type="info", timestamp=None):
        """结构化日志输出。data 可以是纯字符串（任务级公告）或业务元数据字典。"""
        now = timestamp if timestamp else datetime.now().strftime("%H:%M:%S")

        status_str = "info"  # 用于筛选的 status 标识

        if isinstance(data, dict):
            status = data.get("status", "info")
            status_str = status
            if status == "success":
                # 结构化成功卡片
                acc_name = data.get("account_name", "?")
                fname = data.get("fname", "?")
                title = (data.get("title") or "无标题")[:20]
                tid = data.get("tid", 0)
                prog = f"{data.get('progress')}/{data.get('total')}"

                log_item = ft.Container(
                    content=ft.Row([
                        ft.Text(f"[{now}]", size=10, color="onSurfaceVariant", weight=ft.FontWeight.W_300),
                        ft.Icon(icons.CHECK_CIRCLE, color="green", size=14),
                        ft.VerticalDivider(width=1),
                        ft.Row([
                            ft.Icon(icons.PERSON, size=12, color="orange"),
                            ft.Text(acc_name, size=11, weight=ft.FontWeight.BOLD, color="orange"),
                        ], spacing=2),
                        ft.Row([
                            ft.Icon(icons.FORUM, size=12, color="primary"),
                            ft.Text(fname, size=11, weight=ft.FontWeight.BOLD, color="primary"),
                        ], spacing=2),
                        ft.Text(f"「{title}」", size=11, color="onSurface", italic=True),
                        ft.Container(expand=True),
                        ft.Text(prog, size=10, color="onSurfaceVariant", weight=ft.FontWeight.BOLD),
                        ft.IconButton(
                            icons.OPEN_IN_NEW,
                            icon_size=14,
                            tooltip="在浏览器中开启",
                            icon_color="primary",
                            on_click=lambda _: self.page.launch_url(f"https://tieba.baidu.com/p/{tid}")
                        )
                    ], spacing=10),
                    padding=ft.padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=with_opacity(0.05, "green"),
                    border=ft.border.only(left=ft.border.BorderSide(3, "green")),
                    border_radius=ft.border_radius.only(top_right=8, bottom_right=8),
                    margin=ft.padding.only(bottom=5)
                )
            elif status == "skipped":
                # 结构化跳过卡片 (琥珀色)
                fname = data.get("fname", "未知")
                msg = data.get("msg", data.get("message", "已跳过"))
                log_item = ft.Container(
                    content=ft.Row([
                        ft.Text(f"[{now}]", size=10, color="onSurfaceVariant"),
                        ft.Icon(icons.SKIP_NEXT, color="amber", size=14),
                        ft.Text(f"跳过 [{fname}]: {msg}", size=11, color="amber", weight=ft.FontWeight.W_500),
                    ], spacing=10),
                    padding=ft.padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=with_opacity(0.05, "amber"),
                    border=ft.border.only(left=ft.border.BorderSide(3, "amber")),
                    border_radius=ft.border_radius.only(top_right=8, bottom_right=8),
                    margin=ft.padding.only(bottom=5)
                )
            else:
                # 结构化错误卡片
                fname = data.get("fname", "未知")
                msg = data.get("msg", "执行异常")
                log_item = ft.Container(
                    content=ft.Row([
                        ft.Text(f"[{now}]", size=10, color="onSurfaceVariant"),
                        ft.Icon(icons.ERROR_OUTLINE, color="error", size=14),
                        ft.Text(f"拦截于 [{fname}]: {msg}", size=11, color="error", weight=ft.FontWeight.W_500),
                        ft.Container(expand=True),
                        ft.TextButton(
                            ft.Text("查看情报", size=10),
                            style=ft.ButtonStyle(color="error"),
                            on_click=lambda e: self.show_rejection_detail(data)
                        )
                    ], spacing=10),
                    padding=ft.padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=with_opacity(0.05, "error"),
                    border=ft.border.only(left=ft.border.BorderSide(3, "error")),
                    border_radius=ft.border_radius.only(top_right=8, bottom_right=8),
                    margin=ft.padding.only(bottom=5)
                )
        else:
            # 兼容模式：纯文本输出（均为任务级公告，归入"异常/关键"视图）
            color = "onSurfaceVariant" if type == "info" else "error"
            icon = icons.INFO_OUTLINED if type == "info" else icons.WARNING_AMBER
            status_str = "key"

            log_item = ft.Container(
                content=ft.Row([
                    ft.Text(f"[{now}]", size=10, color="onSurfaceVariant"),
                    ft.Icon(icon, color=color, size=12),
                    ft.Text(str(data), size=11, color=color),
                ], spacing=10),
                padding=ft.padding.symmetric(horizontal=12, vertical=4),
                margin=ft.padding.only(bottom=2)
            )

        # 存入原始缓存
        self.raw_items.insert(0, (log_item, status_str))
        if len(self.raw_items) > 100:
            self.raw_items.pop()

        # 根据当前筛选决定是否插入可见列表
        if self.matches_filter(status_str, self.filter_value):
            self.log_list.controls.insert(0, log_item)
            if len(self.log_list.controls) > 100:
                self.log_list.controls.pop()

        self.update_stats()

    # ------------------------------------------------------------------
    def update_stats(self):
        """更新流水统计文本"""
        success_count = sum(1 for _, s in self.raw_items if s == "success")
        error_count = sum(1 for _, s in self.raw_items if s == "error")
        skipped_count = sum(1 for _, s in self.raw_items if s == "skipped")
        self.stats_text.value = f"✅{success_count}  ❌{error_count}  ⏭{skipped_count}"
        try:
            self.stats_text.update()
        except Exception:
            pass

    def clear_ui(self):
        """仅清空 UI（缓存 + 可见列表），不动数据库。"""
        self.log_list.controls.clear()
        self.raw_items.clear()
        self.update_stats()

    async def on_filter_change(self, e):
        """流水筛选下拉框变更（从事件读取新值并重放可见列表）"""
        new_value = getattr(getattr(e, "control", None), "value", None)
        if new_value:
            try:
                self.filter_dropdown.value = new_value
            except Exception:
                pass
        self.log_list.controls.clear()
        for log_item, status in reversed(self.raw_items):
            if self.matches_filter(status, self.filter_value):
                self.log_list.controls.insert(0, log_item)
        try:
            self.log_list.update()
        except Exception:
            pass  # 控件未挂载（单测环境）时静默

    async def clear_logs(self, e=None):
        """清除流水记录（UI + 数据库）"""
        self.clear_ui()
        try:
            deleted = await self.db.clear_old_batch_post_logs(keep_count=0)
            self._show_snackbar(f"已清除 {deleted} 条流水记录", "info")
        except Exception as ex:
            self._show_snackbar(f"清除失败: {ex}", "error")

    def replay_db_logs(self, logs):
        """把数据库流水记录回放进视图（清空后重放）。"""
        self.clear_ui()
        for log in reversed(logs):
            extra = {}
            try:
                import json as _json
                extra = _json.loads(log.data_json) if log.data_json else {}
            except Exception:
                pass
            log_data = {
                "status": "skipped" if log.status == "skip" else log.status,
                "account_name": log.account_name,
                "fname": log.fname,
                "title": log.title,
                "tid": log.tid,
                "msg": log.message,
                "error": log.message,
                "account_id": log.account_id,
                "progress": extra.get("progress", "-"),
                "total": extra.get("total", "-"),
            }
            self.add(log_data, timestamp=format_log_timestamp(log.created_at))

    async def refresh(self, e=None):
        """刷新流水记录（重新从数据库加载）"""
        if not self.db:
            return
        try:
            logs = await self.db.get_batch_post_logs(limit=100)
            self.replay_db_logs(logs)
            try:
                self.log_list.update()
            except Exception:
                pass  # 控件未挂载（单测环境）时静默
            self._show_snackbar("流水已刷新", "info")
        except Exception as ex:
            self._show_snackbar(f"刷新失败: {ex}", "error")

    # ------------------------------------------------------------------
    def show_rejection_detail(self, e):
        """显示拒稿的具体原因弹窗 (增强版：附带战术建议 + 账号/贴吧详情)"""
        # 兼容处理：既支持 Flet 事件，也支持直接传入数据字典
        if hasattr(e, "control") and hasattr(e.control, "data"):
            data = e.control.data
        else:
            data = e

        if isinstance(data, dict):
            error_msg = data.get("error") or "未知拒稿原因"
            account_id = data.get("account_id") or "未知"
            fname = data.get("fname") or "未知吧"
        else:
            error_msg = data or "未知拒稿原因"
            account_id = "未知"
            fname = "未知吧"

        account_display = self._resolve_account(account_id)

        # 获取战术建议
        from ....core.batch_post import BatchPostManager
        advice = BatchPostManager.get_tactical_advice(error_msg)

        confirm_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.ERROR_OUTLINE, color="error"), ft.Text("发帖被拦截详情 / INTERCEPTED")]),
            content=ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Container(
                            content=ft.Row([ft.Icon(icons.FORUM, size=12, color="primary"), ft.Text(f"吧名: {fname}", size=11, weight=ft.FontWeight.W_500)], spacing=5),
                            padding=ft.padding.symmetric(horizontal=8, vertical=4),
                            bgcolor=with_opacity(0.1, "primary"),
                            border_radius=4
                        ),
                        ft.Container(
                            content=ft.Row([ft.Icon(icons.PERSON, size=12, color="orange"), ft.Text(f"账号: {account_display}", size=11, weight=ft.FontWeight.W_500)], spacing=5),
                            padding=ft.padding.symmetric(horizontal=8, vertical=4),
                            bgcolor=with_opacity(0.1, "orange"),
                            border_radius=4
                        ),
                    ], spacing=10),
                    ft.Divider(height=10, color="transparent"),
                    ft.Text("原始错误信息 / RAW ERROR:", size=12, weight=ft.FontWeight.W_500, color="onSurfaceVariant"),
                    ft.Container(
                        content=ft.Text(error_msg, selectable=True, color="error", size=13),
                        padding=10,
                        bgcolor=with_opacity(0.1, "error"),
                        border_radius=5
                    ),
                    ft.Divider(height=10, color="transparent"),
                    ft.Row([ft.Icon(icons.SHIELD_ROUNDED, color="green", size=16), ft.Text("战术情报分析 / STRATEGY", size=12, weight=ft.FontWeight.BOLD)]),
                    ft.Text(f"【拦截诱因】: {advice['reason']}", size=12, color="onSurface"),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("【操作指导】:", size=11, color="green", weight=ft.FontWeight.BOLD),
                            ft.Text(advice['action'], size=11, color="onSurfaceVariant"),
                        ], tight=True, spacing=5),
                        padding=10,
                        bgcolor=with_opacity(0.05, "green"),
                        border=ft.border.all(1, with_opacity(0.2, "green")),
                        border_radius=8
                    )
                ], tight=True, spacing=10),
                width=450,
            ),
            actions=[
                ft.TextButton("我已知晓", on_click=lambda _: self.page.close(confirm_dialog))
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(confirm_dialog)

    # ------------------------------------------------------------------
    def build_toolbar(self) -> ft.Control:
        """筛选 + 统计 + 刷新 + 清除 工具栏"""
        return ft.Row([
            ft.Text("流水筛选:", size=11, color="onSurfaceVariant", weight=ft.FontWeight.W_500),
            self.filter_dropdown,
            self.stats_text,
            ft.Container(expand=True),
            self.refresh_btn,
            self.clear_btn,
        ], alignment=ft.MainAxisAlignment.START, spacing=10)

    def build_view(self, expand: bool = True) -> ft.Control:
        """完整流水视图（工具栏 + 列表容器）"""
        return ft.Container(
            content=ft.Column([
                self.build_toolbar(),
                ft.Container(
                    content=self.log_list, expand=True,
                    border=ft.border.all(1, with_opacity(0.1, "onSurface")), border_radius=10,
                )
            ], expand=True), expand=expand, padding=10
        )
