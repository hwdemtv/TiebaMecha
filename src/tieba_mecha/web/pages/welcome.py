"""Welcome Wizard for initial account setup"""

import flet as ft
import asyncio
from ..utils import with_opacity
from ...core.account import verify_account, add_account as core_add_account

class WelcomePage:
    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate

    def build(self) -> ft.Control:
        # 隐藏导航栏（欢迎页不需要侧边栏）
        # self.page.controls[0].controls[0].visible = False
        
        self.cookie_input = ft.TextField(
            label="输入 Cookie (BDUSS/STOKEN)",
            hint_text="粘贴您的 BDUSS=...; STOKEN=... 字符串",
            multiline=True,
            min_lines=3,
            max_lines=5,
            border_color="primary",
        )
        
        self.status = ft.Text("系统未检测到账号，请先通过量子加密信道完成首次握手。", color="onSurfaceVariant")
        self.verify_btn = ft.FilledButton(
            "启动矩阵连接", 
            icon=ft.icons.SHADOWS, 
            on_click=self._on_verify,
            style=ft.ButtonStyle(bgcolor="primary")
        )

        return ft.Container(
            content=ft.Column([
                ft.Icon(ft.icons.SHIELD_ROUNDED, size=80, color="primary"),
                ft.Text("欢迎使用 TIEBAMECHA v1.1.0", size=24, weight=ft.FontWeight.BOLD),
                ft.Text("INITIALIZATION REQUIRED / 需要初始化", size=12, color="primary", weight=ft.FontWeight.W_300),
                ft.Divider(height=40, color="transparent"),
                ft.Container(
                    content=ft.Column([
                        ft.Text("第一步：获取登录凭据", size=14, weight=ft.FontWeight.BOLD),
                        ft.Text("在贴吧网页端 F12 找到 BDUSS 和 STOKEN。如果您不知道如何获取，请点击右侧教程。", size=12, color="onSurfaceVariant"),
                        ft.TextButton("《手把手：Cookie 提取教程》", icon=ft.icons.HELP_OUTLINE),
                    ], spacing=10),
                    padding=15,
                    bgcolor=with_opacity(0.03, "onSurface"),
                    border_radius=10,
                ),
                ft.Divider(height=20, color="transparent"),
                self.cookie_input,
                self.status,
                ft.Container(height=10),
                self.verify_btn,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            alignment=ft.alignment.center,
            padding=50,
            expand=True,
        )

    async def _on_verify(self, e):
        cookie_raw = self.cookie_input.value.strip()
        if not cookie_raw:
            self.status.value = "请先输入凭证数据。"
            self.status.color = "error"
            self.page.update()
            return

        self.verify_btn.disabled = True
        self.status.value = "正在验证量子签名..."
        self.status.color = "primary"
        self.page.update()

        # 简单提取逻辑
        bduss = ""
        stoken = ""
        if "BDUSS=" in cookie_raw:
            import re
            bduss_match = re.search(r"BDUSS=([^;]+)", cookie_raw)
            if bduss_match: bduss = bduss_match.group(1)
            stoken_match = re.search(r"STOKEN=([^;]+)", cookie_raw)
            if stoken_match: stoken = stoken_match.group(1)
        else:
            # 假设直接输入的是 BDUSS
            bduss = cookie_raw

        is_valid, uid, uname, msg = await verify_account(bduss, stoken)
        
        if is_valid:
            # 使用 core.add_account 进行加密存储
            await core_add_account(
                db=self.db,
                name=uname,
                bduss=bduss,
                stoken=stoken,
            )
            self._show_snackbar(f"验证成功！欢迎回来，{uname}。", "success")
            await asyncio.sleep(1)
            if self.on_navigate:
                self.on_navigate("dashboard")
        else:
            self.status.value = f"验证失败: {msg}"
            self.status.color = "error"
            self.verify_btn.disabled = False
        
        self.page.update()

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
