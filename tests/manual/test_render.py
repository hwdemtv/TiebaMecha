import flet as ft
from src.tieba_mecha.web.components.hud_panel import DualHUD
from src.tieba_mecha.web.components.tiles import FunctionTiles
from src.tieba_mecha.web.components.stream_list import StreamList

def main(page: ft.Page):
    hud = DualHUD("L", "1", ft.icons.CHECK, "R", "2", ft.icons.CLOSE)
    tiles = FunctionTiles(tiles=[{"title": "test", "icon": ft.icons.APPS}])
    sl = StreamList(items=[{"title": "item1"}])
    
    col = ft.Column([hud, tiles, sl])
    page.add(col)
    page.update()

ft.app(main)
