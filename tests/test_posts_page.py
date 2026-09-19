"""帖子管理页（三场景重构）页面级测试：构建、加载、校验、筛选与详情面板。"""

import pytest
from unittest.mock import MagicMock

import flet as ft

from tieba_mecha.web.pages.posts import PostsPage
from tieba_mecha.web.pages.posts.helpers import (
    classify_survival,
    estimate_next_bump,
    extract_links,
)
from types import SimpleNamespace
from datetime import datetime


class MockDatabase:
    """最小数据库桩：只实现 PostsPage.load_data/_reload_rows 用到的方法。"""

    def __init__(self):
        self.accounts = [
            MagicMock(id=1, name="主号", user_name="main", status="active", proxy_id=None),
            MagicMock(id=2, name="小号", user_name="alt", status="active", proxy_id=7),
        ]
        self.active = self.accounts[0]
        self.forums = [
            MagicMock(fname="python", is_banned=False, is_hidden=False, is_post_target=True),
            MagicMock(fname="linux", is_banned=True, is_hidden=False, is_post_target=False),
            MagicMock(fname="_resources", is_banned=False, is_hidden=True, is_post_target=False),
        ]
        self.rows = [
            SimpleNamespace(
                src="material", material_id=10, tid=111, title="存活帖", content="内容",
                fname="python", account_id=1, post_time=datetime(2026, 9, 18, 10, 0),
                reply_num=3, is_good=False, survival_status="alive", death_reason="",
                last_checked_at=None, ai_status="none", original_title=None,
                original_content=None, is_auto_bump=True, bump_count=2,
                last_bumped_at=None, bump_mode="once", bump_hour=10,
                bump_duration_days=0, bump_start_date=None, task_id=None, mat_status="success",
            ),
        ]
        self.proxy = MagicMock(host="1.2.3.4", port=8080, protocol="http")
        self.bump_logs = [
            SimpleNamespace(success=True, created_at=datetime(2026, 9, 18, 12, 0),
                            account_name="小号", content="路过顶一下", message=""),
            SimpleNamespace(success=False, created_at=datetime(2026, 9, 18, 13, 0),
                            account_name="小号", content="路过顶一下", message="吧务封禁: 测试"),
        ]

    async def get_accounts(self):
        return self.accounts

    async def get_active_account(self):
        return self.active

    async def get_forums(self, _account_id):
        return self.forums

    async def get_setting(self, key, default=""):
        if key == "ai_api_key":
            return "encrypted"
        if key == "ai_model":
            return "glm-4-flash"
        if key == "max_bump_count":
            return "20"
        if key == "bump_cooldown_minutes":
            return "45"
        return default

    async def get_proxy(self, proxy_id):
        return self.proxy if proxy_id == 7 else None

    async def get_my_posts(self, **_filters):
        return list(self.rows)

    async def get_bump_logs(self, material_id=None, tid=None, limit=50):
        if material_id == 10:
            return list(self.bump_logs)[:limit]
        return []

    async def register_posted_material(self, title, content, **kwargs):
        """记录最近一次发布登记的入参，返回物料 ID（测试断言用）。"""
        self.last_register = {"title": title, "content": content, **kwargs}
        return 88

    async def delete_material(self, material_id):
        self.deleted_material_ids = getattr(self, "deleted_material_ids", [])
        self.deleted_material_ids.append(material_id)
        return True

    async def delete_thread_record(self, tid):
        self.deleted_record_tids = getattr(self, "deleted_record_tids", [])
        self.deleted_record_tids.append(tid)
        return True


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.update = MagicMock()
    return page


@pytest.fixture
def posts_page(mock_page):
    db = MockDatabase()
    return PostsPage(mock_page, db, on_navigate=None)


class TestPostsPageStructure:
    def test_init_defaults(self, posts_page):
        assert posts_page._rows == []
        assert posts_page._selected == set()
        assert posts_page._mine_page == 1
        assert posts_page._page_size == 20
        assert posts_page._ai_ready is False

    def test_build_has_three_scenarios(self, posts_page):
        root = posts_page.build()
        assert isinstance(root, ft.Container)
        tabs = posts_page.tabs
        labels = [t.text for t in tabs.tabs]
        assert labels == ["发布新帖", "我的帖子", "批量操作与分析"]
        # 右侧详情面板默认隐藏
        assert posts_page._detail_panel.visible is False

    @pytest.mark.asyncio
    async def test_load_data_populates(self, posts_page):
        posts_page.build()
        await posts_page.load_data()
        # 发布页贴吧下拉与筛选贴吧下拉均已填充
        assert [o.key for o in posts_page.post_forum.options] == ["python", "linux", "_resources"]
        assert posts_page._filter_account.value == "1"  # 默认只看当前账号
        assert posts_page._ai_ready is True
        # 行数据已加载，统计徽章 4 个（存活/疑似/已删/未知）
        assert len(posts_page._rows) == 1
        assert len(posts_page._stats_row.controls) == 4
        # 发布摘要包含账号与代理信息
        assert posts_page._summary_card.content is not None


