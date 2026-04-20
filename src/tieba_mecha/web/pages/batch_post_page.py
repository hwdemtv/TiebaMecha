"""Batch posting page with Cyber-Mecha aesthetic and progress monitor"""

import flet as ft
from ..flet_compat import COLORS
import asyncio
import json
from datetime import datetime, timedelta
from ..utils import with_opacity
from ..components import icons
from ...core.account import get_account_credentials
from ...core.batch_post import BatchPostTask, BatchPostManager
from ...core.link_manager import SmartLinkConnector
from ...core.ai_optimizer import AIOptimizer

class BatchPostPage:
    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self.manager = BatchPostManager(db)
        self._is_running = False
        self._tasks = []
        self._accounts = []
        self._materials = [] # now mapped to DB MaterialPool
        self._all_fnames = []
        
        # Link survival status cache: tid -> "checking", "alive", "dead"
        self._survival_cache = {}
        # Archive survival filter: "all", "alive", "dead"
        self._archive_surv_filter = "all"
        self.connector = SmartLinkConnector(db)
        self._file_picker = ft.FilePicker(
            on_result=self._on_file_result,
            on_upload=self._on_upload_progress
        )
        
        # 搜索与批量选择状态
        self._material_search_text = ""
        self._archive_search_text = ""
        self._selected_material_ids = set()
        self._selected_archive_ids = set()
        
        # 矩阵配置持久化状态
        self._selected_account_ids = set()
        self._selected_forum_names = set()
        self._selected_group_names = set()
        self._temp_target_fnames = []  # 新增：用于在弹窗间流转的临时吧名列表
        
        # 账号选择增强状态
        self._account_search_text = ""
        self._account_select_all = False
        self._initial_load_done = False

        # 初始化所有 UI 控件
        self._init_controls()

    async def load_data(self):
        """加载页面数据"""
        if not self.db: return
        try:
            self._tasks = await self.db.get_all_batch_tasks()
            self._accounts = await self.db.get_matrix_accounts()
            # 双擎靶位缓存
            self._native_forums = await self.db.get_native_post_targets()
            self._target_groups = await self.db.get_target_pool_groups()
            
            self._materials = await self.db.get_materials()
            
            # [持久化同步] 从数据库加载历史探测状态到内存缓存
            for m in self._materials:
                if m.status == "success" and m.posted_tid and m.survival_status != "unknown":
                    # 只同步已探测过的有效状态，checking 状态不持久化以防卡死
                    self._survival_cache[m.posted_tid] = m.survival_status

# [自顶配置同步] 加载 max_bump_count 到内存缓存
            max_bump_raw = await self.db.get_setting("max_bump_count")
            self._max_bump_count = int(max_bump_raw) if max_bump_raw else 20
            
            # [自顶配置同步] 恢复 AI 改写与定时执行开关状态
            ai_raw = await self.db.get_setting("use_ai_rewrite")
            if ai_raw is not None:
                self.use_ai_switch.value = ai_raw == "1"
            
            sched_raw = await self.db.get_setting("use_schedule")
            if sched_raw is not None:
                self.use_schedule.value = sched_raw == "1"
                # 恢复定时输入框的可见性
                if hasattr(self, "schedule_time") and hasattr(self, "interval_hours"):
                    self.schedule_time.visible = sched_raw == "1"
                    self.interval_hours.visible = sched_raw == "1"
            
            # [自顶配置同步] 恢复矩阵协同模式开关
            matrix_raw = await self.db.get_setting("bump_matrix_enabled")
            if matrix_raw is not None:
                self.bump_matrix_switch.value = matrix_raw == "1"
            
            # [自顶配置同步] 恢复自顶模式
            bump_mode_raw = await self.db.get_setting("bump_mode")
            if bump_mode_raw is not None and hasattr(self, "bump_mode_group"):
                self.bump_mode_group.value = bump_mode_raw
                self.bump_loop_container.visible = (bump_mode_raw == "matrix_loop")
            
            # [持久化同步] 恢复上次选中的贴吧 
            last_forums_raw = await self.db.get_setting("last_selected_target_forums")
            if last_forums_raw:
                try:
                    self._temp_target_fnames = json.loads(last_forums_raw)
                    count = len(self._temp_target_fnames)
                    if count > 0:
                        self.forum_select_btn.text = f"🎯 火力已锁定: {count} 个目标点"
                        self.forum_select_btn.style = ft.ButtonStyle(color="white", bgcolor="primary")
                except: pass

            # [持久化同步] 恢复上次选中的账号
            last_acc_raw = await self.db.get_setting("last_selected_account_ids")
            has_last_acc = False
            if last_acc_raw:
                try:
                    acc_ids = json.loads(last_acc_raw)
                    if acc_ids:
                        self._selected_account_ids = set(acc_ids)
                        has_last_acc = True
                except: pass

            # 首次加载初始化选择：如果有持久化则用持久化，否则仅勾选状态正常的账号
            if not self._initial_load_done:
                if not has_last_acc:
                    for acc in self._accounts:
                        if acc.status == "active":
                            self._selected_account_ids.add(acc.id)
                self._initial_load_done = True
            
            self._refresh_task_list()
            self._refresh_account_pool()
            self._refresh_forum_pool()
            await self._refresh_material_table()

            # [持久化同步] 从数据库加载最近的流水记录
            logs = await self.db.get_batch_post_logs(limit=100)
            self.log_list.controls[:] = []  # 加载新数据前清空
            for log in reversed(logs):
                log_data = {
                    "status": log.status,
                    "account_name": log.account_name,
                    "fname": log.fname,
                    "title": log.title,
                    "tid": log.tid,
                    "msg": log.message,
                    "error": log.message,
                    "account_id": log.account_id,
                    "progress": "-", "total": "-"
                }
                self._add_log(log_data, timestamp=log.created_at.strftime("%H:%M:%S"))
            
        except Exception as e:
            from ...core.logger import log_error
            await log_error(f"[UI ERROR] load_data failed: {e}")
            self._show_snackbar(f"数据同步异常: {str(e)}", "error")

    def _refresh_task_list(self):
        if hasattr(self, "task_table"):
            self.task_table.rows = [self._build_task_row(t, i) for i, t in enumerate(self._tasks)]
            self.page.update()

    async def _on_account_search_change(self, e):
        """账号池搜索实时过滤"""
        self._account_search_text = e.control.value.lower()
        self._refresh_account_pool()

    async def _on_account_select_all_toggle(self, e):
        """全选/取消全选账号"""
        self._account_select_all = e.control.value
        # 获取当前正在显示的账号（过滤后的）
        visible_accs = [
            acc for acc in self._accounts 
            if not self._account_search_text or 
            self._account_search_text in (acc.name or "").lower() or 
            self._account_search_text in (acc.user_name or "").lower() or
            self._account_search_text in str(acc.id)
        ]
        
        for acc in visible_accs:
            if self._account_select_all:
                self._selected_account_ids.add(acc.id)
            else:
                if acc.id in self._selected_account_ids:
                    self._selected_account_ids.remove(acc.id)
        
        self._refresh_account_pool()

    async def _save_bump_config(self, e):
        """保存自顶配置到数据库"""
        try:
            # 读取并校验最大次数 (5-100)
            try:
                max_count = int(self.bump_max_count_field.value)
                max_count = max(5, min(100, max_count))  # 限制范围 5-100
            except (ValueError, AttributeError):
                max_count = 20  # 默认值
                self.bump_max_count_field.value = str(max_count)
            
            # 读取并校验冷却时间 (10-1440)
            try:
                cooldown = int(self.bump_cooldown_field.value)
                cooldown = max(10, min(1440, cooldown))  # 限制范围 10-1440
            except (ValueError, AttributeError):
                cooldown = 45  # 默认值
                self.bump_cooldown_field.value = str(cooldown)
            
