"""任务详情/编辑对话框回归测试。

2026-09-22 实测修复：保存时账号池勾选框嵌套在 wrap Row 内，
直接遍历 .controls 拿到的是 Row 而非 Checkbox，触发 AttributeError 保存失败。
2026-10-02 原位编辑放开目标贴吧与策略：同一采集器复用于贴吧名（字符串 data），
新增 LaunchConfig 快照同步纯函数测试（「复制配置」优先读快照，不同步会复活旧配置）。
2026-10-07 弹窗按功能分页：编辑态拆四页签（基本信息/账号池/目标贴吧/执行策略），
新增页签结构与字段挂载回归（保存闭包按 fields 引用取值，控件脱节=静默丢改动）。
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

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


# --- 2026-10-07 弹窗按功能分页（四页签） ---

class _RecordingPage:
    def __init__(self):
        self.opened = []
        self.run_task_args = []

    def open(self, dlg):
        self.opened.append(dlg)

    def run_task(self, coro, *a):
        self.run_task_args.append(a)

    def close(self, dlg):
        pass


def _walk_controls(root):
    """深度遍历真实 flet 控件树（content/controls/tabs/actions 四类容器属性）。"""
    stack = [root]
    while stack:
        ctrl = stack.pop()
        yield ctrl
        for attr in ("content", "controls", "tabs", "actions"):
            child = getattr(ctrl, attr, None)
            if child is None or child is ctrl:
                continue
            if isinstance(child, (list, tuple)):
                stack.extend(list(child))
            else:
                stack.append(child)


def _make_detail_task(**over):
    base = dict(
        id=1, status="pending", progress=0, total=2, fname="nas",
        fnames_json='["nas"]', accounts_json="[6]",
        strategy="strict_round_robin", pairing_mode="random", reset_strategy="new_only",
        delay_min=1800.0, delay_max=5400.0, use_ai=False, ai_persona="normal",
        schedule_type="daily", schedule_time=datetime(2026, 10, 7, 20, 50),
        interval_hours=0, schedule_day_of_week=None,
        created_at=datetime(2026, 9, 12, 18, 53), completed_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def _open_detail(center, page, task):
    await center._open_task_detail_dialog(task)
    assert page.opened, "弹窗未打开"
    return page.opened[-1]


@pytest.mark.asyncio
async def test_editable_dialog_splits_into_four_function_tabs():
    """编辑态弹窗拆四功能页签，字段控件全部仍挂在弹窗树上。"""
    page = _RecordingPage()
    mock_db = MagicMock()
    mock_db.get_all_unique_forums = AsyncMock(return_value=[
        {"fname": "电影", "is_banned": False}, {"fname": "nas", "is_banned": False},
    ])
    center = BatchPostCenterPage(page=page, db=mock_db, on_navigate=lambda name: None)
    center._accounts = [SimpleNamespace(id=6, status="active"),
                        SimpleNamespace(id=9, status="banned")]
    center._account_name_map = {6: "hwdemtv5", 9: "hwdemtv187"}

    dialog = await _open_detail(center, page, _make_detail_task())

    tabs = [c for c in _walk_controls(dialog.content) if isinstance(c, ft.Tabs)]
    assert len(tabs) == 1, "编辑态内容区应恰好一个 Tabs"
    assert [t.text for t in tabs[0].tabs] == ["基本信息", "账号池", "目标贴吧", "执行策略"]

    # 保存按钮闭包里的 fields：模拟点击捕获，校验字段引用与挂载一致性
    save_btns = [b for b in dialog.actions
                 if isinstance(b, ft.FilledButton) and b.text == "保存修改"]
    assert len(save_btns) == 1
    save_btns[0].on_click(None)
    assert page.run_task_args, "保存按钮未挂 run_task 闭包"
    _, _, fields = page.run_task_args[0]
    expected_keys = {"schedule", "accounts", "forums", "strategy", "pairing",
                     "reset_strategy", "total", "delay_min", "delay_max",
                     "use_ai", "persona"}
    assert set(fields) == expected_keys
    mounted = list(_walk_controls(dialog.content))
    for key, ctrl in fields.items():
        assert any(c is ctrl for c in mounted), f"字段 {key} 未挂在弹窗控件树上（保存会读到脱节控件）"

    # 账号池页签：活跃号预勾可选、封禁号禁选不预勾；选择区独立页签后加高 260px
    checkboxes = [c for c in mounted if isinstance(c, ft.Checkbox)]
    assert fields["accounts"].height == 260 and fields["forums"].height == 260
    acc_values = [(c.data, c.value, c.disabled) for c in checkboxes if isinstance(c.data, int)]
    assert (6, True, False) in acc_values   # 活跃号预勾可选
    assert (9, False, True) in acc_values   # 封禁号禁选不预勾


@pytest.mark.asyncio
async def test_readonly_dialog_keeps_flat_layout():
    """非编辑态（running 等）保持平铺只读布局，不出现页签与保存按钮。"""
    page = _RecordingPage()
    mock_db = MagicMock()
    mock_db.get_all_unique_forums = AsyncMock(return_value=[])
    center = BatchPostCenterPage(page=page, db=mock_db, on_navigate=lambda name: None)
    center._accounts = []

    dialog = await _open_detail(center, page, _make_detail_task(status="running"))
    assert not [c for c in _walk_controls(dialog.content) if isinstance(c, ft.Tabs)]
    assert not [b for b in dialog.actions if isinstance(b, ft.FilledButton)]
