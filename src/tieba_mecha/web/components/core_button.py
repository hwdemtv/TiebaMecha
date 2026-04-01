"""Core button component"""

import flet as ft

from .theme import GRADIENT_CYAN, create_core_button


class CoreButton(ft.Container):
    """
    机甲心脏核心按钮

    主操作入口，带渐变和发光效果
    """

    def __init__(
        self,
        icon: str = ft.icons.PLAY_ARROW,
        on_click=None,
        size: float = 100,
        **kwargs,
    ):
        super().__init__(**kwargs)
        btn = create_core_button(
            icon=icon,
            on_click=on_click,
            size=size,
        )
        self.content = btn.content
        self.gradient = btn.gradient
        self.width = btn.width
        self.height = btn.height
        self.border_radius = btn.border_radius
        self.shadow = btn.shadow
        self.ink = btn.ink
        self.on_click = btn.on_click
        self.alignment = btn.alignment


class CoreButtonWithLabel(ft.Container):
    """
    带标签的核心按钮
    """

    def __init__(
        self,
        label: str,
        icon: str = ft.icons.PLAY_ARROW,
        on_click=None,
        size: float = 100,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.content = ft.Column(
            controls=[
                create_core_button(
                    icon=icon,
                    on_click=on_click,
                    size=size,
                ),
                ft.Text(
                    label,
                    color="onSurfaceVariant",
                    size=12,
                    weight=ft.FontWeight.W_500,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        )
