"""发帖运行中心：任务队列 / 已发归档 / 运行日志。

从批量发帖页抽离的运行区独立成页，配置页回归"配置 + 启动"职责。
运行数据全部来自数据库（batch_post_tasks / material_pool / batch_post_logs），
两页互不依赖：配置页启动的任务，本页刷新即可见。
"""

import asyncio
import json
from datetime import datetime

import flet as ft

from ..flet_compat import COLORS
from ..utils import with_opacity
from ..components import icons
from .batch_post.log_stream import LogStream, format_log_timestamp


def _format_schedule_display(task) -> str:
    """格式化任务的调度信息显示"""
    schedule_type = getattr(task, 'schedule_type', 'once') or 'once'
    schedule_time = getattr(task, 'schedule_time', None)

    if schedule_type == 'once':
        return schedule_time.strftime("%m-%d %H:%M") if schedule_time else "即时"
    elif schedule_type == 'daily':
        time_str = schedule_time.strftime("%H:%M") if schedule_time else "??:??"
        cycle = getattr(task, 'cycle_count', 0) or 0
        return f"每天 {time_str} (第{cycle+1}轮)"
    elif schedule_type == 'weekly':
        day_names = ["一", "二", "三", "四", "五", "六", "日"]
        day_idx = getattr(task, 'schedule_day_of_week', 0) or 0
        time_str = schedule_time.strftime("%H:%M") if schedule_time else "??:??"
        cycle = getattr(task, 'cycle_count', 0) or 0
        return f"每周{day_names[day_idx]} {time_str} (第{cycle+1}轮)"
    elif schedule_type == 'interval':
        hours = getattr(task, 'interval_hours', 6) or 6
        cycle = getattr(task, 'cycle_count', 0) or 0
        next_str = schedule_time.strftime("%m-%d %H:%M") if schedule_time else ""
        return f"每{hours}h (第{cycle+1}轮) {next_str}"
    return schedule_time.strftime("%m-%d %H:%M") if schedule_time else "即时"