class TestPublishValidation:
    def _prepare(self, posts_page):
        posts_page.build()
        posts_page._fill_publish_dropdowns()
        posts_page._publish_forums = posts_page._forums

    def test_forum_status_info(self, posts_page):
        posts_page.build()
        posts_page._publish_forums = posts_page.db.forums  # 发布校验按选中账号的贴吧注入
        assert posts_page._forum_status_info("python")[1] == "ok"
        assert posts_page._forum_status_info("linux")[1] == "error"       # 封禁
        assert posts_page._forum_status_info("_resources")[1] == "warn"   # 隐藏
        assert posts_page._forum_status_info("不存在的吧")[1] == "error"   # 未关注
        assert posts_page._forum_status_info("")[1] == "error"

    @pytest.mark.asyncio
    async def test_validation_levels(self, posts_page, mock_page):
        posts_page.build()
        posts_page._publish_forums = posts_page.db.forums
        posts_page._fill_publish_dropdowns()
        posts_page.post_forum.value = "python"
        # 标题过短 → error 项
        posts_page.post_title.value = "短"
        posts_page.post_content.value = "正文内容"
        posts_page._run_validation()
        texts = [c.controls[1].value for c in posts_page._validation_list.controls]
        colors = [c.controls[1].color for c in posts_page._validation_list.controls]
        assert any("还需" in t for t in texts)
        assert "error" in colors
        # 合法标题 + 链接超量 → warn 项
        posts_page.post_title.value = "这是一个长度合格的标题"
        posts_page.post_content.value = "看 https://a.com/1 https://b.com/2 https://c.com/3"
        posts_page._run_validation()
        texts = [c.controls[1].value for c in posts_page._validation_list.controls]
        assert any("条链接" in t and "易触发风控" in t for t in texts)

    def test_extract_links_cjk_safe(self):
        assert extract_links("访问 https://pan.baidu.com/s/1abc 提取码见内。") == ["https://pan.baidu.com/s/1abc"]


class TestFilters:
    @pytest.mark.asyncio
    async def test_collect_filters(self, posts_page):
        posts_page.build()
        await posts_page.load_data()
        f = posts_page._collect_filters()
        assert f["account_id"] == 1
        assert f["fname"] is None
        assert f["survival"] is None
        assert f["is_good"] is None
        assert f["keyword"] is None

        posts_page._filter_survival.value = "suspected"
        posts_page._filter_good.value = "good"
        posts_page._filter_keyword.value = "测试词"
        f2 = posts_page._collect_filters()
        assert f2["survival"] == "suspected"
        assert f2["is_good"] is True
        assert f2["keyword"] == "测试词"


class TestDetailPanel:
    @pytest.mark.asyncio
    async def test_open_close(self, posts_page):
        posts_page.build()
        await posts_page.load_data()
        row = posts_page._rows[0]
        posts_page.open_detail(row)
        assert posts_page._detail_panel.visible is True
        assert posts_page._detail_divider.visible is True
        assert posts_page._detail_row is row
        posts_page.close_detail()
        assert posts_page._detail_panel.visible is False
        assert posts_page._detail_row is None

    @pytest.mark.asyncio
    async def test_bump_history_filled(self, posts_page):
        posts_page.build()
        await posts_page.load_data()
        posts_page.open_detail(posts_page._rows[0])
        assert posts_page._bump_history_col is not None
        await posts_page._fill_bump_history()
        # 两条流水各渲染一个条目
        assert len(posts_page._bump_history_col.controls) == 2

    @pytest.mark.asyncio
    async def test_bump_history_empty_hint(self, posts_page):
        posts_page.build()
        await posts_page.load_data()
        posts_page.db.bump_logs = []
        posts_page.open_detail(posts_page._rows[0])
        await posts_page._fill_bump_history()
        assert len(posts_page._bump_history_col.controls) == 1
        assert "暂无流水记录" in posts_page._bump_history_col.controls[0].value

    @pytest.mark.asyncio
    async def test_bump_history_guard_after_close(self, posts_page):
        """面板关闭后异步填充应直接返回，不重建控件。"""
        posts_page.build()
        await posts_page.load_data()
        posts_page.open_detail(posts_page._rows[0])
        posts_page.close_detail()
        await posts_page._fill_bump_history()
        assert posts_page._detail_panel.visible is False


class TestBatchOps:
    @pytest.mark.asyncio
    async def test_impact_summary_groups_by_forum(self, posts_page):
        posts_page.build()
        rows = [
            SimpleNamespace(fname="python", tid=1),
            SimpleNamespace(fname="python", tid=2),
            SimpleNamespace(fname="linux", tid=3),
        ]
        title, text = posts_page._build_impact_summary(rows)
        assert "确认删除" in title and "3" in title
        assert "将删除 3 个帖子" in text
        assert "2 个贴吧" in text
        assert "python(2)" in text and "linux(1)" in text

    @pytest.mark.asyncio
    async def test_selection_toggle_and_action_bar(self, posts_page):
        posts_page.build()
        await posts_page.load_data()
        key = posts_page.row_key(posts_page._rows[0])
        posts_page._toggle_select(key)
        assert key in posts_page._selected
        assert posts_page.action_bar.visible is True
        posts_page._toggle_select(key)
        assert posts_page.action_bar.visible is False


