"""账号列表页面 (AccountsPage) 控件级集成测试。

覆盖 GUI 黑盒测试难以稳定触达的页面逻辑：
- 批量操作栏按钮索引错位回归（批量删除不可达 / 批量验证被改标签）
- 全选与搜索/状态筛选的组合行为
- 账号卡片信息展示（代理回退显示、权重圆点）
- 全域战略吧库的分页与搜索
- 存活分析头部统计
- 异常记录空列表构建
- AccountInfo 缺失 last_verified 字段导致的"最后检测"恒为"从未"

运行方式：pytest tests/test_accounts_page_integration.py
"""

from types import SimpleNamespace

import flet as ft
import pytest

from tieba_mecha.core.account import AccountInfo, list_accounts
from tieba_mecha.web.pages.accounts import AccountsPage


class FakePage:
    """模拟 ft.Page 的最小接口，仅记录调用不做真实渲染。"""

    def __init__(self):
        self.update_count = 0
        self.opened_dialogs = []
        self.snack_bars = []
        self.tasks = []

    def update(self):
        self.update_count += 1

    def open(self, dialog):
        self.opened_dialogs.append(dialog)

    def close(self, dialog):
        pass

    def show_snack_bar(self, snack):
        self.snack_bars.append(snack)

    def run_task(self, handler, *args, **kwargs):
        self.tasks.append((handler, args, kwargs))


class FakeEvent:
    """模拟 Flet 事件对象（e.control.value / e.control.data）。"""

    def __init__(self, value=None, data=None):
        self.control = SimpleNamespace(value=value, data=data)
        self.data = data


def make_accounts():
    """两个测试账号：1 正常 / 2 已失效，账号 2 绑定了不存在的代理 7。"""
    return [
        SimpleNamespace(
            id=1, name="a1", user_name="u1", user_id=111,
            status="active", proxy_id=None, post_weight=8,
            is_maint_enabled=True, last_maint_at=None, cuid="C0E15F6BAA",
        ),
        SimpleNamespace(
            id=2, name="a2", user_name="u2", user_id=222,
            status="expired", proxy_id=7, post_weight=3,
            is_maint_enabled=False, last_maint_at=None, cuid="89B2FB58CC",
        ),
    ]


def make_page():
    fp = FakePage()
    page = AccountsPage(fp, db=None)
    return fp, page


