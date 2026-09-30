"""Sign management page with Cyber-Mecha aesthetic (Dual Mode Support)"""

import asyncio
import flet as ft
from ..flet_compat import COLORS
from datetime import datetime
from typing import List, Optional

from ..components.icons import (
    GROUP_WORK, ARROW_BACK_IOS_NEW,
    SYNC_ROUNDED, PLAY_ARROW_ROUNDED, ACCESS_TIME_ROUNDED, BOLT,
    CHECK, VERIFIED_ROUNDED, RADIO_BUTTON_UNCHECKED, HISTORY_ROUNDED,
    CHECK_CIRCLE,
    ERROR, HISTORY_TOGGLE_OFF, STOP_CIRCLE_ROUNDED,
    HEART_BROKEN, BLOCK, GPP_GOOD_ROUNDED,
)
from ..utils import with_opacity
from ...core.sign import (
    get_follow_forums, sync_forums_to_db, sign_forum, sign_all_forums,
    get_sign_stats, sign_all_accounts, sign_flow_lock, SIGN_SKIP_MESSAGE,
)


class SignPage:
    """签到管理页面"""

    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self._forums = []
        self._accounts = []
        self._matrix_rollup = None  # get_sign_rollup_by_account 结果（矩阵按钮标签/确认弹窗口径）
        self._stats = {"total": 0, "success": 0, "failure": 0}
        self._is_signing = False
        self._stop_requested = False
        self._stop_event = None  # 核心流快速中止信号（1-2s 生效，替代只查 yield 边界的半分钟等待）
        # 模式已合并：列表恒为当前账号贴吧，矩阵全扫是执行控制卡的次级启动按钮（确认弹窗承载范围）

    async def load_data(self):
        """加载数据"""
        if not self.db: return
        self._loaded_date = datetime.now().date()

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

            # 当日日志映射（未签原因行级判定的数据源，一次查询防 N+1）
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            logs_today = await self.db.get_sign_logs(limit=1000, since=today_start)
            self._today_logs = {log.forum_id: log for log in logs_today}

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
            self.list_view.controls.extend(self._build_single_mode_items())
            self.total_stat.value = str(self._stats['total'])
            self.success_stat.value = str(self._stats['success'])
            self.failure_stat.value = str(self._stats['failure'])
            self.pending_stat.value = str(self._stats.get('pending', 0))
            if hasattr(self, "sign_btn") and not self._is_signing:
                self.sign_btn.text = f"启动签到流 · 当前账号 {self._stats.get('pending', 0)} 吧"
            if hasattr(self, "rhythm_summary"):
                self.rhythm_summary.value = (
                    f"吧间 {self.delay_min_input.value}~{self.delay_max_input.value}s"
                    f" · 账号间 {self.acc_delay_min_input.value}~{self.acc_delay_max_input.value}s"
                )
            # 矩阵范围常显在次级按钮上，取代整页账号队列
            if hasattr(self, "matrix_btn"):
                accs = (getattr(self, "_matrix_rollup", None) or {}).get("accounts", [])
                n_acc = len([a for a in accs if a["pending"] > 0])
                self.matrix_btn.text = f"矩阵全扫 · {n_acc} 账号 / {sum(a['pending'] for a in accs)} 吧待签"

            self.page.update()

    def build(self) -> ft.Control:
        # 统计文本组件（闭合账目：总数 = 成功 + 失败 + 待签，熔断在行内展示）
        self.total_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color="primary")
        self.success_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color=COLORS.GREEN_ACCENT_400)
        self.failure_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color=COLORS.RED_ACCENT_400)
        self.pending_stat = ft.Text("0", size=16, weight=ft.FontWeight.BOLD, color="onSurfaceVariant")
        
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
                        ft.Text("智能签到终端 / SMART SIGN", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                        ft.Text("管理当前账号签到 · 一键矩阵全扫", size=11, color="onSurfaceVariant"),
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

        # 同步按钮：语义是刷新右侧列表，归位到执行队列标题行
        self.sync_btn = ft.IconButton(
            icon=SYNC_ROUNDED,
            icon_size=18,
            tooltip="同步贴吧列表（全矩阵账号轮换拉取关注）",
            on_click=lambda e: self.page.run_task(self._do_sync, e),
        )
        
        # 主控按钮：范围并入标签，执行中切换为"停止签到流"（红色）
        self.sign_btn = ft.FilledButton(
            "启动签到流",
            icon=PLAY_ARROW_ROUNDED,
            on_click=lambda e: self.page.run_task(self._do_sign, e),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=14, vertical=12),
            ),
        )
        # 矩阵全扫次级入口：范围常显在按钮标签上，点击走确认弹窗（取代整页矩阵模式）
        self.matrix_btn = ft.OutlinedButton(
            "矩阵全扫",
            icon=GROUP_WORK,
            tooltip="所有可用账号依次签到（账号间防关联延迟），点击后确认范围与预计时长",
            on_click=lambda e: self.page.run_task(self._do_sign_matrix, e),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=14, vertical=12),
            ),
        )

        # 进度与状态
        self.progress_bar = ft.ProgressBar(value=0, visible=False, color="primary", bar_height=3)
        self.status_text = ft.Text("", size=11, color="onSurfaceVariant")
        self.status_text.expand = True

        # 列表区域 (固化ListView实例避免Flet重新挂载导致的 Flex 缩放坍塌问题)
        self.list_view = ft.ListView(expand=True, spacing=8, padding=10)
        self.list_container = ft.Container(content=self.list_view, expand=True)

        # 无浮动标签：分组 caption 已说明各行含义，避免窄输入框内标签与"秒"后缀挤压
        self.delay_min_input = ft.TextField(value="5", text_size=12, expand=True, suffix_text="秒", hint_text="最小")
        self.delay_max_input = ft.TextField(value="15", text_size=12, expand=True, suffix_text="秒", hint_text="最大")

        self.acc_delay_min_input = ft.TextField(value="30", text_size=12, expand=True, suffix_text="秒", hint_text="最小")
        self.acc_delay_max_input = ft.TextField(value="120", text_size=12, expand=True, suffix_text="秒", hint_text="最大")

        # 节奏参数折叠区：专家参数默认收起，摘要行常显当前值。
        # caption 作组标题放在输入行上方；底部留白防文字贴折叠区下缘被分隔线裁剪
        self.rhythm_summary = ft.Text("", size=10, color="onSurfaceVariant")
        self.matrix_settings = ft.ExpansionTile(
            title=ft.Text("节奏参数", size=12, weight=ft.FontWeight.BOLD, color="primary"),
            subtitle=self.rhythm_summary,
            controls=[
                ft.Container(
                    content=ft.Column([
                        ft.Text("吧间延迟 · 单账号/矩阵/守护共用", size=10, color="onSurfaceVariant"),
                        ft.Row([
                            self.delay_min_input,
                            ft.Text("至", size=12, color="onSurfaceVariant"),
                            self.delay_max_input,
                        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Container(height=8),
                        ft.Text("账号间延迟 · 矩阵/守护共用", size=10, color="onSurfaceVariant"),
                        ft.Row([
                            self.acc_delay_min_input,
                            ft.Text("~", size=12),
                            self.acc_delay_max_input,
                        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ], spacing=6),
                    padding=ft.padding.only(left=4, right=4, top=2, bottom=12),
                )
            ],
        )

        # 定时守护配置（压缩为两行 + 保存键）
        self.daemon_switch = ft.Switch(value=False)
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
            prefix_icon=ACCESS_TIME_ROUNDED,
            hint_text="HH:MM",
        )
        self.daemon_save_btn = ft.FilledButton(
            "保存配置并生效",
            icon=BOLT,
            on_click=self._save_daemon_config,
            style=ft.ButtonStyle(
                bgcolor=COLORS.SECONDARY,
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        # 左侧合并控制面板：执行/节奏/守护三段一卡，消除框架开销
        control_panel = ft.Container(
            content=ft.Column([
                self.sign_btn,
                self.matrix_btn,
                ft.Divider(height=1, color=with_opacity(0.08, "onSurface")),
                self.matrix_settings,
                ft.Divider(height=1, color=with_opacity(0.08, "onSurface")),
                ft.Row([
                    ft.Text("定时守护", size=12, weight=ft.FontWeight.BOLD, color="secondary"),
                    ft.Container(expand=True),
                    ft.Text("启用周期执行", size=11, color="onSurfaceVariant"),
                    self.daemon_switch,
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self.daemon_time,
                self.daemon_mode_radio,
                self.daemon_save_btn,
            ], spacing=12),
            padding=16,
            bgcolor=with_opacity(0.03, "onSurface"),
            border_radius=12,
            width=300,
        )

        # 主内容
        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                ft.Row([
                    # 左侧控制面板（合并单卡，常规视口零滚动）
                    ft.Container(
                        content=ft.Column([control_panel], spacing=0),
                        width=320,
                    ),
                    # 右侧执行队列（自动扩展）
                    ft.Column([
                        ft.Row([
                            ft.Text("执行队列", size=14, weight=ft.FontWeight.W_500),
                            self.sync_btn,
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

    @staticmethod
    def _pending_reason(forum, today_logs: dict):
        """未签原因判定（整改#13）：集中一处、用模块常量匹配，禁止 UI 散落字符串比较"""
        if forum.is_banned:
            return "已熔断", COLORS.RED_ACCENT_400
        log = (today_logs or {}).get(forum.id)
        if log is not None and log.message == SIGN_SKIP_MESSAGE:
            return "今日跳过", COLORS.AMBER
        if forum.last_sign_status == "failure":
            msg = (log.message if log else "") or ""
            if msg.startswith("被风控限流"):
                return "风控退避", COLORS.AMBER
            return "失败", COLORS.RED_ACCENT_400
        return "待签", "onSurfaceVariant"

    def _build_single_mode_items(self):
        items = []
        today_logs = getattr(self, "_today_logs", None) or {}
        for f in self._forums:
            is_signed = f.is_sign_today and not f.is_banned
            if f.is_banned:
                lead_icon = ft.Icon(BLOCK, color="error", size=18)
                badge = ("已熔断", COLORS.RED_ACCENT_400)
            elif is_signed:
                lead_icon = ft.Icon(VERIFIED_ROUNDED, color="primary", size=18)
                badge = None
            else:
                lead_icon = ft.Icon(RADIO_BUTTON_UNCHECKED, color="onSurfaceVariant", size=18)
                badge = self._pending_reason(f, today_logs)

            title_row = ft.Row([ft.Text(f.fname, size=13, weight=ft.FontWeight.W_500)], spacing=8)
            if badge:
                label, color = badge
                title_row.controls.append(ft.Container(
                    content=ft.Text(label, size=9, color="white", weight=ft.FontWeight.BOLD),
                    bgcolor=color,
                    padding=ft.padding.symmetric(horizontal=5, vertical=1),
                    border_radius=4,
                    tooltip=f.ban_reason if f.is_banned else None,
                ))

            actions = [
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
            ]
            if f.is_banned:
                # 熔断行不给手签（再撞 3250004），给恢复入口
                actions.append(ft.IconButton(
                    icon=GPP_GOOD_ROUNDED,
                    icon_size=18,
                    icon_color="secondary",
                    tooltip="解除熔断",
                    on_click=lambda e, acc_id=f.account_id, fname=f.fname: self.page.run_task(self._on_unban_forum, acc_id, fname),
                ))
            else:
                actions.append(ft.FilledButton(
                    "签到" if not is_signed else "已签",
                    icon=BOLT if not is_signed else CHECK,
                    on_click=lambda e, fn=f.fname: self.page.run_task(self._do_sign_one, fn) if not self._is_signing else None,
                    disabled=is_signed or self._is_signing,
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=6),
                        padding=ft.padding.symmetric(horizontal=10)
                    )
                ))

            card = ft.Container(
                content=ft.Row([
                    lead_icon,
                    ft.Column([
                        title_row,
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
                    *actions,
                ]),
                bgcolor=with_opacity(0.02, "primary") if is_signed else with_opacity(0.01, "onSurface"),
                padding=8,
                border_radius=8,
            )
            items.append(card)
        return items

    async def _ensure_fresh_day(self):
        """长开页面跨天自愈：日期变更即重置签到状态并重载（统计陈旧问题）"""
        today = datetime.now().date()
        if getattr(self, "_loaded_date", None) != today:
            self._loaded_date = today
            if hasattr(self.db, "check_and_reset_daily_sign"):
                await self.db.check_and_reset_daily_sign()
            await self.load_data()

    async def _do_sync(self, e):
        # 同步会逐账号翻页拉关注列表，与签到流撞同账号即双流并发——必须互斥
        if sign_flow_lock.locked():
            self._show_snackbar("已有签到流在执行中，同步已推迟（避免同账号双流并发）", "warning")
            return
        await self._ensure_fresh_day()
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

    def _set_main_running(self, running: bool):
        """主按钮运行态：执行中变红色"停止签到流"，结束恢复带待签范围标签"""
        if running:
            self.sign_btn.text = "停止签到流"
            self.sign_btn.icon = STOP_CIRCLE_ROUNDED
            self.sign_btn.style = ft.ButtonStyle(
                bgcolor=COLORS.ERROR,
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=14, vertical=12),
            )
        else:
            self.sign_btn.text = f"启动签到流 · 当前账号 {self._stats.get('pending', 0)} 吧"
            self.sign_btn.icon = PLAY_ARROW_ROUNDED
            self.sign_btn.style = ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=14, vertical=12),
            )

    async def _do_sign(self, e):
        await self._ensure_fresh_day()
        if self._is_signing:
            if not self._stop_requested:
                self._stop_requested = True
                if self._stop_event is not None:
                    self._stop_event.set()
                self.status_text.value = "⛔ 正在申请中止执行，请等待当前任务结束..."
                self.page.update()
            return

        await self._do_sign_single()

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
        self._stop_event = asyncio.Event()
        self.progress_bar.visible = True
        self.progress_bar.value = 0

        # UI 切换为停止状态
        self._set_main_running(True)
        self.page.update()

        d_min, d_max = self._validated_delay(self.delay_min_input.value, self.delay_max_input.value, (5.0, 15.0))

        # 分母与核心流队列同口径：点击时的待签数（核心流剔除今日已签后按天洗牌）
        total = max(len(pending), 1)
        current = 0
        try:
            # 与定时守护签到互斥；ignore_skip：手动补扫=明确意图，无视拟人化跳过骰子
            async with sign_flow_lock:
                async for result in sign_all_forums(
                    self.db, delay_min=d_min, delay_max=d_max,
                    ignore_skip=True, stop_event=self._stop_event,
                ):
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
            self._set_main_running(False)

            # 发送结束广播
            self.page.pubsub.send_all_on_topic("sign_progress", {"status": "completed"})

            await self.load_data()

    async def _do_sign_matrix(self, e=None):
        """矩阵入口：预检 + 范围/预计时长确认（30-60 分钟级大任务，误触归零）"""
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

        active_accounts = [a for a in rollup["accounts"] if a["pending"] > 0]
        d_min, d_max = self._validated_delay(self.delay_min_input.value, self.delay_max_input.value, (5.0, 15.0))
        ad_min, ad_max = self._validated_delay(self.acc_delay_min_input.value, self.acc_delay_max_input.value, (30.0, 120.0))
        eta_min = max(1, round(
            (pending_total * (d_min + d_max) / 2 + len(active_accounts) * (ad_min + ad_max) / 2) / 60
        ))

        def _launch(e_):
            self.page.close(dialog)
            self.page.run_task(self._do_sign_matrix_run)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(GROUP_WORK, color="primary"), ft.Text("启动矩阵全扫？")]),
            content=ft.Text(
                f"范围：{len(active_accounts)} 个账号 / {pending_total} 个待签贴吧\n"
                f"节奏：吧间 {d_min:g}~{d_max:g}s，账号间 {ad_min:g}~{ad_max:g}s\n"
                f"预计耗时约 {eta_min} 分钟"
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("启动", icon=PLAY_ARROW_ROUNDED, on_click=_launch),
            ],
        )
        self.page.open(dialog)

    async def _do_sign_matrix_run(self):
        """矩阵执行体（确认弹窗后进入；进入时重验锁与待签，防弹窗期间状态漂移）"""
        if self._is_signing: return
        if sign_flow_lock.locked():
            self._show_snackbar("已有签到流在执行中 (可能是定时守护任务)，请等待其完成", "warning")
            return
        rollup = await self.db.get_sign_rollup_by_account()
        pending_total = sum(a["pending"] for a in rollup["accounts"])
        if not self._accounts or pending_total == 0:
            self._show_snackbar("矩阵中没有需要签到的贴吧", "info")
            return
        
        self._is_signing = True
        self._stop_requested = False
        self._stop_event = asyncio.Event()
        self.progress_bar.visible = True
        self.progress_bar.value = 0

        # UI 切换为停止状态
        self._set_main_running(True)
        self.page.update()

        d_min, d_max = self._validated_delay(self.delay_min_input.value, self.delay_max_input.value, (5.0, 15.0))
        ad_min, ad_max = self._validated_delay(self.acc_delay_min_input.value, self.acc_delay_max_input.value, (30.0, 120.0))

        # 矩阵模式：分母 = 点击时实时全矩阵待签数（与核心流剔除口径一致，进度能走满）
        total_est = max(pending_total, 1)
        current_task_idx = 0

        try:
            # 与定时守护签到互斥；ignore_skip：手动补扫=明确意图，无视拟人化跳过骰子
            async with sign_flow_lock:
                async for result in sign_all_accounts(
                    self.db, d_min, d_max, ad_min, ad_max,
                    ignore_skip=True, stop_event=self._stop_event,
                ):
                    if self._stop_requested:
                        self._show_snackbar("矩阵签到流已由用户手动中止", "warning")
                        break

                    current_task_idx += 1
                    progress = min(current_task_idx / total_est, 1.0)

                    self.progress_bar.value = progress
                    self.status_text.value = f"[{current_task_idx}] 正在签到: {result.get('fname')} (账号: {result.get('account_name')})"

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
            self._set_main_running(False)

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

    async def _on_unban_forum(self, account_id: int, fname: str):
        """解除单吧熔断：恢复每日签到与发帖调度资格（带确认）"""
        async def do_unban(e):
            try:
                self.page.close(dialog)
                ok = await self.db.unban_forum(account_id, fname)
                if ok:
                    self._show_snackbar(f"已解除 '{fname}' 熔断，恢复签到与发帖资格", "success")
                    await self.load_data()
                else:
                    self._show_snackbar(f"'{fname}' 未处于熔断状态", "info")
            except Exception as ex:
                self._show_snackbar(f"解除熔断失败: {str(ex)}", "error")

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(GPP_GOOD_ROUNDED, color="secondary"), ft.Text("解除熔断？")]),
            content=ft.Text(f"确认解除 '{fname}' 的吧务封禁熔断？解除后该吧恢复每日签到与发帖调度。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("解除熔断", style=ft.ButtonStyle(bgcolor="secondary", color="white"), on_click=do_unban),
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
