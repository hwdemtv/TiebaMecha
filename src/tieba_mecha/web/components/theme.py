"""Theme configuration - Cyber-Mecha style with Flet version compatibility"""

import flet as ft
from ..flet_compat import safe_color_scheme, safe_theme
from ...web.utils import with_opacity


def get_dark_theme() -> ft.Theme:
    """
    护眼赛博机甲风 (Cyber-Mecha Dark)
    采用极光底色提亮技术，脱离纯黑，消除长时间凝视产生的高对比度残影
    """
    color_scheme = safe_color_scheme(
        surface="#1C2028",
        primary="#00BFA5",
        secondary="#E8C361",
        error="#EF5350",
        outline="#242A35",
        on_surface="#F2F5F9",
        on_surface_variant="#A3AAB8",
        outline_variant="#2D3440",
        tertiary="#FF9800",
        # 可选参数（自动处理兼容性）
        surface_container_highest="#252B36",
    )

    return safe_theme(color_scheme=color_scheme)


def get_light_theme() -> ft.Theme:
    """
    实验舱风 (Lab-Clinic Light)
    适用于强光差环境，追求类医疗软件或精密仪器的极简、专业、清晰感
    """
    color_scheme = safe_color_scheme(
        surface="#FFFFFF",
        primary="#009688",
        secondary="#C49B27",
        error="#D32F2F",
        outline="#E5E8EB",
        on_surface="#1A1A1A",
        on_surface_variant="#6B7280",
        outline_variant="#D1D5DB",
        tertiary="#F59E0B",
        surface_container_highest="#F0F2F5",
    )

    return safe_theme(color_scheme=color_scheme)


# 渐变色配置
GRADIENT_CYAN = ["#00B894", "#00C2FF"]
GRADIENT_GOLD = ["#E8C361", "#F5A623"]
GRADIENT_DANGER = ["#EF5350", "#FF7043"]


def create_gradient_button(
    text: str,
    icon: str | None = None,
    gradient_colors: list[str] | None = None,
    on_click=None,
    width: float | None = None,
    height: float = 48,
) -> ft.Container:
    """创建渐变按钮 (Aurora Boost 版)"""
    colors = gradient_colors or GRADIENT_CYAN

    content = ft.Row(
        controls=[
            ft.Icon(icon, color="onSurface", size=20) if icon else None,
            ft.Text(text, color="onSurface", size=14, weight=ft.FontWeight.W_500),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=8,
    )
    content.controls = [c for c in content.controls if c is not None]

    return ft.Container(
        content=content,
        gradient=ft.LinearGradient(colors=colors),
        border_radius=12,
        border=ft.border.all(1, with_opacity(0.12, "onSurface")),
        shadow=ft.BoxShadow(
            spread_radius=1,
            blur_radius=20,
            color=with_opacity(0.2, colors[0]),
        ),
        padding=ft.padding.symmetric(horizontal=20, vertical=12),
        width=width,
        height=height,
        ink=True,
        on_click=on_click,
        alignment=ft.alignment.center,
    )


def create_hud_panel(
    title: str,
    value: str,
    icon: str,
    border_position: str = "left",
    value_color: str = None,
) -> ft.Container:
    """创建 HUD 翼板 (Symmetric Capsule)"""
    border_radius = 12
    value_text_color = value_color or "onSurface"
    icon_color = value_color or "primary"

    if border_position == "left":
        border = ft.border.Border(
            left=ft.BorderSide(3, "primary"),
            top=ft.BorderSide(1, "outlineVariant"),
            right=ft.BorderSide(1, "outlineVariant"),
            bottom=ft.BorderSide(1, "outlineVariant"),
        )
    else:
        border = ft.border.Border(
            left=ft.BorderSide(1, "outlineVariant"),
            top=ft.BorderSide(1, "outlineVariant"),
            right=ft.BorderSide(3, "primary"),
            bottom=ft.BorderSide(1, "outlineVariant"),
        )

    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Icon(icon, color=icon_color, size=24),
                ft.Text(
                    value,
                    font_family="Consolas",
                    size=28,
                    color=value_text_color,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(title, color="onSurfaceVariant", size=12),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=5,
        ),
        bgcolor=with_opacity(0.05, "onSurface"),
        border_radius=border_radius,
        border=border,
        padding=15,
        expand=True,
    )


def create_function_tile(
    title: str,
    icon: str,
    subtitle: str = "",
    tooltip: str | None = None,
    on_click=None,
    on_hover=None,
) -> ft.Container:
    """创建功能磁贴 (Function Tiles)"""
    controls = [
        ft.Icon(icon, color="primary", size=32),
        ft.Text(title, color="onSurface", size=14, weight=ft.FontWeight.W_500),
    ]
    if subtitle:
        controls.append(ft.Text(subtitle, color="onSurfaceVariant", size=11))

    return ft.Container(
        content=ft.Column(
            controls=controls,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        bgcolor="surface",
        border_radius=12,
        border=ft.border.all(1, "outline"),
        padding=15,
        ink=True,
        tooltip=tooltip,
        on_click=on_click,
        on_hover=on_hover,
        expand=True,
    )


def create_stream_list_item(
    title: str,
    subtitle: str = "",
    leading_icon: str | None = None,
    trailing_controls: list | None = None,
    on_click=None,
) -> ft.Container:
    """创建流式列表项 (Stream List Item)"""
    leading = [
        ft.Icon(leading_icon, color="primary", size=20) if leading_icon else None,
        ft.Text(title, color="onSurface", size=13, expand=True),
    ]
    leading = [c for c in leading if c is not None]

    trailing = trailing_controls or []
    if subtitle:
        trailing.insert(0, ft.Text(subtitle, color="onSurfaceVariant", size=11))

    return ft.Container(
        content=ft.Row(
            controls=leading + trailing,
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=with_opacity(0.05, "onSurface"),
        border_radius=8,
        border=ft.border.all(1, with_opacity(0.1, "outline")),
        padding=ft.padding.symmetric(horizontal=12, vertical=10),
        on_click=on_click,
    )


def create_core_button(
    icon: str,
    on_click=None,
    size: float = 100,
) -> ft.Container:
    """创建核心按钮 (Core Button)"""
    return ft.Container(
        content=ft.Icon(icon, color="onSurface", size=40),
        gradient=ft.RadialGradient(
            colors=GRADIENT_CYAN,
            center=ft.alignment.center,
        ),
        width=size,
        height=size,
        border_radius=size / 2,
        shadow=ft.BoxShadow(
            spread_radius=3,
            blur_radius=30,
            color=with_opacity(0.3, GRADIENT_CYAN[0]),
        ),
        ink=True,
        on_click=on_click,
        alignment=ft.alignment.center,
    )
