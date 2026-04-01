"""Test minimal Flet app"""
import flet as ft

def main(page: ft.Page):
    page.title = "TiebaMecha Test"
    page.theme_mode = ft.ThemeMode.DARK

    # 简单的导航栏测试
    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.icons.HOME,
                selected_icon=ft.icons.HOME_FILLED,
                label="首页",
            ),
            ft.NavigationRailDestination(
                icon=ft.icons.PERSON,
                selected_icon=ft.icons.PERSON_OUTLINED,
                label="账号",
            ),
        ],
    )

    content = ft.Container(
        content=ft.Text("Hello TiebaMecha!", size=24),
        padding=20,
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
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=9001)
