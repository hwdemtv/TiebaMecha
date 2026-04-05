"""Batch posting page with Cyber-Mecha aesthetic and progress monitor"""

import flet as ft
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
        self.connector = SmartLinkConnector(db)
        self._file_picker = ft.FilePicker(on_result=self._on_file_result)
        
        # 搜索与批量选择状态
        self._material_search_text = ""
        self._archive_search_text = ""
        self._selected_material_ids = set()
        self._selected_archive_ids = set()

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
            
            self._refresh_task_list()
            self._refresh_account_pool()
            self._refresh_forum_pool()
            await self._refresh_material_table()
        except Exception as e:
            from ..core.logger import log_error
            await log_error(f"[UI ERROR] load_data failed: {e}")
            self._show_snackbar(f"数据同步异常: {str(e)}", "error")

    def _refresh_task_list(self):
        if hasattr(self, "task_table"):
            self.task_table.rows = [self._build_task_row(t) for t in self._tasks]
            self.page.update()

    def _refresh_account_pool(self):
        """刷新账号池选择器 UI"""
        if hasattr(self, "account_pool_column"):
            items = []
            for acc in self._accounts:
                is_suspended = (acc.status == "suspended_proxy")
                
                # 状态标识
                proxy_label = "🔴 代理失效" if is_suspended else ("🟢 代理正常" if acc.proxy_id else "🟡 裸连警告")
                weight_dots = "●" * (acc.post_weight // 2) + "○" * (5 - acc.post_weight // 2)
                
                # 获取显示名称，增加针对空名称的容错回退
                display_name = acc.name or acc.user_name or f"账号-{acc.id}"
                
                # Checkbox
                cb = ft.Checkbox(
                    label=f"{display_name} ({proxy_label}) [权重: {weight_dots}]",
                    value=not is_suspended,
                    data=acc.id,
                    fill_color=ft.colors.ERROR if is_suspended else ft.colors.PRIMARY
                )
                items.append(cb)
                
            self.account_pool_column.controls = items
            self.page.update()

    def _refresh_forum_pool(self):
        """刷新贴吧池选择器 UI（用于兼容的本地吧列表）"""
        if hasattr(self, "forum_pool_column"):
            items = []
            for fname in self._native_forums:
                cb = ft.Checkbox(
                    label=fname,
                    value=False,
                    data=fname,
                    fill_color="green"  # 标记为安全的本土吧
                )
                items.append(cb)
            
            self.forum_pool_column.controls = items
            
            # 同时也刷新靶标组
            if hasattr(self, "global_group_column"):
                g_items = []
                for g in self._target_groups:
                    cb = ft.Checkbox(
                        label=g,
                        value=False,
                        data=g,
                        fill_color="red" # 标记为轰炸大池
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
        container.update()

    def _toggle_select_all_forums(self, e):
        """全选/取消贴吧 (保留兼容旧版逻辑)"""
        self._toggle_select_all(self.forum_pool_column, e.control.value)

    def _open_forum_dialog(self, e):
        """打开新型双轨矩阵靶场选择器"""
        if not hasattr(self, "global_group_column"):
            self.global_group_column = ft.Column(spacing=2, height=120, scroll=ft.ScrollMode.ADAPTIVE)
            self._refresh_forum_pool()

        def confirm_selection(e):
            num_native = sum(1 for cb in self.forum_pool_column.controls if isinstance(cb, ft.Checkbox) and cb.value)
            num_global = sum(1 for cb in self.global_group_column.controls if isinstance(cb, ft.Checkbox) and cb.value)
            
            if num_native > 0 and num_global > 0:
                self.forum_select_btn.text = f"混合火力: {num_native}个自留吧 + {num_global}个靶场组"
            elif num_native > 0:
                self.forum_select_btn.text = f"精确定向: {num_native}个自留吧"
            elif num_global > 0:
                self.forum_select_btn.text = f"全域轰炸: {num_global}个靶场组"
            else:
                self.forum_select_btn.text = "点击配置火力抛射矩阵"
                
            self.forum_select_btn.style = ft.ButtonStyle(
                color="white" if (num_native > 0 or num_global > 0) else "onSurfaceVariant",
                bgcolor="error" if num_global > 0 else ("primary" if num_native > 0 else None)
            )
            self.page.close(self.forum_dialog)
            self.page.update()

        self.forum_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(icons.RADAR_ROUNDED, color="red"), ft.Text("配置火力抛射靶场")]),
            content=ft.Container(
                content=ft.Tabs(
                    selected_index=0,
                    animation_duration=200,
                    tabs=[
                        ft.Tab(
                            text="🟢 本地自留区",
                            icon=icons.SHIELD_ROUNDED,
                            content=ft.Column([
                                ft.Row([
                                    ft.TextField(
                                        hint_text="过滤本地吧...",
                                        prefix_icon=icons.SEARCH,
                                        height=35, text_size=11, expand=True,
                                        on_change=lambda e: self._filter_checkboxes(self.forum_pool_column, e.control.value)
                                    ),
                                    ft.Checkbox(label="全选", on_change=lambda e: self._toggle_select_all(self.forum_pool_column, e.control.value)),
                                    ft.IconButton(icons.SETTINGS, on_click=self._open_native_forum_config, icon_color="green", tooltip="配置安全本营")
                                ], spacing=5),
                                ft.Container(
                                    content=self.forum_pool_column if self._native_forums else ft.Container(content=ft.Text("尚无任何贴吧被赋予原生保护权限\n请点击右上方按钮开启防线", color="onSurfaceVariant", text_align="center", size=11), alignment=ft.alignment.center, height=180),
                                    height=180, border=ft.border.all(1, with_opacity(0.1, "green")),
                                    border_radius=8, padding=10,
                                ),
                            ], tight=True, spacing=5),
                        ),
                        ft.Tab(
                            text="🔥 全域轰炸组",
                            icon=icons.LOCAL_FIRE_DEPARTMENT_ROUNDED,
                            content=ft.Column([
                                ft.Row([
                                    ft.TextField(
                                        hint_text="过滤靶场组...",
                                        prefix_icon=icons.SEARCH,
                                        height=35, text_size=11, expand=True,
                                        on_change=lambda e: self._filter_checkboxes(self.global_group_column, e.control.value)
                                    ),
                                    ft.Checkbox(label="全选", on_change=lambda e: self._toggle_select_all(self.global_group_column, e.control.value)),
                                    ft.IconButton(icons.ADD_BOX, on_click=self._open_add_target_pool_dialog, icon_color="error", tooltip="录入新靶群")
                                ], spacing=5),
                                ft.Container(
                                    content=self.global_group_column,
                                    height=180, border=ft.border.all(1, with_opacity(0.1, "error")),
                                    border_radius=8, padding=10,
                                ),
                            ], tight=True, spacing=5),
                        )
                    ],
                ),
                width=450,
                height=300
            ),
            actions=[
                ft.TextButton("关闭", on_click=lambda _: self.page.close(self.forum_dialog)),
                ft.FilledButton("锁定发射坐标", icon=icons.CHECK, on_click=confirm_selection),
            ],
        )
        self.page.open(self.forum_dialog)

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
            if self.forum_dialog.open:
                self.page.close(self.forum_dialog)

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

    async def _bulk_toggle_auto_bump(self, e):
        """批量开关自动回帖监控"""
        if not self._selected_material_ids: return
        
        # 以第一个选中的状态取反作为目标
        target_val = True
        first_id = list(self._selected_material_ids)[0]
        for m in self._materials:
            if m.id == first_id:
                target_val = not m.is_auto_bump
                break
        
        from sqlalchemy import update
        from ..db.models import MaterialPool
        async with self.db.async_session() as session:
            await session.execute(
                update(MaterialPool)
                .where(MaterialPool.id.in_(list(self._selected_material_ids)))
                .values(is_auto_bump=target_val)
            )
            await session.commit()
            
        # 同步内存
        for m in self._materials:
            if m.id in self._selected_material_ids:
                m.is_auto_bump = target_val
        
        num = len(self._selected_material_ids)
        self._selected_material_ids.clear()
        await self._refresh_material_table()
        self._show_snackbar(f"成功批量{'开启' if target_val else '关闭'} {num} 条物料的监控", "success")

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

    async def _refresh_material_table(self):
        if not hasattr(self, "_material_table") or not hasattr(self, "_archive_table"):
            return
            
        pending_rows = []
        archive_rows = []
        
        pending = sum(1 for m in self._materials if m.status == "pending")
        success = sum(1 for m in self._materials if m.status == "success")
        failed = sum(1 for m in self._materials if m.status == "failed")
        
        if hasattr(self, "_stats_text"):
            self._stats_text.value = f"状态分布:  ⏳待发({pending})   ✅成功({success})   ❌失败({failed})"
            
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
                                ft.DataCell(ft.Row([ft.Icon(status_icon, color=status_color, size=14), ft.Text(status_text, color=status_color, size=12)], spacing=4)),
                                ft.DataCell(ft.Row([
                                    ft.Text(ai_text, color=ai_color, size=12),
                                    ft.IconButton(icons.VISIBILITY, icon_size=16, icon_color="primary", data=m, on_click=self._on_preview_ai_click, visible=(m.ai_status=="rewritten"))
                                ], spacing=2)),
                                ft.DataCell(ft.Row([
                                    ft.IconButton(icons.EDIT, icon_color="blue", data=m, on_click=self._on_edit_material_click, tooltip="手动微调文案"),
                                    ft.IconButton(icons.AUTO_AWESOME, icon_color="primary", data=m.id, on_click=self._on_single_ai_rewrite_click, tooltip="触发AI改写"),
                                    ft.IconButton(icons.DELETE, icon_color="error", data=m.id, on_click=self._delete_material_row, tooltip="永久销毁该行"),
                                ], spacing=0)),
                                ft.DataCell(ft.Switch(value=m.is_auto_bump, data=m.id, on_change=self._on_material_toggle_bump, scale=0.8)),
                            ]
                        )
                    )
                else:
                    # 已发归档逻辑
                    archive_rows.append(
                        ft.DataRow(
                            selected=m.id in self._selected_archive_ids,
                            on_select_changed=lambda e, mid=m.id: self.page.run_task(self._on_archive_row_select, mid, e.data),
                            cells=[
                                ft.DataCell(ft.Text(str(m.id))),
                                ft.DataCell(ft.Text(display_t, tooltip=display_t)),
                                ft.DataCell(ft.Text(m_posted_fname, weight=ft.FontWeight.BOLD, color="primary")),
                                ft.DataCell(ft.Text(m.last_used_at.strftime("%y-%m-%d %H:%M") if m.last_used_at else "-")),
                                ft.DataCell(ft.Row([
                                    ft.IconButton(
                                        icons.OPEN_IN_NEW, icon_color="primary", tooltip="在外部浏览器查看原贴",
                                        on_click=lambda e, tid=m.posted_tid: self.page.launch_url(f"https://tieba.baidu.com/p/{tid}") if tid else self._show_snackbar("该贴被系统吞没或未传回TID", "warning")
                                    ),
                                    ft.IconButton(icons.RESTORE, icon_color="orange", data=m.id, on_click=self._reset_material_row, tooltip="该贴被干了？重新回炉排期"),
                                ], spacing=0)),
                                ft.DataCell(ft.Row([
                                    ft.Switch(value=m.is_auto_bump, data=m.id, on_change=self._on_material_toggle_bump, scale=0.7),
                                    ft.Text(f"已顶{m.bump_count}", size=11, color="onSurfaceVariant")
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
            from ..db.models import MaterialPool
            m = await session.get(MaterialPool, mid)
            if m:
                m.is_auto_bump = val
                await session.commit()
                self._show_snackbar(f"物料 [{mid}] 自动回帖已{'开启' if val else '关闭'}", "info")
        # 同步 self._materials 列表中的状态 (如果是内存列表)
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
        # 批量切换选中项的自顶开关
        if not self._selected_material_ids:
            return
        
        # 获取第一个选中的状态，作为基准进行翻转（或者全部设为统一值，这里采用统一设为 True 的逻辑，除非全是 True 才会全部设为 False）
        is_any_off = False
        async with self.db.async_session() as session:
            from ..db.models import MaterialPool
            for mid in list(self._selected_material_ids):
                m = await session.get(MaterialPool, mid)
                if m and not m.is_auto_bump:
                    is_any_off = True
                    break
            
            target_val = is_any_off
            for mid in list(self._selected_material_ids):
                m = await session.get(MaterialPool, mid)
                if m:
                    m.is_auto_bump = target_val
            await session.commit()
            
        self._materials = await self.db.get_materials()
        await self._refresh_material_table()
        self._show_snackbar(f"已批量{'开启' if target_val else '关闭'} {len(self._selected_material_ids)} 项自动回帖", "success")

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
                    from ..core.logger import log_error
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
                    color=ft.colors.ON_PRIMARY if is_selected else ft.colors.ON_SURFACE,
                    bgcolor=ft.colors.PRIMARY if is_selected else with_opacity(0.1, "onSurface"),
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
                    new_content = f"{desc} 👉 {final_url}" if desc else final_url
                else:
                    effective_title = seo_title if seo_title else f"主页输入【{code}】立刻查看网盘资源"
                    new_content = f"{desc} 👉 主页搜【{code}】马上查阅" if desc else f"主页搜【{code}】马上查阅"
                
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

    async def _on_shortlink_search_change(self, e):
        self._search_keyword = e.control.value.lower()
        await self._render_filtered_links()

    async def _on_shortlink_filter_change(self, e):
        status = e.control.data
        self._filter_status = status
        
        # 更新按钮样式以模拟单选卡片
        for btn in self._filter_chips.controls:
            is_sel = (btn.data == status)
            btn.style.color = ft.colors.ON_PRIMARY if is_sel else ft.colors.ON_SURFACE
            btn.style.bgcolor = ft.colors.PRIMARY if is_sel else with_opacity(0.1, "onSurface")
            
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
                    archive_search,
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
        
        # 批量操作 UI 容器
        self._material_selected_count_text = ft.Text(f"已选 0 项", size=11, color="onSurfaceVariant")
        self._material_bulk_actions = ft.Row([
            ft.FilledButton("批量删除", icon=icons.DELETE_SWEEP,
                            style=ft.ButtonStyle(bgcolor="error", color="white"), 
                            on_click=self._bulk_delete_materials),
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
            ft.FilledButton("批量回炉", icon=icons.RESTORE_PAGE,
                            style=ft.ButtonStyle(bgcolor="orange", color="white"), 
                            on_click=self._bulk_reset_archives),
            self._archive_selected_count_text,
        ], visible=False, spacing=10)
        
        # 2. 物料录入与表格
        self._quick_title = ft.TextField(label="快速配置标签(可选)", expand=1, text_size=12, dense=True)
        self._quick_content = ft.TextField(label="正文主段落 (将混合零宽防御)*", expand=2, text_size=12, dense=True)
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
                ft.DataColumn(ft.Text("投递时间", size=11, weight=ft.FontWeight.BOLD)),
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
        self.post_count = ft.TextField(label="发布总数 (帖)", value="10", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True)
        self.min_delay = ft.TextField(label="最小延迟 (秒)", value="60", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True)
        self.max_delay = ft.TextField(label="最大延迟 (秒)", value="300", text_size=12, input_filter=ft.NumbersOnlyInputFilter(), dense=True)
        self.use_ai_switch = ft.Switch(label="启用 AI 智能改写", value=True)
        self.use_schedule = ft.Switch(label="定时执行计划", value=False, on_change=lambda e: self._toggle_schedule(e))
        self.schedule_time = ft.TextField(
            label="计划时间 (YYYY-MM-DD HH:mm)", 
            value=(datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
            visible=False, text_size=12
        )
        self.interval_hours = ft.TextField(label="循环周期 (小时)", value="0", visible=False, text_size=12, input_filter=ft.NumbersOnlyInputFilter())
        
        # 4. 账号与策略
        self.strategy_dropdown = ft.Dropdown(
            label="账号调度策略", value="round_robin", width=200,
            options=[ft.dropdown.Option("round_robin", "轮询 (Round-Robin)"), ft.dropdown.Option("random", "随机 (Random)")]
        )
        self.pairing_mode_dropdown = ft.Dropdown(
            label="文案提取模式", value="random", width=200,
            options=[ft.dropdown.Option("random", "随机混用 (防抽混淆)"), ft.dropdown.Option("strict", "严格配对 (发多资源)")]
        )
        self.account_pool_column = ft.Column(spacing=5, height=150, scroll=ft.ScrollMode.ADAPTIVE)
        self.start_btn = ft.ElevatedButton(
            "启动矩阵任务", icon=icons.PLAY_CIRCLE_FILL_ROUNDED,
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
                ft.Tab(text="物料排期池", icon=icons.LIST_ALT_ROUNDED, content=self._build_material_view()),
                ft.Tab(text="已发归档库", icon=icons.ARCHIVE_ROUNDED, content=self._build_archive_view()),
                ft.Tab(text="实时任务流水", icon=icons.STREAM_ROUNDED, content=self._build_log_view()),
                ft.Tab(text="全域任务队列", icon=icons.UPDATE_ROUNDED, content=self._build_task_queue_view()),
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
                    # 第一栏：核心操作区 (expand=3，最宽，容纳 Tabs 主体)
                    ft.Column([
                        # 标题行：图标按钮替代 TextButton，节省横向空间
                        ft.Row([
                            ft.Text("全局指令集", size=14, weight=ft.FontWeight.W_500),
                            ft.Row([
                                ft.IconButton(icons.ADD_LINK, tooltip="从短链库选取注入物料池", on_click=self._open_shortlink_dialog, icon_color="primary"),
                                ft.IconButton(icons.SYNC_ROUNDED, tooltip="同步云端短码到本地库", on_click=self._sync_shortlinks, icon_color="onSurfaceVariant"),
                                ft.IconButton(icons.UPLOAD_FILE, tooltip="本地载入文件", on_click=lambda _: self._file_picker.pick_files(allow_multiple=False), icon_color="onSurfaceVariant"),
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
                            padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                        ),
                        # 底部标签页（物料池 / 任务流水 / 任务队列）
                        self.bottom_tabs,
                    ], expand=3, spacing=15),  # 3 份宽度，主内容区

                    # 右侧面板：矩阵策略中心（上） + 控制参数（下），垂直堆叠
                    ft.Column([
                        # 上方卡片：矩阵策略中心
                        ft.Text("矩阵策略中心", size=14, weight=ft.FontWeight.W_500),
                        ft.Container(
                            content=ft.Column([
                                self.strategy_dropdown,
                                self.pairing_mode_dropdown,
                                ft.Text("参与账号池 (勾选启用)", size=12, color="onSurfaceVariant"),
                                ft.Container(
                                    content=self.account_pool_column,
                                    border=ft.border.all(1, with_opacity(0.1, "onSurface")),
                                    border_radius=8,
                                    padding=10,
                                ),
                            ], spacing=10),
                            padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                        ),
                        # 下方卡片：控制参数
                        ft.Text("控制参数", size=14, weight=ft.FontWeight.W_500),
                        ft.Container(
                            content=ft.Column([
                                self.post_count,
                                self.use_ai_switch,
                                self.use_schedule,
                                self.schedule_time,
                                ft.Row([self.min_delay, self.max_delay], spacing=10),
                                ft.Divider(height=10, color="transparent"),
                                self.start_btn,
                            ], spacing=10),
                            padding=15, bgcolor=with_opacity(0.05, "surface"), border_radius=12,
                        ),
                    ], expand=2, spacing=12),  # 右侧面板，2 份宽度，两卡片上下排列
                ],
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,  # 顶部对齐
                ),
            ], expand=True, spacing=20),  # 外层 Column 必须 expand=True，否则内部 Row 无法分配空间
            padding=20, expand=True,
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
                ft.DataColumn(ft.Text("ID")),
                ft.DataColumn(ft.Text("贴吧")),
                ft.DataColumn(ft.Text("AI")),
                ft.DataColumn(ft.Text("策略")),
                ft.DataColumn(ft.Text("计划时间")),
                ft.DataColumn(ft.Text("状态")),
                ft.DataColumn(ft.Text("进度")),
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

    def _build_task_row(self, t):
        status_color = {"pending": "orange", "running": "primary", "completed": "green", "failed": "error"}.get(t.status, "onSurface")
        fnames_disp = t.fnames_json if hasattr(t, "fnames_json") and t.fnames_json else t.fname

        return ft.DataRow(cells=[
            ft.DataCell(ft.Text(str(t.id))),
            ft.DataCell(ft.Text(fnames_disp[:15] + ("..." if len(fnames_disp)>15 else ""))),
            ft.DataCell(ft.Icon(icons.AUTO_AWESOME, color="primary", size=16) if t.use_ai else ft.Text("-")),
            ft.DataCell(ft.Text(getattr(t, "strategy", "N/A"))),
            ft.DataCell(ft.Text(t.schedule_time.strftime("%m-%d %H:%M") if t.schedule_time else "即时")),
            ft.DataCell(ft.Text(t.status.upper(), color=status_color, weight=ft.FontWeight.BOLD)),
            ft.DataCell(ft.Text(f"{t.progress}/{t.total}")),
        ])

    def _toggle_schedule(self, e):
        self.schedule_time.visible = e.control.value
        self.interval_hours.visible = e.control.value
        self.page.update()

    async def _on_file_result(self, e):
        if not e.files: return
        file_path = e.files[0].path
        pairs = []
        try:
            if file_path.endswith(".txt"):
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            pairs.append(("暂无标题", line.strip()))
            elif file_path.endswith(".csv"):
                import csv
                with open(file_path, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) >= 2:
                            pairs.append((row[0], row[1]))
                        elif len(row) == 1 and row[0].strip():
                            pairs.append(("暂无标题", row[0].strip()))
            
            added_count = await self.db.add_materials_bulk(pairs)
            self._materials = await self.db.get_materials()
            await self._refresh_material_table()
            self._show_snackbar(f"成功导入 {added_count} 条文案物料", "success")
        except Exception as ex:
            self._show_snackbar(f"文件解析失败: {str(ex)}", "error")

    async def _on_start_click(self, e):
        if self._is_running:
            self._is_running = False
            self.start_btn.text = "启动矩阵任务"
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
        fnames = list(set(selected_forums + manual_forums))

        # Validation
        pending_m = [m for m in self._materials if m.status == "pending"]
        pairing_mode = self.pairing_mode_dropdown.value
        strategy = self.strategy_dropdown.value
        
        if not fnames or not pending_m:
            self._show_snackbar("发射拦截：请补全目标贴吧库，并确保排期池内有处于 [待发(pending)] 的有效子弹", "error")
            return
            
        if self.use_schedule.value:
            try:
                st = datetime.strptime(self.schedule_time.value, "%Y-%m-%d %H:%M")
                await self.db.add_batch_task(
                    fname=fnames[0], # 保留以作向下兼容
                    fnames_json=json.dumps(fnames),
                    titles_json="[]",
                    contents_json="[]",
                    accounts_json=json.dumps(selected_accounts),
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
                return
            except Exception as ex:
                self._show_snackbar(f"定时解析失败: {str(ex)}", "error")
                return

        self._is_running = True
        self.start_btn.text = "停止任务"
        self.start_btn.icon = icons.STOP_CIRCLE_ROUNDED
        self.start_btn.bgcolor = "error"
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
                    acc_id = update.get("account_id", "?")
                    fname = update.get("fname", "?")
                    self._add_log(f"[Acc:{acc_id}] 发布: {fname} | TID: {update['tid']} ({update['progress']}/{update['total']})")
                    self.progress_bar.value = update["progress"] / update["total"]
                elif update["status"] == "error":
                    fname = update.get("fname", "?")
                    self._add_log(f"⚠ 执行失败 [{fname}]: {update.get('msg')}", "error")
                elif update["status"] == "skipped":
                    self._add_log(f"⏭ {update.get('msg')}", "error")
                
                self.page.update()
        except Exception as ex:
            self._add_log(f"CRITICAL ERROR: {str(ex)}", "error")
        finally:
            self._is_running = False
            self.start_btn.text = "启动矩阵任务"
            self.start_btn.icon = icons.PLAY_CIRCLE_FILL_ROUNDED
            self.start_btn.bgcolor = "primary"
            self.progress_bar.visible = False
            self.page.update()

    def _add_log(self, msg, type="info"):
        now = datetime.now().strftime("%H:%M:%S")
        color = "primary" if type == "info" else "error"
        self.log_list.controls.insert(0, ft.Text(f"[{now}] {msg}", size=11, color=color))
        if len(self.log_list.controls) > 100:
            self.log_list.controls.pop()

    def _navigate(self, page_name: str):
        if self.on_navigate: self.on_navigate(page_name)

    def _show_snackbar(self, message: str, type="info"):
        color = "primary" if type != "error" else "error"
        if type == "success": color = ft.colors.GREEN
        self.page.show_snack_bar(ft.SnackBar(content=ft.Text(message), bgcolor=with_opacity(0.8, color), behavior=ft.SnackBarBehavior.FLOATING))
