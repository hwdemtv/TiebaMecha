"""
Flet 版本兼容性工具

解决不同 Flet 版本之间的 API 差异，避免因参数变更导致的错误。
使用方法：
    from flet_compat import safe_color_scheme, get_flet_version

    # 安全创建 ColorScheme
    color_scheme = safe_color_scheme(
        primary="#00D4FF",
        secondary="#B4CDCD",
        inverse_surface="#E1E3E1",  # 自动处理不支持的参数
    )
"""

import flet as ft
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def get_flet_version() -> tuple[int, int, int]:
    """获取 Flet 版本号"""
    try:
        import flet
        version_str = getattr(flet, "__version__", "0.21.0")
        parts = version_str.split(".")
        return tuple(int(p) for p in parts[:3])
    except Exception:
        return (0, 21, 0)


@lru_cache(maxsize=1)
def get_supported_color_scheme_params() -> set[str]:
    """获取当前 Flet 版本支持的 ColorScheme 参数"""
    try:
        # 获取 ColorScheme 的所有有效参数
        import inspect
        sig = inspect.signature(ft.ColorScheme)
        return set(sig.parameters.keys())
    except Exception:
        # 回退到基础参数
        return {
            "primary", "secondary", "tertiary", "error", "surface",
            "on_surface", "on_surface_variant", "outline",
            "primary_container", "secondary_container", "tertiary_container",
            "error_container", "surface_variant", "outline_variant",
            "on_primary", "on_secondary", "on_tertiary", "on_error",
            "on_primary_container", "on_secondary_container",
            "on_tertiary_container", "on_error_container",
            "background", "on_background", "shadow", "scrim",
        }


def safe_color_scheme(**kwargs) -> ft.ColorScheme:
    """
    安全创建 ColorScheme，自动过滤不支持的参数

    Args:
        **kwargs: ColorScheme 参数

    Returns:
        ft.ColorScheme 实例

    Example:
        color_scheme = safe_color_scheme(
            primary="#00D4FF",
            secondary="#B4CDCD",
            inverse_on_surface="#2A302F",  # 可能不支持，自动处理
        )
    """
    supported = get_supported_color_scheme_params()

    # 过滤出支持的参数
    safe_kwargs = {k: v for k, v in kwargs.items() if k in supported}

    # 记录被过滤的参数（调试用）
    filtered = set(kwargs.keys()) - supported
    if filtered:
        import logging
        logging.getLogger(__name__).debug(
            f"ColorScheme 参数被过滤 (不支持): {filtered}"
        )

    return ft.ColorScheme(**safe_kwargs)


def safe_theme(color_scheme: ft.ColorScheme | None = None, **kwargs) -> ft.Theme:
    """
    安全创建 Theme，处理版本差异

    Args:
        color_scheme: ColorScheme 实例
        **kwargs: Theme 的其他参数

    Returns:
        ft.Theme 实例
    """
    theme_kwargs = {}

    if color_scheme:
        theme_kwargs["color_scheme"] = color_scheme

    # 获取 Theme 支持的参数
    try:
        import inspect
        sig = inspect.signature(ft.Theme)
        supported = set(sig.parameters.keys())
        theme_kwargs.update({k: v for k, v in kwargs.items() if k in supported})
    except Exception:
        theme_kwargs.update(kwargs)

    return ft.Theme(**theme_kwargs)


# 预定义的颜色方案参数映射
# 新版本参数名 -> 旧版本参数名（用于回退）
COLOR_SCHEME_PARAM_ALIASES = {
    # 无当前别名映射
}


def resolve_param_alias(param_name: str) -> str:
    """解析参数别名，返回当前版本应使用的参数名"""
    # 如果当前版本不支持此参数，尝试使用别名
    supported = get_supported_color_scheme_params()

    if param_name in supported:
        return param_name

    # 检查是否有别名
    if param_name in COLOR_SCHEME_PARAM_ALIASES:
        alias = COLOR_SCHEME_PARAM_ALIASES[param_name]
        if alias in supported:
            return alias

    return param_name


class ColorSchemeBuilder:
    """
    ColorScheme 构建器，提供链式调用

    Example:
        builder = ColorSchemeBuilder()
        color_scheme = (builder
            .primary("#00D4FF")
            .secondary("#B4CDCD")
            .surface("#121820")
            .build())
    """

    def __init__(self):
        self._params: dict[str, Any] = {}

    def primary(self, value: str) -> "ColorSchemeBuilder":
        self._params["primary"] = value
        return self

    def secondary(self, value: str) -> "ColorSchemeBuilder":
        self._params["secondary"] = value
        return self

    def tertiary(self, value: str) -> "ColorSchemeBuilder":
        self._params["tertiary"] = value
        return self

    def error(self, value: str) -> "ColorSchemeBuilder":
        self._params["error"] = value
        return self

    def surface(self, value: str) -> "ColorSchemeBuilder":
        self._params["surface"] = value
        return self

    def on_surface(self, value: str) -> "ColorSchemeBuilder":
        self._params["on_surface"] = value
        return self

    def on_surface_variant(self, value: str) -> "ColorSchemeBuilder":
        self._params["on_surface_variant"] = value
        return self

    def outline(self, value: str) -> "ColorSchemeBuilder":
        self._params["outline"] = value
        return self

    def set(self, **kwargs) -> "ColorSchemeBuilder":
        """批量设置参数"""
        self._params.update(kwargs)
        return self

    def build(self) -> ft.ColorScheme:
        """构建 ColorScheme"""
        return safe_color_scheme(**self._params)


# 导出
__all__ = [
    "get_flet_version",
    "get_supported_color_scheme_params",
    "safe_color_scheme",
    "safe_theme",
    "resolve_param_alias",
    "ColorSchemeBuilder",
    "COLORS",
]


class ColorsCompat:
    """
    颜色兼容层 - Flet 0.84+ 移除了 ft.colors.XXX，改用字符串

    使用方法:
        from flet_compat import COLORS
        color = COLORS.RED  # 返回 "red"
    """

    # 基础颜色
    RED = "red"
    RED_ACCENT_400 = "redaccent400"
    GREEN = "green"
    GREEN_ACCENT_400 = "greenaccent400"
    BLUE = "blue"
    YELLOW = "yellow"
    ORANGE = "orange"
    AMBER = "amber"
    PURPLE = "purple"
    PINK = "pink"
    WHITE = "white"
    BLACK = "black"
    GREY = "grey"
    GREY_400 = "grey400"
    TRANSPARENT = "transparent"

    # Material Design 颜色
    PRIMARY = "primary"
    SECONDARY = "secondary"
    ERROR = "error"
    SURFACE = "surface"
    ON_SURFACE = "onsurface"
    ON_SURFACE_VARIANT = "onsurfacevariant"
    ON_PRIMARY = "onprimary"
    ON_ERROR = "onerror"
    OUTLINE = "outline"
    OUTLINE_VARIANT = "outlinevariant"

    # 常用语义颜色
    SUCCESS = "green"
    WARNING = "orange"
    DANGER = "red"
    INFO = "blue"


COLORS = ColorsCompat()
