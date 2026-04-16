"""HUD Panel component"""

import flet as ft

from .theme import create_hud_panel


class HUDPanel(ft.Container):
    """
    HUD 翼板组件

    用于仪表盘显示关键数据指标
    """

    def __init__(
        self,
        title: str,
        value: str,
        icon: str,
        position: str = "left",
        **kwargs,
    ):
        super().__init__(**kwargs)
        panel = create_hud_panel(
            title=title,
            value=value,
            icon=icon,
            border_position=position,
        )
        self.content = panel.content
        self.bgcolor = panel.bgcolor
        self.border_radius = panel.border_radius
        self.border = panel.border
        self.padding = panel.padding
        self.expand = panel.expand


class DualHUD(ft.Container):
    """
    双 HUD 面板 (左右护法)

    左右对称显示两个关键指标
    """

    def __init__(
        self,
        left_title: str,
        left_value: str,
        left_icon: str,
        right_title: str,
        right_value: str,
        right_icon: str,
        left_value_color: str = None,
        right_value_color: str = None,
        **kwargs,
    ):
        self._left_title = left_title
        self._left_value = left_value
        self._left_icon = left_icon
        self._right_title = right_title
        self._right_value = right_value
        self._right_icon = right_icon
        self._left_value_color = left_value_color
        self._right_value_color = right_value_color
        super().__init__(**kwargs)
        self.content = ft.Row(
            controls=self._build_panels(),
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            spacing=20,
        )

    def _build_panels(self):
        return [
            create_hud_panel(
                title=self._left_title,
                value=self._left_value,
                icon=self._left_icon,
                border_position="left",
                value_color=self._left_value_color,
            ),
            create_hud_panel(
                title=self._right_title,
                value=self._right_value,
                icon=self._right_icon,
                border_position="right",
                value_color=self._right_value_color,
            ),
        ]

    @property
    def left_value(self): return self._left_value

    @left_value.setter
    def left_value(self, val):
        self._left_value = val
        self.content.controls = self._build_panels()
    
    @property
    def left_value_color(self): return self._left_value_color
    
    @left_value_color.setter
    def left_value_color(self, val):
        self._left_value_color = val
        self.content.controls = self._build_panels()

    @property
    def right_value(self): return self._right_value

    @right_value.setter
    def right_value(self, val):
        self._right_value = val
        self.content.controls = self._build_panels()
    
    @property
    def right_value_color(self): return self._right_value_color
    
    @right_value_color.setter
    def right_value_color(self, val):
        self._right_value_color = val
        self.content.controls = self._build_panels()
