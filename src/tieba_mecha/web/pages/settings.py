"""Settings management page with Integrated Maintenance and Daemon Tabs"""

import asyncio
import flet as ft
from ..flet_compat import COLORS
from typing import List, Optional

from ..components import create_gradient_button
from ..utils import with_opacity
from ... import __version__
from ...core.ai_optimizer import AIOptimizer, _decrypt_api_key, _encrypt_api_key
from ...core.link_manager import SmartLinkConnector
from ...core.auth import get_auth_manager, AuthStatus
from ...core.web_auth import is_password_set, set_password, check_password, clear_password
from ...core.updater import get_update_manager
from ...core.daemon import daemon_instance, do_sign_task, do_auto_monitor_task, do_batch_post_tasks, do_auto_bump_task, do_maintenance_task, do_auth_check_task
from ...core.logger import get_recent_logs, get_log_queue
from ..components import icons


class SettingsPage:
    """系统全局设置页面 - 集中化管理 (通用/授权/更新/风控/养号/守护)"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._settings = {}
        self._latest_release = None
        
        # 养号日志状态
        self._log_task = None
        self._log_task_running = False
        self._maint_config = {
            "maint_acc_delay_min": "300",
            "maint_acc_delay_max": "900",
            "maint_interval_hours": "4",
        }
        
        # 守护任务状态
        self._jobs_info = []

    async def load_data(self):
        """同步数据库中的配置项"""
        if not self.db: return
        
        # 1. 加载核心配置
        raw_key = await self.db.get_setting("ai_api_key", "")
        self._settings["ai_api_key"] = _decrypt_api_key(raw_key)
        self._settings["ai_base_url"] = await self.db.get_setting("ai_base_url", "https://open.bigmodel.cn/api/paas/v4/")
        self._settings["ai_model"] = await self.db.get_setting("ai_model", "glm-4-flash")
        self._settings["ai_system_prompt"] = await self.db.get_setting("ai_system_prompt", "")
        self._settings["proxy_fallback"] = await self.db.get_setting("proxy_fallback", "false") == "true"
        self._settings["heartbeat_interval"] = await self.db.get_setting("heartbeat_interval", "2")
        self._settings["delay_min"] = await self.db.get_setting("delay_min", "5.0")
        self._settings["delay_max"] = await self.db.get_setting("delay_max", "15.0")
        self._settings["quiet_start"] = await self.db.get_setting("quiet_start", "01:00")
        self._settings["quiet_end"] = await self.db.get_setting("quiet_end", "06:00")
        
        # 2. 加载外部短链系统配置
        self._settings["slm_api_url"] = await self.db.get_setting("slm_api_url", "https://s.hubinwei.top")
        self._settings["slm_api_key"] = await self.db.get_setting("slm_api_key", "")
        
        # 3. 加载授权配置
        self._settings["license_key"] = await self.db.get_setting("license_key", "")
        am = await get_auth_manager()
        self._settings["hwid"] = await am.get_hwid()

        # 4. 加载 Web 密码状态
        self._settings["web_password_set"] = await is_password_set(self.db)

        # 5. 加载风控参数
        self._settings["obfuscator_density"] = await self.db.get_setting("obfuscator_density", "0.1")
        self._settings["obfuscator_symbols"] = await self.db.get_setting("obfuscator_symbols", "true") == "true"
        self._settings["obfuscator_spacing"] = await self.db.get_setting("obfuscator_spacing", "true") == "true"
        self._settings["obfuscator_shuffling"] = await self.db.get_setting("obfuscator_shuffling", "true") == "true"

        # 6. 加载养号配置与日志
        for key in self._maint_config.keys():
            self._maint_config[key] = await self.db.get_setting(key, self._maint_config[key])
        
        recent_history = await get_recent_logs(100)
        if hasattr(self, "maint_log_list"):
            self.maint_log_list.controls.clear()
            for log_entry in recent_history:
                if "[BioWarming]" in log_entry["message"]:
                    self._add_maint_log_ui(log_entry)
        
        if not self._log_task_running:
            self._log_task_running = True
            self._log_task = self.page.run_task(self._listen_maint_logs)

        # 7. 加载守护任务
        await self._load_daemon_info()

        # 8. 处理自动页签切换
        target_tab = self.page.session.get("settings_tab_index")
        if target_tab is not None:
            self.tabs.selected_index = int(target_tab)
            self.page.session.set("settings_tab_index", None) # 使用后清除

        await self.refresh_ui()

    async def _load_daemon_info(self):
        self._jobs_info = []
        scheduler = daemon_instance.scheduler
        JOB_NAMES = {
            "global_sign_job": "全域自动签到",
            "global_monitor_job": "自动化规则监控",
            "batch_post_job": "批量发帖排期轮询",
            "update_check_job": "应用更新自动检测",
            "auth_check_job": "授权状态心跳校准",
            "auto_bump_job": "自动回帖(自顶)调度",
            "biowarming_job": "BioWarming 养号维护",
        }
        self.JOB_FUNCS = {
            "global_sign_job": do_sign_task,
            "global_monitor_job": do_auto_monitor_task,
            "batch_post_job": do_batch_post_tasks,
            "update_check_job": get_update_manager().check_update,
            "auth_check_job": do_auth_check_task,
            "auto_bump_job": do_auto_bump_task,
            "biowarming_job": do_maintenance_task,
        }
        for job in scheduler.get_jobs():
            next_run = job.next_run_time
            status = "运行中" if next_run else "已暂停"
            self._jobs_info.append({
                "id": job.id, "name": JOB_NAMES.get(job.id, job.id),
                "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "N/A",
                "status": status
            })

    async def refresh_ui(self):
        if not hasattr(self, "tabs"): return

        # 通用配置更新
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
        self.slm_api_url_field.value = self._settings.get("slm_api_url", "")
        self.slm_api_key_field.value = self._settings.get("slm_api_key", "")
        
        # Web 密码状态
        if self._settings.get("web_password_set"):
            self.web_password_status.value = "已启用"; self.web_password_status.color = COLORS.GREEN
        else:
            self.web_password_status.value = "未设置"; self.web_password_status.color = COLORS.AMBER

        # 授权页签
        self.license_key_field.value = self._settings.get("license_key", "")
        self.hwid_field.value = f"硬件指纹 (HWID): {self._settings.get('hwid', '获取中...')}"
        am = await get_auth_manager()
        status_map = {
            AuthStatus.PRO: ("✔️ 已激活 PRO", COLORS.GREEN, "已解锁全功能及云端加速"),
            AuthStatus.FREE: ("⚠️ 基础版 (FREE)", COLORS.AMBER, "当前使用免费社区版"),
            AuthStatus.EXPIRED: ("❌ 授权已过期", COLORS.RED, "请及时续费以维持功能"),
            AuthStatus.ERROR: ("⚡ 授权异常", COLORS.GREY, "无法连接验证服务器"),
        }
        s_text, s_color, s_desc = status_map.get(am.status, ("未知状态", COLORS.GREY, ""))
        self.auth_badge.text = s_text; self.auth_badge.bgcolor = with_opacity(0.1, s_color); self.auth_badge.color = s_color
        self.auth_desc.value = s_desc
        
        # 改进到期时间显示
        if am.status == AuthStatus.PRO:
            expiry = am.license_info.get('expiry_date') if am.license_info else None
            self.auth_expiry_text.value = f"到期时间: {expiry or '永久有效'}"
            self.auth_expiry_text.color = COLORS.GREEN
        else:
            self.auth_expiry_text.value = "到期时间: 无 (社区版)"
            self.auth_expiry_text.color = "onSurfaceVariant"
        
        # 更新页签
        self.current_version_text.value = f"当前版本: v{__version__}"
        
        # 风控页签
        self.obf_density_slider.value = float(self._settings.get("obfuscator_density", 0.1))
        self.obf_symbols_switch.value = self._settings.get("obfuscator_symbols", True)
        self.obf_spacing_switch.value = self._settings.get("obfuscator_spacing", True)
        self.obf_shuffling_switch.value = self._settings.get("obfuscator_shuffling", True)

        # 养号页签
        for key, field in self.maint_fields.items():
            field.value = self._maint_config.get(key, "")

        # 守护任务页签
        self.daemon_container.controls = self._build_daemon_cards()
        
        self.page.update()

    def build(self) -> ft.Control:
        header = ft.Row(
            controls=[
                ft.Container(
                    content=ft.IconButton(icon=icons.ARROW_BACK_IOS_NEW, icon_size=16, on_click=lambda e: self._navigate("dashboard"),
                                          style=ft.ButtonStyle(color=COLORS.PRIMARY, bgcolor={"": with_opacity(0.1, COLORS.PRIMARY)})),
                    padding=5,
                ),
                ft.Column(
                    controls=[
                        ft.Text("全局控制台 / SYSTEM CONTROL", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("一站式管理授权、AI、风控、养号及后台任务", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # --- 1. 通用设置 ---
        self._init_general_fields()
        general_tab = ft.Column([
            ft.Divider(height=10, color="transparent"),
            self._create_section_title("智谱神经网络 / AI CORE", icons.MEMORY_ROUNDED),
            ft.Row([self.ai_key_field, self.ai_url_field, self.ai_model_field], spacing=10),
            self.ai_prompt_field,
            self._create_section_title("自动化与网络 / AUTOMATION", icons.TIMER_OUTLINED),
            ft.Row([self.delay_min_field, self.delay_max_field, self.heartbeat_field], spacing=10),
            ft.Row([self.quiet_start_field, self.quiet_end_field, self.proxy_fallback_switch], spacing=10),
            self._create_section_title("短链系统 / SHORT LINKS", icons.LINK_ROUNDED),
            ft.Row([self.slm_api_url_field, self.slm_api_key_field], spacing=10),
        ], spacing=15, scroll=ft.ScrollMode.AUTO)

        # --- 2. 授权中心 ---
        self._init_auth_fields()
        auth_tab = ft.Container(content=ft.Column([
            ft.Divider(height=20, color="transparent"),
            ft.Container(content=ft.Column([
                ft.Row([ft.Icon(icons.VERIFIED_ROUNDED, color="primary", size=40), ft.Column([self.auth_badge, self.auth_desc], spacing=2)], spacing=20),
                ft.Divider(height=30, color=with_opacity(0.1, "onSurface")),
                self.auth_expiry_text,
                ft.Text("许可证密钥:", size=14, weight=ft.FontWeight.BOLD),
                ft.Row([
                    self.license_key_field, 
                    ft.TextButton("获取授权码", icon=ft.icons.SHOPPING_BAG_OUTLINED, on_click=lambda _: self.page.launch_url("https://www.hwdemtv.com")),
                    self.verify_auth_btn
                ], spacing=10),
                self.hwid_field,
            ], spacing=15), padding=30, bgcolor=with_opacity(0.05, "surfaceVariant"), border_radius=15),
        ]), padding=ft.padding.symmetric(horizontal=10))

        # --- 3. 更新中心 ---
        self._init_update_fields()
        update_tab = ft.Container(content=ft.Column([
            ft.Divider(height=20, color="transparent"),
            ft.Row([ft.Container(content=ft.Icon(ft.icons.SYSTEM_UPDATE_ALT_ROUNDED, size=40, color="primary"), padding=20, bgcolor=with_opacity(0.1, "primary"), border_radius=20),
                   ft.Column([ft.Text("TiebaMecha 更新中心", size=20, weight=ft.FontWeight.BOLD), self.current_version_text], spacing=5)], spacing=20),
            ft.Container(content=ft.Column([ft.Row([ft.Text("检查新版本", size=16, weight=ft.FontWeight.BOLD), ft.Container(expand=True), 
                                           ft.Row([
                                               ft.TextButton("下载最新版", icon=ft.icons.DOWNLOAD_ROUNDED, on_click=lambda _: self.page.launch_url("https://l.hwdemtv.com/s/hyRszt")),
                                               self.check_update_btn
                                           ], spacing=10)]),
                                          self.latest_version_info, ft.Divider(height=20, color=with_opacity(0.1, "onSurface")),
                                          ft.Text("更新日志:", size=14, weight=ft.FontWeight.W_500), self.changelog_area], spacing=15),
                        padding=25, bgcolor=with_opacity(0.03, "onSurface"), border_radius=15),
        ]), padding=ft.padding.symmetric(horizontal=10))

        # --- 4. 高级风控 ---
        self._init_obf_fields()
        obf_tab = ft.Column([
            ft.Divider(height=10, color="transparent"),
            self._create_section_title("内容指纹混淆 / OBFUSCATOR", ft.icons.FINGERPRINT_ROUNDED),
            ft.Container(content=ft.Column([ft.Text("零宽字符注入密度:", size=14, weight=ft.FontWeight.W_500),
                                          ft.Row([ft.Icon(ft.icons.GRAIN_ROUNDED, size=20), self.obf_density_slider])]),
                        padding=15, bgcolor=with_opacity(0.05, "surfaceVariant"), border_radius=10),
            self.obf_symbols_switch, self.obf_spacing_switch, self.obf_shuffling_switch,
        ], spacing=15, scroll=ft.ScrollMode.AUTO)

        # --- 5. 养号管理 (New) ---
        self._init_maint_fields()
        maint_tab = ft.Column([
            ft.Divider(height=10, color="transparent"),
            self._create_section_title("养号行为引擎 / BIOWARMING", ft.icons.SHIELD_MOON_OUTLINED),
            ft.Row([self.maint_fields["maint_interval_hours"], self.maint_fields["maint_acc_delay_min"], self.maint_fields["maint_acc_delay_max"]], spacing=10),
            ft.Text("执行日志:", size=14, weight=ft.FontWeight.W_500),
            ft.Container(content=self.maint_log_list, height=300, border=ft.border.all(1, with_opacity(0.1, "onSurface")), border_radius=10),
        ], spacing=15, scroll=ft.ScrollMode.AUTO)

        # --- 6. 守护任务 (New) ---
        self.daemon_container = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
        daemon_tab = ft.Column([
            ft.Divider(height=10, color="transparent"),
            ft.Row([self._create_section_title("守护进程任务流 / DAEMON TASKS", ft.icons.AUTO_MODE_ROUNDED),
                   ft.Container(expand=True),
                   ft.TextButton("重载调度器", icon=ft.icons.REFRESH_ROUNDED, on_click=self._reload_daemon)]),
            self.daemon_container,
        ], spacing=10, expand=True)

        # --- 7. Web 安全 ---
        web_sec_tab = ft.Column([
            ft.Divider(height=10, color="transparent"),
            self._create_section_title("Web 访问安全 / SECURITY", icons.SECURITY),
            ft.Row([self.web_old_pwd_field, self.web_new_pwd_field, self.web_confirm_pwd_field], spacing=10),
            ft.Row([ft.Text("Web 密码状态: ", size=12), self.web_password_status, ft.Container(width=20),
                   ft.FilledTonalButton("修改密码", icon=icons.LOCK_ROUNDED, on_click=self._change_password),
                   ft.OutlinedButton("移除密码", icon=icons.BLOCK, on_click=self._clear_password)]),
        ], spacing=15)

        self.tabs = ft.Tabs(
            selected_index=0, animation_duration=300,
            tabs=[
                ft.Tab(text="通用", icon=icons.SETTINGS_OUTLINED, content=general_tab),
                ft.Tab(text="养号", icon=ft.icons.SHIELD_MOON_OUTLINED, content=maint_tab),
                ft.Tab(text="守护", icon=ft.icons.AUTO_MODE_ROUNDED, content=daemon_tab),
                ft.Tab(text="风控", icon=ft.icons.SHIELD_ROUNDED, content=obf_tab),
                ft.Tab(text="授权", icon=icons.VPN_KEY_ROUNDED, content=auth_tab),
                ft.Tab(text="更新", icon=ft.icons.DOWNLOAD_ROUNDED, content=update_tab),
                ft.Tab(text="安全", icon=icons.SECURITY, content=web_sec_tab),
            ],
            expand=True,
        )

        save_btn = create_gradient_button(text="保存设置", icon=icons.SAVE_ROUNDED, on_click=self._do_save)

        return ft.Container(
            content=ft.Column([header, ft.Divider(color=with_opacity(0.1, "primary"), height=5), self.tabs,
                             ft.Row([ft.Text(f"TiebaMecha v{__version__}", size=10, color="onSurfaceVariant"), ft.Container(expand=True), save_btn])]),
            padding=15, expand=True,
        )

    # --- Initialization Helpers ---
    def _init_general_fields(self):
        self.ai_key_field = ft.TextField(label="AI API KEY", password=True, can_reveal_password=True, expand=True)
        self.ai_url_field = ft.TextField(label="Base URL", expand=True)
        self.ai_model_field = ft.TextField(label="Model", width=150)
        self.ai_prompt_field = ft.TextField(label="System Prompt", multiline=True, min_lines=3, max_lines=6, text_size=12)
        self.delay_min_field = ft.TextField(label="签到延迟Min", expand=True)
        self.delay_max_field = ft.TextField(label="签到延迟Max", expand=True)
        self.heartbeat_field = ft.TextField(label="检测间隔(h)", expand=True)
        self.quiet_start_field = ft.TextField(label="静默开始", expand=True)
        self.quiet_end_field = ft.TextField(label="静默结束", expand=True)
        self.proxy_fallback_switch = ft.Switch(label="代理容灾", value=True)
        self.slm_api_url_field = ft.TextField(label="短链 API 地址", expand=True)
        self.slm_api_key_field = ft.TextField(label="短链 API Key", password=True, can_reveal_password=True, expand=True)

    def _init_auth_fields(self):
        self.auth_badge = ft.Chip(label=ft.Text("..."))
        self.auth_desc = ft.Text("...", size=12, color="onSurfaceVariant")
        self.auth_expiry_text = ft.Text("...", size=13)
        self.license_key_field = ft.TextField(label="License Key", password=True, can_reveal_password=True, expand=True)
        self.hwid_field = ft.Text("HWID: ...", size=11, color="onSurfaceVariant", selectable=True)
        self.verify_auth_btn = ft.FilledTonalButton("核销授权", icon=icons.GPP_GOOD_ROUNDED, on_click=self._verify_license_online)

    def _init_update_fields(self):
        self.current_version_text = ft.Text("...", size=14)
        self.check_update_btn = ft.FilledTonalButton("检查更新", icon=ft.icons.SYNC_ROUNDED, on_click=self._manual_check_update)
        self.latest_version_info = ft.Text("...", size=13, color="onSurfaceVariant")
        self.changelog_area = ft.Markdown("", selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)

    def _init_obf_fields(self):
        self.obf_density_slider = ft.Slider(min=0, max=0.5, divisions=10, label="{value}", expand=True)
        self.obf_symbols_switch = ft.Switch(label="注入随机表情/符号", value=True)
        self.obf_spacing_switch = ft.Switch(label="注入隐形换行与空格", value=True)
        self.obf_shuffling_switch = ft.Switch(label="段落级语义乱序", value=True)

    def _init_maint_fields(self):
        self.maint_fields = {
            "maint_interval_hours": ft.TextField(label="养号间隔(h)", expand=True),
            "maint_acc_delay_min": ft.TextField(label="账号延迟Min(s)", expand=True),
            "maint_acc_delay_max": ft.TextField(label="账号延迟Max(s)", expand=True),
        }
        self.maint_log_list = ft.ListView(expand=True, spacing=5, padding=10)

    def _init_general_sec_fields(self):
        self.web_password_status = ft.Text("...", size=12, weight=ft.FontWeight.BOLD)
        self.web_old_pwd_field = ft.TextField(label="当前密码", password=True, can_reveal_password=True, expand=True)
        self.web_new_pwd_field = ft.TextField(label="新密码", password=True, can_reveal_password=True, expand=True)
        self.web_confirm_pwd_field = ft.TextField(label="确认新密码", password=True, can_reveal_password=True, expand=True)
    
    # 修改 build 中引用的安全字段初始化
    def _init_general_fields(self):
        # 之前的逻辑合并
        self.ai_key_field = ft.TextField(label="AI API KEY", password=True, can_reveal_password=True, expand=True)
        self.ai_url_field = ft.TextField(label="Base URL", expand=True)
        self.ai_model_field = ft.TextField(label="Model", width=150)
        self.ai_prompt_field = ft.TextField(label="System Prompt", multiline=True, min_lines=3, max_lines=6, text_size=12)
        self.delay_min_field = ft.TextField(label="签到延迟Min", expand=True)
        self.delay_max_field = ft.TextField(label="签到延迟Max", expand=True)
        self.heartbeat_field = ft.TextField(label="检测间隔(h)", expand=True)
        self.quiet_start_field = ft.TextField(label="静默开始", expand=True)
        self.quiet_end_field = ft.TextField(label="静默结束", expand=True)
        self.proxy_fallback_switch = ft.Switch(label="代理容灾", value=True)
        self.slm_api_url_field = ft.TextField(label="短链 API 地址", expand=True)
        self.slm_api_key_field = ft.TextField(label="短链 API Key", password=True, can_reveal_password=True, expand=True)
        self._init_general_sec_fields()

    def _create_section_title(self, title: str, icon: str):
        return ft.Row([ft.Icon(icon, color="primary", size=18), ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color="primary")], spacing=10)

    # --- Logic Helpers ---
    def _add_maint_log_ui(self, log_entry):
        color = "error" if log_entry["level"] == "ERROR" else "secondary" if log_entry["level"] == "WARN" else "primary"
        log_row = ft.Row([
            ft.Text(f"[{log_entry['time']}]", size=10, color="onSurfaceVariant", font_family="Consolas"),
            ft.Container(content=ft.Text(log_entry["level"], size=9, weight=ft.FontWeight.BOLD), bgcolor=color, padding=ft.padding.symmetric(horizontal=4, vertical=1), border_radius=3),
            ft.Text(log_entry["message"], size=11, expand=True),
        ], spacing=10)
        self.maint_log_list.controls.insert(0, log_row)
        if len(self.maint_log_list.controls) > 100: self.maint_log_list.controls.pop()

    async def _listen_maint_logs(self):
        queue = get_log_queue()
        try:
            while self._log_task_running:
                log_entry = await queue.get()
                if self._log_task_running and "[BioWarming]" in log_entry["message"]:
                    if hasattr(self, "maint_log_list"):
                        self._add_maint_log_ui(log_entry); self.page.update()
                queue.task_done()
        except asyncio.CancelledError: pass
        finally: self._log_task_running = False

    def _build_daemon_cards(self) -> list[ft.Control]:
        cards = []
        for j in self._jobs_info:
            active = j["status"] == "运行中"
            card = ft.Container(
                content=ft.Row([
                    ft.Icon(ft.icons.TIMER_OUTLINED if active else ft.icons.TIMER_OFF_OUTLINED, color="primary" if active else "onSurfaceVariant"),
                    ft.Column([ft.Text(j["name"], size=14, weight=ft.FontWeight.BOLD),
                              ft.Text(f"下次: {j['next_run']}", size=11, color="primary" if active else "onSurfaceVariant")], spacing=2, expand=True),
                    ft.TextButton("触发", on_click=lambda e, jid=j["id"]: self.page.run_task(self._trigger_daemon_job, jid)),
                    ft.Switch(value=active, on_change=lambda e, jid=j["id"]: self.page.run_task(self._toggle_daemon_job, jid, e.control.value)),
                ]), padding=10, border=ft.border.all(1, with_opacity(0.1, "primary" if active else "onSurface")), border_radius=10, bgcolor=with_opacity(0.02, "primary" if active else "onSurface"),
            )
            cards.append(card)
        return cards

    async def _trigger_daemon_job(self, job_id: str):
        func = self.JOB_FUNCS.get(job_id)
        if func:
            self._show_snackbar(f"正在触发: {job_id}"); asyncio.create_task(func()) if asyncio.iscoroutinefunction(func) else func()
        else: self._show_snackbar("未找到执行函数", "error")

    async def _toggle_daemon_job(self, job_id: str, active: bool):
        job = daemon_instance.scheduler.get_job(job_id)
        if job: job.resume() if active else job.pause(); await self._load_daemon_info(); await self.refresh_ui()

    async def _reload_daemon(self, e):
        await daemon_instance.reload(self.db); await self._load_daemon_info(); await self.refresh_ui(); self._show_snackbar("调度器已重载", "success")

    async def _do_save(self, e):
        if not self.db: return
        try:
            config = {
                "ai_api_key": _encrypt_api_key(self.ai_key_field.value), "ai_base_url": self.ai_url_field.value,
                "ai_model": self.ai_model_field.value, "ai_system_prompt": self.ai_prompt_field.value,
                "proxy_fallback": "true" if self.proxy_fallback_switch.value else "false",
                "heartbeat_interval": self.heartbeat_field.value, "delay_min": self.delay_min_field.value, "delay_max": self.delay_max_field.value,
                "quiet_start": self.quiet_start_field.value, "quiet_end": self.quiet_end_field.value,
                "slm_api_url": self.slm_api_url_field.value, "slm_api_key": self.slm_api_key_field.value,
                "license_key": self.license_key_field.value, "obfuscator_density": str(self.obf_density_slider.value),
                "obfuscator_symbols": "true" if self.obf_symbols_switch.value else "false",
                "obfuscator_spacing": "true" if self.obf_spacing_switch.value else "false",
                "obfuscator_shuffling": "true" if self.obf_shuffling_switch.value else "false",
                "maint_interval_hours": self.maint_fields["maint_interval_hours"].value,
                "maint_acc_delay_min": self.maint_fields["maint_acc_delay_min"].value,
                "maint_acc_delay_max": self.maint_fields["maint_acc_delay_max"].value,
            }
            await self.db.set_settings_bulk(config)
            await daemon_instance.reload(self.db) # 强制刷新调度器
            self._show_snackbar("所有设置已保存", "success")
        except Exception as ex: self._show_snackbar(f"保存失败: {ex}", "error")

    # 复用授权、更新、密码逻辑
    async def _verify_license_online(self, e):
        await self.db.set_setting("license_key", self.license_key_field.value)
        am = await get_auth_manager()
        if await am.verify_online(): self._show_snackbar("授权成功", "success")
        else: self._show_snackbar("验证失败", "error")
        await self.load_data()

    async def _manual_check_update(self, e):
        updater = get_update_manager(self.db)
        release = await updater.check_update()
        if release:
            self.latest_version_info.value = f"新版本: {release.tag_name}"; self.latest_version_info.color = COLORS.GREEN
            self.changelog_area.value = await updater.get_changelog(release)
        else: self.latest_version_info.value = "已是最新版本"
        self.page.update()

    async def _change_password(self, e):
        old, new, conf = self.web_old_pwd_field.value, self.web_new_pwd_field.value, self.web_confirm_pwd_field.value
        if await is_password_set(self.db) and not await check_password(self.db, old): self._show_snackbar("原密码错误", "error"); return
        if not new or new != conf: self._show_snackbar("两次输入不一致", "error"); return
        await set_password(self.db, new); await self.load_data(); self._show_snackbar("密码已修改", "success")

    async def _clear_password(self, e):
        if await check_password(self.db, self.web_old_pwd_field.value): await clear_password(self.db); await self.load_data(); self._show_snackbar("密码已移除", "success")
        else: self._show_snackbar("原密码错误", "error")

    def cleanup(self):
        self._log_task_running = False
        if self._log_task and not self._log_task.done(): self._log_task.cancel()

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = COLORS.GREEN if type=="success" else "error" if type=="error" else "primary"
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
