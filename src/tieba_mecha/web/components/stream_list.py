"""Stream list component"""

import flet as ft

from .theme import create_stream_list_item


class StreamList(ft.Container):
    """
    极速雷达流式列表

    用于实时动态排行榜
    """

    def __init__(
        self,
        items: list[dict] | None = None,
        on_item_click=None,
        **kwargs,
    ):
        """
        Args:
            items: 列表项 [{title, subtitle, icon, trailing}, ...]
            on_item_click: 点击回调
        """
        self._items = items or []
        self._on_item_click = on_item_click
        
        super().__init__(**kwargs)
        self.content = ft.Column(
            controls=self._build_items(),
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

    @property
    def items(self):
        return self._items

    @items.setter
    def items(self, val):
        self._items = val
        self.content.controls = self._build_items()
        if self.page:
            self.update()

    def _build_items(self) -> list[ft.Control]:
        items = []
        for i, item in enumerate(self._items):
            trailing_controls = item.get("trailing", [])

            def make_click_handler(idx):
                def handler(e):
                    if self._on_item_click:
                        self._on_item_click(e, idx)

                return handler

            items.append(
                create_stream_list_item(
                    title=item.get("title", ""),
                    subtitle=item.get("subtitle", ""),
                    leading_icon=item.get("icon"),
                    trailing_controls=trailing_controls,
                    on_click=make_click_handler(i),
                )
            )
        return items


class ForumList(StreamList):
    """贴吧列表"""

    def __init__(self, forums: list[dict] | None = None, **kwargs):
        # 转换数据格式
        items = None
        if forums:
            items = [
                {
                    "title": f.get("fname", ""),
                    "subtitle": f"ID: {f.get('fid', 0)}",
                    "icon": ft.icons.FORUM,
                    "trailing": [
                        ft.Icon(
                            ft.icons.CHECK_CIRCLE,
                            color="primary",
                            size=16,
                            visible=f.get("is_sign_today", False),
                        ),
                    ],
                }
                for f in forums
            ]
        super().__init__(items=items, **kwargs)


class SignResultList(StreamList):
    """签到结果列表"""

    def __init__(self, results: list[dict] | None = None, **kwargs):
        items = None
        if results:
            items = [
                {
                    "title": r.get("fname", ""),
                    "subtitle": r.get("message", ""),
                    "icon": ft.icons.CHECK_CIRCLE if r.get("success") else ft.icons.ERROR,
                    "trailing": [],
                }
                for r in results
            ]
        super().__init__(items=items, **kwargs)
