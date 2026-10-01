"""任务详情/编辑对话框回归测试。

2026-09-22 实测修复：保存时账号池勾选框嵌套在 wrap Row 内，
直接遍历 .controls 拿到的是 Row 而非 Checkbox，触发 AttributeError 保存失败。
2026-10-02 原位编辑放开目标贴吧与策略：同一采集器复用于贴吧名（字符串 data），
新增 LaunchConfig 快照同步纯函数测试（「复制配置」优先读快照，不同步会复活旧配置）。
"""

import json

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


# --- 2026-10-02 目标贴吧原位编辑：采集器复用于字符串 data ---

def test_collect_forum_names_strings():
    """目标贴吧勾选传贴吧名（字符串 data），同一采集器直接可用"""
    area = ft.Column([ft.Row([
        ft.Checkbox(value=True, data="电影"),
        ft.Checkbox(value=False, data="综艺"),
        ft.Checkbox(value=True, data="电视剧资源"),
    ], wrap=True, spacing=12, run_spacing=8)], height=130, scroll=ft.ScrollMode.AUTO)
    assert BatchPostCenterPage._collect_checked_account_ids(area) == ["电影", "电视剧资源"]


def test_collect_forum_names_excludes_disabled_banned_forum():
    """已封禁吧 disabled 预勾也必须被剔除（与终态账号同口径，2026-10-01 封禁事故教训）"""
    banned = ft.Checkbox(value=True, data="电影", disabled=True)
    area = ft.Column([ft.Row([
        ft.Checkbox(value=True, data="综艺"), banned,
    ], wrap=True, spacing=12)])
    assert BatchPostCenterPage._collect_checked_account_ids(area) == ["综艺"]


# --- LaunchConfig 快照同步 ---

_SNAPSHOT = json.dumps({
    "account_ids": [6], "local_fnames": ["旧本地吧"], "global_fnames": ["旧全域吧"],
    "strategy": "random", "pairing_mode": "strict", "post_count": 2,
    "delay_min": 1800.0, "delay_max": 5400.0, "use_ai": True, "ai_persona": "seo",
    "use_schedule": True, "schedule_type": "daily", "schedule_time": "2026-09-12 08:50",
    "interval_hours": 0, "schedule_day_of_week": None, "reset_strategy": "new_only",
}, ensure_ascii=False)


def test_sync_snapshot_rewrites_targets_and_strategy():
    out = BatchPostCenterPage._sync_launch_snapshot(
        _SNAPSHOT, fnames=["电影", "综艺"], account_ids=[6, 9],
        strategy="strict_round_robin", pairing_mode="random", reset_strategy="reuse",
        total=3, delay_min=100.0, delay_max=200.0,
        use_ai=False, ai_persona="normal",
        schedule_time=None, schedule_day_of_week=None)
    data = json.loads(out)
    assert data["global_fnames"] == ["电影", "综艺"]
    assert data["local_fnames"] == []          # 原位编辑不分组，统一归全域
    assert data["account_ids"] == [6, 9]
    assert data["strategy"] == "strict_round_robin"
    assert data["pairing_mode"] == "random"
    assert data["reset_strategy"] == "reuse"
    assert data["post_count"] == 3
    assert data["delay_min"] == 100.0 and data["delay_max"] == 200.0
    assert data["use_ai"] is False and data["ai_persona"] == "normal"
    # 未传调度字段时保留原值
    assert data["schedule_time"] == "2026-09-12 08:50"


def test_sync_snapshot_updates_schedule_fields():
    out = BatchPostCenterPage._sync_launch_snapshot(
        _SNAPSHOT, fnames=["电影"], account_ids=[6],
        strategy="random", pairing_mode="strict", reset_strategy="new_only",
        total=2, delay_min=60.0, delay_max=300.0,
        use_ai=True, ai_persona="normal",
        schedule_time=None, schedule_day_of_week=2, interval_hours=8)
    data = json.loads(out)
    assert data["schedule_day_of_week"] == 2
    assert data["interval_hours"] == 8


def test_sync_snapshot_broken_or_missing_returns_none():
    assert BatchPostCenterPage._sync_launch_snapshot(
        None, fnames=["电影"], account_ids=[6], strategy="random", pairing_mode="random",
        reset_strategy="new_only", total=1, delay_min=1.0, delay_max=2.0,
        use_ai=False, ai_persona="normal") is None
    assert BatchPostCenterPage._sync_launch_snapshot(
        "not-json{", fnames=["电影"], account_ids=[6], strategy="random", pairing_mode="random",
        reset_strategy="new_only", total=1, delay_min=1.0, delay_max=2.0,
        use_ai=False, ai_persona="normal") is None
    # 旧任务无目标组的空快照：不动库，走复制配置的展示字段反推降级
    assert BatchPostCenterPage._sync_launch_snapshot(
        "{}", fnames=["电影"], account_ids=[6], strategy="random", pairing_mode="random",
        reset_strategy="new_only", total=1, delay_min=1.0, delay_max=2.0,
        use_ai=False, ai_persona="normal") is None
