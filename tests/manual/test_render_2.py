import flet as ft
from src.tieba_mecha.web.components.hud_panel import DualHUD
from src.tieba_mecha.web.components.tiles import FunctionTiles
from src.tieba_mecha.web.components.stream_list import StreamList
from src.tieba_mecha.web.components.core_button import CoreButtonWithLabel

def main(page: ft.Page):
    hud = DualHUD(
        left_title="SUCCESS / 成功",
        left_value="10",
        left_icon=ft.icons.CHECK_CIRCLE_ROUNDED,
        right_title="PENDING / 待命",
        right_value="5",
        right_icon=ft.icons.SYNC_PROBLEM_ROUNDED,
    )
    tiles = FunctionTiles(
        tiles=[
            {
                "title": "账号矩阵",
                "icon": ft.icons.PEOPLE_ALT_ROUNDED,
                "subtitle": "MULTI-ACCOUNT",
                "on_click": None,
            },
            {
                "title": "任务核心",
                "icon": ft.icons.BOLT_ROUNDED,
                "subtitle": "TASK CORE",
                "on_click": None,
            },
        ]
    )
    core_btn = CoreButtonWithLabel(
        label="开始同步全域签到",
        icon=ft.icons.POWER_SETTINGS_NEW_ROUNDED,
        on_click=None,
        size=100,
    )
    forum_list = StreamList(
        items=[
            {
                "title": "测试",
                "subtitle": "连续签到 1 天",
                "icon": ft.icons.VERIFIED_ROUNDED,
            }
        ],
        on_item_click=None,
    )
    
    col = ft.Column([hud, tiles, core_btn, forum_list])
    page.add(col)
    page.update()

ft.app(main)
