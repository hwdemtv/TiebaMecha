"""Login / Set Password page - Web authentication gate"""

import asyncio
import flet as ft
from ..flet_compat import COLORS
from ..utils import with_opacity
from ..components import icons
from ...core.web_auth import is_password_set, set_password, check_password


class LoginPage:
    """登录/设置密码页面 - 在进入主界面前拦截"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._is_setup_mode = False  # True = 设置密码模式，False = 登录模式
        self._on_success_callback = None  # 认证成功后的回调

    def set_success_callback(self, callback):
        """设置认证成功后的回调（由 app.py 注入）"""
        self._on_success_callback = callback

    async def load_data(self):
        """检测密码状态并切换界面"""
        if not self.db:
            return
        self._is_setup_mode = not await is_password_set(self.db)
        self._refresh_ui()

    def _refresh_ui(self):
        """根据模式刷新 UI"""
        if not hasattr(self, "password_field"):
            return

        if self._is_setup_mode:
            if hasattr(self, "title_text"): self.title_text.value = "设置访问密码"
            if hasattr(self, "subtitle_text"): self.subtitle_text.value = "INITIALIZE ACCESS / 首次配置"
            if hasattr(self, "hint_text"): self.hint_text.value = "为 Web 控制台设置访问密码"
            if hasattr(self, "confirm_field"): self.confirm_field.visible = True
            if hasattr(self, "submit_btn"): self.submit_btn.text = "激活防护矩阵"
            if hasattr(self, "status_text"): 
                self.status_text.value = "首次访问需要设置密码以保护控制台安全。"
                self.status_text.color = "onSurfaceVariant"
        else:
            if hasattr(self, "title_text"): self.title_text.value = "身份验证"
            if hasattr(self, "subtitle_text"): self.subtitle_text.value = "ACCESS CONTROL / 访问控制"
            if hasattr(self, "hint_text"): self.hint_text.value = "输入访问密码"
            if hasattr(self, "confirm_field"): self.confirm_field.visible = False
            if hasattr(self, "submit_btn"): self.submit_btn.text = "验证身份"
            if hasattr(self, "status_text"): 
                self.status_text.value = "请输入密码以访问控制台。"
                self.status_text.color = "onSurfaceVariant"

        self.page.update()

    def build(self) -> ft.Control:
        self.password_field = ft.TextField(
            label="密码",
            hint_text="输入访问密码",
            password=True,
            can_reveal_password=True,
            border_color="primary",
            width=320,
        )

        self.confirm_field = ft.TextField(
            label="确认密码",
            hint_text="再次输入密码",
            password=True,
            can_reveal_password=True,
            border_color="primary",
            width=320,
        )

        self.hint_text = ft.Text(
            "输入访问密码",
            color="onSurfaceVariant",
            size=13,
        )

        self.status_text = ft.Text(
            "检测认证状态中...",
            color="onSurfaceVariant",
            size=12,
        )

        self.title_text = ft.Text(
            "身份验证",
            size=24,
            weight=ft.FontWeight.BOLD,
        )

        self.subtitle_text = ft.Text(
            "ACCESS CONTROL / 访问控制",
            size=12,
            color="primary",
            weight=ft.FontWeight.W_300,
        )

        self.submit_btn = ft.FilledButton(
            "验证身份",
            icon=icons.LOCK_ROUNDED,
            on_click=self._on_submit,
            style=ft.ButtonStyle(bgcolor="primary"),
            width=320,
            height=44,
        )

        self.skip_btn = ft.TextButton(
            "暂不设置，直接进入",
            on_click=self._on_skip,
        )

        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(icons.LOCK_ROUNDED, size=80, color="primary"),
                    self.title_text,
                    self.subtitle_text,
                    ft.Divider(height=40, color="transparent"),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("安全提示", size=14, weight=ft.FontWeight.BOLD),
                                ft.Text(
                                    "设置密码后，每次访问 Web 控制台都需要输入密码验证身份。",
                                    size=12,
                                    color="onSurfaceVariant",
                                ),
                            ],
                            spacing=10,
                        ),
                        padding=15,
                        bgcolor=with_opacity(0.03, "onSurface"),
                        border_radius=10,
                        width=320,
                    ),
                    ft.Divider(height=20, color="transparent"),
                    self.hint_text,
                    self.password_field,
                    self.confirm_field,
                    self.status_text,
                    ft.Container(height=5),
                    self.submit_btn,
                    self.skip_btn,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
            alignment=ft.alignment.center,
            padding=50,
            expand=True,
        )

    async def _on_submit(self, e):
        """提交密码"""
        try:
            password = self.password_field.value.strip() if self.password_field.value else ""

            if not password:
                self.status_text.value = "请输入密码。"
                self.status_text.color = "error"
                self.page.update()
                return

            if len(password) < 4:
                self.status_text.value = "密码长度至少 4 位。"
                self.status_text.color = "error"
                self.page.update()
                return

            if self._is_setup_mode:
                # 设置密码模式
                confirm = self.confirm_field.value.strip() if self.confirm_field.value else ""
                if password != confirm:
                    self.status_text.value = "两次输入的密码不一致。"
                    self.status_text.color = "error"
                    self.page.update()
                    return

                self.submit_btn.disabled = True
                self.status_text.value = "正在配置安全矩阵..."
                self.status_text.color = "primary"
                self.page.update()

                await set_password(self.db, password)
                self._show_snackbar("密码设置成功！", "success")
                await self._auth_success()
            else:
                # 登录模式
                self.submit_btn.disabled = True
                self.status_text.value = "正在验证身份..."
                self.status_text.color = "primary"
                self.page.update()

                ok = await check_password(self.db, password)
                if ok:
                    self._show_snackbar("验证通过，欢迎回来。", "success")
                    await self._auth_success()
                else:
                    self.status_text.value = "密码错误，请重试。"
                    self.status_text.color = "error"
                    self.submit_btn.disabled = False
                    self.page.update()
        except asyncio.CancelledError:
            return

    async def _on_skip(self, e):
        """跳过密码设置"""
        try:
            await self._auth_success()
        except asyncio.CancelledError:
            return

    async def _auth_success(self):
        """认证成功，通知 app 进入主界面"""
        try:
            if self._on_success_callback:
                await self._on_success_callback()
        except asyncio.CancelledError:
            return

    def _show_snackbar(self, message: str, type: str = "info"):
        color = "primary"
        if type == "error":
            color = "error"
        elif type == "success":
            color = COLORS.GREEN
        self.page.show_snack_bar(
            ft.SnackBar(
                content=ft.Text(message),
                bgcolor=with_opacity(0.8, color),
                behavior=ft.SnackBarBehavior.FLOATING,
            )
        )
