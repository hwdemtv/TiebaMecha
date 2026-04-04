"""Settings management page with Cyber-Mecha aesthetic"""

import asyncio
import flet as ft
from typing import List, Optional

from ..components import create_gradient_button
from ..utils import with_opacity
from ...core.ai_optimizer import AIOptimizer
from ...core.link_manager import SmartLinkConnector
from ...core.auth import get_auth_manager, AuthStatus


class SettingsPage:
    """系统全局设置页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._settings = {}

    async def load_data(self):
        """同步数据库中的配置项"""
        if not self.db: return
        
        # 加载核心配置
        self._settings["ai_api_key"] = await self.db.get_setting("ai_api_key")
        self._settings["ai_base_url"] = await self.db.get_setting("ai_base_url", "https://open.bigmodel.cn/api/paas/v4/")
        self._settings["ai_model"] = await self.db.get_setting("ai_model", "glm-4-flash")
        self._settings["ai_system_prompt"] = await self.db.get_setting("ai_system_prompt", "")
        self._settings["proxy_fallback"] = await self.db.get_setting("proxy_fallback", "true") == "true"
        self._settings["heartbeat_interval"] = await self.db.get_setting("heartbeat_interval", "2")
        self._settings["delay_min"] = await self.db.get_setting("delay_min", "5.0")
        self._settings["delay_max"] = await self.db.get_setting("delay_max", "15.0")
        self._settings["quiet_start"] = await self.db.get_setting("quiet_start", "01:00")
        self._settings["quiet_end"] = await self.db.get_setting("quiet_end", "06:00")
        
        # 加载外部短链系统配置 (仅保留 API 模式)
        self._settings["slm_api_url"] = await self.db.get_setting("slm_api_url", "https://s.hubinwei.top")
        self._settings["slm_api_key"] = await self.db.get_setting("slm_api_key", "")
        
        # 加载授权配置
        self._settings["license_key"] = await self.db.get_setting("license_key", "")
        self._settings["device_id"] = await self.db.get_setting("device_id", "")
        self._settings["license_server_url"] = await self.db.get_setting("license_server_url", "https://license.hubinwei.top")
        
        # 获取硬件指纹
        am = get_auth_manager()
        self._settings["hwid"] = await am.get_hwid()
        
        self.refresh_ui()

    def refresh_ui(self):
        if hasattr(self, "ai_key_field"):
            self.ai_key_field.value = self._settings.get("ai_api_key", "")
            self.ai_url_field.value = self._settings.get("ai_base_url", "")
            self.ai_model_field.value = self._settings.get("ai_model", "")
            self.ai_prompt_field.value = self._settings.get("ai_system_prompt", "")
            self.proxy_fallback_switch.value = self._settings.get("proxy_fallback", True)
            self.heartbeat_field.value = self._settings.get("heartbeat_interval", "2")
            self.delay_min_field.value = self._settings.get("delay_min", "5.0")
            self.delay_max_field.value = self._settings.get("delay_max", "15.0")
            self.quiet_start_field.value = self._settings.get("quiet_start", "01:00")
            self.quiet_end_field.value = self._settings.get("quiet_end", "06:00")
            
            self.slm_api_url_field.value = self._settings.get("slm_api_url", "https://s.hubinwei.top")
            self.slm_api_key_field.value = self._settings.get("slm_api_key", "")
            
            self.license_key_field.value = self._settings.get("license_key", "")
            
            # 更新授权状态标签
            am = get_auth_manager()
            if am.status == AuthStatus.PRO:
                self.auth_info_label.value = "✔️ 已激活 Pro 特权"
                self.auth_info_label.color = ft.colors.GREEN
            elif am.status == AuthStatus.FREE:
                self.auth_info_label.value = "⚠️ 未激活 (基础版)"
                self.auth_info_label.color = ft.colors.AMBER
            elif am.status == AuthStatus.EXPIRED:
                self.auth_info_label.value = "❌ 授权已过期"
                self.auth_info_label.color = ft.colors.RED
            else:
                self.auth_info_label.value = "⚡ 授权状态未知 (离线)"
                self.auth_info_label.color = ft.colors.GREY
                
            self.hwid_field.value = f"HWID: {self._settings.get('hwid', '获取中...')}"
            self.page.update()

    def build(self) -> ft.Control:
        # 字段定义
        self.ai_key_field = ft.TextField(
            label="AI API KEY (智谱/OpenAI兼容)",
            password=True,
            can_reveal_password=True,
            border_color="primary",
            text_size=13
        )
        self.ai_url_field = ft.TextField(
            label="API 接口地址 (Base URL)",
            border_color="primary",
            text_size=13
        )
        self.ai_model_field = ft.TextField(
            label="模型名称 (Model)",
            border_color="primary",
            text_size=13
        )
        self.ai_prompt_field = ft.TextField(
            label="AI SEO 系统提示词 (System Prompt)",
            multiline=True,
            min_lines=5,
            max_lines=10,
            border_color="primary",
            text_size=12,
            hint_text="留空则使用系统预设的高强度 SEO 提示词..."
        )
        self.proxy_fallback_switch = ft.Switch(
            label="代理自动容灾 (当绑定代理失效时自动尝试池中节点)",
            value=True,
            label_position=ft.LabelPosition.RIGHT
        )
        self.heartbeat_field = ft.TextField(
            label="账号状态检测间隔 (小时)",
            width=150,
            border_color="primary",
            text_size=13
        )
        self.delay_min_field = ft.TextField(label="最小延迟 (秒)", width=100, border_color="primary", text_size=12)
        self.delay_max_field = ft.TextField(label="最大延迟 (秒)", width=100, border_color="primary", text_size=12)
        self.quiet_start_field = ft.TextField(label="静默开始", width=120, border_color="primary", text_size=12, hint_text="HH:mm")
        self.quiet_end_field = ft.TextField(label="静默结束", width=120, border_color="primary", text_size=12, hint_text="HH:mm")

        # 短链系统 API 配置字段
        self.slm_api_url_field = ft.TextField(label="短链系统 API 地址 (Base URL)", border_color="primary", text_size=12)
        self.slm_api_key_field = ft.TextField(label="API 令牌 (API Key)", password=True, can_reveal_password=True, border_color="primary", text_size=12)

        # 授权配置字段
        self.auth_info_label = ft.Text("正在获取状态...", size=12, italic=True)
        self.license_key_field = ft.TextField(
            label="许可证密钥 (License Key)",
            password=True,
            can_reveal_password=True,
            border_color="primary",
            text_size=13,
            expand=True
        )
        self.hwid_field = ft.Text("HWID: 未识别", size=10, color="onSurfaceVariant", weight=ft.FontWeight.W_300)
        self.verify_auth_btn = ft.FilledTonalButton(
            "立即验证授权",
            icon=ft.icons.GPP_GOOD_ROUNDED,
            on_click=self._verify_license_online,
        )

        # 标题区域
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(
                        icon=ft.icons.ARROW_BACK_IOS_NEW,
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
                        ft.Text("系统配置 / SYSTEM SETTINGS", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("管理 AI 核心、网络代理及自动化全局参数", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # 快速跳转导航栏
        nav_bar = ft.Container(
            content=ft.Row([
                ft.TextButton("授权", icon=ft.icons.VPN_KEY_ROUNDED, on_click=lambda e: self._scroll_to("license")),
                ft.TextButton("AI", icon=ft.icons.MEMORY_ROUNDED, on_click=lambda e: self._scroll_to("ai")),
                ft.TextButton("网络", icon=ft.icons.NETWORK_CHECK, on_click=lambda e: self._scroll_to("network")),
                ft.TextButton("短链", icon=ft.icons.LINK_ROUNDED, on_click=lambda e: self._scroll_to("link")),
                ft.TextButton("自动化", icon=ft.icons.TIMER_OUTLINED, on_click=lambda e: self._scroll_to("auto")),
            ], spacing=5, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=with_opacity(0.05, "primary"),
            border_radius=8,
            padding=ft.padding.symmetric(horizontal=10, vertical=5),
        )

        # 各配置段 (带 key 用于锚点跳转)
        self.license_section = ft.Container(
            key="license",
            content=ft.Column([
                self._create_section_title("软件授权 / LICENSING", ft.icons.VPN_KEY_ROUNDED),
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text("连接授权中心以激活 Pro", size=12, color="onSurfaceVariant"),
                            ft.Container(expand=True),
                            self.auth_info_label,
                        ]),
                        ft.Row([
                            self.license_key_field,
                            self.verify_auth_btn,
                        ], spacing=15),
                        ft.Row([
                            self.hwid_field,
                            ft.Container(expand=True),
                            ft.Text("💡 授权将自动绑定至当前硬件指纹。", size=10, color="onSurfaceVariant", italic=True),
                        ]),
                    ], spacing=10),
                    padding=10,
                ),
            ]),
        )

        self.ai_section = ft.Container(
            key="ai",
            content=ft.Column([
                self._create_section_title("智谱神经网络 / AI CORE", ft.icons.MEMORY_ROUNDED),
                ft.Container(
                    content=ft.Column([
                        # 智谱 AI 注册引导横幅
                        ft.Container(
                            content=ft.Row([
                                ft.Icon(ft.icons.AUTO_AWESOME, color="#FFD700", size=20),
                                ft.Column([
                                    ft.Text("使用智谱 GLM-4-Flash 模型（免费额度充足）", size=13, weight=ft.FontWeight.BOLD, color="#FFD700"),
                                    ft.Text("点击右侧链接注册，复制 API Key 后粘贴到下方字段", size=11, color="#E0E0E0"),
                                ], spacing=2, expand=True),
                                ft.ElevatedButton(
                                    "立即注册智谱 AI →",
                                    icon=ft.icons.OPEN_IN_NEW,
                                    on_click=lambda _: self.page.launch_url("https://www.bigmodel.cn/invite?icode=SQx7axFjnOGhwGsnmgUpHGczbXFgPRGIalpycrEwJ28%3D"),
                                    style=ft.ButtonStyle(
                                        color="#1A1A2E",
                                        bgcolor={"": "#FFD700"},
                                    )
                                ),
                            ], spacing=15, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            bgcolor=with_opacity(0.12, "#FFD700"),
                            border=ft.border.all(1, with_opacity(0.4, "#FFD700")),
                            border_radius=10,
                            padding=ft.padding.symmetric(horizontal=15, vertical=12),
                        ),
                        ft.Row([self.ai_key_field, self.ai_url_field], spacing=15),
                        ft.Row([
                            self.ai_model_field,
                            ft.Container(expand=True),
                            ft.FilledTonalButton(
                                "测试 AI 连通性",
                                icon=ft.icons.WIFI_TETHERING_ROUNDED,
                                on_click=self._test_ai_connection,
                            ),
                        ], spacing=15),
                        self.ai_prompt_field,
                    ], spacing=15),
                    padding=10,
                ),
            ]),
        )

        self.network_section = ft.Container(
            key="network",
            content=ft.Column([
                self._create_section_title("网络容灾 / CONNECTIVITY", ft.icons.NETWORK_CHECK),
                ft.Container(
                    content=ft.Column([
                        self.proxy_fallback_switch,
                    ], spacing=15),
                    padding=10,
                ),
            ]),
        )

        self.link_section = ft.Container(
            key="link",
            content=ft.Column([
                self._create_section_title("短链系统 API 模式 / SMART LINK API", ft.icons.LINK_ROUNDED),
                ft.Container(
                    content=ft.Column([
                        ft.Text("通过 REST API (https://s.hubinwei.top) 与智链管理项目联动。", size=12, color="onSurfaceVariant"),
                        self.slm_api_url_field,
                        ft.Row([
                            self.slm_api_key_field,
                            ft.FilledTonalButton(
                                "测试 API 连通性",
                                icon=ft.icons.LOGIN_ROUNDED,
                                on_click=self._test_slm_connection,
                            ),
                        ], spacing=15),
                        ft.Text("💡 请在 s.hubinwei.top 的开发者中心生成 API Key。", size=10, color="onSurfaceVariant"),
                    ], spacing=15),
                    padding=10,
                ),
            ]),
        )

        self.auto_section = ft.Container(
            key="auto",
            content=ft.Column([
                self._create_section_title("自动化与行为模拟 / AUTO & BEHAVIOR", ft.icons.TIMER_OUTLINED),
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text("随机签到延迟 (秒):", size=13),
                            self.delay_min_field,
                            ft.Text("-", size=13),
                            self.delay_max_field,
                        ], spacing=10),
                        ft.Row([
                            ft.Text("静默时间窗 (禁止自动化):", size=13),
                            self.quiet_start_field,
                            ft.Text("至", size=13),
                            self.quiet_end_field,
                        ], spacing=10),
                        self.heartbeat_field,
                    ], spacing=15),
                    padding=10,
                ),
            ]),
        )

        # 保存按钮
        save_btn = create_gradient_button(
            text="保存所有更改",
            icon=ft.icons.SAVE_ROUNDED,
            on_click=self._do_save,
        )

        # 主内容布局
        return ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Divider(color=with_opacity(0.1, "primary"), height=10),
                    nav_bar,
                    ft.Divider(height=10, color="transparent"),
                    self.license_section,
                    ft.Divider(height=10, color="transparent"),
                    self.ai_section,
                    ft.Divider(height=10, color="transparent"),
                    self.network_section,
                    ft.Divider(height=10, color="transparent"),
                    self.link_section,
                    ft.Divider(height=10, color="transparent"),
                    self.auto_section,
                    ft.Divider(height=10, color="transparent"),
                    # 底部：版本与保存
                    ft.Row([
                        ft.Text("TiebaMecha v2.0.1 | Current Path: Cyber-Router", size=10, color="onSurfaceVariant"),
                        ft.Container(expand=True),
                        save_btn,
                    ]),
                ],
                spacing=0,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=30,
            expand=True,
        )

    def _create_section_title(self, title: str, icon: str):
        return ft.Row([
            ft.Icon(icon, color="primary", size=18),
            ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color="primary")
        ], spacing=10)

    def _scroll_to(self, key: str):
        """滚动到指定配置段"""
        section_map = {
            "license": self.license_section,
            "ai": self.ai_section,
            "network": self.network_section,
            "link": self.link_section,
            "auto": self.auto_section,
        }
        section = section_map.get(key)
        if section and hasattr(section, 'key'):
            self.page.scroll_to(key=key, duration=300)

    async def _test_ai_connection(self, e):
        e.control.disabled = True
        e.control.text = "正在握手..."
        self.page.update()
        
        optimizer = AIOptimizer(self.db)
        success, msg = await optimizer.test_connection(
            api_key=self.ai_key_field.value,
            base_url=self.ai_url_field.value,
            model=self.ai_model_field.value
        )
        
        if success:
            # 自动保存成功的配置
            await self.db.set_setting("ai_api_key", self.ai_key_field.value)
            await self.db.set_setting("ai_base_url", self.ai_url_field.value)
            await self.db.set_setting("ai_model", self.ai_model_field.value)
            await self.db.set_setting("ai_system_prompt", self.ai_prompt_field.value)
            self._show_snackbar(f"✔️ {msg} (配置已自动保存)", "success")
        else:
            self._show_snackbar(f"❌ {msg}", "error")
            
        e.control.disabled = False
        e.control.text = "测试 AI 连通性"
        self.page.update()

    async def _test_slm_connection(self, e):
        e.control.disabled = True
        e.control.text = "正在握手..."
        self.page.update()
        
        # 临时保存以便测试
        # 临时清理旧配置，以免干扰 API 模式

        connector = SmartLinkConnector(self.db)
        
        # 预存以便测试
        await self.db.set_setting("slm_api_url", self.slm_api_url_field.value)
        await self.db.set_setting("slm_api_key", self.slm_api_key_field.value)
        
        success, msg = await connector.test_connection()
        if success:
            self._show_snackbar(f"✔️ {msg}", "success")
        else:
            self._show_snackbar(f"❌ {msg}", "error")
            
        e.control.disabled = False
        e.control.text = "测试 API 连通性"
        self.page.update()

    async def _verify_license_online(self, e):
        e.control.disabled = True
        e.control.text = "正在核销..."
        self.page.update()
        
        # 先保存当前填写的配置，以便验证
        await self.db.set_setting("license_key", self.license_key_field.value)
        
        am = get_auth_manager()
        success = await am.verify_online()
        
        if success:
            self._show_snackbar("🎉 授权激活成功！Pro 功能已解锁。", "success")
        else:
            self._show_snackbar("❌ 授权验证失败，请检查卡密或网络状态。", "error")
            
        e.control.disabled = False
        e.control.text = "立即验证授权"
        self.refresh_ui()

    async def _do_save(self, e):
        if not self.db: return
        
        # 同步各字段到数据库
        await self.db.set_setting("ai_api_key", self.ai_key_field.value)
        await self.db.set_setting("ai_base_url", self.ai_url_field.value)
        await self.db.set_setting("ai_model", self.ai_model_field.value)
        await self.db.set_setting("ai_system_prompt", self.ai_prompt_field.value)
        await self.db.set_setting("proxy_fallback", "true" if self.proxy_fallback_switch.value else "false")
        await self.db.set_setting("heartbeat_interval", self.heartbeat_field.value)
        await self.db.set_setting("delay_min", self.delay_min_field.value)
        await self.db.set_setting("delay_max", self.delay_max_field.value)
        await self.db.set_setting("quiet_start", self.quiet_start_field.value)
        await self.db.set_setting("quiet_end", self.quiet_end_field.value)
        
        await self.db.set_setting("slm_api_url", self.slm_api_url_field.value)
        await self.db.set_setting("slm_api_key", self.slm_api_key_field.value)
        
        await self.db.set_setting("license_key", self.license_key_field.value)
        
        self._show_snackbar("配置已同步至核心数据库", "success")

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