# 矩阵模式开关
            matrix_enabled = "1" if self.bump_matrix_switch.value else "0"
            ai_enabled = "1" if self.use_ai_switch.value else "0"
            schedule_enabled = "1" if self.use_schedule.value else "0"
            
            # 写入数据库
            await self.db.set_setting("max_bump_count", str(max_count))
            await self.db.set_setting("bump_cooldown_minutes", str(cooldown))
            await self.db.set_setting("bump_matrix_enabled", matrix_enabled)
            await self.db.set_setting("use_ai_rewrite", ai_enabled)
            await self.db.set_setting("use_schedule", schedule_enabled)
            
            # 保存自顶模式配置
            bump_mode = self.bump_mode_group.value if hasattr(self, "bump_mode_group") else "once"
            await self.db.set_setting("bump_mode", bump_mode)
            
            if hasattr(self, "bump_hour_field"):
                try:
                    bump_hour = max(0, min(23, int(self.bump_hour_field.value)))
                except (ValueError, AttributeError):
                    bump_hour = 10
                await self.db.set_setting("bump_hour", str(bump_hour))
            
            if hasattr(self, "bump_duration_field"):
                try:
                    bump_duration = int(self.bump_duration_field.value) if self.bump_duration_field.value != "∞" else 0
                except (ValueError, AttributeError):
                    bump_duration = 7
                await self.db.set_setting("bump_duration_days", str(bump_duration))
            
            # 同步更新内存缓存
            self._max_bump_count = max_count
            
            # 刷新物料表以反映新的封顶判断
            await self._refresh_material_table()
            
            self._show_snackbar(f"自顶配置已保存: 最大{max_count}次, 冷却{cooldown}分钟, 矩阵{'开启' if matrix_enabled == '1' else '关闭'}", "success")
        except Exception as ex:
            await log_error(f"[UI ERROR] _save_bump_config failed: {ex}")
            self._show_snackbar(f"保存配置失败: {str(ex)}", "error")

    def _on_bump_mode_change(self, e):
        """自顶模式切换事件"""
        mode = e.control.value
        # 显示/隐藏矩阵轮换配置区域
        if hasattr(self, "bump_loop_container"):
            self.bump_loop_container.visible = (mode == "matrix_loop")
        # 永久模式切换
        if hasattr(self, "bump_duration_field") and hasattr(self, "bump_permanent_switch"):
            if mode == "matrix_loop" and self.bump_permanent_switch.value:
                self.bump_duration_field.disabled = True
                self.bump_duration_field.value = "∞"
            else:
                self.bump_duration_field.disabled = False
                self.bump_duration_field.value = "7"
        self.page.update()

    def _on_bump_permanent_change(self, e):
        """永久循环开关切换"""
        if hasattr(self, "bump_duration_field"):
            if e.control.value:
                self.bump_duration_field.disabled = True
                self.bump_duration_field.value = "∞"
                self.bump_duration_field.tooltip = "永久循环模式，不设天数上限"
            else:
                self.bump_duration_field.disabled = False
                self.bump_duration_field.value = "7"
                self.bump_duration_field.tooltip = None
            self.bump_duration_field.update()

    def _refresh_account_pool(self):
        """刷新账号池选择器 UI - 支持过滤与独立展示"""
        if hasattr(self, "account_pool_column"):
            items = []
            # 过滤逻辑
            filtered_accounts = [
                acc for acc in self._accounts 
                if not self._account_search_text or 
                self._account_search_text in (acc.name or "").lower() or 
                self._account_search_text in (acc.user_name or "").lower() or
                self._account_search_text in str(acc.id)
            ]

            for acc in filtered_accounts:
                is_suspended = (acc.status == "suspended_proxy")
                is_banned = (acc.status == "banned")
                is_expired = (acc.status == "expired")
                
                # 状态标识
                proxy_label = "🟢 代理正常" if acc.proxy_id else "🟡 裸连警告"
                if is_suspended: proxy_label = "🔴 代理失效"
                
                status_icon = "🟢"
                if is_banned: status_icon = "💔 封禁"
                elif is_expired: status_icon = "🔘 失效"
                elif acc.status == "error": status_icon = "🟡 异常"
                
                weight_dots = "●" * (acc.post_weight // 2) + "○" * (5 - acc.post_weight // 2)
                
                # 获取显示名称，增加针对空名称的容错回退
                display_name = acc.name or acc.user_name or f"账号-{acc.id}"
                
                item_label = f"{status_icon} | {display_name} ({proxy_label})"
                
                # Checkbox
                items.append(
                    ft.Checkbox(
                        label=item_label,
                        value=acc.id in self._selected_account_ids,
                        data=acc.id,
                        on_change=self._on_account_select_change, # 修正为正确的名称
                        disabled=is_suspended,
                        label_style=ft.TextStyle(size=11),
                    )
                )
            self.account_pool_column.controls = items
            
            # 更新已选计数提示
            if hasattr(self, "account_pool_title"):
                count = len(self._selected_account_ids)
                self.account_pool_title.value = f"参与账号池 ({count}/{len(self._accounts)})"
            
            try:
                self.page.update()
            except:
                pass

    def _refresh_forum_pool(self):
        """刷新贴吧池选择器 UI（用于兼容的本地吧列表）"""
        if hasattr(self, "forum_pool_column"):
            items = []
            for fname in self._native_forums:
                cb = ft.Checkbox(
                    label=fname,
                    value=fname in self._selected_forum_names,
                    data=fname,
                    fill_color="green",  # 标记为安全的本土吧
                    on_change=self._on_forum_select_change
                )
                items.append(cb)
            
            self.forum_pool_column.controls = items
            
            # 同时也刷新靶标组
            if hasattr(self, "global_group_column"):
                g_items = []
                for g in self._target_groups:
                    cb = ft.Checkbox(
                        label=g,
                        value=g in self._selected_group_names,
                        data=g,
                        fill_color="red", # 标记为轰炸大池
                        on_change=self._on_group_select_change
                    )
                    g_items.append(cb)
                    
                if not g_items:
                    g_items.append(ft.Container(
                        content=ft.Text("尚无任何全域轰炸组数据\n请点击右上角【录入新靶群】", color="onSurfaceVariant", text_align="center", size=12),
                        alignment=ft.alignment.center,
                        height=150
                    ))
                self.global_group_column.controls = g_items

            self.page.update()

    def _filter_checkboxes(self, container: ft.Column, text: str):
        """过滤列表中的复选框"""
        for cb in container.controls:
            if isinstance(cb, ft.Checkbox):
                cb.visible = text.lower() in cb.label.lower()
        container.update()

    def _toggle_select_all(self, container: ft.Column, value: bool):
        """批量全选/取消"""
        for cb in container.controls:
            if isinstance(cb, ft.Checkbox) and cb.visible:
                cb.value = value
                # 同步到状态集
                if container == self.account_pool_column:
                    if value: self._selected_account_ids.add(cb.data)
                    else: self._selected_account_ids.discard(cb.data)
                elif container == self.forum_pool_column:
                    if value: self._selected_forum_names.add(cb.data)
                    else: self._selected_forum_names.discard(cb.data)
                elif hasattr(self, "global_group_column") and container == self.global_group_column:
                    if value: self._selected_group_names.add(cb.data)
                    else: self._selected_group_names.discard(cb.data)
        container.update()
        if container == self.account_pool_column:
            self._save_account_selection()

    def _on_account_select_change(self, e):
        acc_id = e.control.data
        if e.control.value: self._selected_account_ids.add(acc_id)
        else: self._selected_account_ids.discard(acc_id)
        # 同步更新标题已选计数提示
        if hasattr(self, "account_pool_title"):
            count = len(self._selected_account_ids)
            self.account_pool_title.value = f"参与账号池 ({count}/{len(self._accounts)})"
            self.account_pool_title.update()
        self._save_account_selection()

    def _save_account_selection(self):
        """将当前选中的账号持久化到数据库"""
        if self.db:
            ids_json = json.dumps(list(self._selected_account_ids))
            self.page.run_task(self.db.set_setting, "last_selected_account_ids", ids_json)

    def _on_forum_select_change(self, e):
        fname = e.control.data
        if e.control.value: self._selected_forum_names.add(fname)
        else: self._selected_forum_names.discard(fname)

    def _on_group_select_change(self, e):
        group_name = e.control.data
        if e.control.value: self._selected_group_names.add(group_name)
        else: self._selected_group_names.discard(group_name)

    def _toggle_select_all_forums(self, e):
        """全选/取消贴吧 (保留兼容旧版逻辑)"""
        self._toggle_select_all(self.forum_pool_column, e.control.value)


    async def _open_native_forum_config(self, e):
        """配置本机原发安全圈的弹窗 - 增加搜索与全选"""
        forums = await self.db.get_all_unique_forums()
        
        forum_list_container = ft.Column(spacing=5, height=300, scroll=ft.ScrollMode.ADAPTIVE)
        
        async def on_toggle(e):
            fid = e.control.data
            is_checked = e.control.value
            await self.db.toggle_forum_post_target(fid, is_checked)
            self._show_snackbar(f"原生发帖防线 {'[启用]' if is_checked else '[关闭]'} 成功！", "success")

        def build_items(filter_text=""):
            items = []
            for f in forums:
                if filter_text.lower() in f['fname'].lower():
                    items.append(
                        ft.Checkbox(
                            label=f['fname'],
                            value=f['is_post_target'],
                            data=f['fid'],
                            on_change=lambda ev: self.page.run_task(on_toggle, ev),
                            fill_color="green",
                            label_style=ft.TextStyle(color="onSurface")
                        )
                    )
            return items

        def on_search(e):
            forum_list_container.controls = build_items(e.control.value)
            forum_list_container.update()

        def on_select_all(e):
            val = e.control.value
            for cb in forum_list_container.controls:
                if isinstance(cb, ft.Checkbox):
                    cb.value = val
                    # 触发后端更新
                    self.page.run_task(self.db.toggle_forum_post_target, cb.data, val)
            forum_list_container.update()
            self._show_snackbar(f"已批量{'开启' if val else '关闭'}安全权限", "info")

        search_field = ft.TextField(
            hint_text="搜索贴吧名...",
            prefix_icon=icons.SEARCH,
            on_change=on_search,
            expand=True,
            height=40,
            text_size=12,
            content_padding=10
        )

        forum_list_container.controls = build_items()

        if not forums:
            forum_list_container.controls.append(ft.Text("系统内无吧数据，请先于Dashboard运行签到或同步关注贴吧！", color="error"))

        async def close_dialog(_):
            self.page.close(dialog)
            await self.load_data()
            # 设置完立刻刷新底层的 UI，不强制踢出用户
            self._refresh_forum_pool()
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.SHIELD_ROUNDED, color="green"), ft.Text("安全原初打法配置")]),
            content=ft.Column([
                ft.Row([
                    search_field,
                    ft.Checkbox(label="全选", on_change=on_select_all, fill_color="green"),
                ], spacing=10),
                ft.Text("开启专属保护开关后，会强制优先调用原生关注小号出战：", size=11, color="onSurfaceVariant"),
                ft.Container(
                    content=forum_list_container,
                    border=ft.border.all(1, with_opacity(0.1, "green")),
                    border_radius=8,
                    padding=5
                )
            ], tight=True, width=450, spacing=10),
            actions=[
                ft.FilledButton("防抽网络编织完毕锁定", icon=icons.SECURITY, on_click=close_dialog, style=ft.ButtonStyle(bgcolor="green", color="white"))
            ]
        )
        self.page.open(dialog)

    def _open_add_target_pool_dialog(self, e):
        """打开导入全域靶场弹窗"""
        group_input = ft.TextField(label="靶场标签名", hint_text="例如：引流区、同行区等", text_size=12, expand=True)
        forums_input = ft.TextField(label="录入吧名（逗号分隔）", hint_text="c语言,python,java", text_size=12, multiline=True, min_lines=3, max_lines=6)
        
        def save(_):
            group = group_input.value.strip()
            text = forums_input.value.replace("，", ",").split(",")
            fnames = [f.strip() for f in text if f.strip()]
            
            if not group or not fnames:
                self._show_snackbar("标签名和吧名均不能为空", "warning")
                return
                
            async def _bg_task():
                count = await self.db.upsert_target_pools(fnames, group)
                self._show_snackbar(f"成功注入 {len(fnames)} 个标尺贴吧，其中 {count} 个为全新收录！", "success")
                self.page.close(dialog)
                self.page.run_task(self.load_data)
            self.page.run_task(_bg_task)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.LIBRARY_ADD, color="error"), ft.Text("录入全新靶场目标群")]),
            content=ft.Column([
                group_input,
                forums_input,
                ft.Text("支持多个贴吧名以英文逗号批量导入", size=10, color="onSurfaceVariant")
            ], tight=True, width=400, spacing=10),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("封存入库", icon=icons.SAVE, on_click=save, style=ft.ButtonStyle(bgcolor="error", color="white"))
            ]
        )
        self.page.open(dialog)

    def _update_bulk_visibility(self):
        """统一同步批量操作栏的可见性与计数"""
        if hasattr(self, "_material_bulk_actions"):
            self._material_bulk_actions.visible = bool(self._selected_material_ids)
            self._material_selected_count_text.value = f"已选 {len(self._selected_material_ids)} 项"

        if hasattr(self, "_archive_bulk_actions"):
            self._archive_bulk_actions.visible = bool(self._selected_archive_ids)
            self._archive_selected_count_text.value = f"已选 {len(self._selected_archive_ids)} 项"


    async def _on_material_search_change(self, e):
        self._material_search_text = e.control.value
        await self._refresh_material_table()

    async def _on_archive_search_change(self, e):
        self._archive_search_text = e.control.value
        await self._refresh_material_table()

    async def _bulk_delete_materials(self, e):
        if not self._selected_material_ids:
            return
        
        async def do_delete(_):
            for mid in list(self._selected_material_ids):
                await self.db.delete_material(mid)
            self._selected_material_ids.clear()
            self._materials = await self.db.get_materials()
            await self._refresh_material_table()
            self._show_snackbar("批量删除成功", "success")
            self.page.close(dialog)

        dialog = ft.AlertDialog(
            title=ft.Text("确认批量销毁？"),
            content=ft.Text(f"将永久删除选中的 {len(self._selected_material_ids)} 条物料，不可撤回。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("确认爆炸", icon=icons.DELETE_FOREVER, style=ft.ButtonStyle(bgcolor="error", color="white"), on_click=do_delete),
            ]
        )
        self.page.open(dialog)

    async def _bulk_reset_archives(self, e):
        if not self._selected_archive_ids:
            return
        for mid in list(self._selected_archive_ids):
            await self.db.update_material_status(mid, "pending")
        self._selected_archive_ids.clear()
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
        self._show_snackbar("选中记录已回炉重造", "success")

    async def _bulk_reset_materials(self, e):
        """批量重置排期池中的选中项（通常用于将‘失败’重置为‘待发’）"""
        if not self._selected_material_ids:
            return
        for mid in list(self._selected_material_ids):
            await self.db.update_material_status(mid, "pending")
        self._selected_material_ids.clear()
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
        self._show_snackbar(f"已批量重置 {len(self._selected_material_ids)} 条物料到待发状态", "success")

    async def _refresh_material_table(self):
        print(f"[DEBUG] _refresh_material_table 被调用，物料数: {len(self._materials)}")

        if not hasattr(self, "_material_table") or not hasattr(self, "_archive_table"):
            print(f"[DEBUG] _material_table 或 _archive_table 未初始化，跳过刷新")
            return

        pending_rows = []
        archive_rows = []

        pending = sum(1 for m in self._materials if m.status == "pending")
        success = sum(1 for m in self._materials if m.status == "success")
        failed = sum(1 for m in self._materials if m.status == "failed")

        print(f"[DEBUG] 状态统计 - 待发: {pending}, 成功: {success}, 失败: {failed}")
        
        if hasattr(self, "_stats_text"):
            self._stats_text.value = f"状态分布:  ⏳待发({pending})   ✅成功({success})   ❌失败({failed})"

        # 归档存活统计逻辑
        alive_count = 0
        dead_count = 0
        for m in self._materials:
            if m.status == "success" and m.posted_tid:
                surv = self._survival_cache.get(m.posted_tid)
                if surv == "alive":
                    alive_count += 1
                elif surv == "dead":
                    dead_count += 1
        
        if hasattr(self, "_archive_all_count_text"):
            self._archive_all_count_text.value = f" ({success})"
            self._archive_all_count_text.color = "white" if self._archive_surv_filter == "all" else "onSurfaceVariant"
        if hasattr(self, "_archive_alive_count_text"):
            self._archive_alive_count_text.value = f" ({alive_count})"
            self._archive_alive_count_text.color = "white" if self._archive_surv_filter == "alive" else "onSurfaceVariant"
        if hasattr(self, "_archive_dead_count_text"):
            self._archive_dead_count_text.value = f" ({dead_count})"
            self._archive_dead_count_text.color = "white" if self._archive_surv_filter == "dead" else "onSurfaceVariant"

        if hasattr(self, "bottom_tabs"):
            for tab in self.bottom_tabs.tabs:
                if tab.icon == "list_alt_rounded":
                    tab.text = f"物料排期池 ({pending + failed})"
                elif tab.icon == "archive_rounded":
                    tab.text = f"已发归档库 ({success})"
            
        for m in self._materials:
            try:
                # 搜索过滤逻辑
                m_title = m.title or ""
                m_content = m.content or ""
                m_posted_fname = m.posted_fname or "未知吧"
                
                if m.status in ("pending", "failed"):
                    if self._material_search_text and self._material_search_text.lower() not in m_title.lower() and self._material_search_text.lower() not in m_content.lower():
                        continue
                else:
                    if self._archive_search_text and self._archive_search_text.lower() not in m_title.lower() and self._archive_search_text.lower() not in m_posted_fname.lower():
                        continue
                    
                    # 存活状态过滤
                    surv_status = self._survival_cache.get(m.posted_tid, "unknown")
                    if self._archive_surv_filter == "alive" and surv_status != "alive":
                        continue
                    if self._archive_surv_filter == "dead" and surv_status != "dead":
                        continue

                display_t = m_title if len(m_title) <= 15 else m_title[:15] + "..."
                display_c = m_content if len(m_content) <= 18 else m_content[:18] + "..."
                
                ai_text = "✨独立改写" if m.ai_status == "rewritten" else "无处理"
                ai_color = "primary" if m.ai_status == "rewritten" else "onSurfaceVariant"
                
                if m.status in ("pending", "failed"):
                    status_color = "onSurfaceVariant" if m.status == "pending" else "error"
                    status_icon = icons.SCHEDULE if m.status == "pending" else icons.ERROR
                    status_text = "待发送" if m.status == "pending" else "遭遇拒稿"
                    
                    pending_rows.append(
                        ft.DataRow(
                            selected=m.id in self._selected_material_ids,
                            on_select_changed=lambda e, mid=m.id: self.page.run_task(self._on_material_row_select, mid, e.data),
                            cells=[
                                ft.DataCell(ft.Text(str(m.id))),
                                ft.DataCell(ft.Text(display_t, tooltip=display_t)),
                                ft.DataCell(ft.Text(display_c, tooltip=display_c)),
                                ft.DataCell(
                                    ft.Row([
                                        ft.Icon(status_icon, color=status_color, size=14), 
                                        ft.Text(status_text, color=status_color, size=12),
                                        ft.IconButton(
                                            icons.INFO,
                                            icon_size=14,
                                            icon_color=status_color,
                                            tooltip="点击查看拒稿原因详情",
                                            data={
                                                "error": m.last_error,
                                                "account_id": m.posted_account_id,
                                                "fname": m.posted_fname
                                            },
                                            on_click=self._show_rejection_detail,
                                            visible=(m.status == "failed")
                                        )
                                    ], spacing=4)
                                ),
                                ft.DataCell(ft.Row([
                                    ft.Text(ai_text, color=ai_color, size=12),
                                    ft.IconButton(icons.VISIBILITY, icon_size=16, icon_color="primary", data=m, on_click=self._on_preview_ai_click, visible=(m.ai_status=="rewritten"))
                                ], spacing=2)),
                                ft.DataCell(ft.Row([
                                    ft.IconButton(icons.EDIT, icon_color="blue", data=m, on_click=self._on_edit_material_click, tooltip="手动微调文案"),
                                    ft.IconButton(icons.AUTO_AWESOME, icon_color="primary", data=m.id, on_click=self._on_single_ai_rewrite_click, tooltip="触发AI改写"),
                                    ft.IconButton(icons.DELETE, icon_color="error", data=m.id, on_click=self._delete_material_row, tooltip="永久销毁该行"),
                                ], spacing=0)),
                                ft.DataCell(ft.Switch(value=m.is_auto_bump, data=m.id, on_change=self._on_material_toggle_bump, scale=0.8, tooltip="待发布成功后，系统将自动开始循环回帖流程")),
                            ]
                        )
                    )
                else:
                    # 自顶状态逻辑增强：根据模式区分显示
                    bump_mode = getattr(m, 'bump_mode', 'once') or 'once'
                    max_bump = getattr(self, "_max_bump_count", 20)
                    
                    # 判断是否达到上限（仅 once 模式有封顶概念）
                    is_limit_reached = (bump_mode == "once" and m.bump_count >= max_bump)
                    
                    # 判断是否超过持续期
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

                    
                    # 存活探测状态判定
                    surv_status = self._survival_cache.get(m.posted_tid, "unknown")
                    surv_icon = icons.HEALTH_AND_SAFETY
                    surv_color = "grey"
                    surv_tooltip = "探测链接存活状态"
                    if surv_status == "checking":
                        surv_icon = icons.HOURGLASS_EMPTY
                        surv_color = "blue"
                        surv_tooltip = "探测中..."
                    elif surv_status == "alive":
                        surv_icon = icons.CHECK_CIRCLE
                        surv_color = "green"
                        surv_tooltip = "探测完毕：该外链健康存活"
                    elif surv_status == "dead":
                        surv_icon = icons.REMOVE_CIRCLE
                        surv_color = "error"
                        surv_tooltip = "已被抽除或无法访问"

                    # 获取自顶模式信息
                    bump_mode = getattr(m, 'bump_mode', 'once') or 'once'
                    mode_icons = {"once": "🔢", "scheduled": "⏰", "matrix_loop": "🔄"}
                    mode_icon = mode_icons.get(bump_mode, "🔢")
                    mode_labels = {"once": "次数", "scheduled": "周期", "matrix_loop": "轮换"}
                    
                    # 获取当前轮换账号信息
                    loop_info = ""
                    if bump_mode == "matrix_loop":
                        try:
                            account_ids = json.loads(getattr(m, 'bump_account_ids', '[]') or '[]')
                            if account_ids:
                                current_idx = getattr(m, 'bump_account_index', 0) or 0
                                current_acc_id = account_ids[current_idx % len(account_ids)]
                                current_acc = next((a for a in self._accounts if a.id == current_acc_id), None)
                                acc_name = current_acc.name if current_acc else f"账号{current_acc_id}"
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
                                ft.DataCell(ft.Text(display_t, tooltip=display_t)),
                                ft.DataCell(ft.Text(m_posted_fname, weight=ft.FontWeight.BOLD, color="primary")),
                                ft.DataCell(ft.Text(
                                    next((a.name for a in self._accounts if a.id == m.posted_account_id), str(m.posted_account_id) if m.posted_account_id else "-"),
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
                                    ft.Switch(value=m.is_auto_bump, data=m.id, on_change=self._on_material_toggle_bump, scale=0.7),
                                    ft.Text(bump_status_text, size=11, color=bump_color, tooltip=bump_tooltip)
                                ], spacing=2)),
                            ]
                        )
                    )
            except Exception as ex:
                print(f"[ERROR] 渲染物料行 ID:{m.id} 失败: {str(ex)}")
                continue
                
        self._material_table.rows = pending_rows
        self._archive_table.rows = archive_rows

        # 同步更新批量操作栏
        self._update_bulk_visibility()

        # 直接更新整个页面，确保所有嵌套控件都能刷新
        try:
            self.page.update()
        except Exception:
            pass

    async def _on_material_toggle_bump(self, e):
        mid = e.control.data
        val = e.control.value
        async with self.db.async_session() as session:
            from ...db.models import MaterialPool
            m = await session.get(MaterialPool, mid)
            if m:
                m.is_auto_bump = val
                await session.commit()
                self._show_snackbar(f"物料 [{mid}] 自动回帖已{'开启' if val else '关闭'}", "info")
        for m in self._materials:
            if m.id == mid:
                m.is_auto_bump = val
                break

    async def _on_check_link_survival(self, e):
        """处理单条贴子存活状态探测"""
        tid = e.control.data.get("tid")
        if not tid:
            self._show_snackbar("该条归档未绑定TID记录，无法探测", "warning")
            return
            
        # 1. 挂起状态并刷新UI
        self._survival_cache[tid] = "checking"
        await self.load_data()
        
        # 2. 执行网络探测
        is_alive = False
        try:
            import aiotieba
            async with aiotieba.Client() as client:
                res = await client.get_posts(tid)
                # 存活特征：返回了有效的 fid
                if res and res.forum and res.forum.fid > 0:
                    is_alive = True
        except Exception as ex:
            pass # 请求抛出异常一样判定为不存活
            
        # 3. 结果写入缓存并持久化到数据库
        final_status = "alive" if is_alive else "dead"
        self._survival_cache[tid] = final_status
        
        # 寻找对应的物料ID进行持久化
        mid = next((m.id for m in self._materials if m.posted_tid == tid), None)
        if mid:
            await self.db.update_material_survival_status(mid, final_status)

        if is_alive:
            self._show_snackbar("响应成功：贴子目前健康正常开放访问", "success")
        else:
            self._show_snackbar("探测失败：贴子异常或已被抽除", "error")
            
        await self.load_data()

    async def _bulk_check_survival_status(self, e):
        """批量处理选中的贴子存活状态探测 (增强版：带实时进度提示)"""
        if not self._selected_archive_ids:
            self._show_snackbar("请先在列表中勾选想要探测的归档条目", "warning")
            return
            
        # 提取目标 TIDs
        targets = []
        for m in self._materials:
            if m.id in self._selected_archive_ids and m.status == "success" and m.posted_tid:
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
        self._add_log(f"🚀 开始对 {len(targets)} 条贴子执行批量存活探测...")
        
        # 先更新到 checking 状态显示给用户
        await self.load_data()
        
        alive_count = 0
        dead_count = 0
        total = len(targets)
        
        # 并发控制：最多同时探测3个帖子
        semaphore = asyncio.Semaphore(3)
        captcha_detected = False
        
        async def check_single_material(m) -> tuple[str, str, str]:
            """检测单个物料的存活状态"""
            from ...db.models import MaterialPool
            async with semaphore:
                tid = m.posted_tid
                status = "dead"
                reason = "error"
                
                try:
                    import aiotieba
                    async with aiotieba.Client() as client:
                        res = await client.get_posts(tid)
                        
                        # 检测验证码拦截
                        if res and hasattr(res, 'text') and '验证码' in str(res.text or ''):
                            return tid, "dead", "captcha_required"
                        
                        if res and res.forum and res.forum.fid > 0:
                            # 增强判断：检查帖子基本信息完整性
                            if res.thread and res.thread.reply_num is not None:
                                return tid, "alive", ""
                            if res.thread and res.thread.title:
                                return tid, "alive", ""
                            return tid, "alive", ""
                        else:
                            return tid, "dead", "unknown_error"
                except Exception as ex:
                    error_msg = str(ex).lower()
                    if "captcha" in error_msg or "验证码" in str(ex):
                        return tid, "dead", "captcha_required"
                    elif "deleted" in error_msg or "removed" in error_msg:
                        return tid, "dead", "deleted_by_user"
                    elif "banned" in error_msg or "blocked" in error_msg:
                        return tid, "dead", "banned_by_mod"
                    elif "not found" in error_msg or "404" in error_msg:
                        return tid, "dead", "auto_removed"
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
                    await self.db.update_material_survival_status(targets[i].id, "dead")
                    dead_count += 1
                    self._add_log(f"⚠️ [错误] {targets[i].posted_fname} | TID:{tid} | {str(result)}", "error")
                else:
                    tid, status, reason = result
                    self._survival_cache[tid] = status
                    await self.db.update_material_survival_status(targets[i].id, status)
                    
                    if status == "alive":
                        alive_count += 1
                        self._add_log(f"✅ [存活] {targets[i].posted_fname} | TID:{tid}")
                    else:
                        dead_count += 1
                        if reason == "captcha_required":
                            captcha_detected = True
                            self._add_log(f"🚫 [验证码] {targets[i].posted_fname} | TID:{tid}", "warning")
                        else:
                            self._add_log(f"❌ [阵亡] {targets[i].posted_fname} | TID:{tid}", "error")
                
                # 更新进度
                self.archive_progress_bar.value = (i + 1) / total
                self.archive_status_text.value = f"正在探测 ({i+1}/{total})..."
                if (i + 1) % 5 == 0:
                    self.page.update()
            
            # 验证码提示
            if captcha_detected:
                self._show_snackbar("⚠️ 检测到百度验证码，建议30分钟后重试", "warning")
        except Exception as ex:
            self._add_log(f"探测任务异常中止: {str(ex)}", "error")
        finally:
            self.archive_progress_bar.visible = False
            self.archive_status_text.visible = False
            self.page.update()
            
        self._show_snackbar(f"批量探测完毕: {alive_count} 条存活健在，{dead_count} 条已掉线", "info")
        await self.load_data()

    async def _add_material_row(self, e):
        t = self._quick_title.value.strip() or "暂无标题"
        c = self._quick_content.value.strip()
        if not c:
            self._show_snackbar("内容不可为空", "error")
            return
        
        await self.db.add_materials_bulk([(t, c)])
        self._quick_title.value = ""
        self._quick_content.value = ""
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
        self._show_snackbar("成功添加一条物料录入", "success")

    async def _delete_material_row(self, e):
        idx = e.control.data
        if await self.db.delete_material(idx):
            self._materials = await self.db.get_materials()
            await self._refresh_material_table()

    async def _reset_material_row(self, e):
        idx = e.control.data
        await self.db.update_material_status(idx, "pending")
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
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
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
        self._show_snackbar(f"物料 [{mid}] 自顶计数已归零，可继续执行", "success")

    async def _on_edit_material_click(self, e):
        m = e.control.data
        edit_title = ft.TextField(label="基准标题", value=m.title, width=500)
        edit_content = ft.TextField(label="主句文案 (将混合零宽防御)", value=m.content, multiline=True, width=500, min_lines=4, max_lines=7)
        
        def close_dialog(_):
            self.page.close(dialog)
            
        async def save_changes(_):
            if not edit_title.value.strip() and not edit_content.value.strip():
                self._show_snackbar("标题与内容不能同时为空", "error")
                return
            await self.db.update_material_content(m.id, edit_title.value, edit_content.value)
            self._materials = await self.db.get_materials()
            await self._refresh_material_table()
            self._show_snackbar("文案手动修改已被硬编码记录", "success")
            self.page.close(dialog)

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.EDIT_DOCUMENT, color="blue"), ft.Text("人工干预弹药库")]),
            content=ft.Column([
                ft.Text("⚠ 若该物料已经过 AI 魔法改写，二次修改将会覆盖当前的缓存值。"),
                ft.Text("- 链接格式保持原样，不要修改、删除或替换\n- 保留原文的核心信息和关键数据\n- 改写时围绕链接所指向的资源进行自然描述，并在描述文字与链接之间插入两个换行符"),
                edit_title,
                edit_content
            ], tight=True, spacing=15),
            actions=[
                ft.TextButton("算了吧", on_click=close_dialog),
                ft.FilledButton("保存干预修剪", icon=icons.SAVE, on_click=save_changes),
            ]
        )
        self.page.open(dialog)

    async def _clear_all_materials(self, e=None):
        await self.db.clear_materials()
        self._materials = []
        await self._refresh_material_table()
        if e: self._show_snackbar("物料池已全库排空", "success")

    def _open_batch_paste_dialog(self, e):
        """打开批量粘贴导入对话框"""
        paste_content = ft.TextField(
            label="粘贴内容（支持 CSV 或纯文本）",
            hint_text="CSV格式: 每行 标题,内容\n纯文本: 每行一条内容",
            multiline=True,
            min_lines=8,
            max_lines=15,
            width=600,
        )
        format_hint = ft.Text(
            "支持格式：\n• CSV: 标题,内容（每行一条）\n• 纯文本: 每行一条内容，标题自动设为'暂无标题'",
            size=11, color="onSurfaceVariant"
        )

        async def do_import(_):
            text = paste_content.value.strip()
            if not text:
                self._show_snackbar("请先粘贴内容", "warning")
                return

            pairs = []
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # 检测是否为 CSV 格式（包含逗号分隔）
                if ',' in line:
                    parts = line.split(',', 1)  # 只分割第一个逗号
                    if len(parts) == 2:
                        title = parts[0].strip() or "暂无标题"
                        content = parts[1].strip()
                        if content:
                            pairs.append((title, content))
                else:
                    # 纯文本格式
                    pairs.append(("暂无标题", line))

            if not pairs:
                self._show_snackbar("未解析到有效内容", "warning")
                return

            added_count = await self.db.add_materials_bulk(pairs)
            self._materials = await self.db.get_materials()
            await self._refresh_material_table()
            self.page.close(dialog)
            self._show_snackbar(f"成功导入 {added_count} 条文案物料", "success")

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.CONTENT_PASTE_GO, color="primary"), ft.Text("批量粘贴导入")], spacing=10),
            content=ft.Column([paste_content, format_hint], spacing=10, tight=True),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(dialog)),
                ft.FilledButton("导入", on_click=do_import),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

    async def _reset_all_materials(self, e=None):
        await self.db.reset_materials_status()
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
        if e: self._show_snackbar("所有已发送的物料状态和锁已强行卸除", "success")

    async def _export_materials(self, e):
        import csv, os
        from datetime import datetime
        if not self._materials:
            self._show_snackbar("无可导出的物料", "warning")
            return
        
        try:
            filename = f"materials_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            with open(filename, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["ID", "Title", "Content", "Status", "AI_Status"])
                for m in self._materials:
                    writer.writerow([m.id, m.title, m.content, m.status, m.ai_status])
        except Exception:
            pass

    async def _on_material_search_change(self, e):
        self._material_search_text = e.control.value
        await self._refresh_material_table()

    async def _on_archive_search_change(self, e):
        self._archive_search_text = e.control.value
        await self._refresh_material_table()

    async def _on_material_select_all(self, e):
        # 仅选择当前过滤后的可见项 (所见即所得)
        visible_ids = []
        for m in self._materials:
            if m.status in ("pending", "failed"):
                if not self._material_search_text or self._material_search_text.lower() in m.title.lower() or self._material_search_text.lower() in m.content.lower():
                    visible_ids.append(m.id)

        # e.data 是字符串 "true"/"false"，需要转换
        is_select = e.data == "true" if isinstance(e.data, str) else bool(e.data)
        if is_select:
            self._selected_material_ids.update(visible_ids)
        else:
            for vid in visible_ids:
                self._selected_material_ids.discard(vid)
        await self._refresh_material_table()

    async def _on_archive_select_all(self, e):
        visible_ids = []
        for m in self._materials:
            if m.status == "success":
                if not self._archive_search_text or self._archive_search_text.lower() in m.title.lower() or self._archive_search_text.lower() in (m.posted_fname or "").lower():
                    visible_ids.append(m.id)

        # e.data 是字符串 "true"/"false"，需要转换
        is_select = e.data == "true" if isinstance(e.data, str) else bool(e.data)
        if is_select:
            self._selected_archive_ids.update(visible_ids)
        else:
            for vid in visible_ids:
                self._selected_archive_ids.discard(vid)
        await self._refresh_material_table()

    async def _bulk_toggle_auto_bump(self, e):
        # 自动探测当前生效的选择集（排期池或归档库）
        target_ids = list(self._selected_material_ids) if self._selected_material_ids else list(self._selected_archive_ids)
        
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
            
        # 清空对应的选择集并刷新
        count = len(target_ids)
        self._selected_material_ids.clear()
        self._selected_archive_ids.clear()
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
        self._show_snackbar(f"已批量{'开启' if target_val else '关闭'} {count} 项自动回帖", "success")

    async def _bulk_reset_bump_count(self, e):
        """批量归零自顶计数，让封顶/到期的帖子可以继续自顶"""
        target_ids = list(self._selected_material_ids) if self._selected_material_ids else list(self._selected_archive_ids)
        
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
        
        self._selected_material_ids.clear()
        self._selected_archive_ids.clear()
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
        self._show_snackbar(f"已归零 {count} 项自顶计数，可重新开始", "success")

    async def _on_material_row_select(self, mid, selected):
        # Flet e.data 为字符串 "true"/"false"
        is_selected = selected == "true" if isinstance(selected, str) else bool(selected)

        if is_selected:
            self._selected_material_ids.add(mid)
        else:
            self._selected_material_ids.discard(mid)

        self._update_bulk_visibility()
        await self._refresh_material_table()

    async def _on_archive_row_select(self, mid, selected):
        # Flet e.data 为字符串 "true"/"false"
        is_selected = selected == "true" if isinstance(selected, str) else bool(selected)

        if is_selected:
            self._selected_archive_ids.add(mid)
        else:
            self._selected_archive_ids.discard(mid)

        self._update_bulk_visibility()
        await self._refresh_material_table()

    async def _sync_shortlinks(self, e):
        """手动触发向外部 API 同步并持久化短链资产"""
        e.control.disabled = True
        self.page.update()
        
        self._show_snackbar("正在从公网 API 同步短码...", "info")
        success, msg = await self.connector.sync_shortlinks_to_db()
        
        if success:
            self._show_snackbar(f"⚡ {msg}", "success")
        else:
            self._show_snackbar(f"❌ 同步失败: {msg}", "error")
            
        e.control.disabled = False
        self.page.update()

    async def _on_batch_ai_rewrite_click(self, e):
        """触发选中物料或所有待发物料的批量 AI 改写"""
        if self._selected_material_ids:
            pending_m = [m for m in self._materials if m.id in self._selected_material_ids and m.status == "pending"]
            if not pending_m:
                self._show_snackbar("选中的物料中没有处于 [待发] 状态的项，或者它们已经是成功/失败状态，无法改写", "warning")
                return
        else:
            pending_m = [m for m in self._materials if m.status == "pending"]
            if not pending_m:
                self._show_snackbar("没有发现处于 [待发] 状态的物料，无法改写", "warning")
                return
            
        progress_bar = ft.ProgressBar(value=0, width=400, color="primary")
        status_text = ft.Text(f"正在准备 AI 精调 (0/{len(pending_m)})...", size=12)
        
        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.AUTO_AWESOME, color="primary"), ft.Text("AI 批量文案精调中")], spacing=10),
            content=ft.Column([
                ft.Text("系统将对所有 [待发] 物料进行 SEO 优化和敏感词防御处理。此过程可能消耗 API 额度，请确认。", size=14),
                ft.Divider(),
                status_text,
                progress_bar,
            ], width=400, height=120, tight=True),
            actions=[
                ft.TextButton("取消并停止", on_click=lambda _: self.page.close(dialog)),
            ],
            modal=True
        )

        async def run_rewrite(_):
            optimizer = AIOptimizer(self.db)
            total = len(pending_m)
            success_count = 0
            
            for i, m in enumerate(pending_m):
                # 检查对话框是否还开着（是否被手动关闭取消）
                if not dialog.open: break
                
                status_text.value = f"正在改写第 {i+1}/{total} 条: {m.title[:15]}..."
                self.page.update()
                
                # 调用 AI
                try:
                    success, opt_title, opt_content, err = await optimizer.optimize_post(m.title, m.content)
                    if success:
                        await self.db.update_material_ai(m.id, opt_title, opt_content)
                        success_count += 1
                except Exception as ex:
                    from ...core.logger import log_error
                    await log_error(f"AI Batch error on ID {m.id}: {ex}")
                
                progress_bar.value = (i + 1) / total
                self.page.update()
            
            self.page.close(dialog)
            self._materials = await self.db.get_materials()
            await self._refresh_material_table()
            self._show_snackbar(f"AI 批量改写完成！成功优化 {success_count}/{total} 条文案", "success" if success_count > 0 else "error")

        self.page.open(dialog)
        self.page.run_task(run_rewrite, None)

    async def _on_single_ai_rewrite_click(self, e):
        """单条物料 AI 精调"""
        mid = e.control.data
        m = next((i for i in self._materials if i.id == mid), None)
        if not m: return
        
        e.control.disabled = True
        self.page.update()
        
        optimizer = AIOptimizer(self.db)
        success, opt_title, opt_content, err = await optimizer.optimize_post(m.title, m.content)
        
        if success:
            await self.db.update_material_ai(mid, opt_title, opt_content)
            self._materials = await self.db.get_materials()
            await self._refresh_material_table()
            self._show_snackbar("该条文案 AI 优化已就绪", "success")
        else:
            self._show_snackbar(f"AI 改写失败: {err}", "error")
            e.control.disabled = False
            self.page.update()

    async def _on_preview_ai_click(self, e):
        """预览并对比 AI 改写结果"""
        m = e.control.data
        
        def rollback(_):
            async def _do():
                await self.db.update_material_ai(m.id, m.original_title, m.original_content)
                # 修改状态回 none
                async with self.db.async_session() as session:
                    from ...db.models import MaterialPool
                    db_m = await session.get(MaterialPool, m.id)
                    if db_m:
                        db_m.ai_status = "none"
                        await session.commit()
                self.page.close(preview_dialog)
                self._materials = await self.db.get_materials()
                await self._refresh_material_table()
                self._show_snackbar("已还原至初始文案", "info")
            self.page.run_task(_do)

        preview_dialog = ft.AlertDialog(
            title=ft.Text("AI 文案精调对比"),
            content=ft.Column([
                ft.Text("【初始原文】", size=12, weight=ft.FontWeight.BOLD, color="onSurfaceVariant"),
                ft.Text(f"标题: {m.original_title}", size=11, italic=True),
                ft.Container(content=ft.Text(m.original_content, size=11), padding=10, bgcolor=with_opacity(0.05, "onSurface"), border_radius=5),
                ft.Divider(),
                ft.Text("【AI 魔法精调后】", size=12, weight=ft.FontWeight.BOLD, color="primary"),
                ft.Text(f"标题: {m.title}", size=11),
                ft.Container(content=ft.Text(m.content, size=11), padding=10, bgcolor=with_opacity(0.1, "primary"), border_radius=5),
            ], scroll=ft.ScrollMode.ADAPTIVE, width=500, tight=True),
            actions=[
                ft.TextButton("使用原文回退", icon=icons.UNDO, on_click=rollback),
                ft.FilledButton("保持现状", on_click=lambda _: self.page.close(preview_dialog)),
            ]
        )
        self.page.open(preview_dialog)

    def _obfuscate_link(self, url: str) -> str:
        """针对百度网盘链接进行零宽字符混淆防御"""
        if "pan.baidu.com" in url:
            # 在 domain 中间插入零宽空格 \u200b，有效降低自动化爬虫识别
            return url.replace("pan.baidu.com", "pan.ba\u200bidu.com")
        return url

    async def _open_shortlink_dialog(self, e):
        """显示短链选择对话框 (带搜索与状态筛选)"""
        
        # 1. 获取增强型短链列表
        self._all_links = await self.connector.get_shortlinks_with_status(self.db)
        if not self._all_links:
            self._show_snackbar("本地数据库中未发现短码，请先点击【同步云端短码】拉取最新资产。", "error")
            return

        # 2. 状态变量
        self._filter_status = "all"  # all / posted / unposted
        self._search_keyword = ""
        self._selected_links = set() # 存储 shortCode

        # 3. UI 组件
        self._search_field = ft.TextField(
            label="搜索短码或标题...",
            prefix_icon=icons.SEARCH,
            on_change=self._on_shortlink_search_change,
            text_size=13,
            dense=True,
            expand=True
        )

        def create_filter_button(label, status):
            is_selected = (self._filter_status == status)
            return ft.ElevatedButton(
                text=label,
                data=status,
                on_click=self._on_shortlink_filter_change,
                style=ft.ButtonStyle(
                    color=COLORS.ON_PRIMARY if is_selected else COLORS.ON_SURFACE,
                    bgcolor=COLORS.PRIMARY if is_selected else with_opacity(0.1, "onSurface"),
                    shape=ft.RoundedRectangleBorder(radius=20),
                ),
                height=32,
            )

        self._filter_chips = ft.Row([
            create_filter_button("全部", "all"),
            create_filter_button("未发", "unposted"),
            create_filter_button("已发", "posted"),
        ], spacing=10)

        self._link_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Checkbox(on_change=self._on_shortlink_select_all)),
                ft.DataColumn(ft.Text("短码")),
                ft.DataColumn(ft.Text("标题")),
                ft.DataColumn(ft.Text("状态")),
                ft.DataColumn(ft.Text("次数")),
            ],
            rows=[],
            column_spacing=15,
            data_row_min_height=40,
        )

        self._table_container = ft.Column([
            self._link_table
        ], scroll=ft.ScrollMode.ADAPTIVE, height=300)

        # 4. 底部开关
        overwrite_switch = ft.Switch(
            label="覆盖现有物料池",
            value=False,
            label_position=ft.LabelPosition.RIGHT,
            scale=0.8
        )
        direct_mode_switch = ft.Switch(
            label="注入网盘原链模式 (直连分享)",
            value=False,
            label_position=ft.LabelPosition.RIGHT,
            scale=0.8,
            active_color="orange"
        )

        def on_confirm(_):
            if not self._selected_links:
                self._show_snackbar("请选择至少一个短链资产", "warning")
                return

            pairs = []
            # 从原始列表中找到选中的数据
            selected_data = [link for link in self._all_links if link['shortCode'] in self._selected_links]
            
            is_direct = direct_mode_switch.value
            for link_data in selected_data:
                code = link_data['shortCode']
                seo_title = link_data.get('seoTitle') or ""
                desc = link_data.get('description') or ""
                original_url = link_data.get('originalUrl') or ""

                if is_direct and original_url:
                    effective_title = seo_title if seo_title else f"网盘资源分享 - {code}"
                    # 执行混淆防御
                    final_url = self._obfuscate_link(original_url)
                    new_content = f"{desc}\n\n{final_url}" if desc else final_url
                else:
                    effective_title = seo_title if seo_title else f"主页输入【{code}】立刻查看网盘资源"
                    new_content = f"{desc}\n\n主页搜【{code}】马上查阅" if desc else f"主页搜【{code}】马上查阅"
                
                pairs.append((effective_title, new_content))

            async def _bg_task():
                if overwrite_switch.value:
                    await self.db.clear_materials()
                added_count = await self.db.add_materials_bulk(pairs)
                if added_count == 0:
                    self._show_snackbar("选中的短链均已存在，无需重复注入", "info")
                else:
                    self._show_snackbar(f"✅ 成功注入 {added_count} 条短链物料", "success")
                
                self._materials = await self.db.get_materials()
                await self._refresh_material_table()
                self.page.close(self.link_dialog)

            self.page.run_task(_bg_task)

        # 5. 构建对话框
        self.link_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.LINK_ROUNDED, color="primary"), ft.Text("短链资产库精华选取")]),
            content=ft.Container(
                content=ft.Column([
                    ft.Row([self._search_field]),
                    self._filter_chips,
                    ft.Divider(height=1),
                    self._table_container,
                    ft.Divider(height=1),
                    ft.Row([overwrite_switch, direct_mode_switch], spacing=20),
                ], tight=True, spacing=10),
                width=550,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self.page.close(self.link_dialog)),
                ft.FilledButton("确认并注入子弹袋", icon=icons.BOLT, on_click=on_confirm),
            ],
        )

        # 初始渲染
        await self._render_filtered_links()
        self.page.open(self.link_dialog)
        self.page.update()

    async def _open_forum_dialog(self, e):
        """直接进入火力配置主页面"""
        await self._open_firepower_dialog(set(self._temp_target_fnames))

    async def _open_safety_config_dialog(self, pre_selected: set):
        """[子弹窗] 安全原初打法配置 - 配置完返回火力配置"""
        
        # 1. 获取包含安全状态 (is_post_target) 的贴吧详情字典列表
        forums = await self.db.get_all_unique_forums()
        
        # 2. 搜索与全选控制
        search_field = ft.TextField(
            label="搜索贴吧名...",
            prefix_icon=icons.SEARCH,
            dense=True,
            text_size=12,
            expand=True
        )
        
        select_all_cb = ft.Checkbox(label="全选", value=False)
        
        # 3. 贴吧容器
        forums_list_container = ft.Column(spacing=5, scroll=ft.ScrollMode.ADAPTIVE, height=300)
        
        async def on_item_check(e):
            fid = e.control.data
            is_checked = e.control.value
            await self.db.toggle_forum_post_target(fid, is_checked)
            # 同步更新内存状态，确保搜索重渲染时状态不丢失
            for f in forums:
                if f['fid'] == fid:
                    f['is_post_target'] = is_checked
                    break

        def render_forums(keyword=""):
            forums_list_container.controls.clear()
            for f in forums:
                if keyword and keyword.lower() not in f['fname'].lower(): continue
                forums_list_container.controls.append(
                    ft.Checkbox(
                        label=f['fname'], 
                        value=f['is_post_target'], 
                        data=f['fid'], 
                        on_change=lambda ev: self.page.run_task(on_item_check, ev),
                        fill_color="green",
                        label_style=ft.TextStyle(color="onSurface")
                    )
                )
            try:
                forums_list_container.update()
            except:
                pass

        search_field.on_change = lambda e: render_forums(e.control.value)
        
        def on_select_all_change(e):
            """安全配置弹窗的全选处理"""
            select_all = e.control.value
            for cb in forums_list_container.controls:
                if isinstance(cb, ft.Checkbox):
                    cb.value = select_all
                    self.page.run_task(self.db.toggle_forum_post_target, cb.data, select_all)
                    # 同步更新内存状态
                    for f in forums:
                        if f['fid'] == cb.data:
                            f['is_post_target'] = select_all
                            break
            try:
                forums_list_container.update()
            except:
                pass
            self._show_snackbar(f"已批量{'开启' if select_all else '关闭'}安全权限", "info")

        select_all_cb.on_change = on_select_all_change

        async def on_lock_safety(_):
            """锁定安全配置，返回火力配置阶段"""
            self.page.close(safety_dialog)
            # 配置已更新，将原来的火力准备 (pre_selected) 传回，不干涉火力配置
            await self._open_firepower_dialog(pre_selected)

        safety_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.SHIELD_ROUNDED, color="green"), ft.Text("安全原初打法配置")]),
            content=ft.Container(
                content=ft.Column([
                    ft.Row([
                        search_field, 
                        select_all_cb,
                    ], spacing=10),
                    ft.Text("开启专属保护开关后，会强制优先调用原生关注小号出战:", size=11, color="onSurfaceVariant"),
                    ft.Container(
                        content=forums_list_container,
                        padding=10,
                        border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                        border_radius=8,
                    ),
                ], tight=True, spacing=15),
                width=450,
            ),
            actions=[
                ft.TextButton("取消", on_click=on_lock_safety),
                ft.FilledButton(
                    "防抽网络编织完毕锁定", 
                    icon=icons.LOCK_ROUNDED, 
                    style=ft.ButtonStyle(bgcolor="green", color="white"),
                    on_click=on_lock_safety
                ),
            ],
        )
        
        self.page.open(safety_dialog)
        render_forums() # 先开弹窗，后执行包含 update() 的渲染
    async def _open_firepower_dialog(self, pre_selected_fnames: set):
        """[火力配置主页面] 配置火力抛射靶场"""
        
        final_selected = pre_selected_fnames.copy()
        # [优化] 获取所有不重复的贴吧及其权限状态
        local_forums = await self.db.get_all_unique_forums()
        # [新增] 获取靶位组列表
        target_groups = await self.db.get_target_pool_groups()
        
        # 1. 搜索过滤与全选 (用于本地自留区)
        search_field = ft.TextField(
            label="过滤本地吧...",
            prefix_icon=icons.SEARCH,
            dense=True,
            text_size=12,
            expand=True
        )
        
        select_all_cb = ft.Checkbox(label="全选", value=False, scale=0.8)
        
        # 2. 容器预处理
        forums_container = ft.Column(spacing=2, scroll=ft.ScrollMode.ADAPTIVE, height=300)
        groups_container = ft.Column(spacing=2, scroll=ft.ScrollMode.ADAPTIVE, height=150)
        
        # 已选数量提示
        selected_count_text = ft.Text(f"已选: {len(final_selected)} 个目标", size=12, color="primary", weight=ft.FontWeight.BOLD)
        
        def update_selected_count():
            selected_count_text.value = f"已选: {len(final_selected)} 个目标"
            try:
                selected_count_text.update()
            except:
                pass
        
        def on_item_check(e):
            fn = e.control.data
            if e.control.value: final_selected.add(fn)
            else: final_selected.discard(fn)
            update_selected_count()

        async def on_group_check(e):
            group_name = e.control.data
            is_checked = e.control.value
            # 直接拉取数据库中的分组吧名
            fnames = await self.db.get_target_pools_by_group(group_name)
            for fn in fnames:
                if is_checked: final_selected.add(fn)
                else: final_selected.discard(fn)
            # 刷新本地列表的选中状态（如果本地也有重合的吧）
            render_local_list(search_field.value)
            update_selected_count()
            self._show_snackbar(f"{'已添加' if is_checked else '已从待选区移除'} 分组 [{group_name}] 中的 {len(fnames)} 个吧点", "info")

        def render_local_list(keyword=""):
            forums_container.controls.clear()
            
            if not local_forums:
                forums_container.controls.append(
                    ft.Container(
                        content=ft.Text(
                            "系统内尚无贴吧数据\n请先前往 Dashboard 运行签到或同步关注贴吧", 
                            color="onSurfaceVariant", 
                            text_align="center", 
                            size=12
                        ),
                        alignment=ft.alignment.center,
                        expand=True,
                        padding=ft.padding.only(top=40)
                    )
                )
            else:
                for f in local_forums:
                    fn = f['fname']
                    is_safe = f['is_post_target']
                    if keyword and keyword.lower() not in fn.lower(): continue
                    
                    # 安全目标增加标识与颜色区分
                    label_text = f"🛡️ {fn} [安全]" if is_safe else fn
                    item_color = "green" if is_safe else "onSurface"
                    
                    forums_container.controls.append(
                        ft.Checkbox(
                            label=label_text, 
                            value=(fn in final_selected), 
                            data=fn, 
                            on_change=on_item_check,
                            fill_color="green" if is_safe else None,
                            label_style=ft.TextStyle(color=item_color, size=11, weight=ft.FontWeight.W_500 if is_safe else None)
                        )
                    )
            try:
                forums_container.update()
            except:
                pass

        def render_groups():
            groups_container.controls.clear()
            if not target_groups:
                groups_container.controls.append(ft.Text("尚无预设靶场分组", size=11, color="onSurfaceVariant", italic=True))
            else:
                for g in target_groups:
                    groups_container.controls.append(
                        ft.Checkbox(
                            label=f"📁 分组: {g}",
                            data=g,
                            on_change=lambda ev: self.page.run_task(on_group_check, ev),
                            label_style=ft.TextStyle(size=11),
                            fill_color="primary"
                        )
                    )
            try:
                groups_container.update()
            except:
                pass

        search_field.on_change = lambda e: render_local_list(e.control.value)
        
        def on_select_all_change(e):
            """全选/取消全选当前显示列表中的所有贴吧"""
            select_all = e.control.value
            for cb in forums_container.controls:
                if isinstance(cb, ft.Checkbox):
                    cb.value = select_all
                    fn = cb.data
                    if select_all:
                        final_selected.add(fn)
                    else:
                        final_selected.discard(fn)
            update_selected_count()
            try:
                forums_container.update()
            except:
                pass
        
        select_all_cb.on_change = on_select_all_change
        
        async def on_bulk_unfollow_click(_):
            selected_to_purge = [fn for fn in list(final_selected)]
            if not selected_to_purge:
                self._show_snackbar("请先勾选需要清理的阵地", "warning")
                return

            async def do_purge(e):
                self.page.close(confirm_dialog)
                # 执行清理
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                self._show_snackbar(f"开始对 {len(selected_to_purge)} 个吧执行全局清理，请稍后...", "info")
                
                await pm.unfollow_forums_bulk(selected_to_purge)
                
                # 刷新状态
                final_selected.clear()
                # 重载全量数据并刷新 UI
                nonlocal local_forums
                local_forums = await self.db.get_all_unique_forums()
                render_local_list()
                self._show_snackbar(f"✅ 阵地清理完成，已从数据库抹除并尝试让所有账号取关", "success")

            confirm_dialog = ft.AlertDialog(
                title=ft.Row([ft.Icon(icons.WARNING, color="orange"), ft.Text("确认全局清理并取关")]),
                content=ft.Text(f"将对已选的 {len(selected_to_purge)} 个贴吧执行【全局取关】并彻底删除本地记录。\n此操作不可逆，且会触发矩阵网络请求。是否继续？"),
                actions=[
                    ft.TextButton("取消", on_click=lambda _: self.page.close(confirm_dialog)),
                    ft.ElevatedButton("确认清除", bgcolor="error", color="white", on_click=lambda e: self.page.run_task(do_purge, e))
                ]
            )
            self.page.open(confirm_dialog)

        # 3. 手动输入框 (用于全域轰炸组)
        manual_input = ft.TextField(
            label="手动补充吧名 (英文逗号分隔)", 
            hint_text="贴吧1, 贴吧2...",
            multiline=True, 
            min_lines=5, 
            text_size=12,
        )
        
        async def on_confirm(_):
            # 合并手动输入的内容
            if manual_input.value:
                manual_fnames = [f.strip() for f in manual_input.value.split(",") if f.strip()]
                for fn in manual_fnames: final_selected.add(fn)
            
            count = len(final_selected)
            self._temp_target_fnames = list(final_selected)
            
            # [持久化] 保存到数据库
            if self.db:
                self.page.run_task(self.db.set_setting, "last_selected_target_forums", json.dumps(self._temp_target_fnames))
            
            # 同步更新主界面按钮状态
            if count > 0:
                self.forum_select_btn.text = f"🎯 火力已锁定: {count} 个目标点"
                self.forum_select_btn.style = ft.ButtonStyle(color="white", bgcolor="primary")
            else:
                self.forum_select_btn.text = "点击选择目标贴吧"
                self.forum_select_btn.style = ft.ButtonStyle(color="onSurfaceVariant", bgcolor=None)

            self.page.close(fire_dialog)
            self._show_snackbar(f"🎯 已锁定 {len(final_selected)} 个发射坐标点，准备完毕", "success")
            self.page.update()

        # 标题栏
        dialog_title = ft.Row([
            ft.Icon(icons.SETTINGS_SUGGEST, color="blue"), 
            ft.Text("配置火力抛射靶场"),
        ], alignment=ft.MainAxisAlignment.START)

        # 4. 构造页签
        tabs = ft.Tabs(
            selected_index=0,
            tabs=[
                ft.Tab(
                    text="本地自留区", 
                    icon=icons.GPS_FIXED, 
                    content=ft.Container(
                        content=ft.Column([
                            ft.Row([
                                search_field, 
                                select_all_cb, 
                                ft.IconButton(icons.DELETE_SWEEP, icon_color="error", tooltip="删除选中项并同步取消关注", on_click=on_bulk_unfollow_click),
                                ft.IconButton(
                                    icons.SETTINGS, 
                                    icon_color="green", 
                                    tooltip="安全原初打法配置",
                                    on_click=lambda _: self.page.run_task(self._open_safety_config_dialog, final_selected)
                                )
                            ], spacing=5),
                            ft.Container(
                                content=forums_container,
                                border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                                border_radius=8,
                                padding=5
                            )
                        ], tight=True),
                        padding=10
                    )
                ),
                ft.Tab(
                    text="全域轰炸组", 
                    icon=icons.LOCAL_FIRE_DEPARTMENT_ROUNDED, 
                    content=ft.Container(
                        content=ft.Column([
                            ft.Text("在这里输入从未关注但在轰炸计划内的外部目标吧:", size=11, color="onSurfaceVariant"),
                            manual_input,
                            ft.Divider(height=10, color="transparent"),
                            ft.Text("或者从已录入的靶位组中选取 (Target Pool Groups):", size=11, color="onSurfaceVariant"),
                            ft.Container(
                                content=groups_container,
                                border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                                border_radius=8,
                                padding=5
                            )
                        ], tight=True),
                        padding=10
                    )
                ),
            ],
        )

        fire_dialog = ft.AlertDialog(
            title=dialog_title,
            content=ft.Container(
                content=tabs,
                width=500,
                height=450,
            ),
            actions=[
                ft.TextButton("关闭", on_click=lambda _: self.page.close(fire_dialog)),
                selected_count_text,
                ft.FilledButton(
                    "锁定发射坐标", 
                    icon=icons.CHECK, 
                    style=ft.ButtonStyle(bgcolor="primary", color="white"),
                    on_click=on_confirm
                ),
            ],
        )
        
        # [关键修复] 先开弹窗，后执行渲染逻辑
        self.page.open(fire_dialog)
        render_local_list()
        render_groups()
    async def _on_shortlink_search_change(self, e):
        self._search_keyword = e.control.value.lower()
        await self._render_filtered_links()

    async def _on_shortlink_filter_change(self, e):
        status = e.control.data
        self._filter_status = status
        
        # 更新按钮样式以模拟单选卡片
        for btn in self._filter_chips.controls:
            is_sel = (btn.data == status)
            btn.style.color = COLORS.ON_PRIMARY if is_sel else COLORS.ON_SURFACE
            btn.style.bgcolor = COLORS.PRIMARY if is_sel else with_opacity(0.1, "onSurface")
            
        await self._render_filtered_links()

    def _on_shortlink_select_all(self, e):
        # 获取当前显示的行
        value = e.control.value
        for row in self._link_table.rows:
            cb = row.cells[0].content
            cb.value = value
            code = cb.data
            if value:
                self._selected_links.add(code)
            else:
                self._selected_links.discard(code)
        self.page.update()

    def _on_shortlink_item_check(self, e):
        code = e.control.data
        if e.control.value:
            self._selected_links.add(code)
        else:
            self._selected_links.discard(code)

    async def _render_filtered_links(self):
        """核心渲染逻辑：根据筛选器刷新表格"""
        rows = []
        for link in self._all_links:
            # 搜索过滤
            if self._search_keyword:
                seo_title = link.get('seoTitle') or ""
                if (self._search_keyword not in link.get('shortCode', '').lower() and 
                    self._search_keyword not in seo_title.lower()):
                    continue
            
            # 状态过滤
            if self._filter_status == "posted" and link['post_count'] == 0:
                continue
            if self._filter_status == "unposted" and link['post_count'] > 0:
                continue
            
            # 构建行
            status_icon = "✅" if link['post_count'] > 0 else "⏳"
            rows.append(ft.DataRow(
                cells=[
                    ft.DataCell(ft.Checkbox(
                        value=(link['shortCode'] in self._selected_links),
                        data=link['shortCode'],
                        on_change=self._on_shortlink_item_check
                    )),
                    ft.DataCell(ft.Text(link['shortCode'], weight=ft.FontWeight.BOLD, size=12)),
                    ft.DataCell(ft.Text((link.get('seoTitle') or '无标题')[:25], size=12)),
                    ft.DataCell(ft.Text(f"{status_icon} {link['status']}", size=12)),
                    ft.DataCell(ft.Text(str(link['post_count']), size=12)),
                ]
            ))
        
        self._link_table.rows = rows
        try:
            self.page.update()
        except:
            pass

    async def _on_archive_surv_filter_click(self, mode):
        """存活状态筛选切换 (自定义 UI 回调)"""
        self._archive_surv_filter = mode
        # 重新加载 Tab 的内容布局（因为自定义按钮需要刷新颜色）
        self.bottom_tabs.tabs[2].content = self._build_archive_view()
        await self.load_data()

    def _build_material_view(self):
        """独立构建物料池 Tab 内容 - 增加搜索与批量控制"""
        material_search = ft.TextField(
            hint_text="搜索标题或内容...",
            prefix_icon=icons.SEARCH,
            on_change=self._on_material_search_change,
            height=40, text_size=12, content_padding=10,
            width=250 # 给搜索框固定宽度，防止在 Row 中挤压操作栏
        )
        
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icons.FORMAT_ALIGN_LEFT_OUTLINED, size=16),
                    ft.Text("全域物料弹药库 (Pending Rows)", size=12, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    self._stats_text or ft.Text(""),
                    ft.IconButton(icons.REFRESH, icon_size=16, on_click=lambda _: self.page.run_task(self.load_data), tooltip="刷新物料库"),
                ], spacing=10),
                ft.Row([self._quick_title, self._quick_content, self._add_btn], spacing=10),
                ft.Row([
                    material_search,
                    self._material_bulk_actions,
                ], spacing=10),
                ft.Container(
                    content=ft.ListView([self._material_table], expand=True),
                    expand=True,
                    border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                    border_radius=12,
                    padding=5,
                ),
            ], expand=True, spacing=10),
            expand=True,
            padding=ft.padding.only(top=10)
        )

    def _build_archive_view(self):
        """独立构建已发记录归档库 Tab 内容 - 增加搜索与批量重置"""
        archive_search = ft.TextField(
            hint_text="搜索标题或着陆贴吧...",
            prefix_icon=icons.SEARCH,
            on_change=self._on_archive_search_change,
            height=40, text_size=12, content_padding=10,
            width=250 # 固定宽度
        )

        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icons.ARCHIVE_OUTLINED, size=16),
                    ft.Text("发帖档案室 (Historical Archive Rows)", size=12, weight=ft.FontWeight.BOLD),
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
                        ft.Container(
                            content=ft.Row([
                                ft.Text("全部", size=11, color="white" if self._archive_surv_filter == "all" else "onSurfaceVariant"),
                                self._archive_all_count_text
                            ], spacing=2),
                            padding=ft.padding.symmetric(6, 12),
                            bgcolor="primary" if self._archive_surv_filter == "all" else with_opacity(0.1, "onSurface"),
                            border_radius=8,
                            on_click=lambda _: self.page.run_task(self._on_archive_surv_filter_click, "all"),
                            animate=200
                        ),
                        ft.Container(
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
                        ),
                        ft.Container(
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
                        ),
                    ], spacing=5),
                    self._archive_bulk_actions,
                ], spacing=10),
                ft.Container(
                    content=ft.ListView([self._archive_table], expand=True),
                    expand=True,
                    border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                    border_radius=12,
                    padding=5,
                ),
            ], expand=True, spacing=10),
            expand=True,
            padding=ft.padding.only(top=10)
        )

    def _init_controls(self):
        """预初始化页面所有持久化控件，防止 build 时被重置"""
        # 1. 贴吧选择
        self.forum_pool_column = ft.Column(spacing=2, height=120, scroll=ft.ScrollMode.ADAPTIVE)
        self.forum_select_btn = ft.OutlinedButton(
            "点击选择目标贴吧",
            icon=icons.TOUCH_APP_ROUNDED,
            on_click=self._open_forum_dialog,
            style=ft.ButtonStyle(color="onSurfaceVariant"),
        )
        self.manual_forum_input = ft.TextField(
            label="手动补充吧名 (英文逗号分隔)", 
            hint_text="例如: c语言,python", 
            text_size=11,
            border_color=with_opacity(0.2, "onSurface")
        )
        self._stats_text = ft.Text("状态分布:  ⏳待发(0)   ✅成功(0)   ❌失败(0)", size=12, weight=ft.FontWeight.W_500, color="onSurfaceVariant")
        
        # 归档统计文本
        self._archive_all_count_text = ft.Text(" (0)", size=10, weight=ft.FontWeight.BOLD)
        self._archive_alive_count_text = ft.Text(" (0)", size=10, weight=ft.FontWeight.BOLD)
        self._archive_dead_count_text = ft.Text(" (0)", size=10, weight=ft.FontWeight.BOLD)
        
        # 批量操作 UI 容器
        self._material_selected_count_text = ft.Text(f"已选 0 项", size=11, color="onSurfaceVariant")
        self._material_bulk_actions = ft.Row([
            ft.FilledButton("批量删除", icon=icons.DELETE_SWEEP,
                            style=ft.ButtonStyle(bgcolor="error", color="white"), 
                            on_click=self._bulk_delete_materials),
            ft.FilledButton("批量重置", icon=icons.REPLAY_ROUNDED,
                            style=ft.ButtonStyle(bgcolor="orange", color="white"), 
                            on_click=self._bulk_reset_materials),
            ft.FilledButton("批量自顶", icon=icons.BOLT,
                            style=ft.ButtonStyle(bgcolor="primary", color="white"), 
                            on_click=self._bulk_toggle_auto_bump),
            ft.FilledButton("AI 批量改写", icon=icons.AUTO_AWESOME,
                            style=ft.ButtonStyle(bgcolor="teal", color="white"), 
                            on_click=self._on_batch_ai_rewrite_click),
            self._material_selected_count_text,
        ], visible=False, spacing=10)

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
        
        # 2. 物料录入与表格
        self._quick_title = ft.TextField(label="快速配置标签(可选)", expand=1, text_size=12, dense=True)
        self._quick_content = ft.TextField(
            label="正文主段落 (将混合零宽防御)*", 
            expand=2, 
            text_size=12, 
            dense=True,
            multiline=True,
            min_lines=1,
            max_lines=5
        )
        self._add_btn = ft.IconButton(icon=icons.ADD_BOX, icon_color="primary", on_click=self._add_material_row, tooltip="写好就塞进去")
        
        self._material_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("ID", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("基准标题", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("文案引擎池", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("发布状态", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("AI附魔", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("生命控制", size=11, weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("自顶", size=11, weight=ft.FontWeight.BOLD)),
            ],
            rows=[],
            heading_row_height=40, data_row_min_height=45, data_row_max_height=60, 
            column_spacing=18,
            show_checkbox_column=True,
            on_select_all=self._on_material_select_all,
        )

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

        # 3. 参数配置
        self.post_count = ft.TextField(label="发布总数 (帖)", value="10", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True, tooltip="建议: 总数 ≤ 账号数×3，避免单账号集中发帖")
        self.min_delay = ft.TextField(label="最小延迟 (秒)", value="120", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True, expand=True, tooltip="建议: ≥120秒，避开凌晨1-6点高风险时段")
        self.max_delay = ft.TextField(label="最大延迟 (秒)", value="600", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True, expand=True, tooltip="建议: ≥300秒，降低被检测风险")
        self.use_ai_switch = ft.Switch(
            label="AI改写",
            value=False,
            on_change=lambda e: self.page.run_task(self._auto_save_switch, "use_ai_rewrite", e.control.value)
        )
        self.use_schedule = ft.Switch(
            label="定时计划",
            value=False,
            on_change=lambda e: (self._toggle_schedule(e), self.page.run_task(self._auto_save_switch, "use_schedule", e.control.value))[1]
        )
        self.schedule_time = ft.TextField(
            label="计划时间 (YYYY-MM-DD HH:mm)", 
            value=(datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
            visible=False, text_size=12
        )
        self.interval_hours = ft.TextField(label="循环周期 (小时)", value="0", visible=False, text_size=12, input_filter=ft.NumbersOnlyInputFilter(), tooltip="建议: ≥6小时，避免频繁触发导致封号")
        
        # 4. 账号与策略
        self.strategy_dropdown = ft.Dropdown(
            label="账号调度策略", value="round_robin", expand=1, text_size=12,
            options=[
                ft.dropdown.Option("round_robin", "轮询 (Round-Robin)"), 
                ft.dropdown.Option("strict_round_robin", "严格轮询 (Strict RR)"),
                ft.dropdown.Option("random", "随机 (Random)")
            ]
        )
        self.pairing_mode_dropdown = ft.Dropdown(
            label="文案提取模式", value="random", expand=1, text_size=12,
            options=[ft.dropdown.Option("random", "随机混用 (防抽混淆)"), ft.dropdown.Option("strict", "严格配对 (发多资源)")]
        )
        self._strategy_row = ft.Row([self.strategy_dropdown, self.pairing_mode_dropdown], spacing=10)
        
        # 4.1 自顶配置控件
        self.bump_max_count_field = ft.TextField(
            label="最大次数 (5-100)",
            value="20",
            expand=True,
            input_filter=ft.NumbersOnlyInputFilter(),
            text_size=12,
            keyboard_type=ft.KeyboardType.NUMBER,
            dense=True,
        )
        self.bump_cooldown_field = ft.TextField(
            label="冷却 (分钟, 10-1440)",
            value="45",
            expand=True,
            input_filter=ft.NumbersOnlyInputFilter(),
            text_size=12,
            keyboard_type=ft.KeyboardType.NUMBER,
            dense=True,
        )
        self.bump_matrix_switch = ft.Switch(
            label="矩阵协同模式",
            value=False,
        )
        
        # 自顶模式选择
        self.bump_mode_group = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="once", label="次数模式"),
                ft.Radio(value="scheduled", label="定时模式"),
                ft.Radio(value="matrix_loop", label="轮换模式"),
            ], spacing=8),
            value="once",
            on_change=self._on_bump_mode_change,
        )
        
        # 矩阵轮换配置区域 (默认隐藏)
        self.bump_hour_field = ft.TextField(
            label="每日自顶时间",
            value="10",
            expand=True,
            input_filter=ft.NumbersOnlyInputFilter(),
            text_size=12,
            keyboard_type=ft.KeyboardType.NUMBER,
            dense=True,
            hint_text="0-23点",
        )
        self.bump_duration_field = ft.TextField(
            label="持续天数",
            value="7",
            expand=True,
            input_filter=ft.NumbersOnlyInputFilter(),
            text_size=12,
            keyboard_type=ft.KeyboardType.NUMBER,
            dense=True,
            hint_text="0=永久",
        )
        self.bump_permanent_switch = ft.Switch(
            label="永久循环 (不设上限)",
            value=False,
            on_change=self._on_bump_permanent_change,
        )
        self.bump_loop_container = ft.Container(
            content=ft.Column([
                ft.Text("矩阵轮换配置", size=11, weight=ft.FontWeight.W_500, color="primary"),
                ft.Row([
                    self.bump_hour_field,
                    self.bump_duration_field,
                ], spacing=10),
                self.bump_permanent_switch,
                ft.Container(
                    content=ft.Text("将在归档库中为每个帖子单独配置轮换账号", size=10, color="onSurfaceVariant"),
                    padding=5,
                ),
            ], spacing=8),
            padding=10,
            bgcolor=with_opacity(0.08, "surfaceContainerHighest"),
            border_radius=8,
            visible=False,  # 默认隐藏
        )
        
        self.bump_config_save_btn = ft.FilledButton(
            "保存配置",
            icon=icons.SAVE,
            on_click=self._save_bump_config,
            style=ft.ButtonStyle(bgcolor="primary", color="white"),
        )
        # 账号池 UI 增强
        self.account_search_field = ft.TextField(
            hint_text="搜索账号、ID...",
            prefix_icon=icons.SEARCH,
            on_change=self._on_account_search_change,
            height=35, text_size=11, content_padding=5,
            expand=True,
        )
        self.account_all_toggle = ft.Switch(
            label="全选本组", 
            value=False, 
            on_change=self._on_account_select_all_toggle,
            scale=0.8
        )
        self.account_pool_title = ft.Text("参与账号池 (勾选启用)", size=12, color="onSurfaceVariant", weight=ft.FontWeight.W_500)
        self.account_pool_column = ft.Column(spacing=5, expand=True, scroll=ft.ScrollMode.ADAPTIVE)
        self.start_btn = ft.ElevatedButton(
            "制定矩阵任务", 
            icon=icons.PLAY_CIRCLE_FILL_ROUNDED,
            on_click=self._on_start_click,
            style=ft.ButtonStyle(color="white", bgcolor="primary")
        )
        
        # 5. 状态与进度
        self.progress_bar = ft.ProgressBar(value=0, visible=False, color="primary")
        self.log_list = ft.ListView(expand=True, spacing=5, padding=10)
        
        # 6. 整合 Tabs
        self.bottom_tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            tabs=[
                ft.Tab(text="全域任务队列", icon=icons.UPDATE_ROUNDED, content=self._build_task_queue_view()),
                ft.Tab(text="物料排期池", icon=icons.LIST_ALT_ROUNDED, content=self._build_material_view()),
                ft.Tab(text="已发归档库", icon=icons.ARCHIVE_ROUNDED, content=self._build_archive_view()),
                ft.Tab(text="实时任务流水", icon=icons.STREAM_ROUNDED, content=self._build_log_view()),
            ],
            expand=True,
        )

        header = ft.Row([
            ft.IconButton(icons.ARROW_BACK_IOS_NEW, on_click=lambda e: self._navigate("dashboard")),
            ft.Column([
                ft.Text("矩阵发帖终端 / MATRIX POST TERMINAL", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                ft.Text("多账号轮换、多内容池混淆及多贴吧矩阵发布引擎", size=11, color="onSurfaceVariant"),
            ], spacing=0),
        ])

        # --- 封装最终布局界面并预存 ---
        self.main_layout = ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                ft.Row([
                    # 第一栏：矩阵策略中心 + 控制参数 (Left, expand=2)
                    ft.Column([
                        ft.Text("矩阵策略中心", size=14, weight=ft.FontWeight.W_500),
                        ft.Container(
                            content=                        ft.Column([
                            self._strategy_row,
                            ft.Divider(height=5, color="transparent"),
                            ft.Text("自顶增强配置", size=12, weight=ft.FontWeight.W_500, color="onSurfaceVariant"),
                                ft.Row([self.bump_max_count_field, self.bump_cooldown_field], spacing=10),
                                self.bump_matrix_switch,
                                ft.Divider(height=5, color="transparent"),
                                ft.Text("自顶模式选择", size=12, weight=ft.FontWeight.W_500, color="onSurfaceVariant"),
                                self.bump_mode_group,
                                self.bump_loop_container,  # 矩阵轮换配置区
                                self.bump_config_save_btn,
                            ], spacing=10),
                            padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                        ),
                        ft.Text("定时与控制", size=14, weight=ft.FontWeight.W_500),
                        ft.Container(
                            content=ft.Column([
                                self.post_count,
                                ft.Row([self.use_ai_switch, self.use_schedule], spacing=10),
                                self.schedule_time,
                                ft.Row([self.min_delay, self.max_delay], spacing=10),
                                # 时段风险提示卡片
                                ft.Container(
                                    content=ft.Row([
                                        ft.Icon(name=icons.WARNING_AMBER_ROUNDED, color="orange", size=16),
                                        ft.Text("风控提示: 凌晨1-6点为高风险时段，建议延迟设置≥180秒", 
                                               size=10, color="onSurfaceVariant"),
                                    ], spacing=5),
                                    padding=8,
                                    bgcolor=with_opacity(0.08, "orange"),
                                    border_radius=8,
                                ),
                                ft.Divider(height=5, color="transparent"),
                                self.start_btn,
                            ], spacing=10),
                            padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                        ),
                    ], expand=2, spacing=12, scroll=ft.ScrollMode.ADAPTIVE),

                    # 第二栏：核心操作区 (Center, expand=5，最宽)
                    ft.Column([
                        ft.Row([
                            ft.Text("全局指令集", size=14, weight=ft.FontWeight.W_500),
                            ft.Row([
                                ft.IconButton(icons.ADD_LINK, tooltip="从短链库选取注入物料池", on_click=self._open_shortlink_dialog, icon_color="primary"),
                                ft.IconButton(icons.SYNC_ROUNDED, tooltip="同步云端短码到本地库", on_click=self._sync_shortlinks, icon_color="onSurfaceVariant"),
                                ft.IconButton(icons.UPLOAD_FILE, tooltip="本地载入文件", on_click=lambda _: self._file_picker.pick_files(allow_multiple=False), icon_color="onSurfaceVariant", visible=not getattr(self.page, "web", False)),
                                ft.IconButton(icons.CONTENT_PASTE, tooltip="批量粘贴导入", on_click=self._open_batch_paste_dialog, icon_color="secondary"),
                                ft.IconButton(icons.DELETE_SWEEP, tooltip="摧毁总计划（清空物料池）", on_click=self._clear_all_materials, icon_color="error"),
                            ], spacing=0),
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        
                        # 目标贴吧选择区
                        ft.Container(
                            content=ft.Column([
                                ft.Text("目标贴吧 / TARGET FORUMS (必填)", size=12, color="onSurfaceVariant", weight=ft.FontWeight.BOLD),
                                self.forum_select_btn,
                                self.manual_forum_input,
                            ], spacing=8),
                            padding=10, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                        ),
                        # 底部标签页
                        self.bottom_tabs,
                    ], expand=6, spacing=15),

                    # 第三栏：账号池管理 (Right, expand=3)
                    ft.Column([
                        ft.Row([
                            self.account_pool_title,
                            ft.IconButton(icons.REFRESH_ROUNDED, icon_size=16, on_click=lambda _: self.page.run_task(self.load_data), tooltip="刷新账号状态"),
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ft.Container(
                            content=ft.Column([
                                ft.Row([self.account_search_field, self.account_all_toggle], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=10),
                                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                                ft.Container(content=self.account_pool_column, expand=True),
                            ], spacing=10),
                            expand=True,
                            padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                        ),
                    ], expand=2, spacing=12),
                ], expand=True, vertical_alignment=ft.CrossAxisAlignment.START),
            ], expand=True, spacing=20),
            padding=ft.padding.only(left=20, right=20, top=10, bottom=20), expand=True,
        )

    def build(self) -> ft.Control:
        if self._file_picker not in self.page.overlay:
            self.page.overlay.append(self._file_picker)
        return self.main_layout

    def _build_log_view(self):
        # expand=True 使日志视图能填满 Tab 分配的高度
        return ft.Container(
            content=ft.Column([
                self.progress_bar,
                ft.Container(
                    content=self.log_list, expand=True,
                    border=ft.border.all(1, with_opacity(0.1, "onSurface")), border_radius=10,
                )
            ], expand=True), expand=True, padding=10
        )

    def _build_task_queue_view(self):
        self.task_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("序号")),
                ft.DataColumn(ft.Text("贴吧")),
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
                content=ft.ListView([self.task_table], expand=True), expand=True,
                border=ft.border.all(1, with_opacity(0.05, "onSurface")), border_radius=8,
            )
        ], spacing=10, expand=True)

    def _build_task_row(self, t, index):
        import json
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

        return ft.DataRow(cells=[
            ft.DataCell(ft.Text(str(index + 1))),
            ft.DataCell(ft.Text(fnames_disp_short, size=11, tooltip=tooltip_text)),
            ft.DataCell(ft.Icon(icons.AUTO_AWESOME, color="primary", size=16) if t.use_ai else ft.Text("-")),
            ft.DataCell(ft.Text(getattr(t, "strategy", "N/A"))),
            ft.DataCell(ft.Text(t.schedule_time.strftime("%m-%d %H:%M") if t.schedule_time else "即时")),
            ft.DataCell(ft.Text(t.status.upper(), color=status_color, weight=ft.FontWeight.BOLD)),
            ft.DataCell(ft.Text(f"{t.progress}/{t.total}")),
            ft.DataCell(
                ft.Row([
                    ft.IconButton(
                        icons.DELETE_OUTLINE, 
                        icon_color="error", 
                        icon_size=18,
                        tooltip="删除任务",
                        on_click=lambda _: self.page.run_task(self._on_delete_task, t.id)
                    ),
                ], spacing=0)
            ),
        ])

    async def _on_delete_task(self, task_id: int):
        if await self.db.delete_batch_task(task_id):
            self._show_snackbar(f"任务 ID:{task_id} 已删除", "success")
            await self.load_data()
        else:
            self._show_snackbar("删除失败", "error")

    async def _auto_save_switch(self, key: str, value: bool):
        """自动保存开关状态"""
        await self.db.set_setting(key, "1" if value else "0")

    def _toggle_schedule(self, e):
        self.schedule_time.visible = e.control.value
        self.interval_hours.visible = e.control.value
        self.page.update()

    async def _on_file_result(self, e: ft.FilePickerResultEvent):
        if not e.files: return

        file_path = e.files[0].path

        # 兼容性处理：Web 模式下 path 为 None
        if file_path is None:
            try:
                # 开启 Web 上传流程
                self.progress_bar.visible = True
                self.progress_bar.value = 0
                self.page.update()

                self._show_snackbar("正在开启 Web 传输通道，请稍候...", "info")
                upload_files = []
                for f in e.files:
                    # 获取上传 URL (过期时间 60s)
                    u_url = self.page.get_upload_url(f.name, 60)
                    if u_url:
                        upload_files.append(ft.FilePickerUploadFile(f.name, upload_url=u_url))
                    else:
                        # 无法获取上传 URL，可能是 SECRET_KEY 问题
                        self._show_snackbar("无法获取上传 URL，请检查 FLET_SECRET_KEY 配置", "error")
                        self.progress_bar.visible = False
                        self.page.update()
                        return

                if upload_files:
                    self._file_picker.upload(upload_files)
                else:
                    self._show_snackbar("未能创建上传任务，请重试", "warning")
                    self.progress_bar.visible = False
                    self.page.update()
                return
            except Exception as ex:
                self._show_snackbar(f"文件上传初始化失败: {str(ex)}", "error")
                self.progress_bar.visible = False
                self.page.update()
                return

        # 桌面模式：直接处理
        await self._process_file_import(file_path)

    async def _on_upload_progress(self, e: ft.FilePickerUploadEvent):
        """处理 Web 端文件上传进度与后续导入"""
        # 更新上传进度条
        self.progress_bar.value = e.progress
        self.page.update()

        if e.progress == 1.0:
            # 上传完成，文件现在位于服务器的 uploads/ 目录下
            import os
            # 获取 Flet 配置的上传目录 (多重探测)
            env_upload = os.environ.get("FLET_UPLOAD_DIR")
            page_upload = getattr(self.page, 'upload_dir', None)
            
            if env_upload:
                upload_dir = env_upload
            elif page_upload:
                upload_dir = page_upload
            else:
                # 最后的兜底策略：查找项目根目录下的 uploads
                # 从当前文件 src/tieba_mecha/web/pages/batch_post_page.py 向上退 4 级
                current_dir = os.path.dirname(os.path.abspath(__file__))
                root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
                upload_dir = os.path.join(root_dir, "uploads")

            file_server_path = os.path.join(upload_dir, e.file_name)
            
            print(f"[DEBUG] 上传完成，探测目录: {upload_dir}")
            print(f"[DEBUG] 查找目标文件: {file_server_path}")

            # 等待 1.0s 确保 OS 文件句柄释放且缓冲区落盘
            await asyncio.sleep(1.0)

            if os.path.exists(file_server_path):
                try:
                    await self._process_file_import(file_server_path)
                finally:
                    # 处理完后重置进度条
                    self.progress_bar.visible = False
                    self.page.update()

                    # 处理完后清理临时文件
                    try:
                        os.remove(file_server_path)
                    except:
                        pass
            else:
                # 文件不存在，给出明确错误提示
                self.progress_bar.visible = False
                self.page.update()
                self._show_snackbar(f"文件上传后未找到: {file_server_path}，请检查 uploads 目录权限", "error")

    async def _process_file_import(self, file_path: str):
        """通用的文本/CSV物料解析与持久化逻辑"""
        pairs = []
        try:
            print(f"[DEBUG] 开始处理文件导入: {file_path}")

            if file_path.lower().endswith(".txt"):
                # 使用 utf-8-sig 兼容带/不带 BOM 的文本
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        if line.strip():
                            pairs.append(("暂无标题", line.strip()))
            elif file_path.lower().endswith(".csv"):
                import csv
                # 使用 utf-8-sig 确保从 Excel 导出的带 BOM 的 CSV 也能被正确解析标题
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) >= 2:
                            pairs.append((row[0], row[1]))
                        elif len(row) == 1 and row[0].strip():
                            pairs.append(("暂无标题", row[0].strip()))

            print(f"[DEBUG] 解析到 {len(pairs)} 条数据")

            if not pairs:
                self._show_snackbar("文件内容为空或格式不匹配，未导入任何数据", "warning")
                return

            added_count = await self.db.add_materials_bulk(pairs)
            print(f"[DEBUG] 写入数据库: {added_count} 条")

            self._materials = await self.db.get_materials()
            print(f"[DEBUG] 刷新缓存: {len(self._materials)} 条物料")

            await self._refresh_material_table()
            self._show_snackbar(f"成功导入 {added_count} 条文案物料", "success")
        except Exception as ex:
            print(f"[ERROR] 文件导入失败: {str(ex)}")
            import traceback
            traceback.print_exc()
            self._show_snackbar(f"文件解析失败: {str(ex)}", "error")

    async def _on_start_click(self, e):
        if self._is_running:
            self._is_running = False
            self.start_btn.text = "制定矩阵任务"
            self.start_btn.icon = icons.PLAY_CIRCLE_FILL_ROUNDED
            self.page.update()
            return

        # 获取选中的账号
        selected_accounts = [cb.data for cb in self.account_pool_column.controls if cb.value]
        if not selected_accounts:
            self._show_snackbar("请在中间栏至少勾选一个执行账号", "error")
            return

        # 获取选中的本地贴吧
        selected_forums = [cb.data for cb in self.forum_pool_column.controls if isinstance(cb, ft.Checkbox) and cb.value]
        
        # 获取选中的全域轰炸分组并解包成明文吧名
        if hasattr(self, "global_group_column"):
            selected_groups = [cb.data for cb in self.global_group_column.controls if isinstance(cb, ft.Checkbox) and cb.value]
            for g in selected_groups:
                g_fnames = await self.db.get_target_pools_by_group(g)
                selected_forums.extend(g_fnames)

        manual_forums = [f.strip() for f in self.manual_forum_input.value.replace("，", ",").split(",") if f.strip()]
        dialog_forums = getattr(self, "_temp_target_fnames", [])
        fnames = list(set(selected_forums + manual_forums + dialog_forums))

        # Validation
        pending_m = [m for m in self._materials if m.status == "pending"]
        
        if not fnames or not pending_m:
            error_details = []
            if not fnames: error_details.append("目标贴吧库为空")
            if not pending_m: error_details.append("排期池无待发物料 (需手动回炉或重新导入)")
            
            print(f"[VALIDATION FAIL] {', '.join(error_details)}")
            self._show_snackbar(f"发射拦截：{', '.join(error_details)}", "error")
            return
            
        pairing_mode = self.pairing_mode_dropdown.value
        strategy = self.strategy_dropdown.value
            
        if self.use_schedule.value:
            try:
                st = datetime.strptime(self.schedule_time.value, "%Y-%m-%d %H:%M")
                await self.db.add_batch_task(
                    fname=fnames[0], # 保留以作向下兼容
                    fnames_json=json.dumps(fnames, ensure_ascii=False),
                    titles_json="[]",
                    contents_json="[]",
                    accounts_json=json.dumps(selected_accounts, ensure_ascii=False),
                    strategy=f"{strategy}:{pairing_mode}",
                    total=int(self.post_count.value),
                    delay_min=float(self.min_delay.value),
                    delay_max=float(self.max_delay.value),
                    use_ai=self.use_ai_switch.value,
                    interval_hours=int(self.interval_hours.value) if self.interval_hours.value else 0,
                    schedule_time=st,
                    status="pending"
                )
                self._show_snackbar("定时矩阵任务已加入全域队列", "success")
                await self.load_data()

                # --- 自动步进优化：将界面时间向后推移 ---
                # 获取步进长度：如果有循环周期按周期跳，否则默认跳 1 小时
                step_hours = int(self.interval_hours.value) if self.interval_hours.value and int(self.interval_hours.value) > 0 else 1
                next_st = st + timedelta(hours=step_hours)
                self.schedule_time.value = next_st.strftime("%Y-%m-%d %H:%M")
                self.page.update()
                
                return
            except Exception as ex:
                self._show_snackbar(f"定时解析失败: {str(ex)}", "error")
                return

        self._is_running = True
        self.start_btn.text = "停止任务"
        self.start_btn.icon = icons.STOP_CIRCLE_ROUNDED
        self.start_btn.style = ft.ButtonStyle(color="white", bgcolor="error")
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.log_list.controls.clear()
        self.page.update()

        task = BatchPostTask(
            id=f"TASK_{int(datetime.now().timestamp())}",
            fname=fnames[0],
            fnames=fnames,
            titles=[],
            contents=[],
            accounts=selected_accounts,
            strategy=strategy,
            total=int(self.post_count.value),
            delay_min=float(self.min_delay.value),
            delay_max=float(self.max_delay.value),
            use_ai=self.use_ai_switch.value,
            pairing_mode=pairing_mode
        )

        try:
            async for update in self.manager.execute_task(task):
                if not self._is_running:
                    self._add_log("！任务已被人工干预中止")
                    break
                
                if update["status"] == "success":
                    self._add_log(update) # 直接传入字典以进行结构化渲染
                    self.progress_bar.value = update["progress"] / update["total"]
                elif update["status"] == "error":
                    self._add_log(update, "error")
                elif update["status"] == "skipped":
                    self._add_log(update.get('msg'), "error")
                
                self.page.update()
        except asyncio.CancelledError:
            self._add_log("！任务已被系统强制回收")
        except Exception as ex:
            self._add_log(f"CRITICAL ERROR: {str(ex)}", "error")
        finally:
            self._is_running = False
            self.start_btn.text = "制定矩阵任务"
            self.start_btn.icon = icons.PLAY_CIRCLE_FILL_ROUNDED
            self.start_btn.style = ft.ButtonStyle(color="white", bgcolor="primary")
            self.progress_bar.visible = False
            self.page.update()

    def _add_log(self, data, type="info", timestamp=None):
        """
        结构化日志输出系统 (Cyber-Mecha 风格)
        data: 可以是纯字符串，也可以是包含业务元数据的字典
        """
        now = timestamp if timestamp else datetime.now().strftime("%H:%M:%S")
        
        if isinstance(data, dict):
            status = data.get("status", "info")
            if status == "success":
                # 构建结构化成功卡片
                acc_name = data.get("account_name", "?")
                fname = data.get("fname", "?")
                title = (data.get("title") or "无标题")[:20]
                tid = data.get("tid", 0)
                prog = f"{data.get('progress')}/{data.get('total')}"
                
                log_item = ft.Container(
                    content=ft.Row([
                        ft.Text(f"[{now}]", size=10, color="onSurfaceVariant", weight=ft.FontWeight.W_300),
                        ft.Icon(icons.CHECK_CIRCLE, color="green", size=14),
                        ft.VerticalDivider(width=1),
                        ft.Row([
                            ft.Icon(icons.PERSON, size=12, color="orange"),
                            ft.Text(acc_name, size=11, weight=ft.FontWeight.BOLD, color="orange"),
                        ], spacing=2),
                        ft.Row([
                            ft.Icon(icons.FORUM, size=12, color="primary"),
                            ft.Text(fname, size=11, weight=ft.FontWeight.BOLD, color="primary"),
                        ], spacing=2),
                        ft.Text(f"「{title}」", size=11, color="onSurface", italic=True),
                        ft.Container(expand=True),
                        ft.Text(prog, size=10, color="onSurfaceVariant", weight=ft.FontWeight.BOLD),
                        ft.IconButton(
                            icons.OPEN_IN_NEW, 
                            icon_size=14, 
                            tooltip="在浏览器中开启", 
                            icon_color="primary",
                            on_click=lambda _: self.page.launch_url(f"https://tieba.baidu.com/p/{tid}")
                        )
                    ], spacing=10),
                    padding=ft.padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=with_opacity(0.05, "green"),
                    border=ft.border.only(left=ft.border.BorderSide(3, "green")),
                    border_radius=ft.border_radius.only(top_right=8, bottom_right=8),
                    margin=ft.padding.only(bottom=5)
                )
            else:
                # 构建结构化错误卡片
                fname = data.get("fname", "未知")
                msg = data.get("msg", "执行异常")
                log_item = ft.Container(
                    content=ft.Row([
                        ft.Text(f"[{now}]", size=10, color="onSurfaceVariant"),
                        ft.Icon(icons.ERROR_OUTLINE, color="error", size=14),
                        ft.Text(f"拦截于 [{fname}]: {msg}", size=11, color="error", weight=ft.FontWeight.W_500),
                        ft.Container(expand=True),
                        ft.TextButton(
                            "查看情报", 
                            style=ft.ButtonStyle(color="error", size=10),
                            on_click=lambda e: self._show_rejection_detail(data)
                        )
                    ], spacing=10),
                    padding=ft.padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=with_opacity(0.05, "error"),
                    border=ft.border.only(left=ft.border.BorderSide(3, "error")),
                    border_radius=ft.border_radius.only(top_right=8, bottom_right=8),
                    margin=ft.padding.only(bottom=5)
                )
        else:
            # 兼容模式：纯文本输出
            color = "onSurfaceVariant" if type == "info" else "error"
            icon = icons.INFO_OUTLINED if type == "info" else icons.WARNING_AMBER
            
            log_item = ft.Container(
                content=ft.Row([
                    ft.Text(f"[{now}]", size=10, color="onSurfaceVariant"),
                    ft.Icon(icon, color=color, size=12),
                    ft.Text(str(data), size=11, color=color),
                ], spacing=10),
                padding=ft.padding.symmetric(horizontal=12, vertical=4),
                margin=ft.padding.only(bottom=2)
            )

        self.log_list.controls.insert(0, log_item)
        if len(self.log_list.controls) > 100:
            self.log_list.controls.pop()

    def _show_rejection_detail(self, e):
        """显示拒稿的具体原因弹窗 (增强版：附带战术建议 + 账号/贴吧详情)"""
        # 兼容处理：既支持 Flet 事件，也支持直接传入数据字典
        if hasattr(e, "control") and hasattr(e.control, "data"):
            data = e.control.data
        else:
            data = e
        
        if isinstance(data, dict):
            error_msg = data.get("error") or "未知拒稿原因"
            account_id = data.get("account_id") or "未知"
            fname = data.get("fname") or "未知吧"
        else:
            error_msg = data or "未知拒稿原因"
            account_id = "未知"
            fname = "未知吧"
        
        # 获取战术建议
        from ...core.batch_post import BatchPostManager
        advice = BatchPostManager.get_tactical_advice(error_msg)

        confirm_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.ERROR_OUTLINE, color="error"), ft.Text("发帖被拦截详情 / INTERCEPTED")]),
            content=ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Container(
                            content=ft.Row([ft.Icon(icons.FORUM, size=12, color="primary"), ft.Text(f"吧名: {fname}", size=11, weight=ft.FontWeight.W_500)], spacing=5),
                            padding=ft.padding.symmetric(horizontal=8, vertical=4),
                            bgcolor=with_opacity(0.1, "primary"),
                            border_radius=4
                        ),
                        ft.Container(
                            content=ft.Row([ft.Icon(icons.PERSON, size=12, color="orange"), ft.Text(f"账号ID: {account_id}", size=11, weight=ft.FontWeight.W_500)], spacing=5),
                            padding=ft.padding.symmetric(horizontal=8, vertical=4),
                            bgcolor=with_opacity(0.1, "orange"),
                            border_radius=4
                        ),
                    ], spacing=10),
                    ft.Divider(height=10, color="transparent"),
                    ft.Text("原始错误信息 / RAW ERROR:", size=12, weight=ft.FontWeight.W_500, color="onSurfaceVariant"),
                    ft.Container(
                        content=ft.Text(error_msg, selectable=True, color="error", size=13),
                        padding=10,
                        bgcolor=with_opacity(0.1, "error"),
                        border_radius=5
                    ),
                    ft.Divider(height=10, color="transparent"),
                    ft.Row([ft.Icon(icons.SHIELD_ROUNDED, color="green", size=16), ft.Text("战术情报分析 / STRATEGY", size=12, weight=ft.FontWeight.BOLD)]),
                    ft.Text(f"【拦截诱因】: {advice['reason']}", size=12, color="onSurface"),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("【操作指导】:", size=11, color="green", weight=ft.FontWeight.BOLD),
                            ft.Text(advice['action'], size=11, color="onSurfaceVariant"),
                        ], tight=True, spacing=5),
                        padding=10,
                        bgcolor=with_opacity(0.05, "green"),
                        border=ft.border.all(1, with_opacity(0.2, "green")),
                        border_radius=8
                    )
                ], tight=True, spacing=10),
                width=450,
            ),
            actions=[
                ft.TextButton("我已知晓", on_click=lambda _: self.page.close(confirm_dialog))
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(confirm_dialog)

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary" if type != "error" else "error"
        if type == "success": color = COLORS.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
