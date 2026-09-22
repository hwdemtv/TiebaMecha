"""任务详情/编辑对话框回归测试。

2026-09-22 实测修复：保存时账号池勾选框嵌套在 wrap Row 内，
直接遍历 .controls 拿到的是 Row 而非 Checkbox，触发 AttributeError 保存失败。
"""

import flet as ft

from tieba_mecha.web.pages.batch_post_center import BatchPostCenterPage


def _cb(value: bool, data: int) -> ft.Checkbox:
    return ft.Checkbox(value=value, data=data)


def test_collect_flat_checkboxes():
    area = ft.Column([_cb(True, 5), _cb(False, 6), _cb(True, 9)])
    assert BatchPostCenterPage._collect_checked_account_ids(area) == [5, 9]


def test_collect_nested_in_wrap_row():
    # 实际对话框结构：Column → wrap Row → Checkboxes
    area = ft.Column([ft.Row([
        _cb(True, 1), _cb(False, 5), _cb(True, 10),
    ], wrap=True, spacing=12, run_spacing=8)], height=130, scroll=ft.ScrollMode.AUTO)
    assert BatchPostCenterPage._collect_checked_account_ids(area) == [1, 10]


def test_collect_deeply_nested_and_sorted():
    area = ft.Column([
        ft.Row([_cb(True, 10), _cb(True, 1)], wrap=True),
        ft.Column([ft.Row([_cb(True, 6), _cb(False, 9)], wrap=True)]),
    ])
    assert BatchPostCenterPage._collect_checked_account_ids(area) == [1, 6, 10]


def test_collect_none_checked_returns_empty():
    area = ft.Column([ft.Row([_cb(False, 1), _cb(False, 5)], wrap=True)])
    assert BatchPostCenterPage._collect_checked_account_ids(area) == []


def test_collect_excludes_disabled_terminal_accounts():
    """终态账号（disabled）即使处于勾选态也必须被剔除——2026-09-22 封禁号混入账号池事故"""
    banned = ft.Checkbox(value=True, data=5, disabled=True)  # 封禁号：禁选但被预勾
    area = ft.Column([ft.Row([
        _cb(True, 6), banned, _cb(True, 10),
    ], wrap=True, spacing=12)])
    assert BatchPostCenterPage._collect_checked_account_ids(area) == [6, 10]


def test_collect_disabled_unchecked_still_excluded():
    disabled_unchecked = ft.Checkbox(value=False, data=9, disabled=True)
    area = ft.Column([ft.Row([_cb(True, 6), disabled_unchecked], wrap=True)])
    assert BatchPostCenterPage._collect_checked_account_ids(area) == [6]
