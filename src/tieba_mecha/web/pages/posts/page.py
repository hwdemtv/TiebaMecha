"""帖子管理主页面：发布新帖 / 我的帖子 / 批量操作与分析 三场景拆分。

场景拆分目标（避免新建帖子与处理历史帖子相互干扰）：
- 发布新帖：实时校验 + 发布摘要 + AI 原文/建议对比采纳
- 我的帖子：筛选、存活四态监控、详情抽屉、文字+图标行内操作
- 批量操作与分析：选中后按风险分组操作栏、影响预览+二次确认、分析卡片
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

import flet as ft

from ...flet_compat import COLORS
from ...utils import with_opacity
from ...components.icons import (
    ANALYTICS_OUTLINED,
    ARROW_BACK_IOS_NEW,
    FORUM_OUTLINED,
    POST_ADD,
)
from .publish import PublishTabMixin
from .my_posts import MyPostsTabMixin
from .batch_ops import BatchOpsTabMixin
from .detail import DetailDrawerMixin

if TYPE_CHECKING:
    from tieba_mecha.db.crud import Database

PAGE_SIZE = 20


class PostsPage(
    PublishTabMixin,
    MyPostsTabMixin,
    BatchOpsTabMixin,
    DetailDrawerMixin,
):
    """帖子管理页面（三场景）"""

    def __init__(self, page: ft.Page, db=None, on_navigate: Callable | None = None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate

        # 共享数据
        self._accounts: list = []
        self._active_account = None
        self._account_name_map: dict[int, str] = {}
        self._forums: list = []          # 当前账号关注的贴吧（含状态标记）
        self._publish_forums: list = []  # 发布页选中账号的关注贴吧（按账号隔离校验）
        self._publish_account_id: int | None = None  # 发布页选中的发帖账号
        self._ai_ready: bool = False
        self._ai_model: str = ""

        # 统一帖子行（get_my_posts 结果），供"我的帖子/批量"两个 Tab 共用
        self._rows: list = []
        self._mine_page: int = 1         # 我的帖子 分页
        self._batch_page: int = 1        # 批量 分页
        self._page_size: int = PAGE_SIZE
        self._selected: set[str] = set() # 批量选中键："m{material_id}" / "r{tid}"

        self._detail_row = None          # 当前详情面板展示的行
        self._posting = False
        self._proxy_summaries: dict[int, str] = {}  # proxy_id -> "host:port"
        self._export_rows: list = []     # 待导出行（FilePicker 异步回填用）

    # ---------- 共享小工具 ----------

    def row_key(self, row) -> str:
        return f"m{row.material_id}" if row.src == "material" else f"r{row.tid}"

    def _account_display(self, account_id) -> str:
        if not account_id:
            return "-"
        return self._account_name_map.get(account_id, f"账号-{account_id}")

    def _forum_by_name(self, fname: str):
        """在当前发布账号的关注贴吧中查找（发布校验按账号隔离）。"""
        for f in self._publish_forums:
            if f.fname == fname:
                return f
        return None

    def _show_snackbar(self, message: str, type: str = "info"):
        from ...components.toast import show_toast
        show_toast(self.page, message, type)

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)

    # ---------- 数据加载（app.py 导航时调用） ----------

    async def load_data(self):
        if not self.db:
            return

        self._accounts = await self.db.get_accounts()
        self._account_name_map = {
            a.id: (a.name or f"账号-{a.id}") for a in self._accounts
        }
        self._active_account = await self.db.get_active_account()
        if self._active_account:
            self._forums = await self.db.get_forums(self._active_account.id)
        else:
            self._forums = []

        # AI 配置状态（发布摘要展示用）
        try:
            api_key = await self.db.get_setting("ai_api_key", "")
            self._ai_model = await self.db.get_setting("ai_model", "glm-4-flash")
            self._ai_ready = bool(api_key)
        except Exception:
            self._ai_ready, self._ai_model = False, ""

        # 预取账号绑定代理摘要（发布摘要展示用）——并行查询避免 N+1 串行
        self._proxy_summaries = {}
        proxy_ids = sorted({a.proxy_id for a in self._accounts if getattr(a, "proxy_id", None)})
        if proxy_ids:
            proxies = await asyncio.gather(
                *[self.db.get_proxy(pid) for pid in proxy_ids],
                return_exceptions=True,
            )
            for pid, p in zip(proxy_ids, proxies):
                if isinstance(p, Exception) or not p:
                    self._proxy_summaries[pid] = f"代理 #{pid}（已失效）" if not isinstance(p, Exception) else f"代理 #{pid}"
                else:
                    self._proxy_summaries[pid] = f"{p.host}:{p.port} ({p.protocol})"

        # 填充发布页下拉框（账号 + 按选中账号加载其贴吧）
        self._fill_publish_dropdowns()
        await self._load_publish_forums()
        # 填充筛选下拉框（默认只看当前账号）
        self._fill_filter_dropdowns()
        # 发布摘要与实时校验
        self._refresh_publish_summary()
        self._run_validation()

        # 同步头部账号切换芯片（发布校验按当前账号隔离）
        if hasattr(self, "account_chip"):
            await self.account_chip.refresh()

        await self._reload_rows(first_load=True)
        self.page.update()

    def on_data_loaded(self):
        self.page.update()

    async def _reload_after_account_switch(self):
        """芯片切换账号后：重置发布账号下拉为新活跃账号，再原位重载。

        _fill_publish_dropdowns 会保留用户已选；这里主动清空值，
        使回落逻辑选中切换后的活跃账号。
        """
        self.post_account.value = None
        await self.load_data()

    async def _reload_rows(self, first_load: bool = False, reset_page: bool = False):
        """按当前筛选加载统一帖子行，并同步刷新"我的帖子/批量"两个 Tab。"""
        if not self.db:
            return
        filters = self._collect_filters()
        try:
            self._rows = await self.db.get_my_posts(**filters)
        except Exception as ex:
            self._rows = []
            self._show_snackbar(f"帖子数据加载失败: {ex}", "error")

        if reset_page or first_load:
            self._mine_page = 1
            self._batch_page = 1
        # 选中键失效清理
        valid_keys = {self.row_key(r) for r in self._rows}
        self._selected &= valid_keys

        self._update_mine_list()
        self._update_batch_list()
        self._update_stats()           # 我的帖子：存活徽章
        self._update_batch_analysis()  # 批量：分析卡片
        self._update_batch_hint()
        self.page.update()

    # ---------- 页面构建 ----------

    def build(self) -> ft.Control:
        # 头部账号切换芯片（发布校验按当前账号隔离）
        from ...components.account_switcher import AccountSwitchChip
        self.account_chip = AccountSwitchChip(
            self.page, self.db,
            on_switched=self._reload_after_account_switch,
        )

        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=ARROW_BACK_IOS_NEW,
                        icon_size=16,
                        on_click=lambda e: self._navigate("dashboard"),
                        style=ft.ButtonStyle(
                            color=COLORS.PRIMARY,
                            bgcolor={"": with_opacity(0.1, COLORS.PRIMARY)},
                        ),
                    ),
                    padding=5,
                ),
                ft.Column(
                    controls=[
                        ft.Text("帖子管理 / POST CONTROL", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("发布、管理与复盘三种场景，互不干扰", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
                ft.Container(expand=True),
                self.account_chip,
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # 三场景 Tab
        self.tabs = ft.Tabs(
            selected_index=0,
            animation_duration=200,
            label_color=COLORS.PRIMARY,
            unselected_label_color="onSurfaceVariant",
            indicator_color=COLORS.PRIMARY,
            indicator_tab_size=True,
            tabs=[
                ft.Tab(icon=POST_ADD, text="发布新帖", content=self._build_publish_tab()),
                ft.Tab(icon=FORUM_OUTLINED, text="我的帖子", content=self._build_my_posts_tab()),
                ft.Tab(icon=ANALYTICS_OUTLINED, text="批量操作与分析", content=self._build_batch_tab()),
            ],
            on_change=self._on_tab_change,
            expand=False,
        )

        # 右侧详情面板（抽屉）：默认隐藏，打开时占据固定宽度
        self._detail_panel = ft.Container(
            content=None,
            width=420,
            visible=False,
            animate=ft.Animation(250, ft.AnimationCurve.DECELERATE),
            padding=ft.padding.only(left=12),
        )
        self._detail_divider = ft.VerticalDivider(
            width=1, color=with_opacity(0.1, "onSurface"), visible=False,
        )

        body = ft.Row(
            controls=[
                ft.Container(content=self.tabs, expand=True),
                self._detail_divider,
                self._detail_panel,
            ],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
            spacing=0,
        )

        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                body,
            ], spacing=10),
            padding=20,
            expand=True,
        )

    async def _on_tab_change(self, e):
        # 切换 Tab 时收起详情面板，保持各场景视觉独立
        self.close_detail()
        self.page.update()
