"""存活分析页面 - 查看物料存活状态，支持筛选和分页，集成行为审计"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Callable

import flet as ft
from ..flet_compat import COLORS
from ..utils import with_opacity
from ..components.icons import (
    ARROW_BACK_IOS_NEW, ANALYTICS_OUTLINED, CHECK_CIRCLE,
    ERROR, REMOVE_CIRCLE_OUTLINED, OPEN_IN_NEW, INFO_OUTLINED,
    DELETE_OUTLINE, SHIELD_ROUNDED, WARNING_AMBER_ROUNDED,
    VERIFIED_ROUNDED, SPEED_ROUNDED, SCHEDULE_ROUNDED,
    MEMORY_ROUNDED, VPN_KEY_ROUNDED, TRENDING_UP_ROUNDED,
    MONITOR_HEART_ROUNDED
)

if TYPE_CHECKING:
    from tieba_mecha.db.crud import Database

# 常量定义
_PAGE_SIZE = 15  # 每页显示条数
_MAX_ALERTS_DISPLAY = 3  # 最多显示告警数
_MAX_RECOMMENDATIONS_DISPLAY = 2  # 最多显示建议数
_MAX_CONTENT_PREVIEW = 40  # 内容预览最大字符数
_MAX_DETAIL_CONTENT = 500  # 详情弹窗内容最大字符数

# 删帖原因英文代码 → 中文友好显示
_DEATH_REASON_MAP = {
    "deleted_by_system": "🤖 系统风控删除",
    "deleted_by_mod": "👮 吧务手动删除",
    "deleted_by_user": "👤 用户自删",
    "deleted_unknown": "❓ 帖子已删除（原因未明）",
    "banned_by_mod": "👮 吧务删除（触发封禁）",
    "auto_removed": "🗑️ 帖子不存在/已过期",
    "captcha_required": "🔐 验证码拦截",
    "error": "⚠️ 检测异常",
    "unknown_error": "❓ 未知错误",
}


def _death_reason_display(reason: str) -> str:
    """将 death_reason 代码转为中文友好显示"""
    return _DEATH_REASON_MAP.get(reason, reason)


def _parse_date(date_str: str | None, end_of_day: bool = False) -> datetime | None:
    """解析日期字符串

    Args:
        date_str: 日期字符串，格式 YYYY-MM-DD
        end_of_day: 是否设为当天结束时间 (23:59:59)
    """
    if not date_str:
        return None
    try:
        if end_of_day:
            return datetime.strptime(date_str + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None


def _truncate_text(text: str, max_len: int = 500) -> str:
    """按自然断句截断文本"""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # 找最后一个句号/换行
    last_break = max(
        truncated.rfind('。'),
        truncated.rfind('！'),
        truncated.rfind('？'),
        truncated.rfind('\n'),
    )
    if last_break > max_len * 0.8:
        truncated = truncated[:last_break + 1]
    return truncated + f"\n\n... (共 {len(text)} 字)"


def _get_risk_color(score: float) -> tuple[str, str]:
    """返回风险等级对应的颜色和标签

    Returns:
        (颜色代码, 风险等级标签)
    """
    if score >= 5:
        return "#F44336", "高危"
    elif score >= 2.5:
        return "#FF9800", "中危"
    return "#4CAF50", "低危"


class SurvivalPage:
    """存活分析页面"""

    def __init__(self, page: ft.Page, db: Database | None = None, on_navigate: Callable | None = None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._stats: dict[str, int] = {"total": 0, "alive": 0, "dead": 0, "unknown": 0}
        self._account_options: list[ft.dropdown.Option] = []
        self._fname_options: list[ft.dropdown.Option] = []  # 贴吧名选项
        self._death_reason_options: list[ft.dropdown.Option] = []  # 阵亡原因选项
        self._account_name_map: dict[int, str] = {}  # account_id -> name 映射
        self._current_page: int = 1
        self._stat_cards_container: ft.Container | None = None
        self._page_size: int = _PAGE_SIZE
        self._total: int = 0
        self._materials: list = []
        self._audit_reports: list[dict] = []  # 行为审计报告
        self._active_tab = "survival"  # 当前标签页
        self._audit_error = None  # 审计加载错误信息

    def cleanup(self):
        """清理页面资源，导航离开时调用"""
        self._materials = []
        self._audit_reports = []
        self._account_options = []
        self._fname_options = []
        self._death_reason_options = []
        self._account_name_map = {}

    async def load_data(self):
        """加载数据"""
        if not self.db:
            return
        try:
            # 仅首次加载或筛选选项为空时重新加载筛选选项
            if not self._account_options:
                accounts = await self.db.get_accounts()
                self._account_options = [
                    ft.dropdown.Option(str(a.id), a.name or f"账号-{a.id}")
                    for a in accounts
                ]
                # 构建账号 ID -> 名称映射
                self._account_name_map = {a.id: a.name or f"账号-{a.id}" for a in accounts}

            if not self._fname_options:
                fnames = await self.db.get_distinct_fnames()
                self._fname_options = [ft.dropdown.Option(f, f) for f in fnames]

            if not self._death_reason_options:
                reasons = await self.db.get_distinct_death_reasons()
                self._death_reason_options = [
                    ft.dropdown.Option(r, _death_reason_display(r)) for r in reasons
                ]

            # 统计数据每次都需要刷新
            self._stats = await self.db.get_survival_stats()

            await self._load_page(1)
            # 加载行为审计数据
            await self._load_audit_data()
        except Exception as e:
            from ..core.logger import log_error
            await log_error(f"加载存活分析数据失败: {e}")

    async def _load_audit_data(self):
        """加载行为审计报告"""
        try:
            from ...core.behavior_audit import audit_all_accounts
            self._audit_reports = await audit_all_accounts(self.db, days=7)
            self._audit_error = None
        except Exception as e:
            from ..core.logger import log_warn
            await log_warn(f"加载行为审计数据失败: {e}")
            self._audit_reports = []
            self._audit_error = str(e)

    async def _on_tab_change(self, e):
        """切换标签页"""
        self._active_tab = "audit" if e.control.selected_index == 1 else "survival"
        self._tab_panel.content = self._build_active_tab_content()
        self.page.update()

    async def _load_page(self, page: int):
        """加载指定页"""
        self._current_page = page
        status_filter = self._status_filter.value if hasattr(self, "_status_filter") else None
        account_filter = self._account_filter.value if hasattr(self, "_account_filter") else None
        fname_filter = self._fname_filter.value if hasattr(self, "_fname_filter") else None
        death_reason_filter = self._death_reason_filter.value if hasattr(self, "_death_reason_filter") else None
        date_from_filter = self._date_from.value if hasattr(self, "_date_from") else None
        date_to_filter = self._date_to.value if hasattr(self, "_date_to") else None
        account_id = int(account_filter) if account_filter and account_filter != "all" else None
        fname = fname_filter if fname_filter and fname_filter != "all" else None
        death_reason = death_reason_filter if death_reason_filter and death_reason_filter != "all" else None
        status = status_filter if status_filter and status_filter != "all" else None
        # 解析日期
        date_from = _parse_date(date_from_filter)
        date_to = _parse_date(date_to_filter, end_of_day=True)
        materials, total = await self.db.get_materials_paginated(
            survival_status=status,
            account_id=account_id,
            fname=fname,
            death_reason=death_reason,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=self._page_size,
        )
        self._materials = materials
        self._total = total
        self._update_card_list()
        self._update_pagination()

    async def _on_status_change(self, e):
        await self._load_page(1)

    async def _on_account_change(self, e):
        await self._load_page(1)

    async def _on_fname_change(self, e):
        await self._load_page(1)

    async def _on_death_reason_change(self, e):
        await self._load_page(1)

    async def _on_date_change(self, e):
        await self._load_page(1)

    async def _on_prev_page(self, e):
        if self._current_page > 1:
            await self._load_page(self._current_page - 1)

    async def _on_next_page(self, e):
        total_pages = (self._total + self._page_size - 1) // self._page_size
        if self._current_page < total_pages:
            await self._load_page(self._current_page + 1)

    # ========== 统计卡片 ==========

    def _build_stat_cards(self) -> list[ft.Control]:
        """构建统计卡片"""
        stats = self._stats
        total = stats["total"] or 1
        rate = stats["alive"] / total * 100

        def card(icon, label, value, color):
            return ft.Container(
                content=ft.Column([
                    ft.Row([ft.Icon(icon, color=color, size=22), ft.Text(label, size=11, color="onSurfaceVariant")], spacing=5),
                    ft.Text(str(value), size=28, weight=ft.FontWeight.BOLD, color=color),
                ], spacing=2),
                padding=15,
                bgcolor=with_opacity(0.03, color),
                border=ft.border.all(1, with_opacity(0.2, color)),
                border_radius=10,
                expand=1,
            )

        cards = [
            card(CHECK_CIRCLE, "存活 / ALIVE", stats["alive"], COLORS.GREEN),
            card(ERROR, "阵亡 / DEAD", stats["dead"], "error"),
            card(REMOVE_CIRCLE_OUTLINED, "未知 / UNKNOWN", stats["unknown"], "onSurfaceVariant"),
        ]

        # 存活率进度条卡片
        progress_card = ft.Container(
            content=ft.Column([
                ft.Row([ft.Text("存活率 / SURVIVAL", size=11, color="onSurfaceVariant")], spacing=5),
                ft.Text(f"{rate:.1f}%", size=28, weight=ft.FontWeight.BOLD, color=COLORS.PRIMARY),
                ft.Container(
                    content=ft.Stack([
                        ft.Container(
                            width=max(4, min(280, rate / 100 * 280)),
                            height=8,
                            bgcolor=COLORS.PRIMARY,
                            border_radius=4,
                        ),
                        ft.Container(
                            height=8,
                            bgcolor=with_opacity(0.15, "onSurface"),
                            border_radius=4,
                        ),
                    ]),
                    width=280,
                ),
            ], spacing=4),
            padding=15,
            bgcolor=with_opacity(0.03, COLORS.PRIMARY),
            border=ft.border.all(1, with_opacity(0.2, COLORS.PRIMARY)),
            border_radius=10,
            expand=1,
        )
        cards.append(progress_card)
        return cards

    # ========== 筛选栏 ==========

    def _build_filter_bar(self) -> ft.Control:
        """构建筛选栏"""
        self._status_filter = ft.Dropdown(
            label="存活状态",
            value="all",
            width=130,
            text_size=13,
            options=[
                ft.dropdown.Option("all", "全部"),
                ft.dropdown.Option("alive", "存活"),
                ft.dropdown.Option("dead", "阵亡"),
                ft.dropdown.Option("unknown", "未知"),
            ],
            on_change=self._on_status_change,
        )
        self._account_filter = ft.Dropdown(
            label="账号",
            value="all",
            width=160,
            text_size=13,
            options=[ft.dropdown.Option("all", "全部账号")] + self._account_options,
            on_change=self._on_account_change,
        )
        self._fname_filter = ft.Dropdown(
            label="贴吧",
            value="all",
            width=160,
            text_size=13,
            options=[ft.dropdown.Option("all", "全部贴吧")] + self._fname_options,
            on_change=self._on_fname_change,
        )
        self._death_reason_filter = ft.Dropdown(
            label="阵亡原因",
            value="all",
            width=180,
            text_size=13,
            options=[ft.dropdown.Option("all", "全部原因")] + self._death_reason_options,
            on_change=self._on_death_reason_change,
        )
        self._date_from = ft.TextField(
            label="起始日期",
            width=120,
            text_size=12,
            hint_text="YYYY-MM-DD",
            on_submit=self._on_date_change,
        )
        self._date_to = ft.TextField(
            label="结束日期",
            width=120,
            text_size=12,
            hint_text="YYYY-MM-DD",
            on_submit=self._on_date_change,
        )
        return ft.Row(
            [self._status_filter, self._account_filter, self._fname_filter, self._death_reason_filter, self._date_from, self._date_to, ft.IconButton(
                icon="search",
                icon_size=18,
                tooltip="搜索",
                on_click=self._on_date_change,
            )],
            spacing=10,
            wrap=True,
        )

    # ========== 数据回调 ==========

    def on_data_loaded(self):
        """数据加载完成后的回调 - 更新统计卡片和筛选下拉框选项"""
        if self._stat_cards_container:
            self._stat_cards_container.content = ft.Row(self._build_stat_cards(), spacing=10)
        if hasattr(self, "_account_filter") and self._account_options:
            self._account_filter.options = [ft.dropdown.Option("all", "全部账号")] + self._account_options
        if hasattr(self, "_fname_filter") and self._fname_options:
            self._fname_filter.options = [ft.dropdown.Option("all", "全部贴吧")] + self._fname_options
        if hasattr(self, "_death_reason_filter") and self._death_reason_options:
            self._death_reason_filter.options = [ft.dropdown.Option("all", "全部原因")] + self._death_reason_options
        self.page.update()

    # ========== 卡片列表 ==========

    def _build_card_list(self) -> ft.Control:
        """构建卡片列表区域"""
        self._card_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        return ft.Container(
            content=self._card_list,
            expand=True,
            border=ft.border.all(1, with_opacity(0.1, "onSurface")),
            border_radius=8,
            padding=10,
        )

    def _build_material_card(self, m) -> ft.Control:
        """构建单个物料卡片"""
        status_map = {
            "alive": (CHECK_CIRCLE, "存活", COLORS.GREEN),
            "dead": (ERROR, "阵亡", "error"),
            "unknown": (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"),
        }
        icon, label, color = status_map.get(m.survival_status, (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"))

        # 账号名
        account_name = self._account_name_map.get(m.posted_account_id, f"账号-{m.posted_account_id}" if m.posted_account_id else "-")

        # 标题（优先 title，其次 content 截取）
        title = m.title or ""
        if not title and m.content:
            title = m.content[:_MAX_CONTENT_PREVIEW] + ("..." if len(m.content) > _MAX_CONTENT_PREVIEW else "")
        if not title:
            title = "无标题"

        # 时间
        posted_time = m.posted_time.strftime("%m-%d %H:%M") if m.posted_time else "-"

        # 状态标签
        status_badge = ft.Container(
            content=ft.Row([ft.Icon(icon, size=14, color=color), ft.Text(label, size=12, color=color, weight=ft.FontWeight.W_500)], spacing=4),
            bgcolor=with_opacity(0.08, color),
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
        )

        # 顶行：标题 + 状态标签
        top_row = ft.Row([
            ft.Text(title, size=14, weight=ft.FontWeight.W_500, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            status_badge,
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        # 底行：元信息
        meta_items = []
        if m.posted_fname:
            meta_items.append(ft.Row([ft.Icon(INFO_OUTLINED, size=12, color="onSurfaceVariant"), ft.Text(m.posted_fname, size=11, color="onSurfaceVariant")], spacing=3))
        meta_items.append(ft.Row([ft.Icon(INFO_OUTLINED, size=12, color="onSurfaceVariant"), ft.Text(account_name, size=11, color="onSurfaceVariant")], spacing=3))
        meta_items.append(ft.Text(posted_time, size=11, color="onSurfaceVariant"))

        # 阵亡原因
        if m.survival_status == "dead" and m.death_reason:
            reason_text = _death_reason_display(m.death_reason)
            meta_items.append(ft.Text(f"原因: {reason_text}", size=11, color="error", italic=True))

        bottom_row = ft.Row(meta_items, spacing=12, wrap=True)

        card = ft.Container(
            content=ft.Column([top_row, bottom_row], spacing=6),
            bgcolor=with_opacity(0.02, color) if m.survival_status != "unknown" else with_opacity(0.02, "onSurface"),
            border=ft.border.all(1, with_opacity(0.15, color) if m.survival_status != "unknown" else with_opacity(0.1, "onSurface")),
            border_radius=8,
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
            on_click=lambda e, mat=m: self._show_detail(mat),
            ink=True,
        )
        return card

    def _update_card_list(self):
        """更新卡片列表"""
        if not hasattr(self, "_card_list"):
            return
        cards = [self._build_material_card(m) for m in self._materials]
        if not cards:
            cards = [ft.Container(
                content=ft.Text("暂无数据", size=13, color="onSurfaceVariant", text_align=ft.TextAlign.CENTER),
                alignment=ft.alignment.center,
                padding=30,
            )]
        self._card_list.controls = cards
        self.page.update()

    def _show_detail(self, m):
        """点击卡片弹出详情"""
        status_map = {
            "alive": (CHECK_CIRCLE, "存活", COLORS.GREEN),
            "dead": (ERROR, "阵亡", "error"),
            "unknown": (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"),
        }
        icon, label, color = status_map.get(m.survival_status, (REMOVE_CIRCLE_OUTLINED, "未知", "onSurfaceVariant"))
        account_name = self._account_name_map.get(m.posted_account_id, f"账号-{m.posted_account_id}" if m.posted_account_id else "-")

        # 帖子链接
        tid_row = ft.Row([])
        if m.posted_tid and m.posted_fname:
            tid_row = ft.Row([
                ft.Text(f"TID: {m.posted_tid}", size=12, color="onSurfaceVariant"),
                ft.IconButton(
                    icon=OPEN_IN_NEW,
                    icon_size=14,
                    icon_color=COLORS.PRIMARY,
                    tooltip="在浏览器中打开",
                    on_click=lambda e: self.page.launch_url(f"https://tieba.baidu.com/p/{m.posted_tid}"),
                ),
            ])

        content_text = _truncate_text(m.content or "无内容", _MAX_DETAIL_CONTENT)

        detail_items = [
            ft.Row([ft.Icon(icon, color=color, size=18), ft.Text(f"状态: {label}", size=14, color=color, weight=ft.FontWeight.W_500)]),
            ft.Divider(height=1),
            ft.Row([ft.Text("标题:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(m.title or "无", size=12, expand=True, selectable=True)]),
            ft.Row([ft.Text("贴吧:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(m.posted_fname or "-", size=12)]),
            ft.Row([ft.Text("账号:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(account_name, size=12)]),
            tid_row,
            ft.Row([ft.Text("发布时间:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(m.posted_time.strftime("%Y-%m-%d %H:%M") if m.posted_time else "-", size=12)]),
        ]

        # 阵亡原因
        if m.survival_status == "dead" and m.death_reason:
            reason_text = _death_reason_display(m.death_reason)
            detail_items.append(ft.Row([ft.Text("阵亡原因:", size=12, weight=ft.FontWeight.BOLD, color="error"), ft.Text(reason_text, size=12, color="error", expand=True, selectable=True)]))

        # 最后检测时间
        if m.last_checked_at:
            detail_items.append(ft.Row([ft.Text("最后检测:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"), ft.Text(m.last_checked_at.strftime("%Y-%m-%d %H:%M"), size=12)]))

        detail_items.append(ft.Divider(height=1))
        detail_items.append(ft.Text("内容:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"))
        detail_items.append(ft.Container(
            content=ft.Column([ft.Text(content_text, size=12, selectable=True)], scroll=ft.ScrollMode.AUTO, height=180),
            bgcolor=with_opacity(0.03, "onSurface"),
            border_radius=6,
            padding=10,
        ))

        dialog = ft.AlertDialog(
            modal=False,  # 允许点击外部关闭
            title=ft.Row([ft.Icon(icon, color=color), ft.Text(f"物料详情 #{m.id}")]),
            content=ft.Container(
                content=ft.Column(detail_items, spacing=8, scroll=ft.ScrollMode.AUTO),
                width=500,
                height=500,
            ),
            actions=[
                ft.TextButton("删除", icon=DELETE_OUTLINE, on_click=lambda e: self._confirm_delete(m, dialog)),
                ft.TextButton("关闭", on_click=lambda e: self.page.close(dialog)),
            ],
        )
        self.page.open(dialog)

    def _confirm_delete(self, m, parent_dialog):
        """显示删除确认弹窗"""
        self.page.close(parent_dialog)
        confirm = ft.AlertDialog(
            title=ft.Text("确认删除"),
            content=ft.Text(f"确定要删除物料 #{m.id} 吗？此操作不可撤销。"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(confirm)),
                ft.TextButton("删除", style=ft.ButtonStyle(color="error"), on_click=lambda e: self._do_delete(m.id, confirm)),
            ],
        )
        self.page.open(confirm)

    async def _do_delete(self, material_id: int, confirm_dialog):
        """执行删除"""
        self.page.close(confirm_dialog)
        if self.db:
            from ..core.logger import log_info
            await log_info(f"用户删除物料 #{material_id}")
            await self.db.delete_material(material_id)
            # 重新加载统计数据和当前页
            self._stats = await self.db.get_survival_stats()
            if self._stat_cards_container:
                self._stat_cards_container.content = ft.Row(self._build_stat_cards(), spacing=10)
            await self._load_page(self._current_page)

    # ========== 分页控件 ==========

    def _build_pagination(self) -> ft.Control:
        """构建分页控件"""
        self._page_info = ft.Text("", size=12, color="onSurfaceVariant")
        self._prev_btn = ft.IconButton(
            icon=ARROW_BACK_IOS_NEW,
            icon_size=16,
            on_click=self._on_prev_page,
            disabled=True,
        )
        self._next_btn = ft.IconButton(
            icon=ARROW_BACK_IOS_NEW,
            icon_size=16,
            rotate=3.14159,
            on_click=self._on_next_page,
            disabled=True,
        )
        return ft.Row(
            [self._prev_btn, self._page_info, self._next_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
        )

    def _update_pagination(self):
        """更新分页信息"""
        if not hasattr(self, "_page_info"):
            return
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._page_info.value = f"第 {self._current_page} / {total_pages} 页，共 {self._total} 条"
        self._prev_btn.disabled = self._current_page <= 1
        self._next_btn.disabled = self._current_page >= total_pages
        self.page.update()

    # ========== 行为审计 ==========

    def _build_audit_overview(self) -> ft.Control:
        """构建审计概览区（总风险评分 + 高危统计）"""
        reports = self._audit_reports

        # 审计加载失败时显示错误信息
        if self._audit_error:
            return ft.Container(
                content=ft.Column([
                    ft.Icon(ERROR, size=48, color="error"),
                    ft.Text("审计数据加载失败", size=14, color="error"),
                    ft.Text(self._audit_error, size=11, color="onSurfaceVariant"),
                    ft.Text("请检查网络连接或重试", size=11, color="onSurfaceVariant"),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                alignment=ft.alignment.center,
                padding=40,
            )

        if not reports:
            return ft.Container(
                content=ft.Column([
                    ft.Icon(MONITOR_HEART_ROUNDED, size=48, color="onSurfaceVariant"),
                    ft.Text("暂无审计数据", size=14, color="onSurfaceVariant"),
                    ft.Text("需要账号有近 7 天的操作记录才能生成审计报告", size=11, color="onSurfaceVariant"),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                alignment=ft.alignment.center,
                padding=40,
            )

        # 统计汇总
        high_risk = sum(1 for r in reports if r.get("risk_score", 0) >= 5)
        medium_risk = sum(1 for r in reports if 2.5 <= r.get("risk_score", 0) < 5)
        low_risk = sum(1 for r in reports if r.get("risk_score", 0) < 2.5)
        avg_score = sum(r.get("risk_score", 0) for r in reports) / len(reports)

        # 总体评分色
        score_color, _ = _get_risk_color(avg_score)

        overview_cards = ft.Row([
            # 综合风险评分
            ft.Container(
                content=ft.Column([
                    ft.Row([ft.Icon(SHIELD_ROUNDED, color=score_color, size=20), ft.Text("综合风险 / RISK SCORE", size=11, color="onSurfaceVariant")], spacing=5),
                    ft.Text(f"{avg_score:.1f}", size=36, weight=ft.FontWeight.BOLD, color=score_color),
                    ft.Text("/ 10", size=14, color="onSurfaceVariant"),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=15,
                bgcolor=with_opacity(0.05, score_color),
                border=ft.border.all(1, with_opacity(0.2, score_color)),
                border_radius=10,
                expand=1,
                alignment=ft.alignment.center,
            ),
            # 高危
            ft.Container(
                content=ft.Column([
                    ft.Row([ft.Icon(WARNING_AMBER_ROUNDED, color="#F44336", size=20), ft.Text("高危 / HIGH", size=11, color="onSurfaceVariant")], spacing=5),
                    ft.Text(str(high_risk), size=36, weight=ft.FontWeight.BOLD, color="#F44336"),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=15,
                bgcolor=with_opacity(0.05, "#F44336"),
                border=ft.border.all(1, with_opacity(0.2, "#F44336")),
                border_radius=10,
                expand=1,
                alignment=ft.alignment.center,
            ),
            # 中危
            ft.Container(
                content=ft.Column([
                    ft.Row([ft.Icon(INFO_OUTLINED, color="#FF9800", size=20), ft.Text("中危 / MEDIUM", size=11, color="onSurfaceVariant")], spacing=5),
                    ft.Text(str(medium_risk), size=36, weight=ft.FontWeight.BOLD, color="#FF9800"),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=15,
                bgcolor=with_opacity(0.05, "#FF9800"),
                border=ft.border.all(1, with_opacity(0.2, "#FF9800")),
                border_radius=10,
                expand=1,
                alignment=ft.alignment.center,
            ),
            # 低危
            ft.Container(
                content=ft.Column([
                    ft.Row([ft.Icon(VERIFIED_ROUNDED, color="#4CAF50", size=20), ft.Text("低危 / LOW", size=11, color="onSurfaceVariant")], spacing=5),
                    ft.Text(str(low_risk), size=36, weight=ft.FontWeight.BOLD, color="#4CAF50"),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=15,
                bgcolor=with_opacity(0.05, "#4CAF50"),
                border=ft.border.all(1, with_opacity(0.2, "#4CAF50")),
                border_radius=10,
                expand=1,
                alignment=ft.alignment.center,
            ),
        ], spacing=10)

        return overview_cards

    def _build_audit_report_card(self, report: dict) -> ft.Control:
        """构建单个账号审计报告卡片"""
        score = report.get("risk_score", 0)
        alerts = report.get("alerts", [])
        recommendations = report.get("recommendations", [])
        stats = report.get("stats", {})
        name = report.get("account_name", "未知")

        # 风险等级色
        risk_color, risk_label = _get_risk_color(score)
        if score >= 5:
            risk_icon = WARNING_AMBER_ROUNDED
        elif score >= 2.5:
            risk_icon = INFO_OUTLINED
        else:
            risk_icon = VERIFIED_ROUNDED

        # 风险评分进度条
        bar_width = max(4, min(120, score / 10 * 120))

        # 指标徽章
        badges = []
        sign_rate = stats.get("sign_rate", 0)
        if sign_rate > 0:
            badges.append(self._mini_badge("签到率", f"{sign_rate:.0%}",
                          "#4CAF50" if 0.3 <= sign_rate <= 0.95 else "#FF9800"))

        content_variety = stats.get("content_unique_ratio", 1.0)
        badges.append(self._mini_badge("内容多样性", f"{content_variety:.0%}",
                      "#4CAF50" if content_variety >= 0.8 else "#FF9800"))

        avg_interval = stats.get("avg_post_interval_minutes", 0)
        if avg_interval > 0:
            badges.append(self._mini_badge("发帖间隔", f"{avg_interval:.0f}分",
                          "#4CAF50" if avg_interval >= 5 else "#FF9800"))

        proxy_fails = stats.get("proxy_fail_count", 0)
        if proxy_fails > 0:
            badges.append(self._mini_badge("代理失败", str(proxy_fails),
                          "#F44336" if proxy_fails > 5 else "#FF9800"))

        # 告警列表（折叠显示前 N 条）
        alert_controls = []
        for i, alert in enumerate(alerts[:_MAX_ALERTS_DISPLAY]):
            alert_controls.append(ft.Row([
                ft.Icon(WARNING_AMBER_ROUNDED, size=14, color="#FF9800"),
                ft.Text(alert, size=12, color="onSurface", expand=True),
            ], spacing=5))
        if len(alerts) > _MAX_ALERTS_DISPLAY:
            alert_controls.append(ft.Text(f"... 还有 {len(alerts) - _MAX_ALERTS_DISPLAY} 条告警", size=11, color="onSurfaceVariant", italic=True))

        # 建议列表
        rec_controls = []
        for i, rec in enumerate(recommendations[:_MAX_RECOMMENDATIONS_DISPLAY]):
            rec_controls.append(ft.Row([
                ft.Icon(TRENDING_UP_ROUNDED, size=14, color="#4CAF50"),
                ft.Text(rec, size=12, color="onSurface", expand=True),
            ], spacing=5))

        card = ft.Container(
            content=ft.Column([
                # 头行：账号名 + 风险评分
                ft.Row([
                    ft.Icon(risk_icon, color=risk_color, size=20),
                    ft.Text(name, size=14, weight=ft.FontWeight.BOLD, expand=True),
                    ft.Text(f"{score}", size=22, weight=ft.FontWeight.BOLD, color=risk_color),
                    ft.Text(f"/10 {risk_label}", size=12, color=risk_color),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                # 进度条
                ft.Container(
                    content=ft.Stack([
                        ft.Container(width=bar_width, height=6, bgcolor=risk_color, border_radius=3),
                        ft.Container(height=6, bgcolor=with_opacity(0.15, "onSurface"), border_radius=3),
                    ]),
                    width=120,
                ),
                # 指标徽章
                ft.Row(badges, spacing=8, wrap=True) if badges else ft.Container(),
                # 告警
                *alert_controls,
                # 建议
                *rec_controls,
            ], spacing=6),
            bgcolor=with_opacity(0.02, risk_color),
            border=ft.border.all(1, with_opacity(0.15, risk_color)),
            border_radius=10,
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            on_click=lambda e, r=report: self._show_audit_detail(r),
            ink=True,
        )
        return card

    def _mini_badge(self, label: str, value: str, color: str) -> ft.Control:
        """构建指标小徽章"""
        return ft.Container(
            content=ft.Row([
                ft.Text(label, size=10, color="onSurfaceVariant"),
                ft.Text(value, size=11, weight=ft.FontWeight.W_600, color=color),
            ], spacing=3),
            bgcolor=with_opacity(0.08, color),
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=6,
        )

    def _show_audit_detail(self, report: dict):
        """展示账号审计详情弹窗"""
        score = report.get("risk_score", 0)
        alerts = report.get("alerts", [])
        recommendations = report.get("recommendations", [])
        stats = report.get("stats", {})
        name = report.get("account_name", "未知")

        risk_color, risk_label = _get_risk_color(score)

        items = [
            ft.Row([
                ft.Icon(SHIELD_ROUNDED, color=risk_color, size=20),
                ft.Text(f"{name} — 行为审计报告", size=16, weight=ft.FontWeight.BOLD),
            ]),
            ft.Divider(height=1),
            ft.Row([
                ft.Text("风险评分:", size=13, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"),
                ft.Text(f"{score}/10", size=20, weight=ft.FontWeight.BOLD, color=risk_color),
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Text(risk_label, size=13, weight=ft.FontWeight.BOLD, color=risk_color),
                    bgcolor=with_opacity(0.1, risk_color),
                    padding=ft.padding.symmetric(horizontal=12, vertical=4),
                    border_radius=6,
                ),
            ]),
            ft.Divider(height=1),
        ]

        # 详细指标
        metrics = [
            ("签到率", f"{stats.get('sign_rate', 0):.0%}", stats.get('sign_rate', 0) > 0),
            ("总签到数", str(stats.get('total_signs', 0)), stats.get('total_signs', 0) > 0),
            ("总发帖数", str(stats.get('total_posts', 0)), stats.get('total_posts', 0) > 0),
            ("内容多样性", f"{stats.get('content_unique_ratio', 1.0):.0%}", True),
            ("平均发帖间隔", f"{stats.get('avg_post_interval_minutes', 0):.1f} 分钟", stats.get('avg_post_interval_minutes', 0) > 0),
            ("代理失败次数", str(stats.get('proxy_fail_count', 0)), stats.get('proxy_fail_count', 0) > 0),
        ]
        for label, value, show in metrics:
            if show:
                items.append(ft.Row([
                    ft.Text(f"{label}:", size=12, weight=ft.FontWeight.W_500, color="onSurfaceVariant", width=110),
                    ft.Text(value, size=12, expand=True),
                ]))

        # 发帖时间分布
        hour_dist = stats.get("post_hour_distribution", {})
        if hour_dist:
            items.append(ft.Divider(height=1))
            items.append(ft.Text("发帖时间分布:", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"))
            bars = []
            for h in range(24):
                h_str = str(h)
                count = hour_dist.get(h_str, 0)
                bar_h = min(40, count * 4) if count > 0 else 2
                bars.append(ft.Column([
                    ft.Container(
                        width=14, height=max(2, bar_h), bgcolor=COLORS.PRIMARY if count > 0 else with_opacity(0.1, "onSurface"),
                        border_radius=2, alignment=ft.alignment.bottom_center,
                    ),
                    ft.Text(f"{h}", size=8, color="onSurfaceVariant", text_align=ft.TextAlign.CENTER),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2))
            items.append(ft.Row(bars, spacing=1, alignment=ft.MainAxisAlignment.CENTER))

        # 告警
        if alerts:
            items.append(ft.Divider(height=1))
            items.append(ft.Row([ft.Icon(WARNING_AMBER_ROUNDED, color="#FF9800", size=16), ft.Text("风险告警", size=13, weight=ft.FontWeight.BOLD, color="#FF9800")], spacing=5))
            for alert in alerts:
                items.append(ft.Row([
                    ft.Container(width=16),
                    ft.Text(f"• {alert}", size=12, expand=True),
                ]))

        # 建议
        if recommendations:
            items.append(ft.Divider(height=1))
            items.append(ft.Row([ft.Icon(TRENDING_UP_ROUNDED, color="#4CAF50", size=16), ft.Text("改进建议", size=13, weight=ft.FontWeight.BOLD, color="#4CAF50")], spacing=5))
            for rec in recommendations:
                items.append(ft.Row([
                    ft.Container(width=16),
                    ft.Text(f"• {rec}", size=12, expand=True),
                ]))

        dialog = ft.AlertDialog(
            modal=False,  # 允许点击外部关闭
            title=ft.Text("行为审计详情"),
            content=ft.Container(
                content=ft.Column(items, spacing=8, scroll=ft.ScrollMode.AUTO),
                width=560,
                height=550,
            ),
            actions=[
                ft.TextButton("关闭", on_click=lambda e: self.page.close(dialog)),
            ],
        )
        self.page.open(dialog)

    def _build_audit_list(self) -> ft.Control:
        """构建审计报告列表"""
        if not self._audit_reports:
            return self._build_audit_overview()

        cards = [self._build_audit_report_card(r) for r in self._audit_reports]
        return ft.Column([
            self._build_audit_overview(),
            ft.Divider(height=10, color="transparent"),
            ft.Text("账号审计报告 / ACCOUNT AUDITS", size=13, weight=ft.FontWeight.W_500, color="primary"),
            ft.Column(cards, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True),
        ], spacing=10, expand=True)

    def _build_active_tab_content(self) -> ft.Control:
        """根据当前标签页返回对应内容"""
        if self._active_tab == "audit":
            return ft.Container(
                content=self._build_audit_list(),
                padding=ft.padding.only(left=20, right=20, top=10),
                expand=True,
            )
        else:
            return ft.Column([
                self._stat_cards_container,
                ft.Divider(height=10, color="transparent"),
                ft.Container(
                    content=self._build_filter_bar(),
                    padding=ft.padding.only(left=0, right=0),
                ),
                ft.Container(
                    content=self._build_card_list(),
                    padding=ft.padding.only(top=10),
                    expand=True,
                ),
                ft.Container(
                    content=self._build_pagination(),
                ),
            ], spacing=10, expand=True)

    # ========== 页面构建 ==========

    def build(self) -> ft.Control:
        """构建页面"""
        # 创建统计卡片容器（保存引用以便后续更新）
        self._stat_cards_container = ft.Container(
            content=ft.Row(self._build_stat_cards(), spacing=10),
            padding=ft.padding.only(top=15, left=20, right=20),
        )
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=ARROW_BACK_IOS_NEW,
                        icon_size=16,
                        on_click=lambda e: self._navigate("dashboard"),
                        style=ft.ButtonStyle(
                            color=COLORS.PRIMARY,
                            bgcolor={"": with_opacity(0.1, COLORS.PRIMARY)},
                        ),
                    ),
                    padding=5,
                ),
                ft.Row([ft.Icon(ANALYTICS_OUTLINED, color=COLORS.PRIMARY, size=22), ft.Text("存活分析", size=18, weight=ft.FontWeight.BOLD, color=COLORS.PRIMARY)], spacing=8),
            ],
        )

        # 标签页：存活分析 / 行为审计
        self._tab_panel = ft.Container(
            content=self._build_active_tab_content(),
            expand=True,
        )

        tabs = ft.Tabs(
            selected_index=0,
            animation_duration=200,
            label_color=COLORS.PRIMARY,
            unselected_label_color="onSurfaceVariant",
            indicator_color=COLORS.PRIMARY,
            indicator_tab_size=True,
            tabs=[
                ft.Tab(
                    icon=ANALYTICS_OUTLINED,
                    text="存活监控",
                ),
                ft.Tab(
                    icon=SHIELD_ROUNDED,
                    text="行为审计",
                ),
            ],
            on_change=self._on_tab_change,
            expand=True,
        )

        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                tabs,
                self._tab_panel,
            ], spacing=0, expand=True),
            padding=15,
            expand=True,
        )

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)
