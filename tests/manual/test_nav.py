"""Test NavigationRail with expand"""
import flet as ft

def main(page: ft.Page):
    page.title = "TiebaMecha Test"
    page.theme_mode = ft.ThemeMode.DARK

    # 导航栏
    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        expand=True,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.icons.HOME_OUTLINED,
                selected_icon=ft.icons.HOME,
                label="首页",
            ),
            ft.NavigationRailDestination(
                icon=ft.icons.PERSON_OUTLINED,
                selected_icon=ft.icons.PERSON,
                label="账号",
            ),
        ],
    )

    content = ft.Container(
        content=ft.Text("Hello TiebaMecha!", size=24),
        padding=20,
        expand=True,
    )

    page.add(
        ft.Row(
            controls=[
                nav_rail,
                ft.VerticalDivider(width=1),
                content,
            ],
            expand=True,
        )
    )

if __name__ == "__main__":
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=9003)
