import flet as ft

class DualHUD(ft.Row):
    def __init__(
        self,
        left_title: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.controls = [ft.Text(left_title)]

def main(page: ft.Page):
    panel = DualHUD("test left")
    print("Panel controls:", panel.controls)
    page.add(panel)
    page.update()

ft.app(main)
