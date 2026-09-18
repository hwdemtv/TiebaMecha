"""Main Flet application - TiebaMecha"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

import flet as ft

from ..db.crud import get_db
from .utils import with_opacity
from .components import get_dark_theme, get_light_theme, icons

if TYPE_CHECKING:
    from tieba_mecha.db.crud import Database

# 已通过 Web 认证的会话 ID 集合（模块级持久化，跨页面刷新存活）
_authenticated_sessions: set[str] = set()

# 页面懒加载映射（避免启动时导入所有模块）
PAGE_MODULES = {
    "dashboard": ("dashboard", "DashboardPage"),
    "accounts": ("accounts", "AccountsPage"),
    "welcome": ("welcome", "WelcomePage"),
    "login": ("login", "LoginPage"),
    "sign": ("sign", "SignPage"),
    "posts": ("posts", "PostsPage"),
    "proxy": ("proxy", "ProxyPage"),
    "rules": ("rules", "RulesPage"),
    "batch_post": ("batch_post_page", "BatchPostPage"),
    "settings": ("settings", "SettingsPage"),
    "survival": ("survival", "SurvivalPage"),
}


class TiebaMechaApp:
    """TiebaMecha 主应用 - 路由管理与全局状态控制"""

    def __init__(self, page: ft.Page):
        self.page = page
        self.db: Database | None = None
        self.current_page: str = "dashboard"
        self._pages_cache: dict = {}  # 页面实例缓存，避免重复创建导致状态丢失

        self._setup_page()

    def _setup_page(self):
        """初始化页面设置"""
        self.page.title = "TiebaMecha | Command Center"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.theme = get_dark_theme()
        self.page.dark_theme = get_dark_theme()

        # Web 刷新时清除残留对话框（on_connect 在浏览器重新连接时触发）
        self.page.on_connect = self._on_web_reconnect

        # 窗口设置
        self.page.window.width = 1100
        self.page.window.height = 750
        self.page.window.min_width = 1000
        self.page.window.min_height = 650

        # 侧边导航栏 (Compact Aesthetic)
        self.nav_rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=80,
            min_extended_width=160,
            bgcolor=with_opacity(0.1, "surface"),
            destinations=[
                # --- 📊 监控中心 ---
                ft.NavigationRailDestination(
                    icon=icons.RADAR,
                    selected_icon=icons.RADAR,
                    label="指挥中心",
                ),
                # --- 👤 账号资源 ---
                ft.NavigationRailDestination(
                    icon=icons.ACCOUNT_CIRCLE_OUTLINED,
                    selected_icon=icons.ACCOUNT_CIRCLE,
                    label="账号列表",
                ),
                ft.NavigationRailDestination(
                    icon=icons.VPN_LOCK_OUTLINED,
                    selected_icon=icons.VPN_LOCK,
                    label="代理池",
                ),

                # --- ⚡ 核心执行 ---
                ft.NavigationRailDestination(
                    icon=icons.BOLT_OUTLINED,
                    selected_icon=icons.BOLT,
                    label="全域签到",
                ),
                ft.NavigationRailDestination(
                    icon=icons.SEND_ROUNDED,
                    selected_icon=icons.SEND_ROUNDED,
                    label="批量发帖",
                ),
                ft.NavigationRailDestination(
                    icon=icons.FORUM_OUTLINED,
                    selected_icon=icons.FORUM,
                    label="帖子管理",
                ),

                # --- 🤖 智能策略 ---
                ft.NavigationRailDestination(
                    icon=icons.SHIELD_OUTLINED,
                    selected_icon=icons.SHIELD,
                    label="自动化规则",
                ),

                # --- ⚙️ 系统设置 ---
                ft.NavigationRailDestination(
                    icon=icons.SETTINGS_OUTLINED,
                    selected_icon=icons.SETTINGS,
                    label="全局设置",
                ),

                # --- 📊 存活分析 ---
                ft.NavigationRailDestination(
                    icon=icons.ANALYTICS_OUTLINED,
                    selected_icon=icons.ANALYTICS,
                    label="存活分析",
                ),
            ],
            on_change=self._on_nav_change,
        )

        # 通知铃铛（延迟绑定 on_click）
        from .components.notification_bell import NotificationBell
        self.notification_bell = NotificationBell(
            page=self.page,
            on_click=lambda _: self.page.run_task(self._show_notifications)
        )

        # 将铃铛设置为侧边栏头部
        self.nav_rail.leading = ft.Container(
            content=self.notification_bell,
            padding=ft.padding.only(top=20, bottom=10),
            alignment=ft.alignment.center,
        )

        # 内容预览区
        self.content_area = ft.Container(
            padding=0,
            expand=True,
            bgcolor="background",
            animate=ft.Animation(400, ft.AnimationCurve.DECELERATE),
        )

        # 组合布局
        self.page.add(
            ft.Row(
                controls=[
                    self.nav_rail,
                    ft.VerticalDivider(width=1, color=with_opacity(0.1, "onSurface")),
                    self.content_area,
                ],
                expand=True,
                spacing=0,
            )
        )

    async def initialize(self, db: Database):
        """初始化数据库和其他异步资源"""
        self.db = db

        # 密码重置模式：环境变量 TIEBA_MECHA_WEB_PASSWORD_RESET=true 时跳过认证并清除密码
        import os
        if os.getenv("TIEBA_MECHA_WEB_PASSWORD_RESET", "").lower() == "true":
            from ..core.web_auth import clear_password
            await clear_password(db)
            os.environ.pop("TIEBA_MECHA_WEB_PASSWORD_RESET", None)
            from ..core.logger import log_info
            await log_info("密码重置模式：已清除 Web 密码，可进入设置页重新配置")

        # Web 认证检查：未认证则显示登录页（设置密码/登录均由 login 页处理），跳过后台任务初始化
        if self.page.session_id not in _authenticated_sessions:
            self._show_login_only()
            await self._navigate("login")
            return

        # 认证已通过，执行完整初始化
        await self._full_initialize(db)

    def _show_login_only(self):
        """隐藏导航栏，仅显示登录页"""
        self.nav_rail.visible = False
        # 隐藏分隔线（导航栏右侧的 VerticalDivider）
        if self.page.controls and isinstance(self.page.controls[0], ft.Row):
            row = self.page.controls[0]
            for ctrl in row.controls:
                if isinstance(ctrl, ft.VerticalDivider):
                    ctrl.visible = False
        self.page.update()

    def _show_main_ui(self):
        """显示导航栏和主界面"""
        self.nav_rail.visible = True
        if self.page.controls and isinstance(self.page.controls[0], ft.Row):
            row = self.page.controls[0]
            for ctrl in row.controls:
                if isinstance(ctrl, ft.VerticalDivider):
                    ctrl.visible = True
        self.page.update()

    async def _on_login_success(self):
        """登录成功回调"""
        _authenticated_sessions.add(self.page.session_id)
        self._show_main_ui()
        await self._full_initialize(self.db)

    async def _full_initialize(self, db: Database):
        """完整初始化（认证通过后执行）"""
        # 延迟导入重模块
        from ..core.logger import log_info, log_warn, log_error
        from ..core.notification import init_notification_manager, get_notification_manager
        from ..core.updater import get_update_manager
        from .components.notification_bell import show_notification_dialog

        # 初始化通知管理器
        nm = init_notification_manager(db=db, page=self.page)
        self.notification_bell.set_notification_manager(nm)
        await self.notification_bell.refresh()

        # 初始化更新管理器
        get_update_manager(db=db)

        # 启动时自动回填击穿数 & 同步本土作战状态
        try:
            backfilled = await self.db.backfill_success_count()
            if backfilled > 0:
                from ..core.logger import log_info
                await log_info(f"启动回填：更新了 {backfilled} 条击穿数记录")
            await self.db.auto_sync_post_target()
        except Exception as e:
            from ..core.logger import log_warn
            await log_warn(f"启动回填异常（非致命）: {e}")

        # 检查是否是首次运行（无账号）
        accounts = await self.db.get_accounts()
        if not accounts:
            await self._navigate("welcome")
        else:
            await self._navigate("dashboard")

        # 启动进程级自动化（daemon + 心跳/代理巡检/通知同步/更新检测）。
        # 关键：必须用 asyncio.create_task 而非 page.run_task——后者把任务
        # 绑定到浏览器会话，会话断开即取消，导致无人值守时自动化静默停止；
        # AutomationManager 幂等，后续会话接入不会重复启动。
        from .runtime import AutomationManager
        AutomationManager.set_ui_hook(self._refresh_notification_bell)
        started = await AutomationManager.ensure_started(self.db)

        if started:
            await log_info("TiebaMecha 系统内聚核动力引擎已启动（进程级，会话断开不影响）")
        else:
            await log_info("自动化引擎已在运行，本会话接入观察模式")

    async def _refresh_notification_bell(self):
        """刷新通知铃（UI 侧钩子；会话断开时由 runtime 吞掉异常）"""
        await self.notification_bell.refresh()

    async def _show_notifications(self, _=None):
        """显示通知对话框（延迟导入）"""
        from ..core.notification import get_notification_manager
        from .components.notification_bell import show_notification_dialog
        await show_notification_dialog(self.page, get_notification_manager())

    def _on_web_reconnect(self, e):
        """浏览器刷新/重连时清除残留的对话框，忽略已取消的 Future 回调"""
        try:
            overlay_to_keep = []
            for ctrl in self.page.overlay:
                # 仅保留 FilePicker 等非对话框控件
                if isinstance(ctrl, ft.FilePicker):
                    overlay_to_keep.append(ctrl)
            self.page.overlay.clear()
            self.page.overlay.extend(overlay_to_keep)
            self.page.update()
        except (asyncio.CancelledError, Exception):
            pass

    def _on_nav_change(self, e):
        """处理导航切换"""
        dest_map = {
            0: "dashboard",
            1: "accounts",
            2: "proxy",
            3: "sign",
            4: "batch_post",
            5: "posts",
            6: "rules",
            7: "settings",
            8: "survival",
        }
        page_name = dest_map.get(e.control.selected_index, "dashboard")
        self.page.run_task(self._navigate, page_name)

    async def _navigate(self, page_name: str):
        """页面路由跳转核心逻辑"""
        try:
            # 导航离开时清理旧页面（取消订阅、停止后台任务等）
            old_page_obj = self._pages_cache.get(self.current_page)
            if old_page_obj and hasattr(old_page_obj, "cleanup"):
                old_page_obj.cleanup()

            self.current_page = page_name

            # 清除残留的对话框（Web 刷新后 page.overlay 中可能残留之前打开的 AlertDialog/BottomSheet）
            overlay_to_keep = []
            for ctrl in self.page.overlay:
                # 保留 FilePicker 等非对话框控件
                if isinstance(ctrl, ft.FilePicker):
                    overlay_to_keep.append(ctrl)
            self.page.overlay.clear()
            self.page.overlay.extend(overlay_to_keep)

            # 尝试从缓存获取页面对象
            if page_name in self._pages_cache:
                page_obj = self._pages_cache[page_name]
            else:
                # 懒加载页面模块
                module_name, class_name = PAGE_MODULES.get(page_name, ("dashboard", "DashboardPage"))
                from importlib import import_module
                module = import_module(f".pages.{module_name}", package=__name__.rsplit(".", 1)[0])
                page_class = getattr(module, class_name)

                page_obj = page_class(self.page, self.db, self._navigate_sync)
                self._pages_cache[page_name] = page_obj  # 缓存页面对象

            # 登录页注入认证成功回调
            if page_name == "login" and hasattr(page_obj, "set_success_callback"):
                page_obj.set_success_callback(self._on_login_success)

            # 每次导航都重新 build 并挂载（确保 UI 控件引用一致）
            self.content_area.content = page_obj.build()
            self.page.update()
            if hasattr(page_obj, "load_data"):
                await page_obj.load_data()
                # 数据加载完成后，允许页面自定义处理（如下拉框更新）
                if hasattr(page_obj, "on_data_loaded"):
                    page_obj.on_data_loaded()
                self.page.update()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.page.show_snack_bar(ft.SnackBar(content=ft.Text(f"路由错误: {e}"), bgcolor="error"))
            self.page.update()

    def _navigate_sync(self, page_name: str):
        """供子页面使用的同步导航回调"""
        self.page.run_task(self._navigate, page_name)

def run_app(port: int = 9006, host: str | None = None):
    """启动 Flet 应用"""
    # 修复：aiotieba 导入后会将 logging level 30 的名称从标准的 'WARNING'
    # 覆盖为 'WARN'，导致 flet_runtime 传给 uvicorn 的日志级别字符串为 'warn'，
    # 而 uvicorn 的 LOG_LEVELS 字典只接受 'warning'，因此抛出 KeyError。
    # 在此恢复标准名称以确保兼容性。
    logging.addLevelName(logging.WARNING, "WARNING")

    async def main(page: ft.Page):
        app = TiebaMechaApp(page)
        db = await get_db()
        await app.initialize(db)

    # 兼容不同 Flet 版本：优先使用 ft.run()，否则回退到 ft.app()
    kwargs = dict(port=port, view=ft.AppView.WEB_BROWSER)
    if host:
        kwargs["host"] = host
    if hasattr(ft, 'run'):
        ft.run(target=main, **kwargs)
    else:
        ft.app(target=main, **kwargs)


if __name__ == "__main__":
    run_app()
