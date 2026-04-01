"""Function tiles component"""

import flet as ft

from .theme import create_function_tile


class FunctionTiles(ft.Container):
    """
    功能磁贴组 (四大金刚)

    2x2 网格布局的核心功能入口
    """

    def __init__(
        self,
        tiles: list[dict],
        **kwargs,
    ):
        """
        Args:
            tiles: 磁贴配置列表 [{title, icon, subtitle, on_click}, ...]
        
        """
        self.controls = []
        for tile in tiles:
            self.controls.append(
                create_function_tile(
                    title=tile.get("title", ""),
                    icon=tile.get("icon", ft.icons.APPS),
                    subtitle=tile.get("subtitle", ""),
                    tooltip=tile.get("tooltip", None),
                    on_click=tile.get("on_click"),
                    on_hover=tile.get("on_hover"),
                )
            )

        super().__init__(**kwargs)
        self.content = ft.Row(
            controls=self.controls,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            spacing=12,
            run_spacing=12,
            wrap=True,
        )


class TileGrid(ft.Container):
    """
    功能磁贴网格

    可配置行列数的功能网格
    """

    def __init__(
        self,
        tiles: list[dict],
        columns: int = 2,
        **kwargs,
    ):
        self.controls = []
        for i in range(0, len(tiles), columns):
            row_tiles = tiles[i : i + columns]
            row = ft.Row(
                controls=[
                    create_function_tile(
                        title=t.get("title", ""),
                        icon=t.get("icon", ft.icons.APPS),
                        subtitle=t.get("subtitle", ""),
                        tooltip=t.get("tooltip", None),
                        on_click=t.get("on_click"),
                    )
                    for t in row_tiles
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                spacing=12,
            )
            self.controls.append(row)

        super().__init__(**kwargs)
        self.content = ft.Column(
            controls=self.controls,
            spacing=12,
        )
