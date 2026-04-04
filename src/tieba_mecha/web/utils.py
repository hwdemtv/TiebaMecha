"""Web UI utilities"""

def with_opacity(opacity: float, color: str) -> str:
    """将颜色转换为带透明度的格式。

    Args:
        opacity: 透明度值 (0.0 - 1.0)
        color: 颜色名称或十六进制颜色值

    Returns:
        带透明度的十六进制颜色字符串 (#AARRGGBB 格式)
    """
    # 将透明度转换为 00-FF 的十六进制
    alpha = int(opacity * 255)
    alpha_hex = f"{alpha:02X}"

    # 解析颜色
    color = color.strip()
    if color.startswith("#"):
        # 已经是十六进制颜色
        hex_color = color[1:]
        if len(hex_color) == 6:
            return f"#{alpha_hex}{hex_color}"
        elif len(hex_color) == 8:
            # 已经有 alpha 通道，替换它
            return f"#{alpha_hex}{hex_color[2:]}"
        return color

    # 尝试从 Flet 颜色常量获取十六进制值
    color_lower = color.lower()
    # Flet 常用颜色映射
    color_map = {
        "primary": "#6750A4",
        "onprimary": "#FFFFFF",
        "primarycontainer": "#EADDFF",
        "secondary": "#625B71",
        "onsecondary": "#FFFFFF",
        "surface": "#1C1B1F",
        "onsurface": "#E6E1E5",
        "background": "#1C1B1F",
        "onbackground": "#E6E1E5",
        "error": "#B3261E",
        "onerror": "#FFFFFF",
        "outline": "#79747E",
        "grey": "#9E9E9E",
        "green": "#4CAF50",
        "red": "#F44336",
        "blue": "#2196F3",
        "yellow": "#FFEB3B",
        "orange": "#FF9800",
        "purple": "#9C27B0",
        "cyan": "#00BCD4",
        "white": "#FFFFFF",
        "black": "#000000",
        "transparent": "#00000000",
    }

    hex_color = color_map.get(color_lower, "#9E9E9E")  # 默认灰色
    return f"#{alpha_hex}{hex_color[1:]}"
