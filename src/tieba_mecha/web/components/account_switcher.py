"""页面级账号切换芯片 - 放在操作页面头部

芯片常驻显示"当前账号：X"（状态灯颜色即账号状态），点击弹出
PopupMenu 账号菜单，点选即切换，切换后由页面自行重载数据。

实现说明：面板刻意用 PopupMenuButton（Material 原生弹出菜单）承载，
而不用 AlertDialog + page.open/close——后者在 Flet 0.23 web 上存在
关闭与页面更新的竞态（barrier 空壳残留挡死整页，需 F5）。PopupMenu
的开关由框架路由管理，无遮罩、无生命周期竞态。批量发帖页不接入——
它走自己的账号池多选，与"当前账号"语义不同。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Optional

import flet as ft

from ...core.account import switch_account
from ..flet_compat import COLORS
from ..utils import with_opacity
from .icons import (
    EXPAND_MORE,
    PERSON_OFF,
    RADIO_BUTTON_CHECKED,
    RADIO_BUTTON_UNCHECKED,
)
from .toast import show_toast

if TYPE_CHECKING:
    from tieba_mecha.db.crud import Database


def _status_style(status: str) -> tuple[str, str]:
    """账号状态 → (展示文本, 颜色)，口径与账号档案中心的卡片一致"""
    if status == "active":
        return "正常", COLORS.GREEN_ACCENT_400
    if status == "banned":
        return "已封禁", COLORS.RED_ACCENT_400
    if status == "expired":
        return "已过期", COLORS.ERROR
    if status.startswith("invalid"):
        return "凭证失效", COLORS.ERROR
    if status == "error":
        return "异常", COLORS.AMBER
    return "未验证", COLORS.GREY_400


class AccountSwitchChip(ft.Container):
    """页面头部账号切换芯片：状态灯 + 账号名 + 下拉箭头，点击弹出账号菜单"""

    def __init__(
        self,
        page: ft.Page,
        db: "Optional[Database]",
        on_switched: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self._page = page
        self.db = db
        self._on_switched = on_switched
        self._accounts: list = []
        self._active = None
        self._switching = False

        self._row = ft.Row(spacing=8, tight=True)
        self._btn = ft.PopupMenuButton(
            content=self._row,
            tooltip="点击切换当前账号",
            padding=ft.padding.symmetric(horizontal=12, vertical=10),
            shape=ft.RoundedRectangleBorder(radius=20),
            bgcolor=with_opacity(0.05, "onSurface"),
            elevation=4,
            items=[],
        )
        self.content = self._btn
        self._render_empty()

    # ---------- 展示 ----------

    @staticmethod
    def _display_name(acc) -> str:
        return acc.user_name or acc.name or f"#{acc.id}"

    def _render_empty(self):
        """无账号状态：点击引导去账号页接入"""
        self._row.controls = [
            ft.Icon(PERSON_OFF, size=16, color="onSurfaceVariant"),
            ft.Text("未接入账号", size=12, color="onSurfaceVariant"),
        ]
        # 注意：tooltip 必须用纯字符串。赋 ft.Tooltip 对象会被 Flet 0.23
        # 当作子控件渲染成覆盖层，既显示原始文本又拦截点击。
        self._btn.tooltip = "尚未接入账号，点击去接入"
        self._btn.items = []

    def _render_active(self, acc):
        status_label, status_color = _status_style(getattr(acc, "status", "unknown"))
        name = self._display_name(acc)
        self._row.controls = [
            ft.Container(
                width=9,
                height=9,
                border_radius=5,
                bgcolor=status_color,
                tooltip=status_label,
            ),
            ft.Text(
                f"当前账号：{name}",
                size=12,
                weight=ft.FontWeight.W_600,
                color="onSurface",
                max_lines=1,
            ),
            ft.Icon(EXPAND_MORE, size=16, color="onSurfaceVariant"),
        ]
        self._btn.tooltip = f"点击切换当前账号（当前：{name}）"

    async def refresh(self):
        """重新加载账号与当前活跃账号（页面 load_data 末尾调用）"""
        if not self.db:
            return
        try:
            self._accounts = await self.db.get_accounts()
            self._active = await self.db.get_active_account()
        except Exception:
            return  # db 未就绪等场景静默，不打断页面加载
        if self._active:
            self._render_active(self._active)
        else:
            self._render_empty()
        self._sync_menu_items()
        try:
            self.update()
        except Exception:
            pass  # 尚未挂载到页面

    def _sync_menu_items(self):
        """按当前账号列表重建菜单项（refresh 时与面板打开前调用）"""
        if not self._accounts:
            self._btn.items = []
            return
        self._btn.items = [self._build_menu_item(a) for a in self._accounts]

    # ---------- 菜单项 ----------

    def _build_menu_item(self, acc) -> ft.PopupMenuItem:
        is_active = self._active is not None and acc.id == self._active.id
        status_label, status_color = _status_style(getattr(acc, "status", "unknown"))
        name = acc.name or f"#{acc.id}"
        title = (
            f"{name} [{acc.user_name}]"
            if acc.user_name and acc.user_name != name
            else self._display_name(acc)
        )

        return ft.PopupMenuItem(
            content=ft.Row(
                controls=[
                    ft.Container(
                        width=9,
                        height=9,
                        border_radius=5,
                        bgcolor=status_color,
                        tooltip=status_label,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                title,
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color="primary" if is_active else "onSurface",
                                max_lines=1,
                            ),
                            ft.Text(
                                f"UID: {acc.user_id or '待验证'}",
                                size=10,
                                color="onSurfaceVariant",
                            ),
                        ],
                        spacing=1,
                        expand=True,
                    ),
                    ft.Container(
                        content=ft.Text(
                            status_label,
                            size=9,
                            weight=ft.FontWeight.BOLD,
                            color="white",
                        ),
                        bgcolor=status_color,
                        padding=ft.padding.symmetric(horizontal=6, vertical=2),
                        border_radius=4,
                    ),
                    ft.Icon(
                        RADIO_BUTTON_CHECKED if is_active else RADIO_BUTTON_UNCHECKED,
                        size=18,
                        color="primary" if is_active else "onSurfaceVariant",
                    ),
                ],
                spacing=8,
                width=270,
            ),
            disabled=is_active,
            on_click=None if is_active else lambda e, a=acc: self._page.run_task(self._do_switch, a),
        )

    # ---------- 切换 ----------

    async def _do_switch(self, acc):
        if self._switching or not self.db:
            return
        if self._active is not None and acc.id == self._active.id:
            return
        self._switching = True
        try:
            try:
                await switch_account(self.db, acc.id)
            except Exception as ex:
                show_toast(self._page, f"❌ 切换失败: {ex}", "error")
                return
            name = self._display_name(acc)
            await self.refresh()
            show_toast(self._page, f"✅ 已切换至 {name}", "success")
            # 通知宿主页面在原位重载数据（不重建控件树），刷新账号上下文
            if self._on_switched:
                res = self._on_switched()
                if asyncio.iscoroutine(res):
                    await res
        finally:
            self._switching = False