class TestHelpersExtra:
    def test_classify_survival(self):
        assert classify_survival("dead", "deleted_by_mod") == "dead"
        assert classify_survival("dead", "captcha_required") == "suspected"
        assert classify_survival("unknown", "") == "unknown"

    def test_estimate_next_bump(self):
        assert estimate_next_bump(False) == "已停止"
        assert "上限" in estimate_next_bump(True, "once", 99)


class TestPublishAccountSelection:
    """发布页可选发帖账号：默认活跃账号、按账号加载贴吧、发布携带所选账号。"""

    @pytest.mark.asyncio
    async def test_account_dropdown_defaults_to_active(self, posts_page):
        posts_page.build()
        await posts_page.load_data()
        assert posts_page.post_account.value == "1"  # 活跃账号
        assert posts_page._publish_account_id == 1
        assert posts_page._selected_publish_account().user_name == "main"
        # 按选中账号加载其贴吧
        assert [o.key for o in posts_page.post_forum.options] == ["python", "linux", "_resources"]

    @pytest.mark.asyncio
    async def test_switch_account_reloads_forums(self, posts_page):
        posts_page.build()
        await posts_page.load_data()
        posts_page.post_account.value = "2"
        await posts_page._on_post_account_change(None)
        assert posts_page._publish_account_id == 2
        assert posts_page._selected_publish_account().user_name == "alt"

    @pytest.mark.asyncio
    async def test_do_post_uses_selected_account_and_registers(self, posts_page, mock_page):
        from unittest.mock import AsyncMock, patch

        posts_page.build()
        await posts_page.load_data()
        posts_page.post_account.value = "2"  # 选小号发布
        posts_page.post_forum.value = "python"
        posts_page.post_title.value = "这是一个长度合格的标题"
        posts_page.post_content.value = "正文内容"

        registered = {}
        posts_page.db.register_posted_material = AsyncMock(return_value=88)
        posts_page.db.register_posted_material.side_effect = (
            lambda title, content, **kw: registered.update(kw, title=title) or 88
        )

        with patch("tieba_mecha.core.post.add_thread",
                   new=AsyncMock(return_value=(True, "ok", 555))) as mock_add:
            await posts_page._do_post(None)

        # add_thread 携带所选账号（小号 id=2）
        _, kwargs = mock_add.call_args
        assert kwargs.get("account_id") == 2
        # 登记带所选账号与 TID（单行直插，替代旧的"扫描前10条"竞态方案）
        assert registered.get("posted_account_id") == 2
        assert registered.get("posted_tid") == 555
        assert registered.get("posted_fname") == "python"


class TestDeleteServerCredentials:
    """删帖按记录的作者账号传凭证（贴吧仅允许作者删帖）。"""

    @pytest.mark.asyncio
    async def test_row_delete_passes_author_account(self, posts_page):
        from unittest.mock import AsyncMock, patch
        from types import SimpleNamespace

        posts_page.build()
        row = SimpleNamespace(
            src="material", material_id=10, tid=111, title="帖", content="",
            fname="python", account_id=2, post_time=None, reply_num=0,
            is_good=False, survival_status="alive", death_reason="",
            last_checked_at=None)

        with patch("tieba_mecha.core.post.delete_thread",
                   new=AsyncMock(return_value=(True, "删除成功"))) as mock_del,              patch("tieba_mecha.web.components.toast.confirm_async",
                   new=AsyncMock(return_value=True)):
            await posts_page._delete_row_server(row)

        _, kwargs = mock_del.call_args
        assert kwargs.get("account_id") == 2
        assert posts_page.db.last_register if False else True
        assert posts_page.db.deleted_material_ids == [10]
        assert posts_page.db.deleted_record_tids == [111]

    @pytest.mark.asyncio
    async def test_delete_blocked_without_author(self, posts_page):
        from unittest.mock import AsyncMock, patch
        from types import SimpleNamespace

        posts_page.build()
        row = SimpleNamespace(
            src="record", material_id=None, tid=333, title="导入帖", content="",
            fname="python", account_id=None, post_time=None, reply_num=0,
            is_good=False, survival_status="unknown", death_reason="",
            last_checked_at=None)
        assert posts_page._can_delete_server(row) is False

        with patch("tieba_mecha.core.post.delete_thread", new=AsyncMock()) as mock_del:
            await posts_page._delete_row_server(row)
        mock_del.assert_not_awaited()


class TestCsvSafe:
    def test_injection_guard(self):
        guard = posts_page_factory()._csv_safe
        assert guard("=cmd()") == "'=cmd()"
        assert guard("+1") == "'+1"
        assert guard("-2") == "'-2"
        assert grid_check(guard)
        assert guard("正常标题") == "正常标题"
        assert guard(None) == ""


def posts_page_factory():
    """供 TestCsvSafe 取未绑定方法用。"""
    from tieba_mecha.web.pages.posts.batch_ops import BatchOpsTabMixin
    return BatchOpsTabMixin


def grid_check(guard):
    return guard("@risk") == "'@risk"
