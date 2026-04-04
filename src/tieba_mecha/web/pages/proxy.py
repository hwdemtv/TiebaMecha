"""Proxy management page with Cyber-Mecha aesthetic"""

import asyncio
import flet as ft
from typing import List, Optional

from ..components import create_gradient_button
from ..utils import with_opacity
from ...core.proxy import test_proxy
from ..components.icons import (
    ARROW_BACK_IOS_NEW, ADD_LINK_ROUNDED, SEARCH, SPEED, 
    DELETE_SWEEP, LINK_OFF, VPN_LOCK, HTTP, SPEED_ROUNDED, 
    EDIT_NOTE_ROUNDED, DELETE_OUTLINE, CHECK, SAVE_ROUNDED,
    EDIT_ROUNDED, SYNC_ROUNDED
)


class ProxyPage:
    """代理管理页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._proxies = []
        self._search_text = ""
        self._selected_ids = set()

    async def load_data(self):
        """加载数据"""
        if self.db:
            self._proxies = await self.db.get_active_proxies()
            self.refresh_ui()

    def refresh_ui(self):
        if hasattr(self, "proxy_list"):
            self.proxy_list.controls = self._build_proxy_items()
            self.page.update()

    def build(self) -> ft.Control:
        # 标题区域
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=ARROW_BACK_IOS_NEW,
                        icon_size=16,
                        on_click=lambda e: self._navigate("dashboard"),
                        style=ft.ButtonStyle(
                            color=ft.colors.PRIMARY,
                            bgcolor={"": with_opacity(0.1, ft.colors.PRIMARY)},
                        ),
                    ),
                    padding=5,
                ),
                ft.Column(
                    controls=[
                        ft.Text("代理池 / PROXY POOL", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("配置多级代理以保证网络访问的隐匿性与稳定性", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
                ft.Container(expand=True),
                create_gradient_button(
                    text="添加代理",
                    icon=ADD_LINK_ROUNDED,
                    on_click=self._show_add_dialog,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # 搜索栏
        search_field = ft.TextField(
            hint_text="搜索 IP、端口或协议...",
            prefix_icon=SEARCH,
            border_radius=10,
            text_size=13,
            on_change=self._on_search_change,
            bgcolor=with_opacity(0.05, "onSurface"),
            border_color=with_opacity(0.1, "primary"),
            expand=True,
            height=45,
        )

        bulk_actions = ft.Row([
            ft.TextButton("批量测速", icon=SPEED, on_click=self._bulk_test_proxies, visible=False),
            ft.TextButton("批量删除", icon=DELETE_SWEEP, style=ft.ButtonStyle(color="error"), on_click=self._bulk_delete_proxies, visible=False),
        ], spacing=10)
        self.bulk_bar = bulk_actions

        # 代理列表容器
        self.proxy_list = ft.Column(
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # 主布局
        return ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Row([search_field, ft.Checkbox(label="全选", on_change=self._toggle_select_all)], spacing=10),
                    self.bulk_bar,
                    ft.Divider(color=with_opacity(0.1, "primary"), height=10),
                    ft.Container(
                        content=self.proxy_list,
                        expand=True,
                    ),
                ],
                spacing=10,
            ),
            padding=20,
            expand=True,
        )

    def _build_proxy_items(self) -> list[ft.Control]:
        items = []
        if not self._proxies:
            items.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(LINK_OFF, size=50, color="onSurfaceVariant"),
                            ft.Text("暂无可用代理节点", color="onSurfaceVariant"),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=50,
                    alignment=ft.alignment.center,
                )
            )
            return items

        search_lower = self._search_text.lower()
        for p in self._proxies:
            # 过滤逻辑
            if search_lower:
                match = (
                    search_lower in p.host.lower() or
                    search_lower in str(p.port) or
                    search_lower in p.protocol.lower()
                )
                if not match: continue

            is_active = p.is_active
            is_selected = p.id in self._selected_ids

            card = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Checkbox(value=is_selected, data=p.id, on_change=self._on_item_select),
                        # 协议图标
                        ft.Container(
                            content=ft.Icon(
                                VPN_LOCK if p.protocol == "socks5" else HTTP,
                                color="primary" if is_active else "onSurfaceVariant",
                                size=24,
                            ),
                            padding=10,
                            bgcolor=with_opacity(0.05, "primary") if is_active else with_opacity(0.05, "onSurface"),
                            border_radius=8,
                        ),
                        # 代理信息
                        ft.Column(
                            controls=[
                                ft.Text(f"{p.host}:{p.port}", color="onSurface", size=14, weight=ft.FontWeight.W_500),
                                ft.Row([
                                    ft.Text(f"协议: {p.protocol.upper()}", color="onSurfaceVariant", size=11),
                                    ft.Container(width=10),
                                    ft.Text(f"故障率: {p.fail_count}", color="error" if p.fail_count > 0 else "onSurfaceVariant", size=11),
                                ], spacing=4),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        # 状态标识
                        ft.Container(
                            content=ft.Text("ONLINE" if is_active else "OFFLINE", size=9, weight=ft.FontWeight.BOLD, color="black"),
                            bgcolor="primary" if is_active else "onSurfaceVariant",
                            padding=ft.padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                        ),
                        # 操作按钮
                        ft.Row([
                            ft.IconButton(
                                icon=SPEED_ROUNDED,
                                icon_color="primary",
                                tooltip="测试连通性",
                                on_click=lambda e, pr=p: self.page.run_task(self._on_test_click, pr, e),
                            ),
                            ft.IconButton(
                                icon=EDIT_NOTE_ROUNDED,
                                icon_color="primary",
                                tooltip="编辑节点信息",
                                on_click=lambda e, pr=p: self._show_edit_dialog(pr),
                            ),
                            ft.IconButton(
                                icon=DELETE_OUTLINE,
                                icon_color="error",
                                tooltip="移除节点",
                                on_click=lambda e, pid=p.id: self.page.run_task(self._delete_proxy, pid),
                            ),
                        ], spacing=0),
                    ],
                ),
                bgcolor=with_opacity(0.02, "primary") if is_active else with_opacity(0.02, "onSurface"),
                border=ft.border.all(1, with_opacity(0.1, "primary") if is_active else with_opacity(0.1, "onSurface")),
                border_radius=10,
                padding=10,
            )
            items.append(card)
        return items

    def _show_add_dialog(self, e):
        """显示添加代理对话框"""
        host_f = ft.TextField(label="服务器主机 (IP/Domain)", hint_text="127.0.0.1")
        port_f = ft.TextField(label="端口", hint_text="8080", width=120)
        protocol_f = ft.Dropdown(
            label="协议类型",
            options=[ft.dropdown.Option("http"), ft.dropdown.Option("socks5")],
            value="http"
        )
        user_f = ft.TextField(label="用户名 (可选)")
        pass_f = ft.TextField(label="密码 (可选)", password=True, can_reveal_password=True)

        async def on_submit(e):
            if self.db and host_f.value and port_f.value:
                try:
                    await self.db.add_proxy(
                        host=host_f.value,
                        port=int(port_f.value),
                        protocol=protocol_f.value,
                        username=user_f.value,
                        password=pass_f.value
                    )
                    self.page.close(dialog)
                    await self.load_data()
                    self._show_snackbar("新节点已连接并加入池", "success")
                except Exception as ex:
                    self._show_snackbar(f"添加失败: {str(ex)}", "error")

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(ADD_LINK_ROUNDED, color="primary"), ft.Text("部署新代理节点")]),
            content=ft.Column([
                ft.Text("输入远程代理服务器参数:", size=12, color="onSurfaceVariant"),
                ft.Row([host_f, port_f], spacing=10),
                protocol_f, user_f, pass_f
            ], tight=True, spacing=15, width=450),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("保存节点", icon=CHECK, on_click=on_submit),
            ],
        )
        self.page.open(dialog)

    def _show_edit_dialog(self, proxy):
        """显示修改代理节点对话框（预填当前数据）"""
        from ...core.account import decrypt_value

        # 解密现有凭据
        user_val = ""
        pass_val = ""
        if proxy.username:
            try:
                user_val = decrypt_value(proxy.username)
            except Exception:
                user_val = proxy.username
        if proxy.password:
            try:
                pass_val = decrypt_value(proxy.password)
            except Exception:
                pass_val = proxy.password

        host_f = ft.TextField(label="服务器主机", value=proxy.host)
        port_f = ft.TextField(label="端口", value=str(proxy.port), width=120)
        protocol_f = ft.Dropdown(
            label="协议类型",
            options=[ft.dropdown.Option("http"), ft.dropdown.Option("socks5")],
            value=proxy.protocol
        )
        user_f = ft.TextField(label="用户名 (可选)", value=user_val)
        pass_f = ft.TextField(label="密码 (可选)", password=True, can_reveal_password=True, value=pass_val)

        save_btn = ft.FilledButton("保存修改", icon=SAVE_ROUNDED)

        async def on_save(e):
            if not host_f.value or not port_f.value:
                self._show_snackbar("主机和端口不能为空", "error")
                return
            try:
                save_btn.disabled = True
                self.page.update()
                await self.db.update_proxy(
                    proxy.id,
                    host=host_f.value,
                    port=int(port_f.value),
                    protocol=protocol_f.value,
                    username=user_f.value,
                    password=pass_f.value,
                    is_active=True,
                )
                self.page.close(dialog)
                await self.load_data()
                self._show_snackbar("节点配置已更新", "success")
            except Exception as ex:
                save_btn.disabled = False
                self._show_snackbar(f"保存失败: {str(ex)}", "error")
                self.page.update()

        save_btn.on_click = on_save

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(EDIT_ROUNDED, color="primary"), ft.Text("修改代理节点")]),
            content=ft.Column([
                ft.Text("请更新该代理服务器的连接参数:", size=12, color="onSurfaceVariant"),
                ft.Row([host_f, port_f], spacing=10),
                protocol_f, user_f, pass_f
            ], tight=True, spacing=15, width=450),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                save_btn,
            ],
        )
        self.page.open(dialog)

    async def _delete_proxy(self, pid: int):
        if self.db:
            await self.db.delete_proxy(pid)
            await self.load_data()
            self._show_snackbar("节点已从池中移除", "info")

    async def _on_test_click(self, proxy, e):
        try:
            e.control.disabled = True
            e.control.icon = SYNC_ROUNDED
            self.page.update()

            proxy_url = f"{proxy.protocol}://{proxy.host}:{proxy.port}"
            success, msg = await test_proxy(proxy_url, proxy.username, proxy.password)

            if success:
                self._show_snackbar(f"节点测试通过！延迟: {msg}", "success")
            else:
                self._show_snackbar(f"连通性故障: {msg}", "error")
        except Exception as ex:
            self._show_snackbar(f"执行测速时发生严重异常: {str(ex)}", "error")
        finally:
            e.control.disabled = False
            e.control.icon = SPEED_ROUNDED
            self.page.update()

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))

    def _on_search_change(self, e):
        self._search_text = e.control.value
        self.refresh_ui()

    def _on_item_select(self, e):
        pid = e.control.data
        if e.control.value:
            self._selected_ids.add(pid)
        else:
            self._selected_ids.discard(pid)
        self._update_bulk_bar()

    def _toggle_select_all(self, e):
        if e.control.value:
            self._selected_ids = {p.id for p in self._proxies}
        else:
            self._selected_ids.clear()
        self.refresh_ui()
        self._update_bulk_bar()

    def _update_bulk_bar(self):
        has_sel = len(self._selected_ids) > 0
        if hasattr(self, "bulk_bar"):
            self.bulk_bar.controls[0].visible = has_sel
            self.bulk_bar.controls[1].visible = has_sel
            self.bulk_bar.controls[0].text = f"批量测速 ({len(self._selected_ids)})"
            self.bulk_bar.controls[1].text = f"批量删除 ({len(self._selected_ids)})"
            self.page.update()

    async def _bulk_test_proxies(self, e):
        if not self._selected_ids: return
        self._show_snackbar(f"正在启动 {len(self._selected_ids)} 个节点的并行链路测试...", "info")
        for pid in list(self._selected_ids):
            p = next((x for x in self._proxies if x.id == pid), None)
            if p:
                # 寻找对应的测试按钮触发（简单处理直接调用核心逻辑）
                await self._on_test_click(p, None) 
        self._selected_ids.clear()
        self._update_bulk_bar()
        self.refresh_ui()

    async def _bulk_delete_proxies(self, e):
        if not self._selected_ids: return
        count = len(self._selected_ids)
        for pid in list(self._selected_ids):
            await self.db.delete_proxy(pid)
        self._selected_ids.clear()
        self._update_bulk_bar()
        await self.load_data()
        self._show_snackbar(f"成功移除 {count} 个代理节点", "success")