class BatchPostCenterPage:
    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate

        self._tasks = []
        self._accounts = []
        self._account_name_map = {}
        self._archive_items = []          # 归档库当前页数据
        self._survival_cache = {}         # tid -> "checking"/"alive"/"dead"
        self._archive_surv_filter = "all"
        self._archive_search_text = ""
        self._selected_archive_ids = set()
        self._archive_page = 1
        self._archive_page_size = 50
        self._archive_total = 0
        self._max_bump_count = 20

        self._log_stream = LogStream(
            page, db,
            show_snackbar=self._show_snackbar,
            resolve_account=self._resolve_account_name,
            with_toolbar=True,
        )
        # 兼容别名：拦截详情按钮复用
        self._show_rejection_detail = self._log_stream.show_rejection_detail

        self._init_controls()

    # ------------------------------------------------------------------
    def _resolve_account_name(self, account_id) -> str:
        """把账号 ID 解析为可读名称"""
        if isinstance(account_id, int) or (isinstance(account_id, str) and account_id.isdigit()):
            acc_id_int = int(account_id)
            name = self._account_name_map.get(acc_id_int)
            return name or f"账号-{acc_id_int}"
        return str(account_id)

    def _show_snackbar(self, message: str, type="info"):
        from ..components.toast import show_toast
        show_toast(self.page, message, type)

    def _navigate(self, page_name: str):
        if self.on_navigate:
            self.on_navigate(page_name)

    # ==================================================================
    # 数据加载
    # ==================================================================
    async def load_data(self):
        """加载运行数据：任务队列 + 归档 + 流水"""
        if not self.db:
            return
        try:
            (
                self._tasks, self._accounts, cache_data, _s, logs,
            ) = await asyncio.gather(
                self.db.get_all_batch_tasks(),
                self.db.get_accounts(),
                self.db.get_survival_cache_data(),
                self.db.get_settings_bulk(["max_bump_count"]),
                self.db.get_batch_post_logs(limit=100),
            )
            self._account_name_map = {a.id: (a.user_name or a.name) for a in self._accounts if a}
            self._survival_cache.update(cache_data)
            max_bump_raw = _s.get("max_bump_count", "")
            self._max_bump_count = int(max_bump_raw) if max_bump_raw else 20

            self._log_stream.replay_db_logs(logs)
            self._refresh_task_list()
            await self._refresh_archive_table()
        except Exception as e:
            from ...core.logger import log_error
            await log_error(f"[UI ERROR] center load_data failed: {e}")
            self._show_snackbar(f"运行中心数据同步异常: {str(e)}", "error")

    async def _refresh_archive_table(self):
        """分页刷新已发归档库（服务端过滤+分页）"""
        if not hasattr(self, "_archive_table"):
            return

        # 归档存活统计
        surv_counts = await self.db.get_success_survival_counts()
        alive_count = surv_counts.get("alive", 0)
        dead_count = surv_counts.get("dead", 0)
        unknown_count = surv_counts.get("unknown", 0)
        archive_total_count = alive_count + dead_count + unknown_count

        if hasattr(self, "_archive_all_count_text"):
            self._archive_all_count_text.value = f" ({archive_total_count})"
            self._archive_all_count_text.color = "white" if self._archive_surv_filter == "all" else "onSurfaceVariant"
        if hasattr(self, "_archive_alive_count_text"):
            self._archive_alive_count_text.value = f" ({alive_count})"
            self._archive_alive_count_text.color = "white" if self._archive_surv_filter == "alive" else "onSurfaceVariant"
        if hasattr(self, "_archive_dead_count_text"):
            self._archive_dead_count_text.value = f" ({dead_count})"
            self._archive_dead_count_text.color = "white" if self._archive_surv_filter == "dead" else "onSurfaceVariant"

        arch_search = self._archive_search_text if self._archive_search_text.strip() else None
        arch_surv = self._archive_surv_filter if self._archive_surv_filter != "all" else None
        arch_items, self._archive_total = await self.db.get_materials_by_status_paginated(
            statuses=["success"],
            search_text=arch_search,
            page=self._archive_page,
            page_size=self._archive_page_size,
            order_desc=True,
            survival_status=arch_surv,
        )
        self._archive_items = arch_items
        archive_rows = []
        for m in arch_items:
            try:
                m_title = m.title or ""
                m_content = m.content or ""
                m_posted_fname = m.posted_fname or "未知吧"

                display_t = m_title if len(m_title) <= 15 else m_title[:15] + "..."
                # 自顶状态逻辑
                bump_mode = getattr(m, 'bump_mode', 'once') or 'once'
                max_bump = self._max_bump_count
                is_limit_reached = (bump_mode == "once" and m.bump_count >= max_bump)
                is_expired = False
                if bump_mode in ("scheduled", "matrix_loop"):
                    from datetime import date as date_type
                    bump_start = getattr(m, 'bump_start_date', None)
                    bump_duration = getattr(m, 'bump_duration_days', 0) or 0
                    if bump_start and bump_duration > 0:
                        from datetime import timedelta as td
                        end_date = bump_start + td(days=bump_duration)
                        if date_type.today() > end_date:
                            is_expired = True
                bump_status_text = f"已顶{m.bump_count}"
                bump_color = "onSurfaceVariant"
                bump_tooltip = f"当前已累计自顶 {m.bump_count} 次"
                if is_limit_reached:
                    bump_status_text = f"封顶({m.bump_count})"
                    bump_color = "orange"
                    bump_tooltip = f"已达到 {max_bump} 次安全上限，系统已自动停止\n点击🔄可重置计数继续自顶"
                elif is_expired:
                    bump_status_text = f"到期({m.bump_count})"
                    bump_color = "orange"
                    bump_tooltip = f"已超过设定的持续天数，自顶已自动停止\n点击🔄可延长周期继续自顶"
                elif not m.is_auto_bump and m.bump_count > 0:
                    bump_status_text = f"暂停({m.bump_count})"
                    bump_color = "onSurfaceVariant"
                    bump_tooltip = "自顶功能当前处于手动关闭状态"

                # 存活图标：探测中用缓存 checking 状态，否则用数据库字段
                surv_status = m.survival_status or "unknown"
                surv_display = self._survival_cache.get(m.posted_tid) if m.posted_tid else None
                if surv_display != "checking":
                    surv_display = surv_status
                surv_icon = icons.HEALTH_AND_SAFETY
                surv_color = "grey"
                surv_tooltip = "探测链接存活状态"
                if surv_display == "checking":
                    surv_icon = icons.HOURGLASS_EMPTY
                    surv_color = "blue"
                    surv_tooltip = "探测中..."
                elif surv_display == "alive":
                    surv_icon = icons.CHECK_CIRCLE
                    surv_color = "green"
                    surv_tooltip = "探测完毕：该外链健康存活"
                elif surv_display == "dead":
                    surv_icon = icons.REMOVE_CIRCLE
                    surv_color = "error"
                    surv_tooltip = "已被抽除或无法访问"

                mode_icons = {"once": "🔢", "scheduled": "⏰", "matrix_loop": "🔄"}
                mode_icon = mode_icons.get(bump_mode, "🔢")
                mode_labels = {"once": "次数", "scheduled": "周期", "matrix_loop": "轮换"}
                loop_info = ""
                if bump_mode == "matrix_loop":
                    try:
                        account_ids = json.loads(getattr(m, 'bump_account_ids', '[]') or '[]')
                        if account_ids:
                            current_idx = getattr(m, 'bump_account_index', 0) or 0
                            current_acc_id = account_ids[current_idx % len(account_ids)]
                            acc_name = self._account_name_map.get(current_acc_id, f"账号-{current_acc_id}")
                            loop_info = f"\n🔄{acc_name}轮换中({current_idx + 1}/{len(account_ids)})"
                    except (json.JSONDecodeError, TypeError):
                        pass
                bump_status_text = f"{mode_icon}{bump_status_text}"
                bump_tooltip = f"模式: {mode_labels.get(bump_mode, '次数')}{loop_info}\n{bump_tooltip}"

                archive_rows.append(
                    ft.DataRow(
                        selected=m.id in self._selected_archive_ids,
                        on_select_changed=lambda e, mid=m.id: self.page.run_task(self._on_archive_row_select, mid, e.data),
                        cells=[
                            ft.DataCell(ft.Text(str(m.id))),
                            ft.DataCell(ft.Container(ft.Text(display_t, size=12, tooltip=m_title), width=200)),
                            ft.DataCell(ft.Text(m_posted_fname, weight=ft.FontWeight.BOLD, color="primary")),
                            ft.DataCell(ft.Text(
                                self._account_name_map.get(m.posted_account_id, f"账号-{m.posted_account_id}" if m.posted_account_id else "-"),
                                weight=ft.FontWeight.BOLD, color="primary")),
                            ft.DataCell(ft.Text(m.posted_time.strftime("%y-%m-%d %H:%M") if m.posted_time else "-")),
                            ft.DataCell(ft.Row([
                                ft.IconButton(
                                    icons.OPEN_IN_NEW, icon_color="primary", tooltip="在外部浏览器查看原贴",
                                    on_click=lambda e, tid=m.posted_tid: self.page.launch_url(f"https://tieba.baidu.com/p/{tid}") if tid else self._show_snackbar("该贴被系统吞没或未传回TID", "warning")
                                ),
                                ft.IconButton(
                                    surv_icon, icon_color=surv_color, tooltip=surv_tooltip,
                                    data={"tid": m.posted_tid},
                                    on_click=self._on_check_link_survival
                                ),
                                ft.IconButton(icons.RESTORE, icon_color="orange", data=m.id, on_click=self._reset_material_row, tooltip="被屏蔽了？重置为待发状态"),
                                ft.IconButton(icons.REFRESH, icon_color="teal", data=m.id, on_click=self._reset_bump_count, tooltip="归零自顶计数，重新开始"),
                            ], spacing=0)),
                            ft.DataCell(ft.Row([
                                ft.Switch(value=m.is_auto_bump, data=m.id, on_change=self._on_archive_toggle_bump, scale=0.7),
                                ft.Text(bump_status_text, size=11, color=bump_color, tooltip=bump_tooltip)
                            ], spacing=2)),
                        ]
                    )
                )
            except Exception:
                continue

        self._archive_table.rows = archive_rows
        self._update_archive_pagination()
        self._update_bulk_visibility()
        try:
            self._archive_table.update()
        except Exception:
            pass

    # ==================================================================
    # 任务队列
    # ==================================================================
    def _refresh_task_list(self):
        if hasattr(self, "task_table"):
            self.task_table.rows = [self._build_task_row(t, i) for i, t in enumerate(self._tasks)]
            try:
                self.page.update()
            except Exception:
                pass

    def _build_task_queue_view(self):
        self.task_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("序号")),
                ft.DataColumn(ft.Text("贴吧")),
                ft.DataColumn(ft.Text("账号")),
                ft.DataColumn(ft.Text("AI")),
                ft.DataColumn(ft.Text("策略")),
                ft.DataColumn(ft.Text("计划时间")),
                ft.DataColumn(ft.Text("状态")),
                ft.DataColumn(ft.Text("进度")),
                ft.DataColumn(ft.Text("操作")),
            ], rows=[],
        )
        return ft.Column([
            ft.Row([
                ft.Text("近期任务记录", size=12, weight=ft.FontWeight.BOLD),
                ft.IconButton(icons.REFRESH, on_click=lambda e: self.page.run_task(self.load_data)),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(
                content=ft.ListView([ft.Row([self.task_table], scroll=ft.ScrollMode.ADAPTIVE)], expand=True), expand=True,
                border=ft.border.all(1, with_opacity(0.05, "onSurface")), border_radius=8,
            )
        ], spacing=10, expand=True)

    def _build_task_row(self, t, index):
        status_color = {"pending": "orange", "running": "primary", "completed": "green", "failed": "error"}.get(t.status, "onSurface")

        # 优化贴吧列表显示
        try:
            if hasattr(t, "fnames_json") and t.fnames_json:
                fnames = json.loads(t.fnames_json)
                if isinstance(fnames, list):
                    count = len(fnames)
                    if count > 1:
                        fnames_disp = f"{fnames[0]} 等 {count} 吧"
                    else:
                        fnames_disp = fnames[0] if fnames else "未指定"
                else:
                    fnames_disp = str(fnames)
            else:
                fnames_disp = t.fname or "未指定"
        except Exception:
            fnames_disp = t.fname or "解析错误"

        # 截断过长显示，tooltip显示原始JSON
        if len(fnames_disp) > 15:
            fnames_disp_short = fnames_disp[:15] + "..."
        else:
            fnames_disp_short = fnames_disp
        tooltip_text = t.fnames_json if hasattr(t, "fnames_json") and t.fnames_json else fnames_disp

        # 账号信息显示：将 accounts_json ID 列表转为可读名称
        acc_names = []
        account_ids = []
        try:
            account_ids = json.loads(t.accounts_json) if hasattr(t, "accounts_json") and t.accounts_json else []
            if account_ids:
                acc_names = [self._account_name_map.get(aid, f"#{aid}") for aid in account_ids]
                if len(acc_names) > 2:
                    accounts_disp = f"{acc_names[0]}, {acc_names[1]} 等 {len(acc_names)} 号"
                else:
                    accounts_disp = ", ".join(acc_names) if acc_names else "-"
            else:
                accounts_disp = "-"
        except Exception:
            accounts_disp = "-"
        accounts_tooltip = "\n".join(acc_names) if acc_names else ""

        return ft.DataRow(cells=[
            ft.DataCell(ft.Text(str(index + 1))),
            ft.DataCell(ft.Text(fnames_disp_short, size=11, tooltip=tooltip_text)),
            ft.DataCell(ft.Text(accounts_disp, size=11, tooltip=accounts_tooltip)),
            ft.DataCell(ft.Icon(icons.AUTO_AWESOME, color="primary", size=16) if t.use_ai else ft.Text("-")),
            ft.DataCell(ft.Text(getattr(t, "strategy", "N/A"))),
            ft.DataCell(ft.Text(_format_schedule_display(t))),
            ft.DataCell(ft.Text({"pending": "待执行", "running": "运行中", "completed": "已完成", "failed": "失败"}.get(t.status, t.status), color=status_color, weight=ft.FontWeight.BOLD)),
            ft.DataCell(ft.Text(f"{t.progress}/{t.total}")),
            ft.DataCell(
                ft.Row([
                    ft.IconButton(
                        icons.COPY_ALL,
                        icon_color="primary",
                        icon_size=18,
                        tooltip="复制此任务配置（到批量发帖页，启动前需重新确认）",
                        on_click=lambda _: self.page.run_task(self._on_copy_task, t)
                    ),
                    ft.IconButton(
                        icons.DELETE_OUTLINE,
                        icon_color="error",
                        icon_size=18,
                        tooltip="删除任务",
                        on_click=lambda _: self.page.run_task(self._on_delete_task, t)
                    ),
                ], spacing=0)
            ),
        ])

    async def _on_copy_task(self, t):
        """复制历史任务配置：写入交接键并跳回批量发帖页，由预检摘要重新确认。"""
        try:
            fnames = json.loads(t.fnames_json) if getattr(t, "fnames_json", None) else ([t.fname] if t.fname else [])
            account_ids = json.loads(t.accounts_json) if getattr(t, "accounts_json", None) else []
        except Exception:
            self._show_snackbar("任务配置解析失败，无法复制", "error")
            return

        schedule_type = getattr(t, "schedule_type", "once") or "once"
        config = {
            "account_ids": account_ids,
            "local_fnames": [],
            "global_fnames": [fn for fn in fnames if fn],
            "strategy": t.strategy,
            "pairing_mode": t.pairing_mode,
            "post_count": t.total,
            "delay_min": t.delay_min,
            "delay_max": t.delay_max,
            "use_ai": bool(t.use_ai),
            "ai_persona": t.ai_persona or "normal",
            "use_schedule": schedule_type in ("daily", "weekly", "interval"),
            "schedule_type": schedule_type,
            "interval_hours": getattr(t, "interval_hours", 0) or 0,
            "schedule_day_of_week": getattr(t, "schedule_day_of_week", None),
            "reset_strategy": getattr(t, "reset_strategy", "new_only") or "new_only",
        }
        await self.db.set_setting("pending_task_copy", json.dumps(config, ensure_ascii=False))
        self._navigate("batch_post")
        self._show_snackbar(
            f"已载入任务 #{t.id} 配置（账号 {len(account_ids)} · 贴吧 {len(config['global_fnames'])}）。"
            "目标已归入全域组，启动前请通过预检摘要重新核对账号、贴吧与排期",
            "success")

    async def _on_delete_task(self, task):
        task_id = task.id
        # 尝试获取任务描述
        try:
            fnames = json.loads(task.fnames_json) if hasattr(task, "fnames_json") and task.fnames_json else []
            task_desc = fnames[0] if fnames else (task.fname or str(task_id))
        except Exception:
            task_desc = task.fname or str(task_id)

        if await self.db.delete_batch_task(task_id):
            self._show_snackbar(f"矩阵任务 [{task_desc}] 已从队列中移除", "success")
            await self.load_data()
        else:
            self._show_snackbar("删除失败", "error")

    # ==================================================================
    # 已发归档
    # ==================================================================
    async def _on_archive_search_change(self, e):
        self._archive_search_text = e.control.value
        self._archive_page = 1  # 搜索时重置到第1页
        await self._refresh_archive_table()

    async def _on_archive_select_all(self, e):
        # 跨页全选：从数据库查询所有符合条件的 ID
        is_select = e.data == "true" if isinstance(e.data, str) else bool(e.data)
        if is_select:
            arch_search = self._archive_search_text if self._archive_search_text.strip() else None
            # 传入当前存活筛选状态，确保全选只选中当前筛选条件下的记录
            arch_surv = self._archive_surv_filter if self._archive_surv_filter != "all" else None
            all_ids = await self.db.get_material_ids_by_status(
                statuses=["success"],
                search_text=arch_search,
                survival_status=arch_surv,
            )
            self._selected_archive_ids = set(all_ids)
        else:
            self._selected_archive_ids.clear()
        self._update_bulk_visibility()
        await self._refresh_archive_table()

    async def _on_archive_row_select(self, mid, selected):
        # Flet e.data 为字符串 "true"/"false"
        is_selected = selected == "true" if isinstance(selected, str) else bool(selected)

        if is_selected:
            self._selected_archive_ids.add(mid)
        else:
            self._selected_archive_ids.discard(mid)

        self._update_bulk_visibility()
        await self._refresh_archive_table()

    async def _on_archive_prev_page(self, e):
        if self._archive_page > 1:
            self._archive_page -= 1
            await self._refresh_archive_table()

    async def _on_archive_next_page(self, e):
        total_pages = max(1, (self._archive_total + self._archive_page_size - 1) // self._archive_page_size)
        if self._archive_page < total_pages:
            self._archive_page += 1
            await self._refresh_archive_table()

    def _update_archive_pagination(self):
        if not hasattr(self, "_arch_page_info"):
            return
        total_pages = max(1, (self._archive_total + self._archive_page_size - 1) // self._archive_page_size)
        sel_info = f" | 已选{len(self._selected_archive_ids)}" if self._selected_archive_ids else ""
        self._arch_page_info.value = f"{self._archive_page}/{total_pages} 页 (共{self._archive_total}条{sel_info})"
        self._arch_prev_btn.disabled = self._archive_page <= 1
        self._arch_next_btn.disabled = self._archive_page >= total_pages
        try:
            self._arch_page_info.update()
            self._arch_prev_btn.update()
            self._arch_next_btn.update()
        except Exception:
            pass

    async def _on_archive_surv_filter_click(self, mode):
        """存活状态筛选切换"""
        self._archive_surv_filter = mode
        self._archive_page = 1  # 切换筛选时重置到第1页
        # 筛选切换时清空选中集合，防止不可见项残留导致批量操作栏一直显示
        self._selected_archive_ids.clear()
        self._update_archive_filter_btns()
        await self._refresh_archive_table()

    def _update_archive_filter_btns(self):
        """更新归档库存活筛选按钮的样式"""
        f = self._archive_surv_filter
        if hasattr(self, "_archive_all_btn"):
            self._archive_all_btn.bgcolor = "primary" if f == "all" else with_opacity(0.1, "onSurface")
            all_text = self._archive_all_btn.content.controls[0]
            all_text.color = "white" if f == "all" else "onSurfaceVariant"
            try: self._archive_all_btn.update()
            except Exception: pass
        if hasattr(self, "_archive_alive_btn"):
            self._archive_alive_btn.bgcolor = "green" if f == "alive" else with_opacity(0.1, "onSurface")
            alive_icon = self._archive_alive_btn.content.controls[0]
            alive_text = self._archive_alive_btn.content.controls[1]
            alive_icon.color = "green" if f == "alive" else "onSurfaceVariant"
            alive_text.color = "white" if f == "alive" else "onSurfaceVariant"
            try: self._archive_alive_btn.update()
            except Exception: pass
        if hasattr(self, "_archive_dead_btn"):
            self._archive_dead_btn.bgcolor = "error" if f == "dead" else with_opacity(0.1, "onSurface")
            dead_icon = self._archive_dead_btn.content.controls[0]
            dead_text = self._archive_dead_btn.content.controls[1]
            dead_icon.color = "error" if f == "dead" else "onSurfaceVariant"
            dead_text.color = "white" if f == "dead" else "onSurfaceVariant"
            try: self._archive_dead_btn.update()
            except Exception: pass

    async def _on_archive_toggle_bump(self, e):
        """归档行自顶开关"""
        mid = e.control.data
        val = e.control.value
        async with self.db.async_session() as session:
            from ...db.models import MaterialPool
            m = await session.get(MaterialPool, mid)
            if m:
                m.is_auto_bump = val
                await session.commit()
                self._show_snackbar(f"物料 [{mid}] 自动回帖已{'开启' if val else '关闭'}", "info")
        for m in self._archive_items:
            if m.id == mid:
                m.is_auto_bump = val
                break

    async def _bulk_toggle_auto_bump(self, e):
        """批量开启/关闭自动回帖（归档库选中集）"""
        target_ids = list(self._selected_archive_ids)
        if not target_ids:
            return

        # 统一逻辑：如果选中项中有任何一个未开启，则全部开启；否则全部关闭
        is_any_off = False
        async with self.db.async_session() as session:
            from ...db.models import MaterialPool
            for mid in target_ids:
                m = await session.get(MaterialPool, mid)
                if m and not m.is_auto_bump:
                    is_any_off = True
                    break

            target_val = is_any_off
            for mid in target_ids:
                m = await session.get(MaterialPool, mid)
                if m:
                    m.is_auto_bump = target_val
            await session.commit()

        count = len(target_ids)
        self._selected_archive_ids.clear()
        await self._refresh_archive_table()
        self._show_snackbar(f"已批量{'开启' if target_val else '关闭'} {count} 项自动回帖", "success")

    async def _bulk_reset_bump_count(self, e):
        """批量归零自顶计数，让封顶/到期的帖子可以继续自顶"""
        target_ids = list(self._selected_archive_ids)
        if not target_ids:
            self._show_snackbar("请先勾选要归零的物料", "warning")
            return

        count = 0
        async with self.db.async_session() as session:
            from ...db.models import MaterialPool
            for mid in target_ids:
                m = await session.get(MaterialPool, mid)
                if m:
                    m.bump_count = 0
                    m.bump_account_index = 0
                    count += 1
            await session.commit()

        self._selected_archive_ids.clear()
        await self._refresh_archive_table()
        self._show_snackbar(f"已归零 {count} 项自顶计数，可重新开始", "success")

    async def _bulk_reset_archives(self, e):
        if not self._selected_archive_ids:
            return
        for mid in list(self._selected_archive_ids):
            await self.db.update_material_status(mid, "pending")
        self._selected_archive_ids.clear()
        await self._refresh_archive_table()
        self._show_snackbar("选中记录已回炉重造", "success")

    async def _reset_material_row(self, e):
        idx = e.control.data
        await self.db.update_material_status(idx, "pending")
        await self._refresh_archive_table()
        self._show_snackbar("状态已回滚到排期池", "info")

    async def _reset_bump_count(self, e):
        """重置自顶计数，让封顶/到期的帖子可以继续自顶"""
        mid = e.control.data
        async with self.db.async_session() as session:
            from ...db.models import MaterialPool
            m = await session.get(MaterialPool, mid)
            if m:
                m.bump_count = 0
                m.bump_account_index = 0
                # 如果是定时/轮换模式，刷新开始日期
                bump_mode = getattr(m, 'bump_mode', 'once') or 'once'
                if bump_mode in ("scheduled", "matrix_loop"):
                    from datetime import date
                    m.bump_start_date = date.today()
                    m.bump_last_date = None
                await session.commit()
        await self._refresh_archive_table()
        self._show_snackbar(f"物料 [{mid}] 自顶计数已归零，可继续执行", "success")

    async def _on_check_link_survival(self, e):
        """处理单条贴子存活状态探测"""
        tid = e.control.data.get("tid")
        if not tid:
            self._show_snackbar("该条归档未绑定TID记录，无法探测", "warning")
            return

        # 1. 挂起状态并刷新UI
        self._survival_cache[tid] = "checking"
        await self.load_data()

        # 2. 执行网络探测（使用精细化的 check_post_survival）
        from ...core.post import check_post_survival
        try:
            final_status, death_reason = await check_post_survival(tid)
        except Exception:
            final_status, death_reason = "dead", "error"

        # 3. 结果写入缓存并持久化到数据库
        self._survival_cache[tid] = final_status

        # 寻找对应的物料ID进行持久化（优先从归档数据查找，找不到则查库）
        mid = next((m.id for m in getattr(self, "_archive_items", []) if m.posted_tid == tid), None)
        if mid:
            await self.db.update_material_survival_status(mid, final_status, death_reason)

        if final_status == "alive":
            self._show_snackbar("响应成功：贴子目前健康正常开放访问", "success")
        else:
            self._show_snackbar("探测失败：贴子异常或已被抽除", "error")

        await self.load_data()

    async def _bulk_check_survival_status(self, e):
        """批量处理选中的贴子存活状态探测 (增强版：带实时进度提示)"""
        if not self._selected_archive_ids:
            self._show_snackbar("请先在列表中勾选想要探测的归档条目", "warning")
            return

        # 提取目标 TIDs（按选中ID从数据库查询，支持跨页选中）
        targets = []
        selected_materials = await self.db.get_materials_by_ids(list(self._selected_archive_ids))
        for m in selected_materials:
            if m.status == "success" and m.posted_tid:
                targets.append(m)
                self._survival_cache[m.posted_tid] = "checking"

        if not targets:
            self._show_snackbar("所选条目中没有包含有效 TID 的贴子", "warning")
            return

        # 1. 启动进度提示
        self.archive_progress_bar.visible = True
        self.archive_progress_bar.value = 0
        self.archive_status_text.visible = True
        self.archive_status_text.value = f"正在初始化探测任务 (0/{len(targets)})..."
        self._log_stream.add(f"🚀 开始对 {len(targets)} 条贴子执行批量存活探测...")

        # 先更新到 checking 状态显示给用户
        await self.load_data()

        alive_count = 0
        dead_count = 0
        total = len(targets)

        # 并发控制：最多同时探测3个帖子
        semaphore = asyncio.Semaphore(3)
        captcha_detected = False

        async def check_single_material(m) -> tuple[str, str, str]:
            """检测单个物料的存活状态（复用精细化检测逻辑）"""
            from ...core.post import check_post_survival
            async with semaphore:
                tid = m.posted_tid
                try:
                    status, reason = await check_post_survival(tid)
                    return tid, status, reason
                except Exception:
                    return tid, "dead", "error"
                finally:
                    # 限速：每次请求间隔0.5秒
                    await asyncio.sleep(0.5)

        try:
            # 使用 asyncio.gather 并发执行所有检测任务
            results = await asyncio.gather(
                *[check_single_material(m) for m in targets],
                return_exceptions=True
            )

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    # 异常处理
                    tid = targets[i].posted_tid
                    self._survival_cache[tid] = "dead"
                    await self.db.update_material_survival_status(targets[i].id, "dead", "error")
                    dead_count += 1
                    self._log_stream.add(f"⚠️ [错误] {targets[i].posted_fname} | TID:{tid} | {str(result)}", "error")
                else:
                    tid, status, reason = result
                    self._survival_cache[tid] = status
                    await self.db.update_material_survival_status(targets[i].id, status, reason)

                    if status == "alive":
                        alive_count += 1
                        self._log_stream.add(f"✅ [存活] {targets[i].posted_fname} | TID:{tid}")
                    else:
                        dead_count += 1
                        if reason == "captcha_required":
                            captcha_detected = True
                            self._log_stream.add(f"🚫 [验证码] {targets[i].posted_fname} | TID:{tid}", "warning")
                        else:
                            self._log_stream.add(f"❌ [阵亡] {targets[i].posted_fname} | TID:{tid}", "error")

                # 更新进度
                self.archive_progress_bar.value = (i + 1) / total
                self.archive_status_text.value = f"正在探测 ({i+1}/{total})..."
                if (i + 1) % 5 == 0:
                    self.archive_progress_bar.update()
                    self.archive_status_text.update()

            # 验证码提示
            if captcha_detected:
                self._show_snackbar("⚠️ 检测到百度验证码，建议30分钟后重试", "warning")
        except Exception as ex:
            self._log_stream.add(f"探测任务异常中止: {str(ex)}", "error")
        finally:
            self.archive_progress_bar.visible = False
            self.archive_status_text.visible = False
            self.page.update()

        self._show_snackbar(f"批量探测完毕: {alive_count} 条存活健在，{dead_count} 条已掉线", "info")
        await self.load_data()

    def _update_bulk_visibility(self):
        """同步归档批量操作栏的可见性与计数"""
        if hasattr(self, "_archive_bulk_actions"):
            self._archive_bulk_actions.visible = bool(self._selected_archive_ids)
            sel_count = len(self._selected_archive_ids)
            total_count = self._archive_total
            self._archive_selected_count_text.value = f"已选 {sel_count}/{total_count} 项"

    # ==================================================================
    # 控件构建
    # ==================================================================
    def _init_controls(self):
        # 归档分页控件
        self._arch_page_info = ft.Text("1/1 页 (共0条)", size=11, color="onSurfaceVariant")
        self._arch_prev_btn = ft.IconButton(icons.NAVIGATE_BEFORE, icon_size=16, disabled=True)
        self._arch_next_btn = ft.IconButton(icons.NAVIGATE_NEXT, icon_size=16, disabled=True)

        # 归档批量操作
        self._archive_selected_count_text = ft.Text(f"已选 0 项", size=11, color="onSurfaceVariant")
        self._archive_bulk_actions = ft.Row([
            ft.FilledButton("自顶", icon=icons.BOLT,
                            style=ft.ButtonStyle(bgcolor="primary", color="white"),
                            on_click=self._bulk_toggle_auto_bump),
            ft.FilledButton("归零", icon=icons.REFRESH,
                            style=ft.ButtonStyle(bgcolor="amber", color="black"),
                            on_click=self._bulk_reset_bump_count),
            ft.FilledButton("回炉", icon=icons.RESTORE_PAGE,
                            style=ft.ButtonStyle(bgcolor="orange", color="white"),
                            on_click=self._bulk_reset_archives),
            ft.FilledButton("探测", icon=icons.RADAR,
                            style=ft.ButtonStyle(bgcolor="teal", color="white"),
                            on_click=self._bulk_check_survival_status),
            self._archive_selected_count_text,
        ], visible=False, spacing=5, alignment=ft.MainAxisAlignment.START, wrap=True)

        # 归档探测进度控件
        self.archive_progress_bar = ft.ProgressBar(value=0, visible=False, color="teal", expand=True)
        self.archive_status_text = ft.Text("准备探测...", size=11, color="onSurfaceVariant", visible=False)

        self._archive_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("ID", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("发布标题", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("最终着陆吧", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("发帖账号", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("发帖时间", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("时光溯洄", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("自顶状态", size=11, weight=ft.FontWeight.BOLD)),
            ],
            rows=[],
            heading_row_height=40, data_row_min_height=45, data_row_max_height=60,
            column_spacing=18,
            show_checkbox_column=True,
            on_select_all=self._on_archive_select_all,
        )

    def _build_archive_view(self):
        """构建已发归档库视图"""
        archive_search = ft.TextField(
            hint_text="搜索标题或着陆贴吧...",
            prefix_icon=icons.SEARCH,
            on_change=self._on_archive_search_change,
            height=40, text_size=12, content_padding=10,
            width=250
        )

        # 存活筛选按钮（保存引用以便切换时更新样式）
        self._archive_all_count_text = ft.Text("", size=10)
        self._archive_alive_count_text = ft.Text("", size=10)
        self._archive_dead_count_text = ft.Text("", size=10)
        self._archive_all_btn = ft.Container(
            content=ft.Row([
                ft.Text("全部", size=11, color="white" if self._archive_surv_filter == "all" else "onSurfaceVariant"),
                self._archive_all_count_text
            ], spacing=2),
            padding=ft.padding.symmetric(6, 12),
            bgcolor="primary" if self._archive_surv_filter == "all" else with_opacity(0.1, "onSurface"),
            border_radius=8,
            on_click=lambda _: self.page.run_task(self._on_archive_surv_filter_click, "all"),
            animate=200
        )
        self._archive_alive_btn = ft.Container(
            content=ft.Row([
                ft.Icon(icons.CHECK_CIRCLE, size=12, color="green" if self._archive_surv_filter == "alive" else "onSurfaceVariant"),
                ft.Text("存活", size=11, color="white" if self._archive_surv_filter == "alive" else "onSurfaceVariant"),
                self._archive_alive_count_text
            ], spacing=2),
            padding=ft.padding.symmetric(6, 12),
            bgcolor="green" if self._archive_surv_filter == "alive" else with_opacity(0.1, "onSurface"),
            border_radius=8,
            on_click=lambda _: self.page.run_task(self._on_archive_surv_filter_click, "alive"),
            animate=200
        )
        self._archive_dead_btn = ft.Container(
            content=ft.Row([
                ft.Icon(icons.REMOVE_CIRCLE, size=12, color="error" if self._archive_surv_filter == "dead" else "onSurfaceVariant"),
                ft.Text("阵亡", size=11, color="white" if self._archive_surv_filter == "dead" else "onSurfaceVariant"),
                self._archive_dead_count_text
            ], spacing=2),
            padding=ft.padding.symmetric(6, 12),
            bgcolor="error" if self._archive_surv_filter == "dead" else with_opacity(0.1, "onSurface"),
            border_radius=8,
            on_click=lambda _: self.page.run_task(self._on_archive_surv_filter_click, "dead"),
            animate=200
        )

        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icons.ARCHIVE_OUTLINED, size=16),
                    ft.Text("发帖档案室", size=12, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.IconButton(icons.REFRESH, icon_size=16, on_click=lambda _: self.page.run_task(self.load_data), tooltip="刷新档案"),
                ], spacing=10),
                ft.Row([
                    self.archive_status_text,
                    self.archive_progress_bar,
                ], spacing=10),
                ft.Row([
                    archive_search,
                    ft.Row([
                        self._archive_all_btn,
                        self._archive_alive_btn,
                        self._archive_dead_btn,
                    ], spacing=5),
                    self._archive_bulk_actions,
                ], spacing=10),
                ft.Container(
                    content=ft.ListView([ft.Row([self._archive_table], scroll=ft.ScrollMode.ADAPTIVE)], expand=True),
                    expand=True,
                    border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                    border_radius=12,
                    padding=5,
                ),
                # 分页控件
                ft.Row([
                    ft.IconButton(icons.NAVIGATE_BEFORE, icon_size=16, on_click=lambda e: self.page.run_task(self._on_archive_prev_page, e), data="arch_prev"),
                    self._arch_page_info,
                    ft.IconButton(icons.NAVIGATE_NEXT, icon_size=16, on_click=lambda e: self.page.run_task(self._on_archive_next_page, e), data="arch_next"),
                ], alignment=ft.MainAxisAlignment.CENTER, spacing=10),
            ], expand=True, spacing=10),
            expand=True,
            padding=ft.padding.only(top=10)
        )

    def build(self) -> ft.Control:
        header = ft.Row([
            ft.Row([
                ft.IconButton(icons.ARROW_BACK_IOS_NEW, on_click=lambda e: self._navigate("dashboard")),
                ft.Column([
                    ft.Text("发帖运行中心 / BATCH RUN CENTER", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                    ft.Text("任务队列、已发归档与运行日志 — 数据与批量发帖页实时互通", size=11, color="onSurfaceVariant"),
                ], spacing=0),
            ], spacing=5),
            ft.Container(expand=True),
            ft.ElevatedButton(
                "发起新任务",
                icon=icons.PLAY_CIRCLE_FILL_ROUNDED,
                on_click=lambda e: self._navigate("batch_post"),
                style=ft.ButtonStyle(color="white", bgcolor="primary"),
            ),
        ])

        tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            tabs=[
                ft.Tab(text="任务中心", icon=icons.UPDATE_ROUNDED, content=self._build_task_queue_view()),
                ft.Tab(text="已发归档", icon=icons.ARCHIVE_ROUNDED, content=self._build_archive_view()),
                ft.Tab(text="运行日志", icon=icons.STREAM_ROUNDED, content=self._log_stream.build_view()),
            ],
            expand=True,
        )

        return ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                tabs,
            ], expand=True, spacing=12),
            padding=ft.padding.only(left=20, right=20, top=10, bottom=20), expand=True,
        )
