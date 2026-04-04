"""Sign management page with Cyber-Mecha aesthetic (Dual Mode Support)"""

import asyncio
import flet as ft
from datetime import datetime
from typing import List, Optional

from ..components import create_gradient_button, CoreButtonWithLabel
from ..utils import with_opacity
from ...core.sign import get_follow_forums, sync_forums_to_db, sign_forum, sign_all_forums, get_sign_stats, sign_all_accounts


class SignPage:
    """签到管理页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._forums = []
        self._accounts = []
        self._matrix_tasks = [] # [(account, forum)]
        self._stats = {"total": 0, "success": 0, "failure": 0}
        self._is_signing = False
        self._mode = "single"  # single / matrix

    async def load_data(self):
        """加载数据"""
        if not self.db: return
        
        try:
            # 加载贴吧列表 (单账号模式使用)
            account = await self.db.get_active_account()
            if account:
                self._forums = await self.db.get_forums(account.id)
                self._stats = await get_sign_stats(self.db)
            else:
                self._forums = []
                self._stats = {"total": 0, "success": 0, "failure": 0}

            # 加载全量账号索引
            all_accounts = await self.db.get_accounts()
            acc_map = {acc.id: acc for acc in all_accounts}
            self._accounts = all_accounts
            
            # 加载全量贴吧清单 (作为矩阵任务的基础)
            all_forums = await self.db.get_forums()
            self._matrix_tasks = []
            for f in all_forums:
                acc = acc_map.get(f.account_id)
                self._matrix_tasks.append((acc, f))
            
            # 加载全矩阵唯一贴吧数
            all_fnames = await self.db.get_all_unique_fnames()
            self._matrix_total_count = len(all_fnames)
            
            try:
                import json
                raw_sched = await self.db.get_setting("schedule", "{}")
                sched = json.loads(raw_sched) if raw_sched else {}
                if hasattr(self, 'daemon_switch'):
                    self.daemon_switch.value = sched.get("enabled", False)
                    self.daemon_time.value = sched.get("sign_time", "08:00")
            except Exception:
                pass
            
            self.refresh_ui()
        except Exception as e:
            self._show_snackbar(f"数据加载引擎背刺: {str(e)}", "error")
            import traceback
            traceback.print_exc()

    def refresh_ui(self):
        if hasattr(self, "list_view"):
            self.list_view.controls.clear()
            
            if self._mode == "single":
                self.list_view.controls.extend(self._build_single_mode_items())
                self.total_stat.value = str(self._stats['total'])
                self.success_stat.value = str(self._stats['success'])
                self.failure_stat.value = str(self._stats['failure'])
                self.matrix_total_stat.value = str(getattr(self, "_matrix_total_count", 0))
            else:
                self.list_view.controls.extend(self._build_matrix_mode_items())
                # 矩阵模式下，总数反映全矩阵任务总数
                self.total_stat.value = str(len(self._matrix_tasks))
                self.success_stat.value = str(len([f for acc, f in self._matrix_tasks if f.last_sign_status == 'success']))
                self.failure_stat.value = str(len([f for acc, f in self._matrix_tasks if f.last_sign_status == 'failure']))
                self.matrix_total_stat.value = str(getattr(self, "_matrix_total_count", 0))
            
            self.page.update()

    def _toggle_mode(self, e):
        """切换签到模式"""
        if self._is_signing:
            self._show_snackbar("执行中禁止切换模式", "error")
            return
            
        self._mode = "matrix" if self._mode == "single" else "single"
        self.mode_text.value = "矩阵全扫模式" if self._mode == "matrix" else "单账号模式"
        self.mode_icon.name = ft.icons.GROUP_WORK if self._mode == "matrix" else "PERSON"
        self.mode_icon.color = ft.colors.ERROR if self._mode == "matrix" else ft.colors.PRIMARY
        
        # 切换设置面板可见性
        self.matrix_settings.visible = (self._mode == "matrix")
        
        self.refresh_ui()

    def build(self) -> ft.Control:
        # 统计文本组件
        self.total_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color="primary")
        self.success_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color=ft.colors.GREEN_ACCENT_400)
        self.failure_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color=ft.colors.RED_ACCENT_400)
        self.matrix_total_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color="primary")
        
        self.mode_text = ft.Text("单账号模式", size=14, weight=ft.FontWeight.BOLD, color="primary")
        self.mode_icon = ft.Icon("PERSON", color="primary", size=18)
        
        mode_switcher = ft.Container(
            content=ft.Row([
                self.mode_icon,
                self.mode_text,
                ft.Icon(ft.icons.SWAP_HORIZ, size=16, color="onSurfaceVariant")
            ], spacing=5),
            on_click=self._toggle_mode,
            ink=True,  # 替代 cursor 作为可点击反馈
            padding=ft.padding.symmetric(horizontal=12, vertical=6),
            border=ft.border.all(1, with_opacity(0.3, "primary")),
            border_radius=20,
            bgcolor=with_opacity(0.1, "primary")
        )
        
        # 主内
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
                        ft.Row([
                            ft.Text("智能签到终端 / SMART SIGN", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                            mode_switcher
                        ]),
                        ft.Text("支持单账号管理与多账号矩阵全扫流", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=5,
                ),
                ft.Container(expand=True),
                ft.Row([
                    ft.Column([
                        ft.Text("总数", size=9, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"),
                        self.total_stat,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                    ft.VerticalDivider(width=20, color=with_opacity(0.1, "onSurface")),
                    ft.Column([
                        ft.Text("成功", size=9, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"),
                        self.success_stat,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                    ft.VerticalDivider(width=20, color=with_opacity(0.1, "onSurface")),
                    ft.Column([
                        ft.Text("失败", size=9, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"),
                        self.failure_stat,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                    ft.VerticalDivider(width=20, color=with_opacity(0.1, "onSurface")),
                    ft.Column([
                        ft.Text("全矩阵", size=9, weight=ft.FontWeight.BOLD, color="primary"),
                        self.matrix_total_stat,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                ], spacing=10),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # 操作区
        self.sync_btn = create_gradient_button("同步贴吧", icon=ft.icons.SYNC, on_click=lambda e: self.page.run_task(self._do_sync, e))
        
        self.main_action = ft.Container(
            content=ft.Column([
                ft.Text("执行主控", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"),
                CoreButtonWithLabel(
                    label="启动签到流",
                    icon=ft.icons.PLAY_ARROW_ROUNDED,
                    on_click=lambda e: self.page.run_task(self._do_sign, e),
                    size=70,
                ),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=10,
            width=300,
        )

        # 进度与状态
        self.progress_bar = ft.ProgressBar(value=0, visible=False, color="primary", bar_height=3)
        self.status_text = ft.Text("", size=11, color="onSurfaceVariant")
        self.status_text.expand = True

        # 列表区域 (固化ListView实例避免Flet重新挂载导致的 Flex 缩放坍塌问题)
        self.list_view = ft.ListView(expand=True, spacing=8, padding=10)
        self.list_container = ft.Container(content=self.list_view, expand=True)

        self.delay_min_input = ft.TextField(label="最小间隔", value="5.0", text_size=11, expand=True, suffix_text="秒")
        self.delay_max_input = ft.TextField(label="最大间隔", value="15.0", text_size=11, expand=True, suffix_text="秒")
        
        self.acc_delay_min_input = ft.TextField(label="最小延迟", value="30.0", text_size=11, expand=True, suffix_text="秒")
        self.acc_delay_max_input = ft.TextField(label="最大延迟", value="120.0", text_size=11, expand=True, suffix_text="秒")

        self.matrix_settings = ft.Column([
            ft.Divider(height=5, color="transparent"),
            ft.Text("多账号防关联间隔", size=12, color="error"),
            ft.Row([self.acc_delay_min_input, ft.Text("~", size=12), self.acc_delay_max_input], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], visible=False)

        # 侧边设置面板 (Cyber Style)
        settings_panel = ft.Container(
            content=ft.Column([
                ft.Text("行为频率配置 / CONFIG", size=12, weight=ft.FontWeight.BOLD, color="primary"),
                ft.Row([
                    self.delay_min_input,
                    ft.Text("至", size=11, color="onSurfaceVariant"),
                    self.delay_max_input,
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self.matrix_settings,
                ft.Divider(height=20, color="transparent"),
                self.sync_btn,
            ], spacing=15),
            padding=20,
            bgcolor=with_opacity(0.03, "onSurface"),
            border_radius=12,
            width=300,
        )

        # 定时守护配置面板
        self.daemon_switch = ft.Switch(label="启用周期执行", value=False, label_position=ft.LabelPosition.RIGHT)
        self.daemon_time = ft.TextField(
            label="触发时间", 
            value="08:00", 
            text_size=12, 
            width=260,
            prefix_icon=ft.icons.ACCESS_TIME_ROUNDED,
            hint_text="HH:MM (如 08:30)",
        )
        self.daemon_save_btn = ft.FilledButton(
            "保存设置并热部署", 
            icon=ft.icons.BOLT, 
            on_click=self._save_daemon_config, 
            width=260, 
            style=ft.ButtonStyle(
                bgcolor=ft.colors.SECONDARY,
                shape=ft.RoundedRectangleBorder(radius=8),
            )
        )
        
        daemon_panel = ft.Container(
            content=ft.Column([
                ft.Text("守护进程 / DAEMON", size=12, weight=ft.FontWeight.BOLD, color="secondary"),
                ft.Container(content=self.daemon_switch, padding=ft.padding.only(left=-10)),
                self.daemon_time,
                ft.Divider(height=5, color="transparent"),
                self.daemon_save_btn,
            ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=20,
            bgcolor=with_opacity(0.05, "secondary"),
            border=ft.border.all(1, with_opacity(0.2, "secondary")),
            border_radius=12,
            width=300,
        )

        # 主内容
        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                ft.Row([
                    # 左侧控制 (原右侧)
                    ft.Column([
                        self.main_action,
                        settings_panel,
                        daemon_panel,
                    ], spacing=20),
                    # 右侧列表 (原左侧)
                    ft.Column([
                        ft.Row([
                            ft.Text("执行队列", size=14, weight=ft.FontWeight.W_500),
                            ft.Container(width=10),
                            self.status_text,
                        ]),
                        self.progress_bar,
                        ft.Container(
                            content=self.list_container,
                            expand=True,
                            border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                            border_radius=10,
                        ),
                    ], expand=True, spacing=10),
                ], expand=True),
            ], spacing=20),
            padding=20,
            expand=True,
        )

    def _build_single_mode_items(self):
        items = []
        for f in self._forums:
            is_signed = f.is_sign_today
            card = ft.Container(
                content=ft.Row([
                    ft.Icon(
                        ft.icons.VERIFIED_ROUNDED if is_signed else ft.icons.RADIO_BUTTON_UNCHECKED,
                        color="primary" if is_signed else "onSurfaceVariant",
                        size=18
                    ),
                    ft.Column([
                        ft.Text(f.fname, size=13, weight=ft.FontWeight.W_500),
                        ft.Row([
                            ft.Text(f"等级: LV.{f.level if hasattr(f,'level') else '?'} | 连续: {f.sign_count} 天", size=10, color="onSurfaceVariant"),
                            ft.Container(
                                content=ft.Row([
                                    ft.Text(f"总数:{f.history_total}", size=9, color="white"),
                                    ft.Text(f"成功:{f.history_success}", size=9, color=ft.colors.GREEN_ACCENT_400),
                                    ft.Text(f"失败:{f.history_failed}", size=9, color=ft.colors.RED_ACCENT_400),
                                ], spacing=5),
                                bgcolor=with_opacity(0.1, "onSurface"),
                                padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                border_radius=4,
                            ),
                        ], spacing=10, alignment=ft.MainAxisAlignment.START),
                    ], expand=True, spacing=4),
                    ft.IconButton(
                        icon=ft.icons.HISTORY_ROUNDED, 
                        icon_size=18, 
                        icon_color="onSurfaceVariant",
                        tooltip="查看签到日志",
                        on_click=lambda e, fid=f.id, fname=f.fname: self.page.run_task(self._show_forum_history, fid, fname)
                    ),
                    ft.FilledButton(
                        "签到" if not is_signed else "已签",
                        icon=ft.icons.BOLT if not is_signed else ft.icons.CHECK,
                        on_click=lambda e, fn=f.fname: self.page.run_task(self._do_sign_one, fn) if not self._is_signing else None,
                        disabled=is_signed or self._is_signing,
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=6),
                            padding=ft.padding.symmetric(horizontal=10)
                        )
                    ),
                ]),
                bgcolor=with_opacity(0.02, "primary") if is_signed else with_opacity(0.01, "onSurface"),
                padding=8,
                border_radius=8,
            )
            items.append(card)
        return items

    def _build_matrix_mode_items(self):
        items = []
        for acc, f in self._matrix_tasks:
            is_signed = f.last_sign_status == "success"
            
            # 账号与代理状态
            if not acc:
                acc_name = "未知/已遗失"
                status_color = "error"
                acc_status = "ORPHANED"
                proxy_info = "无"
                proxy_color = ft.colors.RED_ACCENT_400
            else:
                acc_name = f"{acc.name} [{acc.user_name}]" if acc.name and acc.user_name and acc.user_name != acc.name else (acc.name or acc.user_name)
                is_acc_ready = acc.status == "ready"
                status_color = "primary" if is_acc_ready else "error"
                acc_status = acc.status.upper()
                proxy_info = f"代理:{acc.proxy_id}" if acc.proxy_id else "裸连"
                proxy_color = ft.colors.GREEN if acc.proxy_id else ft.colors.AMBER

            card = ft.Container(
                content=ft.Row([
                    ft.Icon(
                        ft.icons.GROUP_WORK_ROUNDED if is_signed else ft.icons.RADIO_BUTTON_UNCHECKED,
                        color=status_color if not is_signed else "green",
                        size=20
                    ),
                    ft.Column([
                        ft.Row([
                            ft.Text(f.fname, size=14, weight=ft.FontWeight.W_500),
                            ft.Container(
                                content=ft.Text(f"负责账号: {acc_name}", size=9, color="white"),
                                bgcolor=with_opacity(0.4, status_color),
                                padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                border_radius=4,
                            ),
                        ], spacing=8),
                        ft.Row([
                            ft.Text(f"等级: LV.{f.level} | 状态: {acc_status}", size=10, color="error" if status_color == "error" else "onSurfaceVariant"),
                            ft.Container(
                                content=ft.Row([
                                    ft.Icon(ft.icons.PUBLIC if proxy_info == "裸连" else ft.icons.VPN_LOCK, size=9, color="white"),
                                    ft.Text(proxy_info, size=9, color="white"),
                                ], spacing=2),
                                bgcolor=proxy_color,
                                padding=ft.padding.symmetric(horizontal=4, vertical=1),
                                border_radius=4
                            )
                        ], spacing=10),
                    ], expand=True, spacing=4),
                    ft.Icon(ft.icons.CHECK_CIRCLE if is_signed else ft.icons.PENDING_OUTLINED, 
                           color="green" if is_signed else "onSurfaceVariant", size=18),
                ]),
                bgcolor=with_opacity(0.02, "primary") if is_signed else with_opacity(0.01, "onSurface"),
                padding=10,
                border_radius=8,
                border=ft.border.all(1, with_opacity(0.05, "onSurface")),
            )
            items.append(card)
        return items

    async def _do_sync(self, e):
        # 此时同步逻辑已升级为全自动多账号轮换
        self.sync_btn.disabled = True
        self.status_text.value = "🔍 正在进行全矩阵贴吧深度同步 (多账号轮换)..."
        self.page.update()
        
        try:
            count = await sync_forums_to_db(self.db)
            self._show_snackbar(f"全域同步完成！已扫描矩阵所有账号并载入 {count} 个新目标", "success")
            await self.load_data()
        except Exception as ex:
            self._show_snackbar(f"同步异常: {str(ex)}", "error")
        
        self.sync_btn.disabled = False
        self.status_text.value = ""
        self.page.update()

    async def _do_sign(self, e):
        if self._mode == "single":
            await self._do_sign_single()
        else:
            await self._do_sign_matrix()

    async def _do_sign_single(self):
        if self._is_signing or (self._stats['total'] - self._stats['success']) == 0: return
        
        self._is_signing = True
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.page.update()

        # 修复：分母应为队列中所有贴吧的总数，因为 sign_all_forums 会遍历所有贴吧
        total = self._stats['total']
        current = 0
        try:
            d_min = float(self.delay_min_input.value)
            d_max = float(self.delay_max_input.value)
        except:
            d_min, d_max = 5.0, 15.0

        try:
            async for result in sign_all_forums(self.db, delay_min=d_min, delay_max=d_max):
                current += 1
                self.progress_bar.value = current / total
                self.status_text.value = f"正在签到: {result.fname} ({current}/{total})"
                self.page.update()
            
            self._show_snackbar("所有签到指令已执行完毕", "success")
        except Exception as ex:
            self._show_snackbar(f"任务异常中止: {str(ex)}", "error")
        
        self._is_signing = False
        self.progress_bar.visible = False
        self.status_text.value = ""
        await self.load_data()

    async def _do_sign_matrix(self):
        if self._is_signing or not self._accounts: return
        
        self._is_signing = True
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.page.update()

        try:
            d_min = float(self.delay_min_input.value)
            d_max = float(self.delay_max_input.value)
            ad_min = float(self.acc_delay_min_input.value)
            ad_max = float(self.acc_delay_max_input.value)
        except:
            d_min, d_max, ad_min, ad_max = 5.0, 15.0, 30.0, 120.0

        # 修复：矩阵模式下分母应为全矩阵任务总数，进度应按贴吧任务计数
        total_tasks = len(self._matrix_tasks)
        current_task_idx = 0

        try:
            async for result in sign_all_accounts(self.db, d_min, d_max, ad_min, ad_max):
                current_task_idx += 1
                    
                self.progress_bar.value = current_task_idx / total_tasks
                self.status_text.value = f"[{current_task_idx}/{total_tasks}] 正在签到: {result.get('fname')} (账号: {result.get('account_name')})"
                self.page.update()
            
            self._show_snackbar("矩阵全扫指令已在后台全部执行完毕", "success")
        except Exception as ex:
            self._show_snackbar(f"矩阵任务异常中止: {str(ex)}", "error")
        
        self._is_signing = False
        self.progress_bar.visible = False
        self.status_text.value = ""
        await self.load_data()

    async def _do_sign_one(self, fname):
        self._show_snackbar(f"正在手动签到: {fname}", "info")
        result = await sign_forum(self.db, fname)
        if result.success:
            self._show_snackbar(f"{fname} 签到成功", "success")
            await self.load_data()
        else:
            self._show_snackbar(f"{fname} 失败: {result.message}", "error")

    async def _save_daemon_config(self, e):
        """保存并热部署后台调度器配置"""
        import json
        schedule = {
            "enabled": self.daemon_switch.value,
            "sign_time": self.daemon_time.value
        }
        await self.db.set_setting("schedule", json.dumps(schedule))
        
        try:
            from tieba_mecha.core.daemon import daemon_instance
            await daemon_instance.reload(self.db)
            self._show_snackbar("✔️ 守护进程配置已保存，定时重载完毕！", "success")
        except Exception as err:
            self._show_snackbar(f"❌ 守护进程重载失败: {err}", "error")
            
        self.page.update()

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary"
        if type == "error": color = "error"
        elif type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))

    async def _show_forum_history(self, forum_id: int, fname: str):
        """展示单独贴吧的签到日志记录弹窗"""
        logs = await self.db.get_sign_logs(limit=20, forum_id=forum_id)
        
        lv_items = []
        if not logs:
            lv_items.append(ft.Text("暂无任何签到追踪记录 ~", color="onSurfaceVariant", italic=True, text_align=ft.TextAlign.CENTER))
        else:
            for log in logs:
                c = "green" if log.success else "error"
                icon = ft.icons.CHECK_CIRCLE if log.success else ft.icons.ERROR
                msg_text = log.message if log.message else ("签到成功" if log.success else "未知失败")
                lv_items.append(ft.ListTile(
                    leading=ft.Icon(icon, color=c, size=20),
                    title=ft.Text("成功" if log.success else "拦截/失败", color=c, size=13, weight=ft.FontWeight.BOLD),
                    subtitle=ft.Text(f"[{log.signed_at.strftime('%Y-%m-%d %H:%M')}] {msg_text}", size=11, color="onSurfaceVariant"),
                    content_padding=ft.padding.all(0)
                ))
            
        dlg = ft.AlertDialog(
            title=ft.Row([
                ft.Icon(ft.icons.HISTORY_TOGGLE_OFF, color="primary"),
                ft.Text(f"{fname} - 近期战报日志", size=16, weight=ft.FontWeight.BOLD)
            ], spacing=10),
            content=ft.Container(
                content=ft.ListView(controls=lv_items, spacing=5, expand=True),
                width=350,
                height=350,
            ),
            actions=[ft.TextButton("关闭窗口", on_click=lambda e: self._close_dialog(dlg))],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=12)
        )
        self.page.dialog = dlg
        dlg.open = True
        self.page.update()
        
    def _close_dialog(self, dlg):
        dlg.open = False
        self.page.update()

