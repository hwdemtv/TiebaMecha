"""Accounts management page with Cyber-Mecha aesthetic"""

import asyncio
import flet as ft
from typing import List, Optional

from ..components import create_gradient_button, icons
from ..utils import with_opacity
from ...core.account import add_account, list_accounts, switch_account, remove_account, parse_cookie, verify_account, refresh_account
from ...core.logger import log_info, log_warn, log_error


class AccountsPage:
    """账号管理页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._accounts = []
        self._active_id = None
        self._proxies = []
        self._search_text = ""
        self._filter_status = "all"
        self._selected_ids = set()

    async def load_data(self):
        """加载数据"""
        if not self.db:
            return
        
        # 加载账号
        self._accounts = await list_accounts(self.db)
        active_acc = await self.db.get_active_account()
        self._active_id = active_acc.id if active_acc else None
        
        # 加载代理列表用于下拉框
        self._proxies = await self.db.get_active_proxies()
        
        self.refresh_ui()

    def refresh_ui(self):
        """刷新 UI"""
        if hasattr(self, "account_list"):
            self.account_list.controls = self._build_account_items()
            self.page.update()

    def build(self) -> ft.Control:
        # 标题区域
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=icons.ARROW_BACK_IOS_NEW,
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
                        ft.Text("账号中心 / ACCOUNT CENTER", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("管理您的百度贴吧账号及其关联代理", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
                ft.Container(expand=True),
                create_gradient_button(
                    text="添加账号",
                    icon=icons.PERSON_ADD_ROUNDED,
                    on_click=self._show_add_dialog,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

    
        # 搜索与过滤栏
        search_field = ft.TextField(
            hint_text="搜索账号、用户名或UID...",
            prefix_icon=icons.SEARCH,
            border_radius=10,
            text_size=13,
            on_change=self._on_search_change,
            bgcolor=with_opacity(0.05, "onSurface"),
            border_color=with_opacity(0.1, "primary"),
            expand=True,
            height=45,
        )

        status_filter = ft.Dropdown(
            options=[
                ft.dropdown.Option("all", "全部状态"),
                ft.dropdown.Option("active", "🟢 正常"),
                ft.dropdown.Option("expired", "🔴 已失效"),
                ft.dropdown.Option("error", "🟡 异常"),
            ],
            value=self._filter_status,
            on_change=self._on_filter_change,
            width=120,
            height=45,
            content_padding=10,
            text_size=13,
            border_radius=10,
        )

        self.bulk_bar = ft.Row([
            ft.Checkbox(label="全选", on_change=self._toggle_select_all),
            ft.TextButton("批量验证", icon=icons.VERIFIED_USER, on_click=self._bulk_verify_accounts, visible=False),
            ft.TextButton("批量删除", icon=icons.DELETE_SWEEP, on_click=self._bulk_delete_accounts, style=ft.ButtonStyle(color="error"), visible=False),
        ], spacing=10, visible=True)

        # 账号列表容器
        self.account_list = ft.Column(
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # 主布局
        return ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Row([search_field, status_filter], spacing=10),
                    self.bulk_bar,
                    ft.Divider(color=with_opacity(0.1, "primary"), height=10),
                    ft.Container(
                        content=self.account_list,
                        expand=True,
                    ),
                ],
                spacing=10,
            ),
            padding=20,
            expand=True,
        )

    def _build_account_items(self) -> list[ft.Control]:
        items = []
        if not self._accounts:
            items.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(icons.PERSON_OFF, size=50, color="onSurfaceVariant"),
                            ft.Text("暂无账号，请点击右上角添加", color="onSurfaceVariant"),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=50,
                    alignment=ft.alignment.center,
                )
            )
            return items
        search_lower = self._search_text.lower()
        for acc in self._accounts:
            # 状态过滤
            status = getattr(acc, "status", "unknown")
            if self._filter_status != "all" and status != self._filter_status:
                continue

            # 搜索过滤
            if search_lower:
                match = (
                    search_lower in (acc.name or "").lower() or
                    search_lower in (acc.user_name or "").lower() or
                    search_lower in str(acc.user_id)
                )
                if not match:
                    continue

            is_active = acc.id == self._active_id
            is_selected = acc.id in self._selected_ids
            
            # 状态灯
            status = getattr(acc, "status", "unknown")
            last_v = getattr(acc, "last_verified", None)
            
            status_color = ft.colors.GREY_400
            if status == "active": status_color = ft.colors.GREEN_ACCENT_400
            elif status == "expired": status_color = ft.colors.ERROR
            elif status == "error": status_color = ft.colors.AMBER
            
            # 查找关联代理名称
            proxy_info = "直连"
            if acc.proxy_id:
                p = next((p for p in self._proxies if p.id == acc.proxy_id), None)
                if p: proxy_info = f"{p.protocol}://{p.host}"
            
            card = ft.Container(
                content=ft.Row(
                    controls=[
                        # 选择框
                        ft.Checkbox(value=is_selected, data=acc.id, on_change=self._on_item_select),
                        # 状态核心
                        ft.Container(
                            width=10, height=10, 
                            bgcolor=status_color, 
                            border_radius=5,
                            tooltip=f"状态: {status} | 最后检测: {last_v.strftime('%m-%d %H:%M') if last_v else '从未'}"
                        ),
                        ft.Container(width=5),
                        # 头像/图标
                        ft.Container(
                            content=ft.Icon(
                                icons.ACCOUNT_CIRCLE,
                                color="primary" if is_active else "onSurfaceVariant",
                                size=36,
                            ),
                            padding=5,
                        ),
                        # 信息
                        ft.Column(
                            controls=[
                                ft.Row([
                                    ft.Text(
                                        f"{acc.name} [{acc.user_name}]" if acc.user_name and acc.user_name != acc.name else (acc.name or acc.user_name),
                                        color="onSurface",
                                        size=15,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Container(
                                        content=ft.Text("ACTIVE", size=9, weight=ft.FontWeight.BOLD, color="black"),
                                        bgcolor="primary",
                                        padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                        border_radius=4,
                                        visible=is_active,
                                    ),
                                ], spacing=8),
                                ft.Row([
                                    ft.Icon(icons.FINGERPRINT, size=12, color="onSurfaceVariant"),
                                    ft.Text(f"UID: {acc.user_id}", color="onSurfaceVariant", size=11),
                                    ft.Container(width=10),
                                    ft.Icon(icons.PHONELINK_LOCK_ROUNDED, size=12, color="onSurfaceVariant"),
                                    ft.Text(f"标识: {getattr(acc, 'cuid', '')[:8]}...", color="onSurfaceVariant", size=11, tooltip=f"完整指纹: {getattr(acc, 'cuid', '')}"),
                                    ft.Container(width=10),
                                    ft.Icon(icons.LANGUAGE, size=12, color="onSurfaceVariant"),
                                    ft.Text(f"代理: {proxy_info}", color="onSurfaceVariant", size=11),
                                ], spacing=4),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        # 养号开关 (BioWarming)
                        ft.Column(
                            controls=[
                                ft.Switch(
                                    label="养号",
                                    label_style=ft.TextStyle(size=11, color="primary" if getattr(acc, 'is_maint_enabled', False) else "onSurfaceVariant"),
                                    value=getattr(acc, 'is_maint_enabled', False),
                                    on_change=lambda e, aid=acc.id: self.page.run_task(self._on_maint_toggle, aid, e.control.value),
                                    scale=0.7,
                                    tooltip="开启后，机甲将定期模拟真人浏览与点赞以提升账号权重",
                                ),
                                ft.Text(
                                    f"上次: {acc.last_maint_at.strftime('%m-%d %H:%M')}" if getattr(acc, 'last_maint_at', None) else "待维护",
                                    size=9,
                                    color="onSurfaceVariant",
                                )
                            ],
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=0,
                        ),
                        ft.Container(width=10),
                        # 动作按钮
                        ft.Row(
                            controls=[
                                ft.IconButton(
                                    icon=icons.CHECK if not is_active else icons.RADIO_BUTTON_CHECKED,
                                    tooltip="切换为此账号",
                                    icon_color="primary" if is_active else "onSurfaceVariant",
                                    disabled=is_active,
                                    on_click=lambda e, aid=acc.id: self.page.run_task(self._switch_account, aid)
                                ),
                                ft.IconButton(
                                    icon=icons.REFRESH_ROUNDED,
                                    tooltip="刷新账号信息",
                                    icon_color="primary",
                                    on_click=lambda e, aid=acc.id: self.page.run_task(self._refresh_account_info, aid)
                                ),
                                ft.IconButton(
                                    icon=icons.EDIT_DOCUMENT,
                                    tooltip="编辑账号信息",
                                    icon_color="primary",
                                    on_click=lambda e, a=acc: self.page.run_task(self._show_edit_dialog, a)
                                ),
                                ft.IconButton(
                                    icon=icons.DELETE_OUTLINE,
                                    tooltip="删除账号",
                                    icon_color="error",
                                    on_click=lambda e, aid=acc.id: self.page.run_task(self._show_delete_confirm, aid)
                                ),
                            ],
                            spacing=0,
                        ),
                    ],
                ),
                bgcolor=with_opacity(0.03, "primary") if is_active else with_opacity(0.02, "onSurface"),
                border=ft.border.all(1, with_opacity(0.2, "primary") if is_active else with_opacity(0.1, "onSurface")),
                border_radius=10,
                padding=10,
                on_hover=self._on_item_hover,
            )
            items.append(card)
        return items

    def _on_search_change(self, e):
        self._search_text = e.control.value
        self.refresh_ui()

    def _on_item_hover(self, e):
        e.control.bgcolor = with_opacity(0.08, "primary") if e.data == "true" else \
                           (with_opacity(0.03, "primary") if self._active_id else with_opacity(0.02, "onSurface"))
        e.control.update()

    async def _show_add_dialog(self, e):
        """显示添加账号对话框"""
        cookie_input = ft.TextField(
            label="从 Cookie 导入 (推荐)",
            hint_text="粘贴完整的 Cookie 字符串，我们将自动为您提取 BDUSS 和 STOKEN",
            multiline=True,
            min_lines=2,
            max_lines=4,
            text_size=12,
            border_color="primary",
        )
        
        bduss_field = ft.TextField(label="BDUSS", password=True, can_reveal_password=True, text_size=13, expand=True)
        stoken_field = ft.TextField(label="STOKEN (可选)", password=True, can_reveal_password=True, text_size=13, expand=True)
        name_field = ft.TextField(label="账号备注", hint_text="用于区分不同账号", text_size=13)
        
        proxy_dropdown = ft.Dropdown(
            label="关联代理",
            hint_text="为该账号指定固定出站代理",
            options=[ft.dropdown.Option("0", "不使用代理 / 直连")] + 
                    [ft.dropdown.Option(str(p.id), f"{p.protocol}://{p.host}:{p.port}") for p in self._proxies],
            value="0",
            text_size=13,
        )

        def on_cookie_change(e):
            if not cookie_input.value: return
            bduss, stoken = parse_cookie(cookie_input.value)
            if bduss:
                bduss_field.value = bduss
                stoken_field.value = stoken
                self.page.update()
                self._show_snackbar("已从 Cookie 中提取凭证", "success")

        cookie_input.on_change = on_cookie_change

        async def on_submit(e):
            if not bduss_field.value:
                self._show_snackbar("BDUSS 不能为空", "error")
                return
            
            submit_btn.disabled = True
            submit_btn.text = "验证中..."
            self.page.update()
            
            # 验证账号 (增加 15 秒硬超时防护，防止底层阻塞)
            import asyncio
            try:
                success, uid, uname, err = await asyncio.wait_for(
                    verify_account(bduss_field.value, stoken_field.value),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                self._show_snackbar("网络验证超时: 请检查本地网络或是否在海外", "error")
                submit_btn.disabled = False
                submit_btn.text = "验证并添加"
                self.page.update()
                return
            except Exception as e:
                self._show_snackbar(f"验证过程发生异常: {str(e)}", "error")
                submit_btn.disabled = False
                submit_btn.text = "验证并添加"
                self.page.update()
                return
                
            if not success:
                self._show_snackbar(f"账号验证失败: {err}", "error")
                submit_btn.disabled = False
                submit_btn.text = "验证并添加"
                self.page.update()
                return

            proxy_id = int(proxy_dropdown.value) if proxy_dropdown.value != "0" else None
            
            try:
                # 修复传递参数缺失：将 uid 和 uname 传递进去
                from ...core.account import encrypt_value
                await self.db.add_account(
                    name=name_field.value or uname,
                    bduss=encrypt_value(bduss_field.value),
                    stoken=encrypt_value(stoken_field.value) if stoken_field.value else "",
                    user_id=uid,
                    user_name=uname,
                    proxy_id=proxy_id
                )
                
                await log_info(f"账号库录入成功: {uname} (关联代理: {proxy_id or '无'})")
                
                self.page.close(dialog)
                await self.load_data()
                self._show_snackbar(f"账号 '{uname}' 添加成功", "success")
                
            except Exception as ex:
                self._show_snackbar(f"写入数据库失败: {str(ex)}", "error")
                submit_btn.disabled = False
                submit_btn.text = "验证并添加"
                self.page.update()

        submit_btn = ft.FilledButton("验证并添加", icon=icons.CHECK, on_click=on_submit)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.PERSON_ADD_ROUNDED, color="primary"), ft.Text("添加百度账号")]),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text("通过 Cookie 自动填充或手动输入凭据:", size=12, color="onSurfaceVariant"),
                        cookie_input,
                        ft.Divider(height=10, color="transparent"),
                        ft.Row([bduss_field, stoken_field], spacing=10),
                        name_field,
                        proxy_dropdown,
                    ],
                    tight=True,
                    spacing=15,
                    width=500,
                ),
                padding=10,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                submit_btn,
            ],
        )

        self.page.open(dialog)

    async def _show_edit_dialog(self, account):
        """显示修改账号对话框"""
        from ...core.account import decrypt_value, encrypt_value
        
        # 从数据库获取完整账号对象（含加密凭据）
        full_account = await self.db.get_accounts()
        full_account = next((a for a in full_account if a.id == account.id), None)
        if not full_account:
            self._show_snackbar("无法获取账号信息", "error")
            return
        
        # 解密现有凭据
        bduss_val = ""
        stoken_val = ""
        try:
            bduss_val = decrypt_value(full_account.bduss)
        except Exception:
            bduss_val = ""
        if full_account.stoken:
            try:
                stoken_val = decrypt_value(full_account.stoken)
            except Exception:
                stoken_val = ""

        name_field = ft.TextField(label="账号备注", value=account.name or account.user_name, text_size=13)
        bduss_field = ft.TextField(label="BDUSS", value=bduss_val, password=True, can_reveal_password=True, text_size=13, expand=True)
        stoken_field = ft.TextField(label="STOKEN (可选)", value=stoken_val, password=True, can_reveal_password=True, text_size=13, expand=True)
        
        cookie_input = ft.TextField(
            label="从 Cookie 更新凭据 (可选)",
            hint_text="粘贴新的 Cookie 字符串以快速更新 BDUSS 和 STOKEN",
            multiline=True,
            min_lines=2,
            max_lines=3,
            text_size=11,
            border_color="primary",
        )

        def on_cookie_change(e):
            if not cookie_input.value: return
            bduss, stoken = parse_cookie(cookie_input.value)
            if bduss:
                bduss_field.value = bduss
                stoken_field.value = stoken
                self.page.update()
                self._show_snackbar("凭据已从 Cookie 提取", "success")

        cookie_input.on_change = on_cookie_change

        proxy_dropdown = ft.Dropdown(
            label="关联代理",
            options=[ft.dropdown.Option("0", "不使用代理 / 直连")] + 
                    [ft.dropdown.Option(str(p.id), f"{p.protocol}://{p.host}:{p.port}") for p in self._proxies],
            value=str(account.proxy_id or "0"),
            text_size=13,
        )

        async def on_save(e):
            if not bduss_field.value:
                self._show_snackbar("BDUSS 不能为空", "error")
                return
            
            save_btn.disabled = True
            save_btn.text = "保存中..."
            self.page.update()
            
            try:
                # 如果修改了凭据，则重新验证
                if bduss_field.value != bduss_val or stoken_field.value != stoken_val:
                    success, uid, uname, err = await asyncio.wait_for(
                        verify_account(bduss_field.value, stoken_field.value),
                        timeout=15.0
                    )
                    if not success:
                        self._show_snackbar(f"新凭据验证失败: {err}", "error")
                        save_btn.disabled = False
                        save_btn.text = "保存修改"
                        self.page.update()
                        return
                
                proxy_id = int(proxy_dropdown.value) if proxy_dropdown.value != "0" else None
                
                await self.db.update_account(
                    account.id,
                    name=name_field.value,
                    bduss=encrypt_value(bduss_field.value),
                    stoken=encrypt_value(stoken_field.value) if stoken_field.value else "",
                    proxy_id=proxy_id
                )
                
                self.page.close(dialog)
                await self.load_data()
                self._show_snackbar(f"账号 '{account.user_name}' 信息已更新", "success")
                
            except Exception as ex:
                self._show_snackbar(f"更新失败: {str(ex)}", "error")
                save_btn.disabled = False
                save_btn.text = "保存修改"
                self.page.update()

        save_btn = ft.FilledButton("保存修改", icon=icons.SAVE_ROUNDED, on_click=on_save)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.EDIT_DOCUMENT, color="primary"), ft.Text("修改账号信息")]),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(f"正在编辑账号: {account.user_name} (UID: {account.user_id})", size=12, color="onSurfaceVariant"),
                        name_field,
                        cookie_input,
                        ft.Row([bduss_field, stoken_field], spacing=10),
                        proxy_dropdown,
                    ],
                    tight=True,
                    spacing=15,
                    width=500,
                ),
                padding=10,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                save_btn,
            ],
        )
        self.page.open(dialog)
    async def _switch_account(self, account_id: int):
        """切换账号"""
        await switch_account(self.db, account_id)
        self._active_id = account_id
        self.refresh_ui()
        self._show_snackbar("活跃账号已切换", "success")

    async def _refresh_account_info(self, account_id: int):
        """刷新账号信息"""
        acc = await refresh_account(self.db, account_id)
        if acc:
            await self.load_data()
            if acc.status.startswith("invalid"):
                self._show_snackbar(f"账号 '{acc.name}' 已失效", "error")
            else:
                self._show_snackbar(f"账号 '{acc.user_name}' 刷新成功", "success")
        else:
            self._show_snackbar("刷新失败，账号不存在", "error")

    async def _show_delete_confirm(self, account_id: int):
        """显示删除确认框"""
        async def do_delete(e):
            await remove_account(self.db, account_id)
            await log_warn(f"账号凭据已被用户手动移除: ID {account_id}")
            self.page.close(dialog)
            await self.load_data()
            self._show_snackbar("账号已从本地移除", "info")

        dialog = ft.AlertDialog(
            title=ft.Text("确认移除账号?"),
            content=ft.Text("此操作仅从本地数据库移除凭据，不会影响贴吧账号本身状态。"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                ft.TextButton("确认移除", icon=icons.DELETE_FOREVER, icon_color="error", on_click=do_delete),
            ],
        )
        self.page.open(dialog)

    def _on_search_change(self, e):
        self._search_text = e.control.value
        self.refresh_ui()

    def _on_filter_change(self, e):
        self._filter_status = e.control.value
        self.refresh_ui()

    async def _on_maint_toggle(self, account_id: int, value: bool):
        """开启或关闭养号维护功能"""
        await self.db.update_account(account_id, is_maint_enabled=value)
        # 局部更新内存中的状态
        for acc in self._accounts:
            if acc.id == account_id:
                acc.is_maint_enabled = value
                break
        self._show_snackbar(f"账号养号维护已{'开启' if value else '关闭'}", "success")
        self.refresh_ui()

    def _on_item_select(self, e):
        aid = e.control.data
        if e.control.value:
            self._selected_ids.add(aid)
        else:
            self._selected_ids.discard(aid)
        self._update_bulk_bar()

    def _toggle_select_all(self, e):
        # 仅选择当前过滤后的账号
        search_lower = self._search_text.lower()
        if e.control.value:
            for acc in self._accounts:
                status = getattr(acc, "status", "unknown")
                if self._filter_status != "all" and status != self._filter_status:
                    continue
                if search_lower:
                    match = (search_lower in (acc.name or "").lower() or 
                             search_lower in (acc.user_name or "").lower() or 
                             search_lower in str(acc.user_id))
                    if not match: continue
                self._selected_ids.add(acc.id)
        else:
            self._selected_ids.clear()
        self.refresh_ui()
        self._update_bulk_bar()

    def _update_bulk_bar(self):
        has_sel = len(self._selected_ids) > 0
        self.bulk_bar.controls[1].visible = has_sel
        self.bulk_bar.controls[2].visible = has_sel
        self.bulk_bar.controls[1].text = f"批量验证 ({len(self._selected_ids)})"
        self.bulk_bar.controls[2].text = f"批量删除 ({len(self._selected_ids)})"
        self.page.update()

    async def _bulk_verify_accounts(self, e):
        if not self._selected_ids: return
        count = len(self._selected_ids)
        self._show_snackbar(f"开始批量验证 {count} 个账号...", "info")
        for aid in list(self._selected_ids):
            await verify_account(self.db, aid)
        self._selected_ids.clear()
        self._update_bulk_bar()
        await self.load_data()
        self._show_snackbar(f"成功完成 {count} 个账号的批量效验", "success")

    async def _bulk_delete_accounts(self, e):
        if not self._selected_ids: return
        
        async def do_delete(_):
            count = len(self._selected_ids)
            for aid in list(self._selected_ids):
                await remove_account(self.db, aid)
            self._selected_ids.clear()
            self._update_bulk_bar()
            await self.load_data()
            self._show_snackbar(f"已批量注销 {count} 个账号", "success")
            self.page.close(dialog)

        dialog = ft.AlertDialog(
            title=ft.Text("确认批量从本机注销？"),
            content=ft.Text(f"将注销锁定的 {len(self._selected_ids)} 个账号及其所有的登录凭据。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("确认注销", icon=icons.DELETE_FOREVER, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=do_delete),
            ]
        )
        self.page.open(dialog)

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(
            ft.SnackBar(
                content=ft.Text(message),
                bgcolor=with_opacity(0.8, color),
                behavior=ft.SnackBarBehavior.FLOATING,
            )
        )

