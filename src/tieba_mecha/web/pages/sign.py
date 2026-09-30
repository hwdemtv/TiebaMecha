"""Sign management page with Cyber-Mecha aesthetic (Dual Mode Support)"""

import asyncio
import flet as ft
from ..flet_compat import COLORS
from datetime import datetime
from typing import List, Optional

from ..components import create_gradient_button, CoreButtonWithLabel, GRADIENT_CYAN
from ..components.icons import (
    GROUP_WORK, PERSON, ARROW_BACK_IOS_NEW,
    SYNC_ROUNDED, PLAY_ARROW_ROUNDED, ACCESS_TIME_ROUNDED, BOLT,
    CHECK, VERIFIED_ROUNDED, RADIO_BUTTON_UNCHECKED, HISTORY_ROUNDED,
    PUBLIC, VPN_LOCK, CHECK_CIRCLE,
    ERROR, HISTORY_TOGGLE_OFF, STOP_CIRCLE_ROUNDED,
    HEART_BROKEN, PAUSE_CIRCLE_OUTLINED, PERSON_OFF
)
from ..utils import with_opacity
from ...core.sign import get_follow_forums, sync_forums_to_db, sign_forum, sign_all_forums, get_sign_stats, sign_all_accounts, sign_flow_lock


class SignPage:
    """签到管理页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._forums = []
        self._accounts = []
        self._matrix_rollup = None  # get_sign_rollup_by_account 结果（矩阵态单一口径）
        self._matrix_row_controls = {}  # account_id -> 行控件引用（执行中行内计数联动）
        self._matrix_live = {}  # account_id -> 执行中实时计数
        self._stats = {"total": 0, "success": 0, "failure": 0}
        self._is_signing = False
        self._stop_requested = False
        self._mode = "single"  # single / matrix

    async def load_data(self):
        """加载数据"""
        if not self.db: return
        
        try:
            # [NEW] 数据加载前强制检测并修复跨天签到状态
            if hasattr(self.db, "check_and_reset_daily_sign"):
                await self.db.check_and_reset_daily_sign()
                
            # 加载贴吧列表 (单账号模式使用)
            account = await self.db.get_active_account()
            if account:
                self._forums = await self.db.get_forums(account.id)
                self._stats = await get_sign_stats(self.db)
            else:
                self._forums = []
                self._stats = {"total": 0, "success": 0, "failure": 0}

            # 加载全量账号索引（矩阵前置检查用）
            self._accounts = await self.db.get_accounts()

            # 矩阵态数据：账号队列 rollup（视图/统计/分母单一口径；吧级明细走单账号侧）
            self._matrix_rollup = await self.db.get_sign_rollup_by_account()
            
            try:
                import json
                raw_sched = await self.db.get_setting("schedule", "{}")
                sched = json.loads(raw_sched) if raw_sched else {}
                self.daemon_time.value = sched.get("sign_time", "08:00")
                self.daemon_switch.value = sched.get("enabled", False)
                
                # 同步守护模式单选（持久值，独立于页面执行模式）
                if hasattr(self, "daemon_mode_radio"):
                    self.daemon_mode_radio.value = sched.get("mode", "single")
                
                # 智能格式化加载行为频率参数 (抹除不必要的 .0)
                async def _get_fmt_val(key, default):
                    raw = str(await self.db.get_setting(key, default))
                    try:
                        return str(int(float(raw))) if float(raw).is_integer() else raw
                    except (ValueError, TypeError): return raw

                self.delay_min_input.value = await _get_fmt_val("sign_delay_min", "5")
                self.delay_max_input.value = await _get_fmt_val("sign_delay_max", "15")
                self.acc_delay_min_input.value = await _get_fmt_val("sign_acc_delay_min", "30")
                self.acc_delay_max_input.value = await _get_fmt_val("sign_acc_delay_max", "120")
            except Exception:
                pass

            # 同步头部账号切换芯片
            if hasattr(self, "account_chip"):
                await self.account_chip.refresh()

            self.refresh_ui()
            
            # --- 自动触发逻辑 (来自仪表盘快捷键) ---
            if self.page.session.get("auto_start_sign"):
                self.page.session.set("auto_start_sign", False)
                # 等待一小会确保 UI 已渲染
                self.page.run_task(self._do_sign, None)
                
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
                self.pending_stat.value = str(self._stats.get('pending', 0))
            else:
                self.list_view.controls.extend(self._build_account_queue_items())
                # 矩阵态统计 = 各账号行账目汇总（与队列所见一致，口径自然闭合）
                accs = (getattr(self, "_matrix_rollup", None) or {}).get("accounts", [])
                self.total_stat.value = str(sum(a["total"] for a in accs))
                self.success_stat.value = str(sum(a["signed"] for a in accs))
                self.failure_stat.value = str(sum(a["failed_today"] for a in accs))
                self.pending_stat.value = str(sum(a["pending"] for a in accs))

            self.page.update()

    def _set_mode(self, mode: str):
        """选择签到模式；使用明确的双选入口，避免用户误触切换。"""
        if self._is_signing:
            self._show_snackbar("执行中禁止切换模式", "error")
            return

        self._mode = mode
        self.mode_text.value = "矩阵全扫模式" if self._mode == "matrix" else "单账号模式"
        self.mode_icon.name = GROUP_WORK if self._mode == "matrix" else PERSON
        self.mode_icon.color = COLORS.ERROR if self._mode == "matrix" else COLORS.PRIMARY

        self.single_mode_btn.style = ft.ButtonStyle(
            bgcolor="primary" if mode == "single" else None,
            color="onPrimary" if mode == "single" else "primary",
        )
        self.matrix_mode_btn.style = ft.ButtonStyle(
            bgcolor="secondary" if mode == "matrix" else None,
            color="onPrimary" if mode == "matrix" else "secondary",
        )
        
        # 切换设置面板可见性
        self.matrix_settings.visible = (self._mode == "matrix")
        
        self.refresh_ui()

    def _toggle_mode(self, e):
        """兼容已有调用方的模式切换入口。"""
        self._set_mode("matrix" if self._mode == "single" else "single")

    def build(self) -> ft.Control:
        # 统计文本组件（闭合账目：总数 = 成功 + 失败 + 待签，熔断在行内展示）
        self.total_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color="primary")
        self.success_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color=COLORS.GREEN_ACCENT_400)
        self.failure_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color=COLORS.RED_ACCENT_400)
        self.pending_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color="onSurfaceVariant")
        
        self.mode_text = ft.Text("单账号模式", size=14, weight=ft.FontWeight.BOLD, color="primary")
        self.mode_icon = ft.Icon(PERSON, color="primary", size=18)
        
        self.single_mode_btn = ft.FilledButton(
            "单账号",
            icon=PERSON,
            tooltip="仅对当前活动账号的关注贴吧签到",
            on_click=lambda e: self._set_mode("single"),
        )
        self.matrix_mode_btn = ft.OutlinedButton(
            "矩阵全扫",
            icon=GROUP_WORK,
            tooltip="对所有账号及其关注贴吧执行签到",
            on_click=lambda e: self._set_mode("matrix"),
        )
        mode_switcher = ft.Row(
            [self.single_mode_btn, self.matrix_mode_btn],
            spacing=6,
        )

        # 头部账号切换芯片（单账号模式签的就是当前账号）；
        # 执行中禁止切号：切号会重建列表/统计，而签到流仍在跑原账号
        from ..components.account_switcher import AccountSwitchChip
        self.account_chip = AccountSwitchChip(
            self.page, self.db,
            on_switched=self.load_data,
            is_busy=lambda: self._is_signing,
        )

        # 主内
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
                        ft.Row([
                            ft.Text("智能签到终端 / SMART SIGN", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                            mode_switcher
                        ]),
                        ft.Text("支持单账号管理与多账号矩阵全扫流", size=11, color="onSurfaceVariant"),
                    ],
                    spacing=5,
                ),
                ft.Container(expand=True),
                self.account_chip,
                ft.VerticalDivider(width=20, color=with_opacity(0.1, "onSurface")),
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
                        ft.Text("待签", size=9, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"),
                        self.pending_stat,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                ], spacing=10),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        # 操作区
        self.sync_btn = create_gradient_button("同步贴吧", icon=SYNC_ROUNDED, on_click=lambda e: self.page.run_task(self._do_sync, e))
        
        # 主控按钮组件
        self.sign_btn_icon = ft.Icon(PLAY_ARROW_ROUNDED, color="onSurface", size=30)
        self.sign_btn_text = ft.Text("启动签到流", color="onSurfaceVariant", size=12, weight=ft.FontWeight.W_500)
        
        self.main_action = ft.Container(
            content=ft.Column([
                ft.Text("执行主控", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"),
                ft.Container(
                    content=ft.Column([
                        ft.Container(
                            content=self.sign_btn_icon,
                            gradient=ft.RadialGradient(
                                colors=GRADIENT_CYAN,
                                center=ft.alignment.center,
                            ),
                            width=70,
                            height=70,
                            border_radius=35,
                            shadow=ft.BoxShadow(
                                spread_radius=3,
                                blur_radius=30,
                                color=with_opacity(0.3, GRADIENT_CYAN[0]),
                            ),
                            ink=True,
                            on_click=lambda e: self.page.run_task(self._do_sign, e),
                            alignment=ft.alignment.center,
                        ),
                        self.sign_btn_text,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
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

        self.delay_min_input = ft.TextField(label="最小间隔", value="5", text_size=11, expand=True, suffix_text="秒")
        self.delay_max_input = ft.TextField(label="最大间隔", value="15", text_size=11, expand=True, suffix_text="秒")
        
        self.acc_delay_min_input = ft.TextField(label="最小延迟", value="30", text_size=11, expand=True, suffix_text="秒")
        self.acc_delay_max_input = ft.TextField(label="最大延迟", value="120", text_size=11, expand=True, suffix_text="秒")

        self.matrix_settings = ft.Column([
            ft.Divider(height=5, color="transparent"),
            ft.Text("多账号防关联间隔", size=12, color="error"),
            ft.Row([self.acc_delay_min_input, ft.Text("~", size=12), self.acc_delay_max_input], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text("账号间延迟：矩阵模式与守护共用", size=9, color="onSurfaceVariant"),
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
                ft.Text("吧间延迟：单账号、矩阵与守护共用", size=9, color="onSurfaceVariant"),
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
        # 守护模式独立单选：与页面执行模式解耦——保存的即此值，不再隐式快照页面当前模式
        self.daemon_mode_radio = ft.RadioGroup(
            value="single",
            content=ft.Row(
                [
                    ft.Radio(value="single", label="单账号"),
                    ft.Radio(value="matrix", label="矩阵全扫"),
                ],
                spacing=10,
                tight=True,
            ),
        )
        self.daemon_time = ft.TextField(
            label="触发时间",
            value="08:00",
            text_size=12,
            width=260,
            prefix_icon=ACCESS_TIME_ROUNDED,
            hint_text="HH:MM (如 08:30)",
        )
        self.daemon_save_btn = ft.FilledButton(
            "保存配置并生效",
            icon=BOLT,
            on_click=self._save_daemon_config,
            width=260,
            style=ft.ButtonStyle(
                bgcolor=COLORS.SECONDARY,
                shape=ft.RoundedRectangleBorder(radius=8),
            )
        )

        daemon_panel = ft.Container(
            content=ft.Column([
                ft.Text("守护进程 / DAEMON", size=12, weight=ft.FontWeight.BOLD, color="secondary"),
                ft.Container(content=self.daemon_switch, padding=ft.padding.only(left=-10)),
                ft.Text("守护执行模式", size=10, color="onSurfaceVariant"),
                self.daemon_mode_radio,
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
                    # 左侧控制面板 (固定宽度，可滚动)
                    ft.Container(
                        content=ft.Column([
                            self.main_action,
                            settings_panel,
                            daemon_panel,
                        ], spacing=20, scroll=ft.ScrollMode.AUTO),
                        width=320,
                    ),
                    # 右侧执行队列 (自动扩展)
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
                ], expand=True, vertical_alignment=ft.CrossAxisAlignment.START),
            ], spacing=20, expand=True),
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
                        VERIFIED_ROUNDED if is_signed else RADIO_BUTTON_UNCHECKED,
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
                                    ft.Text(f"成功:{f.history_success}", size=9, color=COLORS.GREEN_ACCENT_400),
                                    ft.Text(f"失败:{f.history_failed}", size=9, color=COLORS.RED_ACCENT_400),
                                ], spacing=5),
                                bgcolor=with_opacity(0.1, "onSurface"),
                                padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                border_radius=4,
                            ),
                        ], spacing=10, alignment=ft.MainAxisAlignment.START),
                    ], expand=True, spacing=4),
                    ft.IconButton(
                        icon=HEART_BROKEN,
                        icon_size=18,
                        icon_color="error",
                        tooltip="取消关注",
                        on_click=lambda e, fname=f.fname: self.page.run_task(self._on_unfollow_forum, fname)
                    ),
                    ft.IconButton(
                        icon=HISTORY_ROUNDED,
                        icon_size=18,
                        icon_color="onSurfaceVariant",
                        tooltip="查看签到日志",
                        on_click=lambda e, fid=f.id, fname=f.fname: self.page.run_task(self._show_forum_history, fid, fname)
                    ),
                    ft.FilledButton(
                        "签到" if not is_signed else "已签",
                        icon=BOLT if not is_signed else CHECK,
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

    def _build_account_queue_items(self):
        """矩阵态账号队列：每账号一行账目 + 挂起/孤儿聚合行。

        吧级明细不在矩阵态重复展示——切单账号侧（头部芯片）看更全还能操作；
        执行中当前账号行高亮、行内计数逐吧跳动（见 _do_sign_matrix 联动）。
        """
        rollup = getattr(self, "_matrix_rollup", None) or {
            "accounts": [], "suspended_forums": 0, "orphan_forums": 0
        }
        self._matrix_row_controls = {}
        items = []

        for a in rollup["accounts"]:
            done = a["pending"] == 0 and a["total"] > 0
            if a["proxy_status"] == "ok":
                proxy_label, proxy_color, proxy_icon = "代理", COLORS.GREEN, VPN_LOCK
            elif a["proxy_status"] == "suspended":
                proxy_label, proxy_color, proxy_icon = "代理失效", COLORS.RED_ACCENT_400, VPN_LOCK
            else:
                proxy_label, proxy_color, proxy_icon = "裸连", COLORS.AMBER, PUBLIC

            counts_text = ft.Text(
                f"已签 {a['signed']}  待签 {a['pending']}" + (f"  熔断 {a['banned']}" if a["banned"] else ""),
                size=11, color="onSurfaceVariant",
            )
            status_icon = ft.Icon(
                CHECK_CIRCLE if done else PLAY_ARROW_ROUNDED,
                color=COLORS.GREEN_ACCENT_400 if done else "primary", size=20,
            )
            row = ft.Container(
                content=ft.Row([
                    status_icon,
                    ft.Text(a["name"], size=13, weight=ft.FontWeight.W_600, expand=True),
                    ft.Container(
                        content=ft.Row(
                            [ft.Icon(proxy_icon, size=10, color="white"),
                             ft.Text(proxy_label, size=9, color="white")],
                            spacing=2,
                        ),
                        bgcolor=proxy_color,
                        padding=ft.padding.symmetric(horizontal=5, vertical=2),
                        border_radius=4,
                    ),
                    counts_text,
                ], spacing=10),
                bgcolor=with_opacity(0.02, "primary") if done else with_opacity(0.01, "onSurface"),
                padding=10,
                border_radius=8,
                border=ft.border.all(1, with_opacity(0.05, "onSurface")),
            )
            self._matrix_row_controls[a["account_id"]] = {
                "row": row, "counts": counts_text, "icon": status_icon,
            }
            items.append(row)

        if rollup.get("suspended_forums"):
            items.append(ft.Container(
                content=ft.Row([
                    ft.Icon(PAUSE_CIRCLE_OUTLINED, color="error", size=18),
                    ft.Text("挂起/封禁账号", size=12, color="error", expand=True),
                    ft.Text(f"{rollup['suspended_forums']} 吧不参与执行", size=10, color="onSurfaceVariant"),
                ], spacing=10),
                bgcolor=with_opacity(0.03, "error"),
                padding=10, border_radius=8,
            ))
        if rollup.get("orphan_forums"):
            items.append(ft.Container(
                content=ft.Row([
                    ft.Icon(PERSON_OFF, color="onSurfaceVariant", size=18),
                    ft.Text("孤儿数据（账号已删除）", size=12, color="onSurfaceVariant", expand=True),
                    ft.Text(f"{rollup['orphan_forums']} 吧仅存历史", size=10, color="onSurfaceVariant"),
                ], spacing=10),
                bgcolor=with_opacity(0.01, "onSurface"),
                padding=10, border_radius=8,
            ))
        return items

    async def _do_sync(self, e):
        # 同步会逐账号翻页拉关注列表，与签到流撞同账号即双流并发——必须互斥
        if sign_flow_lock.locked():
            self._show_snackbar("已有签到流在执行中，同步已推迟（避免同账号双流并发）", "warning")
            return
        # 此时同步逻辑已升级为全自动多账号轮换
        self.sync_btn.disabled = True
        self.status_text.value = "🔍 正在进行全矩阵贴吧深度同步 (多账号轮换)..."
        self.page.update()

        try:
            async with sign_flow_lock:
                count = 0
                async for r in sync_forums_to_db(self.db):
                    count += r.get("added", 0)
                    if "error" in r:
                        self.status_text.value = f"🔍 同步 {r['account']} 出错: {r['error']}"
                    else:
                        self.status_text.value = f"🔍 同步 {r['account']}：新增 {r['added']} | 标记隐藏 {r['stale']}"
                    self.page.update()
            self._show_snackbar(f"全域同步完成！已扫描矩阵所有账号并载入 {count} 个新目标", "success")
            await self.load_data()
        except Exception as ex:
            self._show_snackbar(f"同步异常: {str(ex)}", "error")

        self.sync_btn.disabled = False
        self.status_text.value = ""
        self.page.update()

    async def _do_sign(self, e):
        if self._is_signing:
            if not self._stop_requested:
                self._stop_requested = True
                self.status_text.value = "⛔ 正在申请中止执行，请等待当前任务结束..."
                self.page.update()
            return

        if self._mode == "single":
            await self._do_sign_single()
        else:
            await self._do_sign_matrix()

    async def _do_sign_single(self):
        if self._is_signing: return
        if sign_flow_lock.locked():
            self._show_snackbar("已有签到流在执行中 (可能是定时守护任务)，请等待其完成", "warning")
            return
        # 点击时实时取待签队列：load_data 快照在守护跑完后口径会漂移
        account = await self.db.get_active_account()
        forums = await self.db.get_forums(account.id, include_banned=False) if account else []
        pending = [f for f in forums if not f.is_sign_today]
        if not pending:
            self._show_snackbar("当前账号今日已无待签贴吧", "info")
            return

        self._is_signing = True
        self._stop_requested = False
        self.progress_bar.visible = True
        self.progress_bar.value = 0

        # UI 切换为停止状态
        self.sign_btn_icon.name = STOP_CIRCLE_ROUNDED
        self.sign_btn_text.value = "停止签到流"
        self.page.update()

        d_min, d_max = self._validated_delay(self.delay_min_input.value, self.delay_max_input.value, (5.0, 15.0))

        # 分母与核心流队列同口径：点击时的待签数（核心流剔除今日已签后按天洗牌）
        total = max(len(pending), 1)
        current = 0
        try:
            # 与定时守护签到互斥
            async with sign_flow_lock:
                async for result in sign_all_forums(self.db, delay_min=d_min, delay_max=d_max):
                    if self._stop_requested:
                        self._show_snackbar("签到流已由用户手动中止", "warning")
                        break

                    current += 1
                    self.progress_bar.value = min(current / total, 1.0)
                    self.status_text.value = f"正在签到: {result.fname} ({current}/{total})"

                    # --- 方案 A: 跨页面进度广播 ---
                    self.page.pubsub.send_all_on_topic("sign_progress", {
                        "value": min(current / total, 1.0),
                        "text": f"正在签到: {result.fname} ({current}/{total})",
                        "status": "running"
                    })

                    self.page.update()

                if not self._stop_requested:
                    self._show_snackbar("所有签到指令已执行完毕", "success")
        except Exception as ex:
            self._show_snackbar(f"任务异常中止: {str(ex)}", "error")
        finally:
            # finally 保证任务被取消（CancelledError）时也能复位状态，避免按钮永久卡在"停止签到流"
            self._is_signing = False
            self._stop_requested = False
            self.progress_bar.visible = False
            self.status_text.value = ""

            # UI 恢复
            self.sign_btn_icon.name = PLAY_ARROW_ROUNDED
            self.sign_btn_text.value = "启动签到流"

            # 发送结束广播
            self.page.pubsub.send_all_on_topic("sign_progress", {"status": "completed"})

            await self.load_data()

    async def _do_sign_matrix(self):
        if self._is_signing: return
        if sign_flow_lock.locked():
            self._show_snackbar("已有签到流在执行中 (可能是定时守护任务)，请等待其完成", "warning")
            return
        # 与签到队列口径一致：点击时实时统计全矩阵待签（rollup 单一口径）
        rollup = await self.db.get_sign_rollup_by_account()
        pending_total = sum(a["pending"] for a in rollup["accounts"])
        if not self._accounts or pending_total == 0:
            self._show_snackbar("矩阵中没有需要签到的贴吧", "info")
            return
        
        self._is_signing = True
        self._stop_requested = False
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        
        # UI 切换为停止状态
        self.sign_btn_icon.name = STOP_CIRCLE_ROUNDED
        self.sign_btn_text.value = "停止签到流"
        self.page.update()

        d_min, d_max = self._validated_delay(self.delay_min_input.value, self.delay_max_input.value, (5.0, 15.0))
        ad_min, ad_max = self._validated_delay(self.acc_delay_min_input.value, self.acc_delay_max_input.value, (30.0, 120.0))

        # 执行期账号队列行内计数基线（从点击时 rollup 起算）
        self._matrix_live = {
            a["account_id"]: {"signed": a["signed"], "pending": a["pending"], "total": a["total"], "banned": a["banned"]}
            for a in rollup["accounts"]
        }

        # 矩阵模式：分母 = 点击时实时全矩阵待签数（与核心流剔除口径一致，进度能走满）
        total_est = max(pending_total, 1)
        current_task_idx = 0

        try:
            # 与定时守护签到互斥
            async with sign_flow_lock:
                async for result in sign_all_accounts(self.db, d_min, d_max, ad_min, ad_max):
                    if self._stop_requested:
                        self._show_snackbar("矩阵签到流已由用户手动中止", "warning")
                        break

                    current_task_idx += 1
                    progress = min(current_task_idx / total_est, 1.0)

                    self.progress_bar.value = progress
                    self.status_text.value = f"[{current_task_idx}] 正在签到: {result.get('fname')} (账号: {result.get('account_name')})"

                    # 账号队列行内联动：当前账号行高亮 + 计数逐吧跳动
                    aid = result.get("account_id")
                    live = self._matrix_live.get(aid)
                    row_ctl = self._matrix_row_controls.get(aid)
                    if live and row_ctl:
                        live["pending"] = max(live["pending"] - 1, 0)
                        if result.get("success"):
                            live["signed"] += 1
                        suffix = f"  熔断 {live['banned']}" if live.get("banned") else ""
                        row_ctl["counts"].value = f"已签 {live['signed']}  待签 {live['pending']}{suffix}"
                        done = live["pending"] == 0 and live["total"] > 0
                        row_ctl["icon"].name = CHECK_CIRCLE if done else PLAY_ARROW_ROUNDED
                        row_ctl["icon"].color = COLORS.GREEN_ACCENT_400 if done else "primary"
                        for rid, ctl in self._matrix_row_controls.items():
                            ctl["row"].bgcolor = (
                                with_opacity(0.06, "primary") if rid == aid and live["pending"] > 0
                                else (with_opacity(0.02, "primary") if self._matrix_live[rid]["pending"] == 0 and self._matrix_live[rid]["total"] > 0
                                      else with_opacity(0.01, "onSurface"))
                            )

                    # --- 方案 A: 跨页面进度广播 (矩阵模式) ---
                    self.page.pubsub.send_all_on_topic("sign_progress", {
                        "value": progress,
                        "text": f"正在矩阵签到: {result.get('fname')}",
                        "status": "running"
                    })

                    self.page.update()

                if not self._stop_requested:
                    self._show_snackbar("矩阵全扫指令已在后台全部执行完毕", "success")
        except Exception as ex:
            self._show_snackbar(f"矩阵任务异常中止: {str(ex)}", "error")
        finally:
            self._is_signing = False
            self._stop_requested = False
            self.progress_bar.visible = False
            self.status_text.value = ""

            # UI 恢复
            self.sign_btn_icon.name = PLAY_ARROW_ROUNDED
            self.sign_btn_text.value = "启动签到流"

            # 发送结束广播
            self.page.pubsub.send_all_on_topic("sign_progress", {"status": "completed"})
            await self.load_data()

    async def _do_sign_one(self, fname):
        # 非阻塞检查：全扫流持锁可达一小时，阻塞等锁会卡死按钮；
        # 而放行并发正是 sign_flow_lock 要防的同账号频率叠加
        if sign_flow_lock.locked():
            self._show_snackbar("已有签到流在执行中，稍后再试单吧手签", "warning")
            return
        self._show_snackbar(f"正在手动签到: {fname}", "info")
        async with sign_flow_lock:
            result = await sign_forum(self.db, fname)
        if result.success:
            self._show_snackbar(f"{fname} 签到成功", "success")
            await self.load_data()
        else:
            self._show_snackbar(f"{fname} 失败: {result.message}", "error")

    async def _save_daemon_config(self, e):
        """保存并热部署后台调度器配置"""
        import json

        # 校验触发时间格式，防止非法值导致守护进程静默失效 (reload 解析失败会移除旧任务)
        time_str = (self.daemon_time.value or "").strip()
        try:
            hour_s, minute_s = time_str.split(":")
            hour, minute = int(hour_s), int(minute_s)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("时间超出范围")
        except ValueError:
            self._show_snackbar("❌ 触发时间格式无效，请使用 HH:MM (如 08:30)", "error")
            self.page.update()
            return

        schedule = {
            "enabled": self.daemon_switch.value,
            "sign_time": time_str,
            "mode": self.daemon_mode_radio.value,
        }
        await self.db.set_setting("schedule", json.dumps(schedule))

        # 保存行为频率参数（校验钳制后落库；单账号/矩阵/守护三处共用）
        d_min, d_max = self._validated_delay(self.delay_min_input.value, self.delay_max_input.value, (5.0, 15.0))
        ad_min, ad_max = self._validated_delay(self.acc_delay_min_input.value, self.acc_delay_max_input.value, (30.0, 120.0))
        self.delay_min_input.value, self.delay_max_input.value = f"{d_min:g}", f"{d_max:g}"
        self.acc_delay_min_input.value, self.acc_delay_max_input.value = f"{ad_min:g}", f"{ad_max:g}"
        await self.db.set_setting("sign_delay_min", self.delay_min_input.value)
        await self.db.set_setting("sign_delay_max", self.delay_max_input.value)
        await self.db.set_setting("sign_acc_delay_min", self.acc_delay_min_input.value)
        await self.db.set_setting("sign_acc_delay_max", self.acc_delay_max_input.value)

        try:
            from tieba_mecha.core.daemon import daemon_instance
            await daemon_instance.reload(self.db)

            # 保存清单显式化：让用户看见这次保存动了哪些东西
            mode_zh = "矩阵全扫" if self.daemon_mode_radio.value == "matrix" else "单账号模式"
            self._show_snackbar(
                f"✔️ 已保存：守护{'启用' if self.daemon_switch.value else '停用'} · 触发 {time_str} · {mode_zh}"
                f" · 吧间延迟 {d_min:g}~{d_max:g}s · 账号间延迟 {ad_min:g}~{ad_max:g}s",
                "success",
            )
        except Exception as err:
            self._show_snackbar(f"❌ 守护进程重载失败: {err}", "error")

        self.page.update()

    def _validated_delay(self, raw_min: str, raw_max: str, default: tuple) -> tuple:
        """解析延迟区间：逐边解析（单边坏只回退该边）；下限 2s 钳制；倒挂自动交换。
        防手滑产生零/负延迟连发或倒挂区间。"""
        def _one(raw, fallback):
            try:
                return float(raw)
            except (ValueError, TypeError):
                return fallback

        lo = _one(raw_min, default[0])
        hi = _one(raw_max, default[1])
        clamped_lo = max(lo, 2.0)
        clamped_hi = max(hi, 2.0)
        if clamped_lo != lo or clamped_hi != hi:
            self._show_snackbar("延迟低于 2s 已自动钳制为 2s（防连发触发风控）", "warning")
        if clamped_lo > clamped_hi:
            clamped_lo, clamped_hi = clamped_hi, clamped_lo
            self._show_snackbar(f"延迟区间倒挂已自动交换为 {clamped_lo:g}~{clamped_hi:g}s", "warning")
        return clamped_lo, clamped_hi

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        from ..components.toast import show_toast
        show_toast(self.page, message, type)

    async def _on_unfollow_forum(self, fname: str):
        """取消关注单个贴吧"""
        async def do_unfollow(e):
            try:
                self.page.close(dialog)
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                res = await pm.unfollow_forums_bulk([fname])
                ok, bad = len(res["success"]), len(res["failed"])
                if ok:
                    self._show_snackbar(f"✅ 已取消关注 '{fname}'（{ok} 个账号）", "success")
                if bad:
                    self._show_snackbar(f"⚠️ {bad} 个账号取关失败，记录已保留", "warning")
                if ok + bad == 0:
                    self._show_snackbar(f"ℹ️ 没有账号关注 '{fname}'，本地记录已清理", "info")
                await self.load_data()
            except Exception as ex:
                self._show_snackbar(f"❌ 取消关注失败: {str(ex)}", "error")

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(HEART_BROKEN, color="error"), ft.Text("确认取消关注？")]),
            content=ft.Text(f"确定要取消关注 '{fname}' 吗？此操作将同时从所有账号取关该贴吧。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("确认取消", icon=HEART_BROKEN, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=do_unfollow),
            ]
        )
        self.page.open(dialog)

    async def _show_forum_history(self, forum_id: int, fname: str):
        """展示单独贴吧的签到日志记录弹窗"""
        logs = await self.db.get_sign_logs(limit=20, forum_id=forum_id)
        
        lv_items = []
        if not logs:
            lv_items.append(ft.Text("暂无任何签到追踪记录 ~", color="onSurfaceVariant", italic=True, text_align=ft.TextAlign.CENTER))
        else:
            for log in logs:
                c = "green" if log.success else "error"
                icon = CHECK_CIRCLE if log.success else ERROR
                msg_text = log.message if log.message else ("签到成功" if log.success else "未知失败")
                lv_items.append(ft.ListTile(
                    leading=ft.Icon(icon, color=c, size=20),
                    title=ft.Text("成功" if log.success else "拦截/失败", color=c, size=13, weight=ft.FontWeight.BOLD),
                    subtitle=ft.Text(f"[{log.signed_at.strftime('%Y-%m-%d %H:%M')}] {msg_text}", size=11, color="onSurfaceVariant"),
                    content_padding=ft.padding.all(0)
                ))
            
        dlg = ft.AlertDialog(
            title=ft.Row([
                ft.Icon(HISTORY_TOGGLE_OFF, color="primary"),
                ft.Text(f"{fname} - 近期战报日志", size=16, weight=ft.FontWeight.BOLD)
            ], spacing=10),
            content=ft.Container(
                content=ft.ListView(controls=lv_items, spacing=5, expand=True),
                width=350,
                height=350,
            ),
            actions=[ft.TextButton("关闭窗口", on_click=lambda e: self.page.close(dlg))],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=12)
        )
        self.page.open(dlg)
