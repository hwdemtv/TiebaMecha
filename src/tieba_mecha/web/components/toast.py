"""全局 UI 反馈组件：统一 toast 提示与异步确认对话框。

此前 _show_snackbar 在 10+ 个页面各复制一份（颜色映射/时长略有差异），
确认对话框也有 8 处手写。此模块是单源实现。
"""

from __future__ import annotations

import asyncio

import flet as ft

from ..flet_compat import COLORS
from ..utils import with_opacity


def show_toast(page, message: str, type: str = "info", duration: int = 3000) -> None:
    """在页面底部弹出浮动提示。

    Args:
        type: info / success / warning / error
    """
    if not page:
        return
    color = "primary"
    if type == "error":
        color = "error"
    elif type == "success":
        color = COLORS.GREEN
    elif type == "warning":
        color = COLORS.AMBER

    try:
        page.show_snack_bar(
            ft.SnackBar(
                content=ft.Text(message),
                bgcolor=with_opacity(0.8, color),
                behavior=ft.SnackBarBehavior.FLOATING,
                duration=duration,
            )
        )
        page.update()
    except Exception:
        # 页面已销毁等场景下静默失败，不影响业务流程
        pass


async def confirm_async(
    page,
    title: str,
    message: str,
    confirm_text: str = "确认",
    cancel_text: str = "取消",
    danger: bool = True,
) -> bool:
    """弹出模态确认框，等待用户选择并返回布尔结果。

    用法::

        if await confirm_async(page, "确认删除？", "此操作不可恢复"):
            await do_delete(...)
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bool] = loop.create_future()

    def _resolve(value: bool):
        if not future.done():
            future.set_result(value)

    dialog = ft.AlertDialog(
        title=ft.Text(title),
        content=ft.Text(message) if message else None,
        actions=[
            ft.TextButton(cancel_text, on_click=lambda _: (_resolve(False), page.close(dialog))),
            ft.FilledButton(
                confirm_text,
                style=ft.ButtonStyle(bgcolor="error", color="white") if danger else None,
                on_click=lambda _: (_resolve(True), page.close(dialog)),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.open(dialog)
    try:
        return await future
    finally:
        try:
            page.close(dialog)
        except Exception:
            pass
