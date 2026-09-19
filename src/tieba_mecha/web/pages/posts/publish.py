"""发布新帖 Tab：实时校验 + 发布摘要 + AI 原文/建议对比采纳。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import flet as ft

from ...utils import with_opacity
from ...components.icons import AUTO_AWESOME, CHECK, CHECK_CIRCLE, ERROR, SEND, WARNING_AMBER
from ...components import create_gradient_button
from .helpers import CONTENT_MAX, LINK_WARN_THRESHOLD, TITLE_MAX, TITLE_MIN, extract_links

# 校验项级别 → (图标, 颜色)
_LEVEL_STYLE = {
    "ok": (CHECK_CIRCLE, "green"),
    "warn": (WARNING_AMBER, "#FF9800"),
    "error": (ERROR, "error"),
    "idle": ("radio_button_unchecked", "onSurfaceVariant"),
}


class PublishTabMixin:
    """发布新帖场景（混入 PostsPage）。"""

    # ---------- 构建 ----------

    def _build_publish_tab(self) -> ft.Control:
        # 输入控件
        self.title_counter = ft.Text(f"0/{TITLE_MAX} 字 (最少{TITLE_MIN}字)", size=10, color="onSurfaceVariant")
        self.content_counter = ft.Text(f"0/{CONTENT_MAX} 字", size=10, color="onSurfaceVariant")

        self.post_account = ft.Dropdown(
            label="发帖账号",
            text_size=13,
            border_color=with_opacity(0.2, "primary"),
            options=[],
            on_change=self._on_post_account_change,
            tooltip="选择用哪个账号发布（默认当前活跃账号）",
        )
        self.post_forum = ft.Dropdown(
            label="选择贴吧",
            text_size=13,
            border_color=with_opacity(0.2, "primary"),
            options=[],
            on_change=self._on_forum_change,
        )
        self.post_title = ft.TextField(
            label="帖子标题",
            text_size=13,
            border_color=with_opacity(0.2, "primary"),
            on_change=self._on_title_change,
        )
        self.post_content = ft.TextField(
            label="内容",
            multiline=True,
            min_lines=4,
            max_lines=8,
            text_size=13,
            border_color=with_opacity(0.2, "primary"),
            on_change=self._on_content_change,
        )
        self.post_status = ft.Text("", size=11)
        self.post_submit_btn = create_gradient_button("立即发布", icon=SEND, on_click=self._do_post)
        self._ai_optimize_btn = ft.TextButton(
            "AI 优化",
            icon=AUTO_AWESOME,
            on_click=self._ai_optimize_post,
            style=ft.ButtonStyle(color="primary"),
            tooltip="生成优化建议，与原文对比后再决定是否采纳",
        )

        # 实时校验清单
        self._validation_list = ft.Column(spacing=4)
        self._dup_count: int = 0
        self._dup_seq: int = 0

        # 发布摘要
        self._summary_card = ft.Container(content=None)

        form = ft.Column([
            self.post_account,
            self.post_forum,
            self.post_title,
            ft.Row([ft.Container(expand=True), self.title_counter]),
            self.post_content,
            ft.Row([ft.Container(expand=True), self.content_counter]),
            ft.Text("实时校验 / PRE-CHECK", size=10, color="onSurfaceVariant", weight=ft.FontWeight.W_500),
            self._validation_list,
            self._summary_card,
            ft.Row([
                self._ai_optimize_btn,
                ft.Container(expand=True),
                self.post_submit_btn,
            ]),
            self.post_status,
        ], spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

        return ft.Container(
            content=form,
            padding=15,
            bgcolor=with_opacity(0.03, "primary"),
            border=ft.border.all(1, with_opacity(0.1, "primary")),
            border_radius=12,
        )

    def _fill_publish_dropdowns(self):
        """填充发帖账号下拉；默认当前活跃账号，保留用户已选。"""
        terminal = {"banned", "expired", "suspended", "suspended_proxy"}
        options = [
            ft.dropdown.Option(
                str(a.id),
                f"{a.name or f'账号-{a.id}'}（{getattr(a, 'status', 'unknown')}）",
            )
            for a in self._accounts
        ]
        self.post_account.options = options

        current = self.post_account.value
        valid_ids = {str(a.id) for a in self._accounts}
        if current in valid_ids:
            return  # 保留用户已选
        # 回落：优先活跃账号，否则第一个非终态账号，否则第一个
        fallback = None
        if self._active_account:
            fallback = str(self._active_account.id)
        else:
            for a in self._accounts:
                if getattr(a, "status", "") not in terminal:
                    fallback = str(a.id)
                    break
            else:
                fallback = str(self._accounts[0].id) if self._accounts else None
        self.post_account.value = fallback

    async def _load_publish_forums(self):
        """按当前选中的发帖账号加载其关注贴吧，刷新贴吧下拉与摘要/校验。"""
        account_id = int(self.post_account.value) if self.post_account.value else None
        self._publish_account_id = account_id
        if account_id and self.db:
            try:
                self._publish_forums = await self.db.get_forums(account_id)
            except Exception:
                self._publish_forums = []
        else:
            self._publish_forums = []

        options = [ft.dropdown.Option(f.fname) for f in self._publish_forums]
        self.post_forum.options = options
        if options:
            # 保留旧选择，失效则回落到第一个
            values = [f.fname for f in self._publish_forums]
            if self.post_forum.value not in values:
                self.post_forum.value = values[0]
        else:
            self.post_forum.value = None

    async def _on_post_account_change(self, e):
        await self._load_publish_forums()
        self._refresh_publish_summary()
        self._run_validation()
        self.page.update()

    def _selected_publish_account(self):
        """当前选中的发帖账号对象（可能为 None）。"""
        if not self.post_account.value:
            return None
        try:
            aid = int(self.post_account.value)
        except ValueError:
            return None
        return next((a for a in self._accounts if a.id == aid), None)

    # ---------- 发布摘要 ----------

    def _forum_status_info(self, fname: str) -> tuple[str, str]:
        """(状态文本, 级别) — 级别 ok/warn/error"""
        f = self._forum_by_name(fname)
        if not f:
            return ("未关注该吧，可能无法发帖", "error") if fname else ("未选择贴吧", "error")
        if getattr(f, "is_banned", False):
            return ("已被吧务封禁，禁止发帖", "error")
        if getattr(f, "is_hidden", False):
            return ("该吧已隐藏，签到与发帖会被跳过", "warn")
        if not getattr(f, "is_post_target", False):
            return ("未开放发帖许可（可在矩阵发帖中开启）", "warn")
        return ("状态正常，允许发帖", "ok")

    def _refresh_publish_summary(self):
        """发布前摘要：选中账号、代理、目标贴吧、是否启用 AI。"""
        acc = self._selected_publish_account()
        if acc:
            acc_name = acc.name or acc.user_name or f"账号-{acc.id}"
            account_text = f"{acc_name}  ·  {getattr(acc, 'status', 'unknown')}"
            status_colors = {"active": "green", "expired": "error", "error": "error"}
            acc_status = getattr(acc, "status", "unknown")
            acc_color = status_colors.get(acc_status, "onSurfaceVariant")
        else:
            account_text, acc_color = "未选择账号", "error"

        proxy_text = "直连（未绑定代理）"
        if acc and getattr(acc, "proxy_id", None):
            proxy_text = self._proxy_summaries.get(acc.proxy_id) or f"代理 #{acc.proxy_id}"

        fname = (self.post_forum.value or "").strip() if hasattr(self, "post_forum") else ""
        forum_text, forum_level = self._forum_status_info(fname)
        forum_color = {"ok": "green", "warn": "#FF9800", "error": "error"}[forum_level]

        if self._ai_ready:
            ai_text, ai_color = f"已启用 ({self._ai_model})", "green"
        else:
            ai_text, ai_color = "未配置（可在全局设置开启）", "onSurfaceVariant"

        # AI 未配置时禁用按钮，引导去设置页而非点击后报错
        if hasattr(self, "_ai_optimize_btn"):
            self._ai_optimize_btn.disabled = not self._ai_ready
            self._ai_optimize_btn.tooltip = (
                "生成优化建议，与原文对比后再决定是否采纳"
                if self._ai_ready else "未配置 AI（请先到 全局设置 配置 API Key）"
            )

        def _row(label, value_widget):
            return ft.Row([
                ft.Container(
                    content=ft.Text(label, size=11, color="onSurfaceVariant", weight=ft.FontWeight.W_500),
                    width=64,
                ),
                value_widget,
            ], spacing=8)

        self._summary_card.content = ft.Container(
            content=ft.Column([
                ft.Text("发布摘要 / LAUNCH BRIEF", size=10, color="onSurfaceVariant", weight=ft.FontWeight.W_500),
                _row("当前账号", ft.Text(account_text, size=12, color=acc_color)),
                _row("代理", ft.Text(proxy_text, size=12, color="onSurfaceVariant")),
                _row("目标贴吧", ft.Row([
                    ft.Text(fname or "-", size=12, color="onSurface"),
                    ft.Text(f"· {forum_text}", size=11, color=forum_color),
                ], spacing=6)),
                _row("AI 优化", ft.Text(ai_text, size=12, color=ai_color)),
            ], spacing=6),
            padding=10,
            bgcolor=with_opacity(0.05, "onSurface"),
            border=ft.border.all(1, with_opacity(0.1, "onSurface")),
            border_radius=8,
        )

    # ---------- 实时校验 ----------

    def _check_item(self, level: str, text: str) -> ft.Control:
        icon, color = _LEVEL_STYLE[level]
        return ft.Row([
            ft.Icon(icon, size=13, color=color),
            ft.Text(text, size=11, color=color),
        ], spacing=5)

    def _run_validation(self, e=None):
        """根据当前输入刷新校验清单（标题/正文/链接/重复/贴吧状态）。"""
        if not hasattr(self, "_validation_list"):
            return
        title = (self.post_title.value or "").strip()
        content = (self.post_content.value or "").strip()
        items: list[ft.Control] = []

        tlen = len(title)
        if tlen == 0:
            items.append(self._check_item("idle", f"标题：{TITLE_MIN}-{TITLE_MAX} 字"))
        elif tlen < TITLE_MIN:
            items.append(self._check_item("error", f"标题 {tlen} 字，还需 {TITLE_MIN - tlen} 字"))
        elif tlen > TITLE_MAX:
            items.append(self._check_item("error", f"标题 {tlen} 字，超出 {tlen - TITLE_MAX} 字"))
        else:
            items.append(self._check_item("ok", f"标题 {tlen} 字，符合要求"))

        clen = len(content)
        if clen == 0:
            items.append(self._check_item("idle", f"正文：1-{CONTENT_MAX} 字"))
        elif clen > CONTENT_MAX:
            items.append(self._check_item("error", f"正文 {clen} 字，超出 {clen - CONTENT_MAX} 字"))
        else:
            items.append(self._check_item("ok", f"正文 {clen} 字，符合要求"))

        links = extract_links(content)
        if len(links) > LINK_WARN_THRESHOLD:
            items.append(self._check_item("warn", f"正文含 {len(links)} 条链接，超过 {LINK_WARN_THRESHOLD} 条易触发风控"))
        elif links:
            items.append(self._check_item("ok", f"正文含 {len(links)} 条链接"))
        else:
            items.append(self._check_item("idle", "正文未检测到链接"))

        if self._dup_count > 0:
            items.append(self._check_item("warn", f"物料池已有 {self._dup_count} 条同标题内容，注意去重"))
        else:
            items.append(self._check_item("idle", "重复内容：与物料池比对中/无重复"))

        fname = (self.post_forum.value or "").strip()
        forum_text, forum_level = self._forum_status_info(fname)
        items.append(self._check_item(forum_level, f"目标贴吧[{fname or '-'}]：{forum_text}"))

        self._validation_list.controls = items
        try:
            self._validation_list.update()
        except Exception:
            pass  # 首次 build 尚未挂载

    def _on_forum_change(self, e):
        self._refresh_publish_summary()
        self._run_validation()

    def _on_title_change(self, e):
        length = len(e.control.value or "")
        self._update_title_counter(length)
        self._schedule_dup_check()
        self._run_validation()

    def _update_title_counter(self, length: int):
        if length < TITLE_MIN:
            self.title_counter.value = f"{length}/{TITLE_MAX} 字 (还需{TITLE_MIN - length}字)"
            self.title_counter.color = "error"
        elif length > TITLE_MAX:
            self.title_counter.value = f"{length}/{TITLE_MAX} 字 (超出{length - TITLE_MAX}字)"
            self.title_counter.color = "error"
        else:
            self.title_counter.value = f"{length}/{TITLE_MAX} 字"
            self.title_counter.color = "onSurfaceVariant"
        self.page.update()

    def _on_content_change(self, e):
        length = len(e.control.value or "")
        self._update_content_counter(length)
        self._run_validation()

    def _update_content_counter(self, length: int):
        if length > CONTENT_MAX:
            self.content_counter.value = f"{length}/{CONTENT_MAX} 字 (超出{length - CONTENT_MAX}字)"
            self.content_counter.color = "error"
        else:
            self.content_counter.value = f"{length}/{CONTENT_MAX} 字"
            self.content_counter.color = "onSurfaceVariant"
        self.page.update()

    async def _dup_check_worker(self, seq: int):
        """防抖比对物料池重复标题"""
        await asyncio.sleep(0.6)
        if seq != self._dup_seq:
            return  # 已有更新的输入，放弃本次
        title = (self.post_title.value or "").strip()
        if not title or not self.db:
            self._dup_count = 0
        else:
            try:
                self._dup_count = await self.db.count_duplicate_titles(title)
            except Exception:
                self._dup_count = 0
        self._run_validation()

    def _schedule_dup_check(self):
        self._dup_seq += 1
        self.page.run_task(self._dup_check_worker, self._dup_seq)

    # ---------- AI 优化（原文 / 优化建议 对比采纳） ----------

    async def _ai_optimize_post(self, e):
        title = (self.post_title.value or "").strip()
        content = (self.post_content.value or "").strip()

        if not title or not content:
            self._show_snackbar("请先填写标题和内容再进行优化", "error")
            return

        self._show_snackbar("AI 神经元正在计算优化方案...", "info")

        async def optimize():
            from ....core.ai_optimizer import AIOptimizer

            optimizer = AIOptimizer(self.db)
            try:
                success, opt_title, opt_content, err = await optimizer.optimize_post(title, content)
            finally:
                await optimizer.close()

            if not success:
                self._show_snackbar(err or "AI 优化失败", "error")
                return
            self._open_ai_compare_dialog(title, content, opt_title, opt_content)

        self.page.run_task(optimize)

    def _open_ai_compare_dialog(self, old_title: str, old_content: str, new_title: str, new_content: str):
        """原文 / 优化建议 左右对比，由用户决定是否采纳（不直接覆盖原文）"""

        def _block(header: str, title: str, content: str, highlight: bool):
            color = "primary" if highlight else "onSurfaceVariant"
            return ft.Column([
                ft.Text(header, size=11, weight=ft.FontWeight.BOLD, color=color),
                ft.Container(
                    content=ft.Text(title, selectable=True, weight=ft.FontWeight.W_500),
                    padding=8,
                    bgcolor=with_opacity(0.05, "primary" if highlight else "onSurface"),
                    border_radius=6,
                ),
                ft.Container(height=6),
                ft.Container(
                    content=ft.Column([ft.Text(content, selectable=True, size=12)], scroll=ft.ScrollMode.AUTO),
                    height=220,
                    padding=8,
                    bgcolor=with_opacity(0.05, "primary" if highlight else "onSurface"),
                    border_radius=6,
                ),
            ], spacing=4, expand=True)

        def apply_opt(e):
            self.post_title.value = new_title
            self.post_content.value = new_content
            self._update_title_counter(len(new_title))
            self._update_content_counter(len(new_content))
            self._dup_count = 0
            self._schedule_dup_check()
            self._run_validation()
            self.page.close(dialog)
            self.page.update()
            self._show_snackbar("SEO 方案已采纳", "success")

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(AUTO_AWESOME, color="primary"), ft.Text("AI 优化对比 — 采纳前请核对")]),
            content=ft.Container(
                content=ft.Row([
                    _block("原文 / ORIGINAL", old_title, old_content, highlight=False),
                    ft.VerticalDivider(width=1),
                    _block("优化建议 / SUGGESTED", new_title, new_content, highlight=True),
                ], spacing=12, crossAxisAlignment=ft.CrossAxisAlignment.START),
                width=760,
                height=340,
            ),
            actions=[
                ft.TextButton("保留原文", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("采纳建议", icon=CHECK, on_click=apply_opt),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

    # ---------- 发布 ----------

    async def _do_post(self, e):
        fname = (self.post_forum.value or "").strip()
        title = (self.post_title.value or "").strip()
        content = (self.post_content.value or "").strip()

        # 硬校验（warn 仅提示不阻断）
        if not fname or not title or not content:
            self._show_snackbar("请填写完整发帖信息", "error")
            return
        tlen, clen = len(title), len(content)
        if tlen < TITLE_MIN or tlen > TITLE_MAX:
            self._show_snackbar(f"标题需{TITLE_MIN}-{TITLE_MAX}字，当前{tlen}字", "error")
            return
        if clen > CONTENT_MAX:
            self._show_snackbar(f"内容超出限制，当前{clen}字", "error")
            return
        forum_text, forum_level = self._forum_status_info(fname)
        if forum_level == "error":
            self._show_snackbar(f"目标贴吧不可用：{forum_text}", "error")
            return

        if self._posting:
            self._show_snackbar("正在发布中，请勿重复提交", "warning")
            return
        self._posting = True
        submit_btn = getattr(self, "post_submit_btn", None)
        account = self._selected_publish_account()
        account_id = account.id if account else None
        try:
            if submit_btn:
                submit_btn.disabled = True
            self.post_status.value = "正在通过加密信道传输数据..."
            self.post_status.color = "primary"
            self.page.update()

            from ....core.post import add_thread

            success, msg, tid = await add_thread(self.db, fname, title, content, account_id=account_id)
        except Exception as ex:
            success, msg, tid = False, f"发布过程发生异常: {ex}", 0
        finally:
            self._posting = False
            if submit_btn:
                submit_btn.disabled = False

        if not success:
            self._show_snackbar(msg, "error")
            self.post_status.value = "传输失败"
            self.post_status.color = "error"
            self.page.update()
            return

        # 物料池登记并标记已发布（详情抽屉/存活监控的数据来源）：
        # 单行直插 success 并带全部 posted 字段，返回 ID 直改，
        # 不再"先插 pending 再扫描前 10 条匹配"（大池漏标记/去重错标旧行）。
        registered_id = 0
        try:
            registered_id = await self.db.register_posted_material(
                title, content,
                posted_fname=fname,
                posted_tid=tid,
                posted_account_id=account_id,
                posted_time=datetime.now(),
            )
        except Exception as reg_ex:
            from ....core.logger import log_warn
            await log_warn(f"发布登记物料失败（不影响发帖）: {reg_ex}")
        if not registered_id:
            self._show_snackbar("发帖成功，但本地追踪记录登记失败（不影响帖子）", "warning")

        self._show_snackbar(f"发帖成功! TID: {tid}", "success")
        self.post_status.value = ""
        self.post_title.value = ""
        self.post_content.value = ""
        self._dup_count = 0
        self._update_title_counter(0)
        self._update_content_counter(0)
        self._run_validation()
        # 刷新"我的帖子/批量"数据（新帖已入库）
        await self._reload_rows()
