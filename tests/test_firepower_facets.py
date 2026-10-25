"""选吧简化 A+B 交互包测试（2026-10-25）。

A 包：主页面摘要条非安全计数、预检处置后选择集移除与快照重算
B 包：预检警告行内处置按钮（移除非安全/移除空降/补齐关注）的挂载与渲染
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

from tieba_mecha.web.pages.batch_post_page import BatchPostPage
from tieba_mecha.web.pages.batch_post.preflight import (
    PreflightIssue,
    PreflightReport,
)


class _FakePage:
    def __init__(self):
        self.run_task = MagicMock()
        self.open = MagicMock()
        self.close = MagicMock()
        self.show_snack_bar = MagicMock()
        self.update = MagicMock()


def make_page():
    mock_db = MagicMock()
    return BatchPostPage(page=_FakePage(), db=mock_db)


def collect_buttons(control, out=None):
    """递归收集控件树中所有 ft.TextButton 的文本。"""
    if out is None:
        out = []
    if isinstance(control, ft.TextButton):
        out.append(control.text)
    for attr in ("controls", "content", "actions"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for c in child:
                collect_buttons(c, out)
        elif isinstance(child, ft.Control):
            collect_buttons(child, out)
    return out


def _bare_page():
    """绕过 __init__ 的最小实例：只喂 _update_forum_select_btn 需要的属性"""
    page = BatchPostPage.__new__(BatchPostPage)
    page.forum_select_btn = ft.TextButton("")
    page.local_status_btn = ft.TextButton("")
    page.global_status_btn = ft.TextButton("")
    page._temp_local_fnames = []
    page._temp_global_fnames = []
    page._local_unsafe_n = 0
    page._known_safe_fnames = set()
    return page


# ── A 包：主页面摘要条 ──

def test_summary_btn_shows_unsafe_count():
    page = _bare_page()
    page._temp_local_fnames = ["安全吧", "危险吧"]
    page._local_unsafe_n = 1
    page._update_forum_select_btn()
    assert "2 个目标" in page.local_status_btn.text
    assert "1 非安全" in page.local_status_btn.text


def test_summary_btn_no_suffix_when_all_safe():
    page = _bare_page()
    page._temp_local_fnames = ["安全吧"]
    page._local_unsafe_n = 0
    page._update_forum_select_btn()
    assert page.local_status_btn.text == "🏠 本地自留区: 1 个目标"


# ── B 包：处置动作核心 ──

def test_remove_forums_from_selection():
    page = _bare_page()
    page._temp_local_fnames = ["安全吧", "非安全A", "非安全B"]
    page._temp_global_fnames = ["轰炸C"]
    page._known_safe_fnames = {"安全吧"}
    removed = page._remove_forums_from_selection(["非安全A", "非安全B", "轰炸C", "幽灵吧"])
    assert removed == 3
    assert page._temp_local_fnames == ["安全吧"]
    assert page._temp_global_fnames == []
    # 快照按已知安全集重算：仅剩安全吧 → 0
    assert page._local_unsafe_n == 0


def test_remove_forums_from_selection_recomputes_unsafe():
    page = _bare_page()
    page._temp_local_fnames = ["安全吧", "非安全A", "非安全B"]
    page._known_safe_fnames = {"安全吧"}
    page._remove_forums_from_selection(["安全吧"])
    assert page._temp_local_fnames == ["非安全A", "非安全B"]
    assert page._local_unsafe_n == 2


# ── B 包：警告行内按钮渲染 ──

def test_summary_issue_row_carries_actions():
    issue = PreflightIssue(level="warning", code="forums_unsafe", message="3 个非安全贴吧")
    btn = ft.TextButton("移除非安全吧")
    row = make_page()._build_summary_issue_row(issue, [btn])
    assert btn in row.controls


def test_summary_content_renders_remediation_buttons():
    report = PreflightReport()
    report.stats["estimated_duration"] = ("1min", "2min")
    report.issues = [
        PreflightIssue(level="warning", code="forums_unsafe", message="2 个非安全贴吧: a、b"),
        PreflightIssue(level="warning", code="forums_parachute", message="1 个空降: c"),
    ]
    remediations = {
        "forums_unsafe": [ft.TextButton("移除非安全吧")],
        "forums_parachute": [ft.TextButton("补齐关注"), ft.TextButton("移除空降吧")],
    }
    content = make_page()._build_summary_content(report, dry=False, remediations=remediations)
    texts = collect_buttons(content)
    assert "移除非安全吧" in texts
    assert "补齐关注" in texts
    assert "移除空降吧" in texts


@pytest.mark.asyncio
async def test_launch_dialog_builds_remediations_from_stats():
    """确认发射弹窗按 stats 自动生成行内处置按钮（unsafe→移除；parachute→补齐+移除）"""
    page = make_page()
    report = PreflightReport()
    report.stats["estimated_duration"] = ("1min", "2min")
    report.stats["forums_unsafe"] = ["a吧", "b吧"]
    report.stats["forums_parachute"] = ["c吧"]
    report.effective_account_ids = [6]
    report.effective_fnames = ["a吧", "b吧", "c吧"]
    report.issues = [
        PreflightIssue(level="warning", code="forums_unsafe", message="2 个非安全贴吧"),
        PreflightIssue(level="warning", code="forums_parachute", message="1 个空降"),
    ]

    task = asyncio.create_task(page._show_launch_summary_dialog(report, dry=True))
    await asyncio.sleep(0.2)  # 等弹窗打开（future 挂起属预期）
    assert page.page.open.called
    dialog = page.page.open.call_args[0][0]
    texts = collect_buttons(dialog.content)
    assert "移除非安全吧" in texts
    assert "补齐关注" in texts
    assert "移除空降吧" in texts
    task.cancel()


@pytest.mark.asyncio
async def test_launch_dialog_no_remediation_without_warnings():
    """无空降/非安全警告时不生成处置按钮（干跑关闭键独存）"""
    page = make_page()
    report = PreflightReport()
    report.stats["estimated_duration"] = ("1min", "2min")
    report.effective_fnames = ["a吧"]

    task = asyncio.create_task(page._show_launch_summary_dialog(report, dry=True))
    await asyncio.sleep(0.2)
    dialog = page.page.open.call_args[0][0]
    texts = collect_buttons(dialog.content)
    assert "移除非安全吧" not in texts
    assert "补齐关注" not in texts
    task.cancel()
