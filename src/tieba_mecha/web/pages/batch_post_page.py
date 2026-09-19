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
from .batch_post.launch_config import LaunchConfig, LaunchConfigError
from .batch_post.preflight import PreflightService, PreflightIssue, PreflightReport, scan_import_pairs

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


class BatchPostPage:
    def __init__(self, page: ft.Page, db=None, on_navigate=None):
        self.page = page
        self.db = db
        self.on_navigate = on_navigate
        self.manager = BatchPostManager(db)
        self._is_running = False
        self._accounts = []
        self._materials = [] # now mapped to DB MaterialPool (当前页数据)
        self._all_fnames = []

        self.connector = SmartLinkConnector(db)
        self._file_picker = ft.FilePicker(
            on_result=self._on_file_result,
            on_upload=self._on_upload_progress
        )

        # 搜索与批量选择状态
        self._material_search_text = ""
        self._selected_material_ids = set()

        # 分页状态
        self._material_page = 1
        self._material_page_size = 50
        self._material_total = 0

        # 矩阵配置持久化状态
        self._selected_account_ids = set()
        self._temp_local_fnames = []    # 本地自留区锁定的吧名
        self._temp_global_fnames = []   # 全域轰炸组锁定的吧名

        # 账号选择增强状态
        self._account_search_text = ""
        self._account_select_all = False
        self._initial_load_done = False

        # 运行流水（即时执行监视；完整流水视图在发帖运行中心页）
        from .batch_post.log_stream import LogStream
        self._log_stream = LogStream(
            page, db,
            show_snackbar=self._show_snackbar,
            resolve_account=self._resolve_account_name,
            with_toolbar=False,
        )

        # 初始化所有 UI 控件
        self._init_controls()

    # —— LogStream 委托：保持既有内部 API 兼容（测试与执行循环引用） ——
    @property
    def log_list(self):
        return self._log_stream.log_list

    @property
    def _log_raw_items(self):
        return self._log_stream.raw_items

    @property
    def _log_filter_dropdown(self):
        return self._log_stream.filter_dropdown

    @property
    def _log_stats_text(self):
        return self._log_stream.stats_text

    def _resolve_account_name(self, account_id) -> str:
        """把账号 ID 解析为可读名称（拦截详情弹窗用）"""
        if isinstance(account_id, int) or (isinstance(account_id, str) and account_id.isdigit()):
            acc_id_int = int(account_id)
            name = self._account_name_map.get(acc_id_int)
            return name or f"账号-{acc_id_int}"
        return str(account_id)

    @staticmethod
    def _log_matches_filter(status_str: str, filter_val: str) -> bool:
        from .batch_post.log_stream import LogStream
        return LogStream.matches_filter(status_str, filter_val)

    def _add_log(self, data, type="info", timestamp=None):
        self._log_stream.add(data, type=type, timestamp=timestamp)

    def _update_log_stats(self):
        self._log_stream.update_stats()

    async def _on_log_filter_change(self, e):
        await self._log_stream.on_filter_change(e)

    async def _on_clear_logs(self, e):
        await self._log_stream.clear_logs(e)

    async def _refresh_logs(self, e=None):
        await self._log_stream.refresh(e)

    def _show_rejection_detail(self, e):
        self._log_stream.show_rejection_detail(e)

    def _format_log_timestamp(self, dt_or_str):
        from .batch_post.log_stream import format_log_timestamp
        return format_log_timestamp(dt_or_str)

    async def load_data(self):
        """加载页面数据"""
        if not self.db: return
        try:
            # 并行加载互不依赖的数据；配置项合并为一次批量查询
            # 运行区数据（任务队列/归档/流水）已移交发帖运行中心页
            _setting_keys = [
                "max_bump_count", "use_ai_rewrite", "ai_persona", "use_schedule",
                "bump_matrix_enabled", "bump_ai_content", "bump_mode",
                "last_selected_local_forums", "last_selected_global_forums", "last_selected_account_ids",
                "pending_task_copy",
            ]
            (
                self._accounts, _all_accs,
                all_valid_forums, _s,
            ) = await asyncio.gather(
                self.db.get_matrix_accounts(),
                self.db.get_accounts(),
                self.db.get_all_unique_forums(),
                self.db.get_settings_bulk(_setting_keys),
            )
            # 账号名映射（拦截详情弹窗解析用）
            self._account_name_map = {a.id: (a.user_name or a.name) for a in _all_accs if a}

            # [自顶配置同步] 加载 max_bump_count 到内存缓存
            max_bump_raw = _s.get("max_bump_count", "")
            self._max_bump_count = int(max_bump_raw) if max_bump_raw else 20

            # [自顶配置同步] 恢复 AI 改写与定时执行开关状态
            ai_raw = _s.get("use_ai_rewrite", "")
            if ai_raw is not None:
                self.use_ai_switch.value = ai_raw == "1"

            # 恢复 AI 人格设定
            persona_raw = _s.get("ai_persona", "")
            if persona_raw:
                self.ai_persona_dropdown.value = persona_raw
            else:
                self.ai_persona_dropdown.value = "normal"

            sched_raw = _s.get("use_schedule", "")
            if sched_raw is not None:
                self.use_schedule.value = sched_raw == "1"
                # 恢复定时输入框的可见性
                if hasattr(self, "schedule_type_dropdown"):
                    self.schedule_type_dropdown.visible = sched_raw == "1"
                    if sched_raw == "1":
                        st = self.schedule_type_dropdown.value or "once"
                        if hasattr(self, '_update_schedule_visibility'):
                            self._update_schedule_visibility(st)

            # [自顶配置同步] 恢复矩阵协同模式开关
            matrix_raw = _s.get("bump_matrix_enabled", "")
            if matrix_raw is not None:
                self.bump_matrix_switch.value = matrix_raw == "1"

            # [自顶配置同步] 恢复 AI自顶内容开关
            bump_ai_raw = _s.get("bump_ai_content", "")
            if bump_ai_raw is not None:
                self.bump_ai_content_switch.value = bump_ai_raw == "1"

            # [自顶配置同步] 恢复自顶模式
            bump_mode_raw = _s.get("bump_mode", "")
            if bump_mode_raw is not None and hasattr(self, "bump_mode_group"):
                self.bump_mode_group.value = bump_mode_raw
                self.bump_loop_container.visible = (bump_mode_raw == "matrix_loop")

            # [持久化同步] 恢复上次选中的贴吧 (分本地/全域两组独立)
            # [修复] 校验持久化贴吧是否仍然有效（排除已隐藏/已封禁/已删除的贴吧）
            valid_fnames = {f['fname'] for f in all_valid_forums if not f['is_banned']}
            last_local_raw = _s.get("last_selected_local_forums", "")
            if last_local_raw:
                try:
                    restored = json.loads(last_local_raw)
                    self._temp_local_fnames = [fn for fn in restored if fn in valid_fnames]
                except Exception: pass
            last_global_raw = _s.get("last_selected_global_forums", "")
            if last_global_raw:
                try:
                    restored = json.loads(last_global_raw)
                    self._temp_global_fnames = [fn for fn in restored if fn in valid_fnames]
                except Exception: pass
            self._update_forum_select_btn()

            # [持久化同步] 恢复上次选中的账号
            last_acc_raw = _s.get("last_selected_account_ids", "")
            has_last_acc = False
            if last_acc_raw:
                try:
                    acc_ids = json.loads(last_acc_raw)
                    if acc_ids:
                        self._selected_account_ids = set(acc_ids)
                        has_last_acc = True
                except Exception: pass

            # 首次加载初始化选择：如果有持久化则用持久化，否则仅勾选状态正常的账号
            if not self._initial_load_done:
                if not has_last_acc:
                    for acc in self._accounts:
                        if acc.status == "active":
                            self._selected_account_ids.add(acc.id)
                self._initial_load_done = True

            # [修复] 过滤掉数据库中已不存在的账号 ID，防止出现 6/5 这种逻辑错误
            current_ids = {acc.id for acc in self._accounts}
            self._selected_account_ids = {aid for aid in self._selected_account_ids if aid in current_ids}

            # [跨页交接] 运行中心"复制配置"写入的待载入任务配置
            copy_raw = _s.get("pending_task_copy", "")
            if copy_raw:
                try:
                    await self._apply_task_config_values(json.loads(copy_raw))
                    await self.db.set_setting("pending_task_copy", "")
                except Exception as copy_err:
                    from ...core.logger import log_warn
                    await log_warn(f"应用复制任务配置失败: {copy_err}")

            self._refresh_account_pool()
            await self._refresh_material_table()

        except Exception as e:
            from ...core.logger import log_error
            await log_error(f"[UI ERROR] load_data failed: {e}")
            self._show_snackbar(f"数据同步异常: {str(e)}", "error")

    async def _apply_task_config_values(self, data: dict):
        """把任务配置字典（LaunchConfig 序列化格式）应用到当前表单。

        运行中心"复制配置"与历史任务复制共用此入口；
        账号/贴吧/排期会在启动前的预检摘要中重新确认。
        """
        self._temp_local_fnames = list(data.get("local_fnames") or [])
        self._temp_global_fnames = [fn for fn in (data.get("global_fnames") or []) if fn]
        self._selected_account_ids = set(data.get("account_ids") or [])
        self._save_account_selection()

        if data.get("post_count"):
            self.post_count.value = str(data["post_count"])
        if data.get("delay_min") is not None:
            self.min_delay.value = str(data["delay_min"])
        if data.get("delay_max") is not None:
            self.max_delay.value = str(data["delay_max"])
        self.use_ai_switch.value = bool(data.get("use_ai"))
        if data.get("ai_persona"):
            self.ai_persona_dropdown.value = data["ai_persona"]

        schedule_type = data.get("schedule_type") or "once"
        if data.get("use_schedule") and schedule_type in ("daily", "weekly", "interval"):
            self.use_schedule.value = True
            self.schedule_type_dropdown.value = schedule_type
            if schedule_type == "interval" and data.get("interval_hours"):
                self.interval_hours.value = str(data["interval_hours"])
            if schedule_type == "weekly" and data.get("schedule_day_of_week") is not None:
                self.schedule_day_of_week.value = str(data["schedule_day_of_week"])
        else:
            # once 任务复制为立即执行，避免载入过去的时间点
            self.use_schedule.value = False
        self._update_schedule_visibility(self.schedule_type_dropdown.value or "once")

        self._update_forum_select_btn()
        self._refresh_account_pool()
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
            
            # AI自顶内容开关
            bump_ai_content = "1" if self.bump_ai_content_switch.value else "0"
            
            # 写入数据库
            await self.db.set_setting("max_bump_count", str(max_count))
            await self.db.set_setting("bump_cooldown_minutes", str(cooldown))
            await self.db.set_setting("bump_matrix_enabled", matrix_enabled)
            await self.db.set_setting("use_ai_rewrite", ai_enabled)
            await self.db.set_setting("use_schedule", schedule_enabled)
            await self.db.set_setting("bump_ai_content", bump_ai_content)
            
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
            try: self.bump_duration_field.update()
            except Exception: pass

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
                
                weight = max(1, min(10, acc.post_weight or 5))
                weight_dots = "●" * (weight // 2) + "○" * (5 - weight // 2)
                
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
            except Exception:
                pass

    def _toggle_select_all(self, container: ft.Column, value: bool):
        """批量全选/取消"""
        for cb in container.controls:
            if isinstance(cb, ft.Checkbox) and cb.visible:
                cb.value = value
                # 同步到状态集
                if container == self.account_pool_column:
                    if value: self._selected_account_ids.add(cb.data)
                    else: self._selected_account_ids.discard(cb.data)
        try:
            container.update()
        except Exception:
            pass
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
            try: self.account_pool_title.update()
            except Exception: pass
        self._save_account_selection()

    def _save_account_selection(self):
        """将当前选中的账号持久化到数据库"""
        if self.db:
            ids_json = json.dumps(list(self._selected_account_ids))
            self.page.run_task(self.db.set_setting, "last_selected_account_ids", ids_json)

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
        """同步批量操作栏的可见性与计数"""
        if hasattr(self, "_material_bulk_actions"):
            self._material_bulk_actions.visible = bool(self._selected_material_ids)
            sel_count = len(self._selected_material_ids)
            total_count = self._material_total
            self._material_selected_count_text.value = f"已选 {sel_count}/{total_count} 项"


    async def _bulk_toggle_auto_bump(self, e):
        """批量开启/关闭选中物料的自动回帖（排期池选中集）"""
        target_ids = list(self._selected_material_ids)
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

        # 同步内存引用并刷新
        count = len(target_ids)
        for m in self._materials:
            if m.id in self._selected_material_ids:
                m.is_auto_bump = target_val
        self._selected_material_ids.clear()
        await self._refresh_material_table()
        self._show_snackbar(f"已批量{'开启' if target_val else '关闭'} {count} 项自动回帖", "success")

    async def _on_material_search_change(self, e):
        self._material_search_text = e.control.value
        self._material_page = 1  # 搜索时重置到第1页
        await self._refresh_material_table()

    async def _bulk_delete_materials(self, e):
        if not self._selected_material_ids:
            return
        
        async def do_delete(_):
            for mid in list(self._selected_material_ids):
                await self.db.delete_material(mid)
            self._selected_material_ids.clear()
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

    async def _bulk_reset_materials(self, e):
        """批量重置排期池中的选中项（通常用于将‘失败’重置为‘待发’）"""
        if not self._selected_material_ids:
            return
        count = len(self._selected_material_ids)
        for mid in list(self._selected_material_ids):
            await self.db.update_material_status(mid, "pending")
        self._selected_material_ids.clear()
        await self._refresh_material_table()
        self._show_snackbar(f"已批量重置 {count} 条物料到待发状态", "success")

    async def _refresh_material_table(self):
        """分页刷新物料排期池表，服务端过滤+分页（已发归档在运行中心页）"""
        if not hasattr(self, "_material_table"):
            return

        # 获取状态计数（用于统计显示）
        self._status_counts = await self.db.get_materials_status_counts()
        pending = self._status_counts.get("pending", 0)
        success = self._status_counts.get("success", 0)
        failed = self._status_counts.get("failed", 0)

        if hasattr(self, "_stats_text"):
            self._stats_text.value = f"状态分布:  ⏳待发({pending})   ✅成功({success})   ❌失败({failed})"

        # --- 排期池分页查询 ---
        mat_search = self._material_search_text if self._material_search_text.strip() else None
        mat_items, self._material_total = await self.db.get_materials_by_status_paginated(
            statuses=["pending", "failed"],
            search_text=mat_search,
            page=self._material_page,
            page_size=self._material_page_size,
        )
        self._materials = mat_items  # 保持兼容，其他方法可能引用
        pending_rows = []
        for m in mat_items:
            try:
                m_title = m.title or ""
                m_content = m.content or ""
                display_t = m_title if len(m_title) <= 15 else m_title[:15] + "..."
                display_c = m_content if len(m_content) <= 18 else m_content[:18] + "..."
                ai_text = "✨独立改写" if m.ai_status == "rewritten" else "无处理"
                ai_color = "primary" if m.ai_status == "rewritten" else "onSurfaceVariant"
                status_color = "onSurfaceVariant" if m.status == "pending" else "error"
                status_icon = icons.SCHEDULE if m.status == "pending" else icons.ERROR
                status_text = "待发送" if m.status == "pending" else "遭遇拒稿"
                pending_rows.append(
                    ft.DataRow(
                        selected=m.id in self._selected_material_ids,
                        on_select_changed=lambda e, mid=m.id: self.page.run_task(self._on_material_row_select, mid, e.data),
                        cells=[
                            ft.DataCell(ft.Text(str(m.id))),
                            ft.DataCell(ft.Container(ft.Text(display_t, size=12, tooltip=m_title), width=170)),
                            ft.DataCell(ft.Container(ft.Text(display_c, size=12, tooltip=m_content), width=200)),
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
            except Exception as ex:
                continue

        self._material_table.rows = pending_rows

        # 更新分页控件
        self._update_material_pagination()

        # 同步更新批量操作栏
        self._update_bulk_visibility()

        # 精确更新而非全页面刷新
        try:
            if hasattr(self, "_material_table"):
                self._material_table.update()
        except Exception:
            pass

    # ========== 分页导航方法 ==========

    async def _on_material_prev_page(self, e):
        if self._material_page > 1:
            self._material_page -= 1
            await self._refresh_material_table()

    async def _on_material_next_page(self, e):
        total_pages = max(1, (self._material_total + self._material_page_size - 1) // self._material_page_size)
        if self._material_page < total_pages:
            self._material_page += 1
            await self._refresh_material_table()

    def _update_material_pagination(self):
        if not hasattr(self, "_mat_page_info"):
            return
        total_pages = max(1, (self._material_total + self._material_page_size - 1) // self._material_page_size)
        sel_info = f" | 已选{len(self._selected_material_ids)}" if self._selected_material_ids else ""
        self._mat_page_info.value = f"{self._material_page}/{total_pages} 页 (共{self._material_total}条{sel_info})"
        self._mat_prev_btn.disabled = self._material_page <= 1
        self._mat_next_btn.disabled = self._material_page >= total_pages
        try:
            try:
                self._mat_page_info.update()
                self._mat_prev_btn.update()
                self._mat_next_btn.update()
            except Exception:
                pass
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

    async def _add_material_row(self, e):
        t = self._quick_title.value.strip() or "暂无标题"
        c = self._quick_content.value.strip()
        if not c:
            self._show_snackbar("内容不可为空", "error")
            return
        
        await self.db.add_materials_bulk([(t, c)])
        self._quick_title.value = ""
        self._quick_content.value = ""
        await self._refresh_material_table()
        self._show_snackbar("成功添加一条物料录入", "success")

    async def _delete_material_row(self, e):
        idx = e.control.data
        if await self.db.delete_material(idx):
            await self._refresh_material_table()

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
        if e is not None:
            # 用户手动触发：先弹统一确认框，防止误触清空全部物料
            from ..components.toast import confirm_async
            confirmed = await confirm_async(
                self.page,
                "确认摧毁总计划？",
                "将清空物料池中的全部物料（含排期池与归档库），此操作不可恢复。",
                confirm_text="确认清空",
            )
            if not confirmed:
                return
            try:
                await self._clear_all_materials(e=None)
                self._show_snackbar("物料池已全库排空", "success")
            except Exception as ex:
                self._show_snackbar(f"清空失败: {ex}", "error")
            return

        await self.db.clear_materials()
        self._material_page = 1
        self._archive_page = 1
        await self._refresh_material_table()
        if e: self._show_snackbar("物料池已全库排空", "success")

    async def _resolve_import_pairs(self, pairs: list):
        """导入预检：扫描质量并弹预览确认。

        返回最终要导入的 (标题, 正文) 列表；用户取消返回 None。
        空内容/超长标题在两种导入选项下都会被剔除（写入即无效）。
        """
        if not pairs:
            return pairs
        scan = scan_import_pairs(pairs)
        if not scan.has_warnings:
            return pairs

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        def _resolve_import(choice):
            if not future.done():
                future.set_result(choice)
            try:
                self.page.close(preview_dialog)
            except Exception:
                pass

        def color_of(icon):
            return {"⚠️": "orange", "❌": "error"}.get(icon, "primary")

        issue_rows = []
        if scan.empty_entries:
            issue_rows.append(("⚠️", f"{len(scan.empty_entries)} 条内容为空（将被跳过）"))
        if scan.missing_title:
            preview_ids = scan.missing_title[:8]
            more = "…" if len(scan.missing_title) > 8 else ""
            issue_rows.append(("⚠️", f"{len(scan.missing_title)} 条缺标题（将仅发正文，序号 {preview_ids}{more}）"))
        if scan.overlong_title:
            issue_rows.append(("❌", f"{len(scan.overlong_title)} 条标题超过 500 字（超出字段上限，将被跳过）"))
        if scan.duplicate_groups:
            extra = sum(len(g) - 1 for g in scan.duplicate_groups)
            issue_rows.append(("⚠️", f"{len(scan.duplicate_groups)} 组完全重复（去重可减少 {extra} 条，同内容重复投放易触发风控）"))
        if scan.with_links:
            issue_rows.append(("ℹ️", f"{len(scan.with_links)} 条含链接/短链（建议导入后执行短链同步）"))

        content = ft.Column([
            ft.Text(f"共解析 {scan.total} 条，导入前请确认：", size=12, weight=ft.FontWeight.BOLD),
            *[
                ft.Row([
                    ft.Icon(name=icons.WARNING_AMBER_ROUNDED if icon == "⚠️" else
                            (icons.ERROR_ROUNDED if icon == "❌" else icons.INFO_OUTLINED),
                            color=color_of(icon), size=15),
                    ft.Text(msg, size=11, expand=True, selectable=True),
                ], spacing=6)
                for icon, msg in issue_rows
            ],
        ], spacing=6, tight=True)

        valid_n = len(scan.valid_indices())
        dedup_n = len(scan.dedup_indices())
        preview_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("物料导入预检"),
            content=ft.Container(content=content, width=480),
            actions=[
                ft.TextButton("取消", on_click=lambda _: _resolve_import(None)),
                ft.TextButton(f"导入去重后 {dedup_n} 条", on_click=lambda _: _resolve_import("dedup")),
                ft.FilledButton(f"导入 {valid_n} 条", on_click=lambda _: _resolve_import("all")),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(preview_dialog)
        choice = await future
        if choice is None:
            return None
        if choice == "dedup":
            return [pairs[i] for i in scan.dedup_indices()]
        return [pairs[i] for i in scan.valid_indices()]

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

            final_pairs = await self._resolve_import_pairs(pairs)
            if final_pairs is None:
                return  # 用户取消
            if not final_pairs:
                self._show_snackbar("没有可导入的有效内容", "warning")
                return
            added_count = await self.db.add_materials_bulk(final_pairs)
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

    async def _on_material_select_all(self, e):
        # 跨页全选：从数据库查询所有符合条件的 ID
        is_select = e.data == "true" if isinstance(e.data, str) else bool(e.data)
        if is_select:
            mat_search = self._material_search_text if self._material_search_text.strip() else None
            all_ids = await self.db.get_material_ids_by_status(
                statuses=["pending", "failed"],
                search_text=mat_search,
            )
            self._selected_material_ids = set(all_ids)
        else:
            self._selected_material_ids.clear()
        self._update_bulk_visibility()
        await self._refresh_material_table()

    async def _on_material_row_select(self, mid, selected):
        # Flet e.data 为字符串 "true"/"false"
        is_selected = selected == "true" if isinstance(selected, str) else bool(selected)

        if is_selected:
            self._selected_material_ids.add(mid)
        else:
            self._selected_material_ids.discard(mid)

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
            # 从数据库按ID查询，支持跨页选中
            selected_mats = await self.db.get_materials_by_ids(list(self._selected_material_ids))
            pending_m = [m for m in selected_mats if m.status == "pending"]
            if not pending_m:
                self._show_snackbar("选中的物料中没有处于 [待发] 状态的项，或者它们已经是成功/失败状态，无法改写", "warning")
                return
        else:
            # 获取全部待发物料
            pending_m = await self.db.get_materials(status="pending", limit=None)
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
            try:
                for i, m in enumerate(pending_m):
                    # 检查对话框是否还开着（是否被手动关闭取消）
                    if not dialog.open: break

                    status_text.value = f"正在改写第 {i+1}/{total} 条: {m.title[:15]}..."
                    self.page.update()

                    # 调用 AI
                    try:
                        persona = self.ai_persona_dropdown.value or "normal"
                        success, opt_title, opt_content, err = await optimizer.optimize_post(m.title, m.content, persona=persona)
                        if success:
                            await self.db.update_material_ai(m.id, opt_title, opt_content)
                            success_count += 1
                    except Exception as ex:
                        from ...core.logger import log_error
                        await log_error(f"AI Batch error on ID {m.id}: {ex}")

                    progress_bar.value = (i + 1) / total
                    self.page.update()
            finally:
                await optimizer.close()

            self.page.close(dialog)
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
        persona = self.ai_persona_dropdown.value or "normal"
        try:
            success, opt_title, opt_content, err = await optimizer.optimize_post(m.title, m.content, persona=persona)
        finally:
            await optimizer.close()

        if success:
            await self.db.update_material_ai(mid, opt_title, opt_content)
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
        await self._open_firepower_dialog(0)

    async def _open_firepower_dialog_tab(self, tab_index: int):
        """按指定 tab 打开火力配置对话框"""
        await self._open_firepower_dialog(tab_index)

    def _update_forum_select_btn(self):
        """根据本地/全域两组锁定状态刷新主界面按钮"""
        local_count = len(self._temp_local_fnames)
        global_count = len(self._temp_global_fnames)
        total = local_count + global_count

        # 原始按钮：仅两组都无选择时显示
        if total == 0:
            self.forum_select_btn.text = "点击选择目标贴吧"
            self.forum_select_btn.style = ft.ButtonStyle(color="onSurfaceVariant", bgcolor=None)
            self.forum_select_btn.visible = True
            self.local_status_btn.visible = False
            self.global_status_btn.visible = False
        else:
            self.forum_select_btn.visible = False
            # 本地自留区按钮
            if local_count > 0:
                self.local_status_btn.text = f"🏠 本地自留区: {local_count} 个目标"
                self.local_status_btn.style = ft.ButtonStyle(color="white", bgcolor="primary")
            else:
                self.local_status_btn.text = "🏠 本地自留区: 未选择"
                self.local_status_btn.style = ft.ButtonStyle(color="onSurfaceVariant", bgcolor=None)
            self.local_status_btn.visible = True
            # 全域轰炸组按钮
            if global_count > 0:
                self.global_status_btn.text = f"🔥 全域轰炸组: {global_count} 个目标"
                self.global_status_btn.style = ft.ButtonStyle(color="white", bgcolor="orange")
            else:
                self.global_status_btn.text = "🔥 全域轰炸组: 未选择"
                self.global_status_btn.style = ft.ButtonStyle(color="onSurfaceVariant", bgcolor=None)
            self.global_status_btn.visible = True

        try:
            self.forum_select_btn.update()
            self.local_status_btn.update()
            self.global_status_btn.update()
        except Exception:
            pass

    async def _open_safety_config_dialog(self, pre_selected: set):
        """[子弹窗] 安全原初打法状态查看（自动判定，不可手动切换）"""
        
        # 1. 先自动同步，确保数据最新
        await self.db.auto_sync_post_target()
        forums = await self.db.get_all_unique_forums()
        
        # 2. 搜索控制（全选仅用于选择贴吧，不修改安全状态）
        search_field = ft.TextField(
            label="搜索贴吧名...",
            prefix_icon=icons.SEARCH,
            dense=True,
            text_size=12,
            expand=True
        )
        
        # 3. 贴吧容器
        forums_list_container = ft.Column(spacing=5, scroll=ft.ScrollMode.ADAPTIVE, height=300)

        def render_forums(keyword=""):
            forums_list_container.controls.clear()
            safe_count = 0
            unsafe_count = 0
            for f in forums:
                if keyword and keyword.lower() not in f['fname'].lower(): continue
                is_safe = f['is_post_target']
                if is_safe:
                    safe_count += 1
                else:
                    unsafe_count += 1
                forums_list_container.controls.append(
                    ft.Row([
                        ft.Icon(icons.SHIELD_ROUNDED if is_safe else icons.SHIELD_OUTLINED, 
                                size=16, color="green" if is_safe else "error"),
                        ft.Text(f['fname'], size=12, weight="bold" if is_safe else None,
                                color="onSurface" if is_safe else "onSurfaceVariant"),
                        ft.Text("安全" if is_safe else ("封禁" if f.get('is_banned') else "有删帖记录"),
                                size=10, color="green" if is_safe else "error"),
                    ], spacing=6)
                )
            # 在顶部显示统计
            summary_text.value = f"✅ 安全: {safe_count} 个 | ⚠️ 不安全: {unsafe_count} 个"
            try:
                forums_list_container.update()
                summary_text.update()
            except Exception:
                pass

        search_field.on_change = lambda e: render_forums(e.control.value)

        summary_text = ft.Text("", size=12, weight="bold")

        async def on_close(_):
            self.page.close(safety_dialog)
            await self._open_firepower_dialog(0)

        safety_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.SHIELD_ROUNDED, color="green"), ft.Text("安全原初打法状态（自动判定）")]),
            content=ft.Container(
                content=ft.Column([
                    ft.Text("本土作战许可已改为自动判定：未封禁且无删帖记录 = 安全", size=11, color="onSurfaceVariant"),
                    summary_text,
                    ft.Row([search_field], spacing=10),
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
                ft.TextButton("取消", on_click=lambda _: self.page.close(safety_dialog)),
                ft.FilledButton(
                    "确认返回", 
                    icon=icons.CHECK_CIRCLE_ROUNDED, 
                    style=ft.ButtonStyle(bgcolor="green", color="white"),
                    on_click=on_close
                ),
            ],
        )
        
        self.page.open(safety_dialog)
        render_forums()
    async def _open_firepower_dialog(self, initial_tab: int = 0):
        """[火力配置主页面] 本地自留区与全域轰炸组独立锁定"""

        def _risk_badge(risk):
            """风险画像徽标：覆盖账号数 · 发帖数 · 删帖数(率)。"""
            if not risk:
                return ""
            parts = [f"覆盖{risk['cover_accounts']}"]
            if risk["posted_count"]:
                parts.append(f"发{risk['posted_count']}")
                if risk["dead_count"]:
                    parts.append(f"删{risk['dead_count']}({risk['dead_rate']:.0%})")
            return " · ".join(parts)

        def _is_risky(risk):
            """高风险判定：已封禁，或发帖样本≥3且删帖率≥50%。"""
            if not risk:
                return False
            if risk["is_banned"]:
                return True
            return risk["posted_count"] >= 3 and risk["dead_rate"] >= 0.5

        # 获取数据
        local_forums = await self.db.get_all_unique_forums()
        target_groups = await self.db.get_target_pool_groups()
        # 风险画像聚合（覆盖/发帖/删帖），失败时降级为无徽标不阻断弹窗
        risk_map = {}
        try:
            risk_map = {s["fname"]: s for s in await self.db.get_forum_risk_stats()}
        except Exception:
            pass
        
        # ===== 两个独立的选中集合 =====
        # 本地自留区选中：若上次有持久化选择则沿用，否则默认选中所有安全吧
        # [修复] 清除已封禁的贴吧；对 is_post_target 状态变化发出警告
        if self._temp_local_fnames:
            local_selected = set(self._temp_local_fnames)
            # 过滤掉已不存在或已封禁的贴吧
            current_fname_set = {f['fname'] for f in local_forums}
            banned_fnames = {f['fname'] for f in local_forums if f['is_banned']}
            stale_fnames = local_selected - current_fname_set
            banned_selected = local_selected & banned_fnames
            if stale_fnames:
                local_selected -= stale_fnames
            if banned_selected:
                local_selected -= banned_selected
            # 汇总警告（延迟到 UI 渲染后显示）
            _local_warn_count = len(stale_fnames) + len(banned_selected)
        else:
            # 默认选中所有安全贴吧；高风险（删帖率≥50%）贴吧默认排除
            local_selected = {
                f['fname'] for f in local_forums
                if f['is_post_target'] and not _is_risky(risk_map.get(f['fname']))
            }
            _local_warn_count = 0
        global_selected = set(self._temp_global_fnames)  # 全域轰炸组选中
        
        # ===== 本地自留区 UI =====
        local_search_field = ft.TextField(
            label="过滤本地吧...",
            prefix_icon=icons.SEARCH,
            dense=True,
            text_size=12,
            expand=True
        )
        local_select_all_cb = ft.Checkbox(label="全选安全", value=False, scale=0.8, fill_color="green")
        local_container = ft.Column(spacing=2, scroll=ft.ScrollMode.ADAPTIVE, height=300)
        local_count_text = ft.Text(f"已选: {len(local_selected)} 个目标", size=12, color="primary", weight=ft.FontWeight.BOLD)
        
        def update_local_count():
            local_count_text.value = f"已选: {len(local_selected)} 个目标"
            try: local_count_text.update()
            except Exception: pass
        
        def on_local_item_check(e):
            fn = e.control.data
            if e.control.value: local_selected.add(fn)
            else: local_selected.discard(fn)
            update_local_count()
        
        def render_local_list(keyword=""):
            local_container.controls.clear()
            if not local_forums:
                local_container.controls.append(
                    ft.Container(
                        content=ft.Text("系统内尚无贴吧数据\n请先前往 Dashboard 运行签到或同步关注贴吧", color="onSurfaceVariant", text_align="center", size=12),
                        alignment=ft.alignment.center, expand=True, padding=ft.padding.only(top=40)
                    )
                )
            else:
                for f in local_forums:
                    fn = f['fname']
                    is_safe = f['is_post_target']
                    if keyword and keyword.lower() not in fn.lower(): continue
                    risk = risk_map.get(fn)
                    badge = _risk_badge(risk)
                    badge_text = f"  ({badge})" if badge else ""
                    is_checked = fn in local_selected
                    item_disabled = False
                    # [修复] 被选中但非安全的贴吧添加⚠️警告标识，提醒用户状态变化
                    if f['is_banned']:
                        label_text = f"⛔ {fn} [已封禁]{badge_text}"
                        item_color = "error"
                        item_disabled = True
                    elif is_checked and not is_safe:
                        label_text = f"⚠️ {fn} [非安全]{badge_text}"
                        item_color = "orange"
                    elif _is_risky(risk):
                        # 高风险（删帖率≥50%）默认禁选；持久化已选的保留并标红
                        label_text = f"🔴 {fn} [高风险]{badge_text}"
                        item_color = "error"
                        item_disabled = not is_checked
                    elif is_safe:
                        label_text = f"🛡️ {fn} [安全]{badge_text}"
                        item_color = "green"
                    else:
                        label_text = f"{fn}{badge_text}"
                        item_color = "onSurface"
                    local_container.controls.append(
                        ft.Checkbox(
                            label=label_text, value=is_checked, data=fn, on_change=on_local_item_check,
                            disabled=item_disabled,
                            tooltip="高风险贴吧（删帖率高或已封禁），默认禁选；如需投放请先在存活分析复核" if item_disabled else None,
                            fill_color="green" if is_safe else ("orange" if is_checked else None),
                            label_style=ft.TextStyle(color=item_color, size=11, weight=ft.FontWeight.W_500 if is_safe else None)
                        )
                    )
            try: local_container.update()
            except Exception: pass
        
        local_search_field.on_change = lambda e: render_local_list(e.control.value)
        
        def on_local_select_all(e):
            select_all = e.control.value
            # 构建安全贴吧名集合，用于快速判定
            safe_fnames = {f['fname'] for f in local_forums if f['is_post_target']}
            for cb in local_container.controls:
                if isinstance(cb, ft.Checkbox):
                    if cb.disabled: continue  # 封禁/高风险禁选项不参与全选
                    fn = cb.data
                    if select_all:
                        # 全选时仅勾选安全贴吧，跳过不安全的
                        is_safe = fn in safe_fnames
                        cb.value = is_safe
                        if is_safe: local_selected.add(fn)
                    else:
                        cb.value = False
                        local_selected.discard(fn)
            update_local_count()
            try: local_container.update()
            except Exception: pass
        
        local_select_all_cb.on_change = on_local_select_all
        
        async def on_local_lock(_):
            # [修复] 检测并警告非安全贴吧
            safe_fnames = {f['fname'] for f in local_forums if f['is_post_target']}
            unsafe_selected = local_selected - safe_fnames
            if unsafe_selected:
                self._show_snackbar(f"⚠️ 包含 {len(unsafe_selected)} 个非安全贴吧(未标记发布目标)，发帖时可能被拦截", "warning")
            self._temp_local_fnames = list(local_selected)
            if self.db:
                self.page.run_task(self.db.set_setting, "last_selected_local_forums", json.dumps(self._temp_local_fnames))
            self._update_forum_select_btn()
            self.page.close(fire_dialog)
            self._show_snackbar(f"🏠 本地自留区已锁定 {len(local_selected)} 个目标", "success")
        
        async def on_bulk_unfollow_click(_):
            selected_to_purge = list(local_selected)
            if not selected_to_purge:
                self._show_snackbar("请先勾选需要清理的阵地", "warning")
                return
            async def do_purge(e):
                self.page.close(confirm_dialog)
                from ...core.batch_post import BatchPostManager
                pm = BatchPostManager(self.db)
                self._show_snackbar(f"开始对 {len(selected_to_purge)} 个吧执行全局清理，请稍后...", "info")
                res = await pm.unfollow_forums_bulk(selected_to_purge)
                ok, bad = len(res["success"]), len(res["failed"])
                local_selected.clear()
                nonlocal local_forums
                local_forums = await self.db.get_all_unique_forums()
                render_local_list()
                update_local_count()
                if bad:
                    self._show_snackbar(f"⚠️ 阵地清理完成：取关 {ok} 项成功，{bad} 项失败（失败记录已保留）", "warning")
                else:
                    self._show_snackbar(f"✅ 阵地清理完成，已从数据库抹除并取关 {ok} 项", "success")
            confirm_dialog = ft.AlertDialog(
                title=ft.Row([ft.Icon(icons.WARNING, color="orange"), ft.Text("确认全局清理并取关")]),
                content=ft.Text(f"将对已选的 {len(selected_to_purge)} 个贴吧执行【全局取关】并彻底删除本地记录。\n此操作不可逆，且会触发矩阵网络请求。是否继续？"),
                actions=[
                    ft.TextButton("取消", on_click=lambda _: self.page.close(confirm_dialog)),
                    ft.ElevatedButton("确认清除", bgcolor="error", color="white", on_click=lambda e: self.page.run_task(do_purge, e))
                ]
            )
            self.page.open(confirm_dialog)
        
        # ===== 全域轰炸组 UI =====
        global_manual_input = ft.TextField(
            label="手动补充吧名 (英文逗号分隔)",
            hint_text="贴吧1, 贴吧2...",
            multiline=True, min_lines=3, text_size=12,
        )
        groups_container = ft.Column(spacing=2, scroll=ft.ScrollMode.ADAPTIVE, expand=True)
        global_count_text = ft.Text(f"已选: {len(global_selected)} 个目标", size=12, color="orange", weight=ft.FontWeight.BOLD)
        
        def update_global_count():
            # 合计手动输入 + 分组选择
            manual_fnames = set()
            if global_manual_input.value:
                manual_fnames = {f.strip() for f in global_manual_input.value.split(",") if f.strip()}
            total = len(global_selected | manual_fnames)
            global_count_text.value = f"已选: {total} 个目标"
            try: global_count_text.update()
            except Exception: pass
        
        async def on_group_check(e):
            group_name = e.control.data
            is_checked = e.control.value
            fnames = await self.db.get_target_pools_by_group(group_name)
            for fn in fnames:
                if is_checked: global_selected.add(fn)
                else: global_selected.discard(fn)
            update_global_count()
            self._show_snackbar(f"{'已添加' if is_checked else '已从待选区移除'} 分组 [{group_name}] 中的 {len(fnames)} 个吧点", "info")
            # 刷新子项勾选状态
            await refresh_group_items()
        
        global_manual_input.on_change = lambda e: update_global_count()
        
        # 分组展开状态缓存: {group_name: bool}
        _group_expanded = {}

        async def toggle_group_expand(group_name: str):
            """切换分组展开/收起状态"""
            _group_expanded[group_name] = not _group_expanded.get(group_name, False)
            await refresh_group_items()

        def on_single_forum_check(e):
            """单个贴吧勾选/取消"""
            fn = e.control.data
            if e.control.value:
                global_selected.add(fn)
            else:
                global_selected.discard(fn)
            update_global_count()
            # 刷新分组级 Checkbox 的 indeterminate 状态
            self.page.run_task(refresh_group_items)

        async def refresh_group_items():
            """刷新分组列表（保留展开状态）"""
            try:
                groups_container.controls.clear()
                if not target_groups:
                    groups_container.controls.append(ft.Text("尚无预设靶场分组", size=11, color="onSurfaceVariant", italic=True))
                    return
                for g in target_groups:
                    is_expanded = _group_expanded.get(g, False)
                    # 获取该分组的贴吧名称用于显示
                    fnames = await self.db.get_target_pools_by_group(g)
                    # 计算该分组选中状态
                    all_selected = all(fn in global_selected for fn in fnames) if fnames else False

                    group_header = ft.Container(
                        content=ft.Row([
                            ft.Checkbox(
                                data=g,
                                value=all_selected,
                                on_change=lambda ev: self.page.run_task(on_group_check, ev),
                                fill_color="primary",
                            ),
                            ft.GestureDetector(
                                content=ft.Text(
                                    f"📂{'▼' if is_expanded else '▶'} {g} ({len(fnames)}个吧, {sum(1 for f in fnames if f in global_selected)}已选)",
                                    size=11,
                                ),
                                on_tap=lambda _, gn=g: self.page.run_task(toggle_group_expand, gn),
                            ),
                        ], spacing=0),
                        border_radius=4,
                        bgcolor=with_opacity(0.08, "primary") if is_expanded else None,
                        padding=ft.padding.only(left=2, top=2, bottom=2),
                    )
                    controls = [group_header]

                    if is_expanded and fnames:
                        # 展开显示所有贴吧名称，每个贴吧带独立 Checkbox + 风险徽标
                        forum_items = []
                        for fn in fnames[:30]:
                            risk = risk_map.get(fn)
                            badge = _risk_badge(risk)
                            badge_text = f"  ({badge})" if badge else ""
                            is_banned_fn = bool(risk and risk["is_banned"])
                            is_risky_fn = _is_risky(risk)
                            forum_items.append(
                                ft.Row([
                                    ft.Checkbox(
                                        value=fn in global_selected,
                                        data=fn,
                                        on_change=on_single_forum_check,
                                        disabled=is_banned_fn,
                                        tooltip="已封禁贴吧，禁止选择" if is_banned_fn else None,
                                        fill_color="orange",
                                        label=f"{'⛔ ' if is_banned_fn else '🔴 ' if is_risky_fn else ''}{fn}{badge_text}",
                                        label_style=ft.TextStyle(
                                            size=10,
                                            color="error" if is_banned_fn or is_risky_fn else None,
                                        ),
                                    ),
                                ], spacing=0)
                            )
                        forum_list = ft.Column(forum_items, spacing=0)
                        if len(fnames) > 30:
                            forum_list.controls.append(ft.Text(f"    ... 还有 {len(fnames)-30} 个", size=10, color="onSurfaceVariant"))
                        controls.append(forum_list)
                    elif is_expanded:
                        controls.append(ft.Text("    (空)", size=10, color="onSurfaceVariant"))

                    groups_container.controls.append(ft.Column(controls, spacing=0))
                try:
                    groups_container.update()
                except Exception:
                    pass
            except Exception:
                pass

        async def render_groups():
            """初始化渲染分组列表"""
            await refresh_group_items()
        
        async def on_global_lock(_):
            # 合并手动输入
            if global_manual_input.value:
                manual_fnames = [f.strip() for f in global_manual_input.value.split(",") if f.strip()]
                for fn in manual_fnames: global_selected.add(fn)
            # [修复] 校验全域轰炸组中的贴吧是否在数据库中有效
            current_local_fnames = {f['fname'] for f in local_forums}
            invalid_global = global_selected - current_local_fnames
            if invalid_global:
                # 手动输入的贴吧可能不在本地库中，不强制移除但给出警告
                self._show_snackbar(f"⚠️ {len(invalid_global)} 个贴吧不在本地吧库中，可能为外部空降目标", "warning")
            self._temp_global_fnames = list(global_selected)
            if self.db:
                self.page.run_task(self.db.set_setting, "last_selected_global_forums", json.dumps(self._temp_global_fnames))
            self._update_forum_select_btn()
            self.page.close(fire_dialog)
            self._show_snackbar(f"🔥 全域轰炸组已锁定 {len(global_selected)} 个目标", "success")
        
        # ===== 构造弹窗 =====
        dialog_title = ft.Row([
            ft.Icon(icons.SETTINGS_SUGGEST, color="blue"),
            ft.Text("配置火力抛射靶场"),
        ], alignment=ft.MainAxisAlignment.START)
        
        tabs = ft.Tabs(
            selected_index=initial_tab,
            tabs=[
                ft.Tab(
                    text="本地自留区",
                    icon=icons.GPS_FIXED,
                    content=ft.Container(
                        content=ft.Column([
                            ft.Row([
                                local_search_field,
                                local_select_all_cb,
                                ft.IconButton(icons.DELETE_SWEEP, icon_color="error", tooltip="删除选中项并同步取消关注", on_click=on_bulk_unfollow_click),
                            ], spacing=5),
                            ft.Container(
                                content=local_container,
                                border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                                border_radius=8, padding=5
                            ),
                            ft.Row([
                                local_count_text,
                                ft.FilledButton(
                                    "锁定本地自留区",
                                    icon=icons.LOCK_ROUNDED,
                                    style=ft.ButtonStyle(bgcolor="primary", color="white"),
                                    on_click=lambda e: self.page.run_task(on_local_lock, e)
                                ),
                            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
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
                            global_manual_input,
                            ft.Divider(height=10, color="transparent"),
                            ft.Text("或者从已录入的靶位组中选取 (Target Pool Groups):", size=11, color="onSurfaceVariant"),
                            ft.Container(
                                content=groups_container,
                                border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                                border_radius=8, padding=5,
                                expand=True,
                            ),
                            ft.Row([
                                global_count_text,
                                ft.FilledButton(
                                    "锁定全域轰炸组",
                                    icon=icons.LOCAL_FIRE_DEPARTMENT_ROUNDED,
                                    style=ft.ButtonStyle(bgcolor="orange", color="white"),
                                    on_click=lambda e: self.page.run_task(on_global_lock, e)
                                ),
                            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ], tight=False),  # 改为 False 允许 Column 填充空间
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
                height=500,
            ),
            actions=[
                ft.TextButton("关闭", on_click=lambda _: self.page.close(fire_dialog)),
            ],
        )
        
        self.page.open(fire_dialog)
        render_local_list()
        self.page.run_task(render_groups)
        # [修复] 延迟显示过滤警告，确保 UI 已挂载后再调用 snackbar
        if _local_warn_count > 0:
            async def _delayed_warn():
                await asyncio.sleep(0.5)
                self._show_snackbar(f"已自动移除 {_local_warn_count} 个已封禁/失效贴吧", "warning")
            self.page.run_task(_delayed_warn)
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
        except Exception:
            pass

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
                    content=ft.ListView([ft.Row([self._material_table], scroll=ft.ScrollMode.ADAPTIVE)], expand=True),
                    expand=True,
                    border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                    border_radius=12,
                    padding=5,
                ),
                # 分页控件
                ft.Row([
                    ft.IconButton(icons.NAVIGATE_BEFORE, icon_size=16, on_click=lambda e: self.page.run_task(self._on_material_prev_page, e), data="mat_prev"),
                    self._mat_page_info,
                    ft.IconButton(icons.NAVIGATE_NEXT, icon_size=16, on_click=lambda e: self.page.run_task(self._on_material_next_page, e), data="mat_next"),
                ], alignment=ft.MainAxisAlignment.CENTER, spacing=10),
            ], expand=True, spacing=10),
            expand=True,
            padding=ft.padding.only(top=10)
        )

    def _init_controls(self):
        """预初始化页面所有持久化控件，防止 build 时被重置"""
        # 0. 分页控件
        self._mat_page_info = ft.Text("1/1 页 (共0条)", size=11, color="onSurfaceVariant")
        self._mat_prev_btn = ft.IconButton(icons.NAVIGATE_BEFORE, icon_size=16, disabled=True)
        self._mat_next_btn = ft.IconButton(icons.NAVIGATE_NEXT, icon_size=16, disabled=True)
        # 状态计数缓存
        self._status_counts = {}

        # 1. 贴吧选择
        self.forum_select_btn = ft.OutlinedButton(
            "点击选择目标贴吧",
            icon=icons.TOUCH_APP_ROUNDED,
            on_click=self._open_forum_dialog,
            style=ft.ButtonStyle(color="onSurfaceVariant"),
        )
        self.local_status_btn = ft.OutlinedButton(
            "🏠 本地自留区: 未选择",
            icon=icons.GPS_FIXED,
            on_click=lambda _: self.page.run_task(self._open_firepower_dialog_tab, 0),
            style=ft.ButtonStyle(color="onSurfaceVariant"),
            visible=False,
        )
        self.global_status_btn = ft.OutlinedButton(
            "🔥 全域轰炸组: 未选择",
            icon=icons.LOCAL_FIRE_DEPARTMENT_ROUNDED,
            on_click=lambda _: self.page.run_task(self._open_firepower_dialog_tab, 1),
            style=ft.ButtonStyle(color="onSurfaceVariant"),
            visible=False,
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

        # 3. 参数配置
        self.post_count = ft.TextField(label="发布总数 (帖)", value="10", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True, tooltip="建议: 总数 ≤ 账号数×3，避免单账号集中发帖")
        self.min_delay = ft.TextField(label="最小延迟 (秒)", value="120", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True, expand=True, tooltip="建议: ≥120秒，避开凌晨1-6点高风险时段")
        self.max_delay = ft.TextField(label="最大延迟 (秒)", value="600", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True, expand=True, tooltip="建议: ≥300秒，降低被检测风险")
        self.use_ai_switch = ft.Switch(
            label="AI改写",
            value=False,
            on_change=lambda e: self.page.run_task(self._auto_save_switch, "use_ai_rewrite", e.control.value)
        )
        self.ai_persona_dropdown = ft.Dropdown(
            label="AI人格化设定",
            value="normal",
            text_size=11,
            dense=True,
            expand=True,
            options=[
                ft.dropdown.Option("normal", "标准 SEO (通用平衡)"),
                ft.dropdown.Option("resource_god", "资源大神 (专业/极简)"),
                ft.dropdown.Option("casual", "随缘路人 (口语/自然)"),
                ft.dropdown.Option("newbie", "好奇萌新 (求助/互动)"),
            ],
            on_change=lambda e: self.page.run_task(self._save_ai_persona, e.control.value)
        )
        self.use_schedule = ft.Switch(
            label="定时计划",
            value=False,
            on_change=lambda e: (self._toggle_schedule(e), self.page.run_task(self._auto_save_switch, "use_schedule", e.control.value))[1]
        )
        # --- 循环调度控件 ---
        self.schedule_type_dropdown = ft.Dropdown(
            label="循环模式",
            value="once",
            visible=False,
            expand=1,
            text_size=12,
            options=[
                ft.dropdown.Option("once", "单次执行"),
                ft.dropdown.Option("daily", "每天定时"),
                ft.dropdown.Option("weekly", "每周定时"),
                ft.dropdown.Option("interval", "自定义间隔"),
            ],
            on_change=self._on_schedule_type_change,
        )
        # 单次模式：完整日期+时间
        self.schedule_time = ft.TextField(
            label="计划时间 (YYYY-MM-DD HH:mm)", 
            value=(datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
            visible=False, text_size=12
        )
        # daily/weekly 模式：仅时分
        self.schedule_time_hm = ft.TextField(
            label="执行时间 (HH:mm)",
            value=datetime.now().strftime("%H:%M"),
            visible=False, text_size=12, width=120,
        )
        self.schedule_day_of_week = ft.Dropdown(
            label="每周几",
            value="0",
            visible=False,
            expand=1,
            text_size=12,
            options=[
                ft.dropdown.Option("0", "周一"),
                ft.dropdown.Option("1", "周二"),
                ft.dropdown.Option("2", "周三"),
                ft.dropdown.Option("3", "周四"),
                ft.dropdown.Option("4", "周五"),
                ft.dropdown.Option("5", "周六"),
                ft.dropdown.Option("6", "周日"),
            ],
        )
        self.interval_hours = ft.TextField(
            label="间隔 (小时, 最小6)", value="6", visible=False, text_size=12,
            input_filter=ft.NumbersOnlyInputFilter(),
            tooltip="建议: ≥6小时，避免频繁触发导致封号",
            width=120,
        )
        self.reset_strategy_dropdown = ft.Dropdown(
            label="物料轮转",
            value="new_only",
            visible=False,
            expand=1,
            text_size=12,
            options=[
                ft.dropdown.Option("new_only", "仅新物料 (安全)"),
                ft.dropdown.Option("reuse", "重置复用"),
            ],
            tooltip="循环轮次开始前如何处理已用完的物料",
        )
        
        # 4. 账号与策略
        self.strategy_dropdown = ft.Dropdown(
            label="账号调度策略", value="round_robin", text_size=12,
            options=[
                ft.dropdown.Option("round_robin", "轮询 (Round-Robin)"),
                ft.dropdown.Option("strict_round_robin", "严格轮询 (Strict RR)"),
                ft.dropdown.Option("random", "随机 (Random)")
            ]
        )
        self.pairing_mode_dropdown = ft.Dropdown(
            label="文案提取模式", value="random", text_size=12,
            options=[ft.dropdown.Option("random", "随机混用 (防抽混淆)"), ft.dropdown.Option("strict", "严格配对 (发多资源)")]
        )
        # 纵向堆叠并横向拉伸，避免窄栏内互相挤压截断（expand 在 Column 里是纵向拉伸，不能用）
        self._strategy_row = ft.Column(
            [self.strategy_dropdown, self.pairing_mode_dropdown],
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        
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
            label="矩阵协同",
            value=False,
        )
        self.bump_ai_content_switch = ft.Switch(
            label="AI自顶",
            value=True,
            tooltip="开启后使用AI生成差异化的自顶回复，关闭则使用固定模板",
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
        self.dry_run_btn = ft.OutlinedButton(
            "干跑预检",
            icon=icons.CHECK_CIRCLE_ROUNDED,
            on_click=self._on_dry_run_click,
            tooltip="不发帖，只验证账号/代理/目标贴吧/物料可用性",
        )
        
        # 5. 状态与进度（即时执行监视；完整流水视图在发帖运行中心页）
        self.progress_bar = ft.ProgressBar(value=0, visible=False, color="primary")

        # 6. 物料视图 + 执行监视区（原底部 Tabs 的运行区三视图已迁往运行中心）
        self.live_monitor = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icons.STREAM_ROUNDED, size=14, color="primary"),
                    ft.Text("执行监视", size=12, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.TextButton("运行中心", icon=icons.MONITOR_HEART_ROUNDED, on_click=lambda e: self._navigate("batch_post_center"), tooltip="任务队列/已发归档/完整运行日志"),
                ], spacing=6),
                self.progress_bar,
                ft.Container(
                    content=self._log_stream.log_list,
                    expand=True,
                    border=ft.border.all(1, with_opacity(0.1, "onSurface")), border_radius=10,
                ),
            ], spacing=6),
            height=260,
            padding=10,
            bgcolor=with_opacity(0.03, "surface"),
            border_radius=10,
        )

        header = ft.Row([
            ft.IconButton(icons.ARROW_BACK_IOS_NEW, on_click=lambda e: self._navigate("dashboard")),
            ft.Column([
                ft.Text("矩阵发帖终端 / MATRIX POST TERMINAL", size=20, weight=ft.FontWeight.BOLD, color="primary"),
                ft.Text("多账号轮换、多内容池混淆及多贴吧矩阵发布引擎", size=11, color="onSurfaceVariant"),
            ], spacing=0),
        ])

        # --- 四步向导布局：选择账号与目标 → 准备物料 → 策略与排期 → 确认发射 ---
        # 步骤 1：账号池 + 目标贴吧选择
        step_accounts = ft.Column([
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
        ], expand=1, spacing=12)
        step_forums = ft.Column([
            ft.Text("目标贴吧 / TARGET FORUMS (必填)", size=12, color="onSurfaceVariant", weight=ft.FontWeight.BOLD),
            self.forum_select_btn,
            ft.Row([self.local_status_btn, self.global_status_btn], spacing=8, wrap=True),
        ], expand=1, spacing=12)
        self._wizard_step_views = [
            ft.Row([step_accounts, step_forums], expand=True,
                   vertical_alignment=ft.CrossAxisAlignment.START, spacing=15),
            ft.Column([
                ft.Row([
                    ft.Text("全域指令集", size=14, weight=ft.FontWeight.W_500),
                    ft.Row([
                        ft.IconButton(icons.ADD_LINK, tooltip="从短链库选取注入物料池", on_click=self._open_shortlink_dialog, icon_color="primary"),
                        ft.IconButton(icons.SYNC_ROUNDED, tooltip="同步云端短码到本地库", on_click=self._sync_shortlinks, icon_color="onSurfaceVariant"),
                        ft.IconButton(icons.UPLOAD_FILE, tooltip="本地载入文件", on_click=lambda _: self._file_picker.pick_files(allow_multiple=False), icon_color="onSurfaceVariant", visible=not getattr(self.page, "web", False)),
                        ft.IconButton(icons.CONTENT_PASTE, tooltip="批量粘贴导入", on_click=self._open_batch_paste_dialog, icon_color="secondary"),
                        ft.IconButton(icons.DELETE_SWEEP, tooltip="摧毁总计划（清空物料池）", on_click=self._clear_all_materials, icon_color="error"),
                    ], spacing=0),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self._build_material_view(),
            ], expand=True, spacing=12),
            ft.Column([
                ft.Container(
                    content=ft.Column([
                        self._strategy_row,
                        ft.Divider(height=5, color="transparent"),
                        ft.Text("自顶增强配置", size=12, weight=ft.FontWeight.W_500, color="onSurfaceVariant"),
                        ft.Row([self.bump_max_count_field, self.bump_cooldown_field], spacing=10),
                        ft.Row([self.bump_matrix_switch, self.bump_ai_content_switch], spacing=5),
                        ft.Divider(height=5, color="transparent"),
                        ft.Text("自顶模式选择", size=12, weight=ft.FontWeight.W_500, color="onSurfaceVariant"),
                        self.bump_mode_group,
                        self.bump_loop_container,  # 矩阵轮换配置区
                        self.bump_config_save_btn,
                    ], spacing=10),
                    padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                ),
                ft.Container(
                    content=ft.Column([
                        self.post_count,
                        ft.Row([self.use_ai_switch, self.ai_persona_dropdown], spacing=10),
                        ft.Row([self.use_schedule], spacing=10),
                        ft.Row([self.schedule_type_dropdown, self.reset_strategy_dropdown], spacing=10),
                        self.schedule_time,
                        ft.Row([self.schedule_time_hm, self.schedule_day_of_week], spacing=10),
                        self.interval_hours,
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
                    ], spacing=10),
                    padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                ),
            ], expand=True, spacing=12, scroll=ft.ScrollMode.ADAPTIVE),
            ft.Column([
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Icon(icons.CHECK_CIRCLE_ROUNDED, color="green", size=16),
                            ft.Text("启动前将自动执行干跑预检，并弹出任务摘要供确认（账号/贴吧/物料/预计发布/耗时/风险评分）",
                                    size=12, color="onSurfaceVariant", expand=True),
                        ], spacing=6),
                        ft.Row([self.start_btn, self.dry_run_btn], spacing=10),
                    ], spacing=10),
                    padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                ),
            ], expand=True, spacing=12),
        ]

        # 自绘步骤条（规避 ft.Stepper 测试桩兼容问题）
        self._current_wizard_step = 1
        self._wizard_step_labels = ["选择账号与目标", "准备物料", "策略与排期", "确认发射"]
        self._wizard_step_chips = []
        for idx, label in enumerate(self._wizard_step_labels, start=1):
            badge = ft.Container(
                content=ft.Text(str(idx), size=11, weight=ft.FontWeight.BOLD, color="white"),
                width=22, height=22, border_radius=11,
                alignment=ft.alignment.center, bgcolor="primary",
            )
            chip = ft.Container(
                content=ft.Row([badge, ft.Text(label, size=12)], spacing=6, tight=True),
                padding=ft.padding.symmetric(horizontal=10, vertical=6),
                border_radius=10,
                on_click=lambda e, n=idx: self._switch_wizard_step(n),
            )
            self._wizard_step_chips.append(chip)
        self._wizard_stepper = ft.Row(
            self._wizard_step_chips, spacing=8, wrap=True,
            alignment=ft.MainAxisAlignment.START,
        )

        self._wizard_prev_btn = ft.OutlinedButton("上一步", icon=icons.ARROW_BACK_IOS_NEW, on_click=lambda e: self._switch_wizard_step(self._current_wizard_step - 1))
        self._wizard_next_btn = ft.ElevatedButton("下一步", icon=icons.ARROW_FORWARD, on_click=lambda e: self._switch_wizard_step(self._current_wizard_step + 1), style=ft.ButtonStyle(color="white", bgcolor="primary"))
        self._wizard_content = ft.Container(content=self._wizard_step_views[0], expand=True)
        self._apply_wizard_step_styles()

        # --- 封装最终布局界面并预存 ---
        self.main_layout = ft.Container(
            content=ft.Column([
                header,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                ft.Row([
                    self._wizard_stepper,
                    ft.Container(expand=True),
                    self._wizard_prev_btn,
                    self._wizard_next_btn,
                ], spacing=10),
                self._wizard_content,
                self.live_monitor,
            ], expand=True, spacing=12),
            padding=ft.padding.only(left=20, right=20, top=10, bottom=20), expand=True,
        )

    def _apply_wizard_step_styles(self):
        """根据当前步骤刷新步骤条与导航按钮状态"""
        current = self._current_wizard_step
        for idx, chip in enumerate(self._wizard_step_chips, start=1):
            try:
                is_done = idx < current
                is_active = idx == current
                chip.bgcolor = with_opacity(0.12, "primary") if is_active else (with_opacity(0.06, "green") if is_done else with_opacity(0.05, "onSurface"))
                badge = chip.content.controls[0]
                badge.bgcolor = "primary" if is_active else ("green" if is_done else with_opacity(0.3, "onSurface"))
                label = chip.content.controls[1]
                label.weight = ft.FontWeight.BOLD if is_active else ft.FontWeight.W_500
                label.color = "primary" if is_active else "onSurfaceVariant"
            except (AttributeError, IndexError, TypeError):
                continue  # 测试桩环境下控件树不完整，跳过样式细节
        self._wizard_prev_btn.visible = current > 1
        self._wizard_next_btn.visible = current < len(self._wizard_step_views)

    def _switch_wizard_step(self, step: int):
        """切换向导步骤；向前越过步骤 1 时校验账号与目标贴吧已就绪"""
        if step == self._current_wizard_step:
            return
        step = max(1, min(len(self._wizard_step_views), step))
        if step > 1 and not (self._temp_local_fnames or self._temp_global_fnames):
            self._show_snackbar("请先锁定目标贴吧（本地自留区或全域轰炸组至少一组）", "warning")
            return
        if step > 1 and not self._selected_account_ids:
            self._show_snackbar("请先勾选至少一个执行账号", "warning")
            return
        self._current_wizard_step = step
        self._wizard_content.content = self._wizard_step_views[step - 1]
        self._apply_wizard_step_styles()
        try:
            self.page.update()
        except Exception:
            pass

    def build(self) -> ft.Control:
        if self._file_picker not in self.page.overlay:
            self.page.overlay.append(self._file_picker)
        return self.main_layout

    async def _auto_save_switch(self, key: str, value: bool):
        """自动保存开关状态"""
        await self.db.set_setting(key, "1" if value else "0")

    async def _save_ai_persona(self, persona: str):
        """保存AI人格化设定"""
        await self.db.set_setting("ai_persona", persona)

    def _toggle_schedule(self, e):
        visible = e.control.value
        self.schedule_type_dropdown.visible = visible
        # 根据循环模式决定具体时间控件和物料轮转的可见性
        if visible:
            self._update_schedule_visibility(self.schedule_type_dropdown.value or "once")
        else:
            self.schedule_time.visible = False
            self.schedule_time_hm.visible = False
            self.schedule_day_of_week.visible = False
            self.interval_hours.visible = False
            self.reset_strategy_dropdown.visible = False
        self.page.update()

    def _on_schedule_type_change(self, e):
        """循环模式切换时，更新时间输入控件和物料轮转可见性"""
        self._update_schedule_visibility(e.control.value)
        self.page.update()

    def _update_schedule_visibility(self, schedule_type: str):
        """根据循环模式统一控制所有调度相关控件的可见性"""
        # 时间输入：once→完整日期时间, daily/weekly→仅时分, interval→隐藏
        self.schedule_time.visible = (schedule_type == "once")
        self.schedule_time_hm.visible = (schedule_type in ("daily", "weekly"))
        self.schedule_day_of_week.visible = (schedule_type == "weekly")
        self.interval_hours.visible = (schedule_type == "interval")
        # 物料轮转：仅循环模式显示
        self.reset_strategy_dropdown.visible = (schedule_type != "once")

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
                    except Exception:
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

            if not pairs:
                self._show_snackbar("文件内容为空或格式不匹配，未导入任何数据", "warning")
                return

            final_pairs = await self._resolve_import_pairs(pairs)
            if final_pairs is None:
                return  # 用户取消
            if not final_pairs:
                self._show_snackbar("没有可导入的有效内容", "warning")
                return
            added_count = await self.db.add_materials_bulk(final_pairs)

            await self._refresh_material_table()
            self._show_snackbar(f"成功导入 {added_count} 条文案物料", "success")
        except Exception as ex:
            from ...core.logger import log_error
            await log_error(f"文件导入失败: {ex}")
            self._show_snackbar(f"文件解析失败: {str(ex)}", "error")

    def _collect_launch_config(self) -> LaunchConfig:
        """从 UI 控件收集结构化任务配置（唯一读取点）。

        解析失败抛 LaunchConfigError，由调用方转 Snackbar——
        文案与既有拦截提示保持一致。
        """
        try:
            if self.use_schedule.value:
                schedule_type = self.schedule_type_dropdown.value or "once"
                reset_strategy = self.reset_strategy_dropdown.value or "new_only"
                now = datetime.now()
                if schedule_type == "once":
                    st = datetime.strptime(self.schedule_time.value, "%Y-%m-%d %H:%M")
                elif schedule_type in ("daily", "weekly"):
                    hm = datetime.strptime(self.schedule_time_hm.value, "%H:%M")
                    st = now.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
                    if st <= now:
                        st += timedelta(days=1 if schedule_type == "daily" else 7)
                else:  # interval 及未知类型：立即开始（或1小时后）
                    st = now + timedelta(hours=1)
            else:
                schedule_type = "once"
                reset_strategy = "new_only"
                st = None
            return LaunchConfig(
                account_ids=sorted(self._selected_account_ids),
                local_fnames=list(self._temp_local_fnames),
                global_fnames=list(self._temp_global_fnames),
                strategy=self.strategy_dropdown.value,
                pairing_mode=self.pairing_mode_dropdown.value,
                post_count=int(self.post_count.value),
                delay_min=float(self.min_delay.value),
                delay_max=float(self.max_delay.value),
                use_ai=self.use_ai_switch.value,
                ai_persona=self.ai_persona_dropdown.value or "normal",
                use_schedule=self.use_schedule.value,
                schedule_type=schedule_type,
                schedule_time=st,
                interval_hours=int(self.interval_hours.value) if self.interval_hours.value and schedule_type == "interval" else 0,
                schedule_day_of_week=int(self.schedule_day_of_week.value) if schedule_type == "weekly" else None,
                reset_strategy=reset_strategy,
            )
        except LaunchConfigError:
            raise
        except Exception as ex:
            raise LaunchConfigError(f"定时解析失败: {str(ex)}") from ex

    async def _collect_and_preflight(self):
        """收集配置并执行预检。返回 (config, report)；配置异常时弹提示并返回 None。"""
        try:
            config = self._collect_launch_config()
        except LaunchConfigError as ex:
            self._show_snackbar(str(ex), "error")
            return None
        if not config.account_ids:
            self._show_snackbar("请在中间栏至少勾选一个执行账号", "error")
            return None
        report = await PreflightService(self.db).run(config)
        return config, report

    def _build_summary_issue_row(self, issue: PreflightIssue):
        icon_map = {
            "error": (icons.ERROR_ROUNDED, "error"),
            "warning": (icons.WARNING_AMBER_ROUNDED, "orange"),
            "info": (icons.INFO_OUTLINED, "primary"),
        }
        icon_name, color = icon_map.get(issue.level, (icons.INFO_OUTLINED, "primary"))
        return ft.Row([
            ft.Icon(name=icon_name, color=color, size=16),
            ft.Container(expand=True, content=ft.Text(issue.message, size=11, selectable=True)),
        ], spacing=6)

    def _build_summary_content(self, report: PreflightReport, dry: bool) -> ft.Control:
        s = report.stats
        est_min, est_max = s.get("estimated_duration", ("—", "—"))
        mult_note = "（凌晨双倍延迟已启用）" if s.get("delay_multiplier", 1) > 1 else ""

        if s.get("mode") == "scheduled":
            type_labels = {"once": "单次定时", "daily": "每天", "weekly": "每周", "interval": "循环间隔"}
            mode_line = f"{type_labels.get(s.get('schedule_type', 'once'), '定时')} · {s.get('schedule_time', '')}"
        else:
            mode_line = "立即执行"

        score = report.risk_score
        score_color = "error" if score >= 6 else ("orange" if score >= 3 else COLORS.GREEN)
        risk_line = ft.Row([
            ft.Text("任务风险评分", size=12, color="onSurfaceVariant"),
            ft.Container(
                content=ft.Text(f"{score:g} / 10", size=12, weight=ft.FontWeight.BOLD, color="white"),
                bgcolor=score_color, border_radius=10, padding=ft.padding.only(left=10, right=10, top=2, bottom=2),
            ),
        ], spacing=8)

        stat_rows = [
            ft.Text(mode_line, size=12, weight=ft.FontWeight.BOLD),
            ft.Text(
                f"账号 {s.get('accounts_selected', 0)} 个（有效 {s.get('accounts_effective', 0)}） · "
                f"贴吧 {len(report.effective_fnames)} 个 · "
                f"待发物料 {s.get('materials_pending', 0)} 条 · "
                f"预计发布 {s.get('planned_posts', 0)} 帖",
                size=12,
            ),
            ft.Text(f"预计耗时 {est_min} ~ {est_max}{mult_note}", size=11, color="onSurfaceVariant"),
        ]
        if report.risk_factors:
            stat_rows.append(ft.Text("风险构成: " + "；".join(report.risk_factors),
                                     size=10, color="onSurfaceVariant", selectable=True))

        issue_rows = [self._build_summary_issue_row(i) for i in report.warnings + report.infos]
        if not report.warnings and not report.infos:
            issue_rows.append(ft.Row([
                ft.Icon(name=icons.CHECK_CIRCLE_ROUNDED, color=COLORS.GREEN, size=16),
                ft.Text("预检通过：账号、代理、目标贴吧、物料均可用", size=11),
            ], spacing=6))

        return ft.Container(
            content=ft.Column([
                risk_line,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                *stat_rows,
                ft.Divider(height=1, color=with_opacity(0.1, "onSurface")),
                ft.Column(issue_rows, spacing=6, scroll=ft.ScrollMode.ADAPTIVE, height=200 if len(issue_rows) > 3 else None),
            ], spacing=8, tight=True),
            width=520,
        )

    async def _show_launch_summary_dialog(self, report: PreflightReport, dry: bool = False) -> bool:
        """展示启动摘要/预检报告。dry=True 仅展示（干跑/错误查看），否则等待确认。返回是否确认。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def _resolve(value: bool):
            if not future.done():
                future.set_result(value)
            try:
                self.page.close(dialog)
            except Exception:
                pass

        has_errors = bool(report.errors)
        title = "干跑预检报告" if dry else ("发射拦截" if has_errors else "确认发射")
        if has_errors:
            # 错误视图：只列拦截项
            content = ft.Container(
                content=ft.Column(
                    [self._build_summary_issue_row(i) for i in report.errors],
                    spacing=6, tight=True),
                width=520,
            )
            actions = [ft.TextButton("返回", on_click=lambda _: _resolve(False))]
        else:
            content = self._build_summary_content(report, dry)
            if dry:
                actions = [ft.TextButton("关闭", on_click=lambda _: _resolve(False))]
            else:
                actions = [
                    ft.TextButton("返回调整", on_click=lambda _: _resolve(False)),
                    ft.FilledButton(
                        "确认发射",
                        icon=icons.ROCKET_LAUNCH,
                        style=ft.ButtonStyle(bgcolor="primary", color="white"),
                        on_click=lambda _: _resolve(True),
                    ),
                ]

        dialog = ft.AlertDialog(modal=True, title=ft.Text(title), content=content, actions=actions)
        self.page.open(dialog)
        return await future

    async def _on_dry_run_click(self, e):
        """干跑预检：不发帖，只验证账号/代理/物料/目标贴吧可用性。"""
        collected = await self._collect_and_preflight()
        if collected is None:
            return
        config, report = collected
        await self._show_launch_summary_dialog(report, dry=True)

    async def _on_start_click(self, e):
        if self._is_running:
            self._is_running = False
            self.start_btn.text = "制定矩阵任务"
            self.start_btn.icon = icons.PLAY_CIRCLE_FILL_ROUNDED
            self.page.update()
            return

        # [预检闭环] 收集配置 → 干跑预检 → 摘要确认后才真正启动
        collected = await self._collect_and_preflight()
        if collected is None:
            return
        config, report = collected
        if report.errors:
            await self._show_launch_summary_dialog(report, dry=True)
            return
        if not await self._show_launch_summary_dialog(report):
            return

        # 使用预检后的有效目标（已剔除封禁/失效贴吧）
        fnames = report.effective_fnames
        selected_accounts = config.account_ids
        pairing_mode = config.pairing_mode
        strategy = config.strategy

        if config.use_schedule:
            try:
                schedule_type = config.schedule_type
                st = config.schedule_time

                new_task = await self.db.add_batch_task(
                    fname=fnames[0], # 保留以作向下兼容
                    fnames_json=json.dumps(fnames, ensure_ascii=False),
                    titles_json="[]",
                    contents_json="[]",
                    accounts_json=json.dumps(selected_accounts, ensure_ascii=False),
                    strategy=strategy,
                    pairing_mode=pairing_mode,
                    total=config.post_count,
                    delay_min=config.delay_min,
                    delay_max=config.delay_max,
                    use_ai=config.use_ai,
                    ai_persona=config.ai_persona,
                    schedule_type=schedule_type,
                    interval_hours=config.interval_hours if schedule_type == "interval" else 0,
                    schedule_day_of_week=config.schedule_day_of_week if schedule_type == "weekly" else None,
                    reset_strategy=config.reset_strategy if schedule_type != "once" else "new_only",
                    schedule_time=st,
                    status="pending"
                )
                # [精确调度] once 类型任务注册 APScheduler date 触发器，精确到分钟执行
                if schedule_type == "once":
                    try:
                        from ...core.daemon import daemon_instance
                        daemon_instance.schedule_once_task(str(new_task.id), st)
                    except Exception as _sched_err:
                        from ...core.logger import log_warn
                        await log_warn(f"once 精度调度注册失败（将由 30min 轮询兜底）: {_sched_err}")
                # 生成提示
                type_labels = {"once": "单次", "daily": "每天", "weekly": "每周", "interval": f"每{config.interval_hours}小时"}
                self._show_snackbar(f"{type_labels.get(schedule_type, '')}矩阵任务已加入全域队列", "success")
                await self.load_data()

                # --- 自动步进优化：将界面时间向后推移 ---
                if schedule_type == "daily":
                    next_st = st + timedelta(days=1)
                    self.schedule_time_hm.value = next_st.strftime("%H:%M")
                elif schedule_type == "weekly":
                    next_st = st + timedelta(weeks=1)
                    self.schedule_time_hm.value = next_st.strftime("%H:%M")
                elif schedule_type == "interval":
                    step_hours = config.interval_hours if config.interval_hours and config.interval_hours > 0 else 6
                    next_st = st + timedelta(hours=step_hours)
                else:
                    next_st = st + timedelta(hours=1)
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
            accounts=selected_accounts,
            strategy=strategy,
            total=config.post_count,
            delay_min=config.delay_min,
            delay_max=config.delay_max,
            use_ai=config.use_ai,
            ai_persona=config.ai_persona,
            pairing_mode=pairing_mode
        )

        # 持久化即时任务到数据库，使 UI 刷新后任务队列可见
        db_task_id = None
        try:
            db_task = await self.db.add_batch_task(
                fname=fnames[0],
                fnames_json=json.dumps(fnames, ensure_ascii=False),
                titles_json="[]",
                contents_json="[]",
                accounts_json=json.dumps(selected_accounts, ensure_ascii=False),
                strategy=strategy,
                pairing_mode=pairing_mode,
                total=config.post_count,
                delay_min=config.delay_min,
                delay_max=config.delay_max,
                use_ai=config.use_ai,
                ai_persona=config.ai_persona,
                schedule_type="once",
                status="running"
            )
            db_task_id = db_task.id
            await self.load_data()
        except Exception:
            pass  # 持久化失败不阻塞执行

        try:
            async for update in self.manager.execute_task(task):
                if not self._is_running:
                    self._add_log("！任务已被人工干预中止")
                    # 更新数据库状态为 stopped
                    if db_task_id:
                        try:
                            await self.db.update_batch_task(db_task_id, status="stopped", progress=task.progress)
                        except Exception:
                            pass
                    break

                if update["status"] == "success":
                    self._add_log(update) # 直接传入字典以进行结构化渲染
                    total = update.get("total") or 0
                    self.progress_bar.value = (update["progress"] / total) if total > 0 else 0
                elif update["status"] == "error":
                    self._add_log(update, "error")
                elif update["status"] == "skipped":
                    self._add_log(update)

                try:
                    self.log_list.update()
                    self.progress_bar.update()
                except Exception:
                    pass
            else:
                # 循环正常结束（未 break），更新数据库状态为 completed
                if db_task_id:
                    try:
                        await self.db.update_batch_task(db_task_id, status="completed", progress=task.progress)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            self._add_log("！任务已被系统强制回收")
            if db_task_id:
                try:
                    await self.db.update_batch_task(db_task_id, status="failed", progress=task.progress)
                except Exception:
                    pass
        except Exception as ex:
            self._add_log(f"CRITICAL ERROR: {str(ex)}", "error")
            if db_task_id:
                try:
                    await self.db.update_batch_task(db_task_id, status="failed", progress=task.progress)
                except Exception:
                    pass
        finally:
            self._is_running = False
            self.start_btn.text = "制定矩阵任务"
            self.start_btn.icon = icons.PLAY_CIRCLE_FILL_ROUNDED
            self.start_btn.style = ft.ButtonStyle(color="white", bgcolor="primary")
            self.progress_bar.visible = False
            self.start_btn.update()
            self.progress_bar.update()
            # 刷新任务列表以反映最终状态
            self._refresh_task_list()

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        from ..components.toast import show_toast
        show_toast(self.page, message, type)
