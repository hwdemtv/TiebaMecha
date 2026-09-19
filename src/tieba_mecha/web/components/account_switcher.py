"""页面级账号切换芯片 - 放在操作页面头部

芯片常驻显示"当前账号：X"（状态灯颜色即账号状态），点击弹出
切换面板，一键切换后由页面自行重载数据。批量发帖页不接入——
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


# 会话级互斥锁：同一时刻每个浏览器会话只允许一个切换面板实例存在。
# 页面重载会创建新的芯片实例，若仅用实例级 _dialog 判重，
# 旧实例的对话框未关闭时新实例还能再开一个，遮罩会互相残留。
# 按会话 ID 分桶，避免会话间互相锁死。
_dialog_locks: dict = {}


class AccountSwitchChip(ft.Container):
    """页面头部账号切换芯片：状态灯 + 账号名 + 下拉箭头，点击弹出切换面板"""

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
        self._dialog: Optional[ft.AlertDialog] = None

        # 用 Material 按钮承载点击（与账号页"接入账号"相同的可行模式；
        # Container.on_click 在部分场景下打开对话框不渲染）
        self._row = ft.Row(spacing=8, tight=True)
        self._btn = ft.TextButton(
            content=self._row,
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(horizontal=12, vertical=8),
                shape=ft.RoundedRectangleBorder(radius=20),
                bgcolor=with_opacity(0.05, "onSurface"),
                side=ft.BorderSide(1, with_opacity(0.15, "onSurface")),
            ),
            on_click=self._open_dialog,
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
        # 当作子控件渲染成覆盖层，既显示原始文本又拦截 on_click。
        self._btn.tooltip = "尚未接入账号，点击去接入"

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
        try:
            self.update()
        except Exception:
            pass  # 尚未挂载到页面

    # ---------- 切换面板 ----------

    def _build_account_row(self, acc) -> ft.Control:
        is_active = self._active is not None and acc.id == self._active.id
        status_label, status_color = _status_style(getattr(acc, "status", "unknown"))
        name = acc.name or f"#{acc.id}"
        title = (
            f"{name} [{acc.user_name}]"
            if acc.user_name and acc.user_name != name
            else self._display_name(acc)
        )

        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        width=10,
                        height=10,
                        border_radius=5,
                        bgcolor=status_color,
                        tooltip=status_label,
                    ),
                    ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Text(
                                        title,
                                        size=14,
                                        weight=ft.FontWeight.W_600,
                                        color="primary" if is_active else "onSurface",
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
                                        padding=ft.padding.symmetric(
                                            horizontal=6, vertical=2
                                        ),
                                        border_radius=4,
                                    ),
                                ],
                                spacing=8,
                            ),
                            ft.Text(
                                f"UID: {acc.user_id or '待验证'}",
                                size=11,
                                color="onSurfaceVariant",
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Icon(
                        RADIO_BUTTON_CHECKED if is_active else RADIO_BUTTON_UNCHECKED,
                        size=20,
                        color="primary" if is_active else "onSurfaceVariant",
                        tooltip="当前账号" if is_active else "切换到此账号",
                    ),
                ],
                spacing=10,
            ),
            padding=ft.padding.symmetric(horizontal=12, vertical=10),
            border_radius=10,
            bgcolor=with_opacity(0.06, "primary") if is_active else None,
            border=ft.border.all(
                1,
                with_opacity(0.25, "primary") if is_active else with_opacity(0.08, "onSurface"),
            ),
            # 当前账号行禁点，其余行点击即切换
            on_click=None if is_active else lambda e, a=acc: self._page.run_task(self._do_switch, a),
            ink=True,
        )

    def _locked(self) -> bool:
        sid = getattr(self._page, "session_id", None)
        return bool(_dialog_locks.get(sid))

    def _acquire_lock(self):
        _dialog_locks[getattr(self._page, "session_id", None)] = True

    def _release_lock(self):
        _dialog_locks[getattr(self._page, "session_id", None)] = False

    async def _open_dialog(self, e):
        if not self.db or self._locked():
            return
        if not self._accounts:
            # 账号数据未就绪（页面刚加载），先同步一次再判断
            await self.refresh()
        if not self._accounts:
            show_toast(self._page, "请先在「账号列表」接入账号", "warning")
            return

        # 注意：content 不可用 scroll+tight Column / max_height 约束的组合——
        # 滚动 Column 在对话框无界高度里布局冲突，Flutter 会把整个对话框渲染成空。
        # 用固定高度 + scroll 保证多账号时可滚动、少账号时贴合内容。
        rows = [self._build_account_row(a) for a in self._accounts]
        box_h = min(76 * len(rows) + 16, 420)
        self._dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("切换当前账号", weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column(
                    controls=rows,
                    spacing=6,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=420,
                height=box_h,
            ),
            actions=[ft.TextButton("关闭", on_click=self._close_dialog)],
        )
        self._acquire_lock()
        self._page.open(self._dialog)

    def _close_dialog(self, _=None):
        d, self._dialog = self._dialog, None
        self._release_lock()
        if not d:
            return
        try:
            self._page.close(d)
        except Exception:
            pass
        # 兜底：从页面 offstage 容器强制摘除，防止遮罩残留挡住整页点击
        try:
            off = getattr(self._page, "_Page__offstage", None)
            if off is not None and d in off.controls:
                off.controls.remove(d)
                self._page.update()
        except Exception:
            pass
        self._release_lock()

    async def _do_switch(self, acc):
        if not self.db or not self._dialog:
            # 防重入：行点击事件可能重放两次，第二次进来时面板已关，直接忽略
            return
        # 先同步关闭面板并释放锁，再执行切换，避免关闭与页面重载竞态
        self._close_dialog()
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