def find_texts(control, out=None):
    """递归收集控件树中所有 ft.Text。"""
    if out is None:
        out = []
    if isinstance(control, ft.Text):
        out.append(control)
    for attr in ("controls", "content", "title", "label"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for c in child:
                find_texts(c, out)
        elif isinstance(child, ft.Control):
            find_texts(child, out)
    return out


# ── 批量操作栏 ──

def test_bulk_bar_buttons_appear_on_selection():
    fp, page = make_page()
    page._build_accounts_tab()
    page._selected_ids = {1, 2}
    page._update_bulk_bar()

    verify_btn = page._bulk_verify_btn
    delete_btn = page._bulk_delete_btn
    assert verify_btn.visible is True
    assert verify_btn.text == "批量验证 (2)"
    assert delete_btn.visible is True
    assert delete_btn.text == "批量删除 (2)"


def test_bulk_bar_buttons_hidden_without_selection():
    fp, page = make_page()
    page._build_accounts_tab()
    page._selected_ids = set()
    page._update_bulk_bar()

    assert page._bulk_verify_btn.visible is False
    assert page._bulk_delete_btn.visible is False
    # 齿轮（评分模型配置）不受批量选择影响，始终可见
    assert page.bulk_bar.controls[2].visible is not False


@pytest.mark.asyncio
async def test_toggle_select_all_respects_status_filter():
    fp, page = make_page()
    page._accounts = make_accounts()
    page._filter_status = "active"
    page._build_accounts_tab()

    page._toggle_select_all(FakeEvent(value=True))
    assert page._selected_ids == {1}

    page._toggle_select_all(FakeEvent(value=False))
    assert page._selected_ids == set()


@pytest.mark.asyncio
async def test_toggle_select_all_respects_search():
    fp, page = make_page()
    page._accounts = make_accounts()
    page._search_text = "u2"
    page._build_accounts_tab()

    page._toggle_select_all(FakeEvent(value=True))
    assert page._selected_ids == {2}


def test_item_select_updates_selection():
    fp, page = make_page()
    page._build_accounts_tab()

    page._on_item_select(FakeEvent(value=True, data=1))
    assert 1 in page._selected_ids
    page._on_item_select(FakeEvent(value=False, data=1))
    assert 1 not in page._selected_ids


# ── 账号卡片构建 ──

def test_build_account_items_search_and_status():
    fp, page = make_page()
    page._accounts = make_accounts()
    page._proxies = []
    page._build_accounts_tab()

    assert len(page._build_account_items()) == 2

    page._search_text = "u1"
    assert len(page._build_account_items()) == 1

    page._search_text = ""
    page._filter_status = "expired"
    assert len(page._build_account_items()) == 1

    page._filter_status = "banned"
    banned_items = page._build_account_items()
    assert len(banned_items) == 1  # 无匹配提示条
    assert "未找到匹配的账号" in banned_items[0].content.value

    # 按 UID 数字搜索
    page._filter_status = "all"
    page._search_text = "222"
    assert len(page._build_account_items()) == 1


def test_account_card_proxy_fallback_shows_disabled():
    """绑定的代理不在活动代理列表中时，应显示“代理#N (已停用)”而非“直连”。"""
    fp, page = make_page()
    page._accounts = make_accounts()
    page._proxies = []  # 代理 7 已被熔断，不在活动列表
    page._build_accounts_tab()

    items = page._build_account_items()
    card_texts = [t.value for t in find_texts(items[1])]
    assert any("代理#7 (已停用)" in str(v) for v in card_texts if v)
    # 未绑定代理的账号仍显示“直连”
    card1_texts = [t.value for t in find_texts(items[0])]
    assert any(str(v).startswith("代理: 直连") for v in card1_texts if v)


def test_account_card_weight_dots():
    fp, page = make_page()
    accs = make_accounts()
    accs[0].post_weight = 8   # 8/10 → 4 实 1 空
    accs[1].post_weight = 3   # 3/10 → 1 实 4 空
    page._accounts = accs
    page._proxies = []
    page._build_accounts_tab()

    items = page._build_account_items()
    texts = [t.value for t in find_texts(items[0]) if t.value]
    assert any("●●●●○" in v for v in texts)
    texts2 = [t.value for t in find_texts(items[1]) if t.value]
    assert any("●○○○○" in v for v in texts2)


def test_account_card_banned_badge_logic():
    fp, page = make_page()
    accs = make_accounts()
    accs[0].status = "banned"
    page._accounts = accs
    page._proxies = []
    page._build_accounts_tab()

    # 战损报警文案
    page.refresh_ui()
    assert "已封禁账号" in page.account_stats_info.content.value
    assert page.account_stats_info.visible is True


def test_banned_banner_click_sets_filter():
    """战损报警横幅点击后应一键切换到已封禁筛选。"""
    fp, page = make_page()
    accs = make_accounts()
    accs[0].status = "banned"
    page._accounts = accs
    page._proxies = []
    page._build_accounts_tab()
    page.refresh_ui()

    page._on_banned_banner_click()
    assert page._filter_status == "banned"
    assert page._status_filter_dropdown.value == "banned"


def test_status_matches_prefix_and_unknown():
    """`invalid: xxx` 带详情的状态应按前缀归入 invalid 筛选。"""
    assert AccountsPage._status_matches("active", "all")
    assert AccountsPage._status_matches("invalid: 网络超时", "invalid")
    assert AccountsPage._status_matches("invalid", "invalid")
    assert not AccountsPage._status_matches("invalid: 网络超时", "active")
    assert AccountsPage._status_matches("unknown", "unknown")
    assert not AccountsPage._status_matches("expired", "banned")


def test_account_sort_modes():
    fp, page = make_page()
    accs = [
        SimpleNamespace(id=1, name="a", user_name="a", user_id=1, status="active",
                        post_weight=3, last_verified=None),
        SimpleNamespace(id=2, name="b", user_name="b", user_id=2, status="banned",
                        post_weight=9, last_verified=None),
        SimpleNamespace(id=3, name="c", user_name="c", user_id=3, status="active",
                        post_weight=10, last_verified=None),
    ]
    page._accounts = accs
    page._proxies = []
    page._build_accounts_tab()
    page._filter_status = "all"

    # 按权重：10 → 9 → 3
    page._sort_mode = "weight"
    assert [a.id for a in sorted(accs, key=page._account_sort_key)] == [3, 2, 1]

    # 按状态：封禁 > 正常
    page._sort_mode = "status"
    assert [a.id for a in sorted(accs, key=page._account_sort_key)] == [2, 1, 3]

    # 默认：按录入顺序
    page._sort_mode = "default"
    assert [a.id for a in sorted(accs, key=page._account_sort_key)] == [1, 2, 3]


def test_build_account_items_applies_sort():
    """列表构建应应用排序，封禁账号在“按状态”模式下排最前。"""
    fp, page = make_page()
    accs = [
        SimpleNamespace(id=1, name="a", user_name="a", user_id=1, status="active",
                        proxy_id=None, post_weight=3, is_maint_enabled=False,
                        last_maint_at=None, cuid="C"),
        SimpleNamespace(id=2, name="b", user_name="b", user_id=2, status="banned",
                        proxy_id=None, post_weight=9, is_maint_enabled=False,
                        last_maint_at=None, cuid="C"),
    ]
    page._accounts = accs
    page._proxies = []
    page._build_accounts_tab()

    page._sort_mode = "status"
    items = page._build_account_items()
    texts = [t.value for t in find_texts(items[0]) if t.value]
    assert any("b" == v for v in texts)  # 封禁账号 b 排第一


@pytest.mark.asyncio
async def test_bulk_verify_busy_guard(db):
    """已有操作进行中时，再次触发批量验证应被拒绝且不执行。"""
    from unittest.mock import AsyncMock, patch

    fp, page = make_page()
    page.db = db
    page._build_accounts_tab()
    page._selected_ids = {1}
    page._busy = True  # 模拟已有操作进行中

    with patch("tieba_mecha.web.pages.accounts.refresh_account", new_callable=AsyncMock) as m:
        await page._bulk_verify_accounts(None)
        m.assert_not_called()
    assert page._selected_ids == {1}  # 选择未被消费


# ── 全域战略吧库 ──

def make_matrix_stats(n):
    return [
        {
            "fname": f"吧{i}",
            "post_group": "",
            "account_count": i % 3,
            "account_names": "",
            "success_count": i,
            "is_target": i % 5 == 0,
            "is_banned": i == 0,
            "deleted_count": 1 if i == 0 else 0,
        }
        for i in range(n)
    ]


def test_matrix_pagination():
    fp, page = make_page()
    page._build_strategic_tab()
    page._matrix_stats = make_matrix_stats(25)

    items = page._build_matrix_items()
    assert len(items) == page._matrix_page_size  # 第一页 20 条
    assert page._matrix_filtered_count == 25
    assert "第 1/2 页" in page._matrix_page_info.value
    assert page._matrix_prev_btn.disabled is True
    assert page._matrix_next_btn.disabled is False


@pytest.mark.asyncio
async def test_matrix_pagination_next_prev():
    fp, page = make_page()
    page._build_strategic_tab()
    page._matrix_stats = make_matrix_stats(25)
    page._build_matrix_items()

    await page._on_matrix_next_page()
    items = page._build_matrix_items()
    assert len(items) == 5
    assert "第 2/2 页" in page._matrix_page_info.value
    assert page._matrix_next_btn.disabled is True

    await page._on_matrix_prev_page()
    page._build_matrix_items()
    assert "第 1/2 页" in page._matrix_page_info.value


@pytest.mark.asyncio
async def test_matrix_search_filters_and_resets_page():
    fp, page = make_page()
    page._build_strategic_tab()
    page._active_tab_index = 1  # 吧库 tab 活动时 refresh_ui 才会重建列表
    page._matrix_stats = make_matrix_stats(25)

    # 翻到第 2 页后搜索应重置回第 1 页
    await page._on_matrix_next_page()
    page._on_matrix_search_change(FakeEvent(value="吧2"))
    page._build_matrix_items()
    assert page._matrix_current_page == 1
    # 匹配 吧2, 吧20..吧24 → 6 条
    assert page._matrix_filtered_count == 6

    page._on_clear_matrix_search(FakeEvent())
    assert page._matrix_search_text == ""
    page._build_matrix_items()
    assert page._matrix_filtered_count == 25


@pytest.mark.asyncio
async def test_matrix_select_all_respects_banned_filter():
    fp, page = make_page()
    page._build_strategic_tab()
    page._matrix_stats = make_matrix_stats(25)
    page._matrix_banned_filter = True
    page._build_matrix_items()

    page._on_matrix_select_all(FakeEvent(value=True))
    assert page._matrix_selected_fnames == {"吧0"}

    page._matrix_banned_filter = False
    page._build_matrix_items()
    page._on_matrix_select_all(FakeEvent(value=True))
    assert len(page._matrix_selected_fnames) == 25


@pytest.mark.asyncio
async def test_matrix_banned_filter_toggles_state():
    fp, page = make_page()
    page._build_strategic_tab()
    page._matrix_stats = make_matrix_stats(25)

    page._on_toggle_banned_filter(FakeEvent())
    assert page._matrix_banned_filter is True
    page._build_matrix_items()
    assert page._matrix_filtered_count == 1

    page._on_toggle_banned_filter(FakeEvent())
    assert page._matrix_banned_filter is False


def test_update_matrix_header_stats():
    fp, page = make_page()
    page._build_strategic_tab()
    stats = make_matrix_stats(4)
    stats[0]["account_count"] = 2
    stats[1]["account_count"] = 0
    stats[2]["account_count"] = 1
    stats[3]["account_count"] = 0
    page._matrix_stats = stats

    page._update_matrix_header()
    assert "战略资源: 4 个贴吧" in page.matrix_header_info.value
    assert "覆盖率 50.0%" in page.matrix_header_info.value


# ── 存活分析 ──

def test_survival_header_calculation():
    fp, page = make_page()
    page._build_survival_tab()

    page._survival_stats = {"total": 8, "alive": 7, "dead": 1, "unknown": 0}
    page._update_survival_header()
    assert page.survival_rate_display.value == "存活率: 87.5%"

    page._survival_stats = {"total": 0, "alive": 0, "dead": 0, "unknown": 0}
    page._update_survival_header()
    assert page.survival_rate_display.value == "存活率: 0.0%"


def test_build_survival_items_empty_and_filter():
    fp, page = make_page()
    page._build_survival_tab()

    page._survival_by_account = []
    items = page._build_survival_items()
    assert len(items) == 1  # 空状态提示

    page._survival_by_account = [
        {"account_name": "a1", "total": 10, "alive": 9, "dead": 1, "unknown": 0},
        {"account_name": "b2", "total": 4, "alive": 1, "dead": 3, "unknown": 0},
    ]
    page._survival_search_text = "a1"
    items = page._build_survival_items()
    assert len(items) == 1


# ── 异常记录 ──

@pytest.mark.asyncio
async def test_exception_tab_empty_state():
    from unittest.mock import AsyncMock

    fp, page = make_page()
    page._build_exception_tab()
    page.db = SimpleNamespace(get_captcha_events=AsyncMock(return_value=[]))

    await page._load_exception_events()
    assert len(page.exception_list.controls) == 1
    assert page.exception_pending_count.value == "待处理: 0"


@pytest.mark.asyncio
async def test_exception_tab_lists_events():
    from datetime import datetime
    from unittest.mock import AsyncMock

    fp, page = make_page()
    page._build_exception_tab()
    page._accounts = make_accounts()
    events = [
        {
            "id": 7,
            "status": "pending",
            "reason": "触发风控",
            "account_id": 1,
            "task_id": "T-1",
            "created_at": datetime(2026, 9, 16, 8, 30),
            "resolved_at": None,
        },
        {
            "id": 6,
            "status": "resolved",
            "reason": "测试",
            "account_id": 2,
            "task_id": "T-0",
            "created_at": datetime(2026, 9, 15, 8, 30),
            "resolved_at": datetime(2026, 9, 15, 9, 0),
        },
    ]
    page.db = SimpleNamespace(get_captcha_events=AsyncMock(return_value=events))

    await page._load_exception_events()
    assert len(page.exception_list.controls) == 2
    assert page.exception_pending_count.value == "待处理: 1"


# ── 数据模型 ──

def test_account_info_carries_last_verified():
    """AccountInfo 必须携带 last_verified，账号卡片的“最后检测”
    悬浮提示才能反映真实的最近验证时间。"""
    from datetime import datetime

    assert "last_verified" in AccountInfo.__dataclass_fields__
    info = AccountInfo(
        id=1, name="a", user_id=1, user_name="a",
        is_active=True, last_verified=datetime(2026, 9, 16, 8, 0),
    )
    assert info.last_verified == datetime(2026, 9, 16, 8, 0)


@pytest.mark.asyncio
async def test_list_accounts_populates_last_verified():
    from datetime import datetime
    from unittest.mock import AsyncMock

    ts = datetime(2026, 9, 16, 8, 30)
    db = SimpleNamespace(
        get_accounts=AsyncMock(return_value=[
            SimpleNamespace(
                id=1, name="a", user_id=1, user_name="a", is_active=True,
                status="active", cuid="C", user_agent="UA", proxy_id=None,
                post_weight=5, is_maint_enabled=False, last_maint_at=None,
                last_verified=ts,
            )
        ])
    )
    accounts = await list_accounts(db)
    assert accounts[0].last_verified == ts


def test_survival_search_field_exists_and_filters():
    """存活分析页应有搜索入口，输入后按账号名过滤列表。"""
    fp, page = make_page()
    page._build_survival_tab()
    assert hasattr(page, "survival_search_field")

    page._survival_by_account = [
        {"account_name": "a1", "total": 10, "alive": 9, "dead": 1, "unknown": 0},
        {"account_name": "b2", "total": 4, "alive": 1, "dead": 3, "unknown": 0},
    ]
    page._on_survival_search_change(FakeEvent(value="a1"))
    assert page._survival_search_text == "a1"
    items = page._build_survival_items()
    assert len(items) == 1


def test_build_account_items_shows_hint_when_filter_empty():
    """有账号但搜索/筛选无匹配时，应显示提示而非空白。"""
    fp, page = make_page()
    page._accounts = make_accounts()
    page._proxies = []
    page._build_accounts_tab()

    page._search_text = "zzz无匹配"
    items = page._build_account_items()
    assert len(items) == 1
    assert "未找到匹配的账号" in items[0].content.value


@pytest.mark.asyncio
async def test_delete_account_cascades_history_and_events(db):
    """删除账号应级联清理其权重历史与验证码事件，避免孤儿数据。"""
    from tieba_mecha.core.account import encrypt_value

    acc = await db.add_account(
        name="casc", bduss=encrypt_value("x"), stoken="",
        user_id=99, user_name="casc",
    )
    await db.update_account_weight(acc.id, 9, source="manual")  # 写入一条权重历史
    await db.save_captcha_event(account_id=acc.id, reason="test")

    assert await db.delete_account(acc.id) is True
    assert await db.get_weight_history(account_id=acc.id) == []
    assert await db.get_captcha_events(account_id=acc.id) == []
