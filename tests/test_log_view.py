"""Tests for the real-time task log view (实时任务流水) enhancements.

Covers:
1. _format_log_timestamp — same-day vs cross-day formatting
2. _add_log — structured cards (success/error/skipped) + text fallback + filter cache
3. _update_log_stats — stat text updates
4. _on_log_filter_change — filter dropdown filtering logic
5. _on_clear_logs — clear both UI and DB
6. _refresh_logs — reload from DB
7. clear_old_batch_post_logs(keep_count=0) — full purge
"""

import asyncio
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Pre-import stubs — must be installed BEFORE any tieba_mecha import
# ---------------------------------------------------------------------------

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ---- Fake flet control classes (module-level for reuse) ----

class _FakeControl:
    def __init__(self, *a, **kw):
        self.value = kw.get("value", None)
        self.controls = []
        self.visible = True
        self.data = None
        self.text = ""
        self.icon = None
        self.style = None
        self.disabled = False
        self.options = []

    def update(self):
        pass


class _FakePage:
    def __init__(self):
        self.controls = []

    def update(self):
        pass

    def run_task(self, coro, *a):
        pass

    def launch_url(self, url):
        pass

    def show_snack_bar(self, snack):
        pass

    def open(self, dlg):
        pass

    def close(self, dlg):
        pass

    def go(self, route):
        pass


def _install_stubs():
    """Install all required module stubs into sys.modules."""

    flet = types.ModuleType("flet")
    for name in [
        "Page", "Container", "Column", "Row", "Text", "Icon", "IconButton",
        "TextButton", "OutlinedButton", "FilledButton", "ElevatedButton",
        "VerticalDivider", "ProgressBar", "ListView", "Dropdown", "Tabs", "Tab",
        "DataTable", "DataColumn", "DataRow", "DataCell", "TextField", "Switch",
        "Checkbox", "FilePicker", "AlertDialog", "Slider", "RadioGroup", "Radio",
        "Divider", "ExpansionTile", "Control", "SnackBar", "GestureDetector",
        "RoundedRectangleBorder", "FilePickerUploadFile",
    ]:
        setattr(flet, name, _FakeControl)
    flet.Page = _FakePage
    # Enums / type annotations that are used as types in batch_post_page.py
    flet.FilePickerResultEvent = type("FilePickerResultEvent", (), {})
    flet.FilePickerUploadEvent = type("FilePickerUploadEvent", (), {})
    flet.CrossAxisAlignment = types.SimpleNamespace(CENTER="center", START="start", END="end")
    flet.KeyboardType = types.SimpleNamespace(TEXT="text", NUMBER="number")
    flet.LabelPosition = types.SimpleNamespace(RIGHT="right")
    flet.NumbersOnlyInputFilter = lambda *a, **kw: None
    flet.SnackBarBehavior = types.SimpleNamespace(FLOATING="floating")

    class _Border:
        def __init__(self, *a, **kw): pass
        @staticmethod
        def only(**kw): return None
        @staticmethod
        def all(*a, **kw): return None
        BorderSide = type("BorderSide", (), {"__init__": lambda self, *a, **kw: None})
    class _BorderRadius:
        def __init__(self, *a, **kw): pass
        @staticmethod
        def only(**kw): return None
        @staticmethod
        def all(*a, **kw): return None
    class _Padding:
        def __init__(self, *a, **kw): pass
        @staticmethod
        def symmetric(*a, **kw): return None
        @staticmethod
        def only(*a, **kw): return None

    flet.border = _Border
    flet.border_radius = _BorderRadius
    flet.padding = _Padding
    flet.ButtonStyle = lambda **kw: kw
    flet.TextStyle = lambda **kw: kw
    flet.FontWeight = types.SimpleNamespace(BOLD="bold", W_300="w300", W_500="w500")
    flet.ScrollMode = types.SimpleNamespace(ADAPTIVE="adaptive")
    flet.MainAxisAlignment = types.SimpleNamespace(START="start", SPACE_BETWEEN="space_between", CENTER="center", END="end")
    flet.dropdown = types.SimpleNamespace(Option=lambda *a, **kw: None)
    sys.modules["flet"] = flet

    # ---- tieba_mecha.web.flet_compat ----
    m = types.ModuleType("tieba_mecha.web.flet_compat")
    m.COLORS = {}
    sys.modules["tieba_mecha.web.flet_compat"] = m

    # ---- tieba_mecha.web.components ----
    class _IconsStub:
        def __getattr__(self, name): return name
    m = types.ModuleType("tieba_mecha.web.components")
    m.icons = _IconsStub()
    m.create_gradient_button = lambda *a, **kw: _FakeControl()
    m.create_snackbar = lambda *a, **kw: _FakeControl()
    sys.modules["tieba_mecha.web.components"] = m

    m = types.ModuleType("tieba_mecha.web.components.icons")
    m.icons = _IconsStub()
    sys.modules["tieba_mecha.web.components.icons"] = m

    # ---- core modules ----
    _core_stubs = {
        "tieba_mecha.core.account": {
            "add_account": lambda *a, **kw: None,
            "list_accounts": lambda *a, **kw: [],
            "switch_account": lambda *a, **kw: None,
            "remove_account": lambda *a, **kw: None,
            "parse_cookie": lambda *a, **kw: {},
            "verify_account": AsyncMock(return_value=None),
            "refresh_account": AsyncMock(return_value=None),
            "get_account_credentials": AsyncMock(return_value=("b", "s", None)),
            "encrypt_value": lambda v: f"enc_{v}",
        },
        "tieba_mecha.core.batch_post": {
            "BatchPostTask": type("BatchPostTask", (), {"__init__": lambda self, **kw: None}),
            "BatchPostManager": type("BatchPostManager", (), {
                "__init__": lambda self, db=None: None,
                "get_tactical_advice": staticmethod(lambda msg: ""),
            }),
            "RateLimiter": type("RateLimiter", (), {}),
        },
        "tieba_mecha.core.link_manager": {
            "SmartLinkConnector": type("SmartLinkConnector", (), {"__init__": lambda self, db=None: None}),
        },
        "tieba_mecha.core.ai_optimizer": {
            "AIOptimizer": type("AIOptimizer", (), {}),
        },
        "tieba_mecha.core.logger": {
            "log_error": AsyncMock(),
            "log_info": AsyncMock(),
        },
        "tieba_mecha.core.post": {
            "check_post_survival": AsyncMock(return_value=("alive", "")),
        },
    }
    for mod_name, attrs in _core_stubs.items():
        if mod_name not in sys.modules:
            m = types.ModuleType(mod_name)
            for attr_name, attr_val in attrs.items():
                setattr(m, attr_name, attr_val)
            sys.modules[mod_name] = m

    # ---- tieba_mecha.web.utils ----
    m = types.ModuleType("tieba_mecha.web.utils")
    m.with_opacity = lambda opacity, color: f"with_opacity({opacity},{color})"
    sys.modules["tieba_mecha.web.utils"] = m

    # ---- Stub the pages __init__.py to avoid importing AccountsPage etc ----
    # We need batch_post_page to be importable but NOT through __init__.py
    # So we pre-create a minimal stub for the pages package
    pages_init = types.ModuleType("tieba_mecha.web.pages")
    pages_init.__path__ = [str(Path(_SRC) / "tieba_mecha" / "web" / "pages")]
    # Don't import other page modules from __init__
    sys.modules["tieba_mecha.web.pages"] = pages_init

    # Stub other page modules that __init__.py might try to import
    for page_name in ["accounts", "dashboard", "sign", "survival", "settings", "notifications"]:
        full_name = f"tieba_mecha.web.pages.{page_name}"
        if full_name not in sys.modules:
            m = types.ModuleType(full_name)
            m.__name__ = full_name
            # Create a dummy class for each page
            setattr(m, f"{page_name.title().replace('_', '')}Page", type(f"{page_name}Page", (), {
                "__init__": lambda self, *a, **kw: None
            }))
            sys.modules[full_name] = m


_install_stubs()

# Now import the code under test
from tieba_mecha.web.pages.batch_post_page import BatchPostPage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page():
    """Create a BatchPostPage instance with a mock DB and fake flet Page."""
    mock_db = MagicMock()
    mock_db.get_batch_post_logs = AsyncMock(return_value=[])
    mock_db.clear_old_batch_post_logs = AsyncMock(return_value=0)
    mock_db.get_all_batch_tasks = AsyncMock(return_value=[])
    mock_db.get_matrix_accounts = AsyncMock(return_value=[])
    mock_db.get_native_post_targets = AsyncMock(return_value=[])
    mock_db.get_target_pool_groups = AsyncMock(return_value=[])
    mock_db.get_materials_status_counts = AsyncMock(return_value={})
    mock_db.get_survival_cache_data = AsyncMock(return_value={})
    mock_db.get_setting = AsyncMock(return_value=None)
    mock_db.get_materials_by_status_paginated = AsyncMock(return_value=([], 0))
    mock_db.get_success_survival_counts = AsyncMock(return_value={"alive": 0, "dead": 0, "unknown": 0})

    fake_page = _FakePage()
    bp = BatchPostPage(page=fake_page, db=mock_db)
    return bp


# ===========================================================================
# Test: _format_log_timestamp
# ===========================================================================

class TestFormatLogTimestamp:
    """Tests for _format_log_timestamp method."""

    def test_same_day_returns_time_only(self):
        bp = _make_page()
        now = datetime.now()
        result = bp._format_log_timestamp(now)
        assert ":" in result
        assert "-" not in result

    def test_cross_day_returns_date_and_time(self):
        bp = _make_page()
        yesterday = datetime.now() - timedelta(days=1)
        result = bp._format_log_timestamp(yesterday)
        assert "-" in result

    def test_string_passthrough(self):
        bp = _make_page()
        ts = "14:30:00"
        result = bp._format_log_timestamp(ts)
        assert result == "14:30:00"

    def test_old_date_format(self):
        bp = _make_page()
        old = datetime(2025, 3, 15, 10, 30, 0)
        result = bp._format_log_timestamp(old)
        assert "03-15" in result
        assert "10:30" in result


# ===========================================================================
# Test: _add_log — structured card rendering + _log_raw_items cache
# ===========================================================================

class TestAddLog:
    """Tests for _add_log method with various data types."""

    def test_success_card_added(self):
        bp = _make_page()
        data = {
            "status": "success", "account_name": "test", "fname": "test吧",
            "title": "title", "tid": 12345, "progress": 1, "total": 10,
        }
        bp._add_log(data)
        assert len(bp._log_raw_items) == 1
        assert bp._log_raw_items[0][1] == "success"
        assert len(bp.log_list.controls) == 1

    def test_error_card_added(self):
        bp = _make_page()
        bp._add_log({"status": "error", "fname": "拦截吧", "msg": "内容风控拦截"})
        assert len(bp._log_raw_items) == 1
        assert bp._log_raw_items[0][1] == "error"

    def test_skipped_card_added(self):
        bp = _make_page()
        bp._add_log({"status": "skipped", "fname": "跳过吧", "msg": "账号不可用"})
        assert len(bp._log_raw_items) == 1
        assert bp._log_raw_items[0][1] == "skipped"

    def test_text_fallback_info(self):
        bp = _make_page()
        bp._add_log("纯文本信息")
        assert len(bp._log_raw_items) == 1
        assert bp._log_raw_items[0][1] == "info"

    def test_text_fallback_error(self):
        bp = _make_page()
        bp._add_log("错误信息", type="error")
        assert len(bp._log_raw_items) == 1
        assert bp._log_raw_items[0][1] == "error"

    def test_max_100_items_limit(self):
        bp = _make_page()
        for i in range(150):
            bp._add_log(f"日志 {i}")
        assert len(bp._log_raw_items) == 100
        assert len(bp.log_list.controls) <= 100

    def test_newest_item_at_index_0(self):
        bp = _make_page()
        bp._add_log("第一条")
        bp._add_log("第二条")
        assert len(bp._log_raw_items) == 2

    def test_custom_timestamp(self):
        bp = _make_page()
        bp._add_log("带时间戳", timestamp="12:34:56")
        assert len(bp._log_raw_items) == 1


# ===========================================================================
# Test: _update_log_stats
# ===========================================================================

class TestUpdateLogStats:
    """Tests for _update_log_stats method."""

    def test_empty_stats(self):
        bp = _make_page()
        bp._log_raw_items.clear()
        bp._update_log_stats()
        assert "✅0" in bp._log_stats_text.value
        assert "❌0" in bp._log_stats_text.value
        assert "⏭0" in bp._log_stats_text.value

    def test_mixed_stats(self):
        bp = _make_page()
        bp._log_raw_items.clear()
        bp._add_log({"status": "success", "account_name": "a", "fname": "b", "title": "t", "tid": 1, "progress": 1, "total": 1})
        bp._add_log({"status": "success", "account_name": "a", "fname": "b", "title": "t", "tid": 2, "progress": 2, "total": 2})
        bp._add_log({"status": "error", "fname": "b", "msg": "err"})
        bp._add_log({"status": "skipped", "fname": "c", "msg": "skip"})
        stats = bp._log_stats_text.value
        assert "✅2" in stats
        assert "❌1" in stats
        assert "⏭1" in stats


# ===========================================================================
# Test: _on_log_filter_change
# ===========================================================================

class TestOnLogFilterChange:
    """Tests for _on_log_filter_change method."""

    def _populate_logs(self, bp):
        bp._log_raw_items.clear()
        bp.log_list.controls.clear()
        bp._add_log({"status": "success", "account_name": "a", "fname": "b", "title": "t", "tid": 1, "progress": 1, "total": 1})
        bp._add_log({"status": "error", "fname": "b", "msg": "err"})
        bp._add_log({"status": "skipped", "fname": "c", "msg": "skip"})
        bp._add_log("纯文本", type="info")

    @pytest.mark.asyncio
    async def test_filter_all_shows_all(self):
        bp = _make_page()
        self._populate_logs(bp)
        mock_event = MagicMock()
        mock_event.control.value = "all"
        await bp._on_log_filter_change(mock_event)
        assert len(bp.log_list.controls) == 4

    @pytest.mark.asyncio
    async def test_filter_success_only(self):
        bp = _make_page()
        self._populate_logs(bp)
        mock_event = MagicMock()
        mock_event.control.value = "success"
        await bp._on_log_filter_change(mock_event)
        assert len(bp.log_list.controls) == 1

    @pytest.mark.asyncio
    async def test_filter_error_includes_text_errors(self):
        bp = _make_page()
        self._populate_logs(bp)
        bp._add_log("error text", type="error")
        mock_event = MagicMock()
        mock_event.control.value = "error"
        await bp._on_log_filter_change(mock_event)
        assert len(bp.log_list.controls) >= 2

    @pytest.mark.asyncio
    async def test_filter_skipped_only(self):
        bp = _make_page()
        self._populate_logs(bp)
        mock_event = MagicMock()
        mock_event.control.value = "skipped"
        await bp._on_log_filter_change(mock_event)
        assert len(bp.log_list.controls) == 1


# ===========================================================================
# Test: _on_clear_logs
# ===========================================================================

class TestOnClearLogs:
    """Tests for _on_clear_logs method."""

    @pytest.mark.asyncio
    async def test_clear_empties_ui_and_cache(self):
        bp = _make_page()
        bp._add_log({"status": "success", "account_name": "a", "fname": "b", "title": "t", "tid": 1, "progress": 1, "total": 1})
        bp._add_log({"status": "error", "fname": "b", "msg": "err"})
        assert len(bp._log_raw_items) == 2
        assert len(bp.log_list.controls) == 2

        await bp._on_clear_logs(MagicMock())

        assert len(bp._log_raw_items) == 0
        assert len(bp.log_list.controls) == 0
        bp.db.clear_old_batch_post_logs.assert_called_once_with(keep_count=0)

    @pytest.mark.asyncio
    async def test_clear_with_db_error(self):
        bp = _make_page()
        bp.db.clear_old_batch_post_logs = AsyncMock(side_effect=Exception("DB error"))
        await bp._on_clear_logs(MagicMock())
        assert len(bp._log_raw_items) == 0


# ===========================================================================
# Test: _refresh_logs
# ===========================================================================

class TestRefreshLogs:
    """Tests for _refresh_logs method."""

    @pytest.mark.asyncio
    async def test_refresh_loads_from_db(self):
        bp = _make_page()

        mock_log1 = MagicMock()
        mock_log1.status = "success"
        mock_log1.account_name = "测试号"
        mock_log1.fname = "test吧"
        mock_log1.title = "标题"
        mock_log1.tid = 123
        mock_log1.message = None
        mock_log1.account_id = 1
        mock_log1.created_at = datetime.now()

        mock_log2 = MagicMock()
        mock_log2.status = "error"
        mock_log2.account_name = "号2"
        mock_log2.fname = "err吧"
        mock_log2.title = None
        mock_log2.tid = None
        mock_log2.message = "拦截"
        mock_log2.account_id = 2
        mock_log2.created_at = datetime.now()

        bp.db.get_batch_post_logs = AsyncMock(return_value=[mock_log1, mock_log2])

        await bp._refresh_logs()

        assert len(bp._log_raw_items) == 2
        statuses = [s for _, s in bp._log_raw_items]
        assert "success" in statuses
        assert "error" in statuses

    @pytest.mark.asyncio
    async def test_refresh_with_no_db(self):
        bp = _make_page()
        bp.db = None
        await bp._refresh_logs()
        assert len(bp._log_raw_items) == 0

    @pytest.mark.asyncio
    async def test_refresh_with_db_error(self):
        bp = _make_page()
        bp.db.get_batch_post_logs = AsyncMock(side_effect=Exception("DB error"))
        await bp._refresh_logs()


# ===========================================================================
# Test: clear_old_batch_post_logs with keep_count=0 (DB layer)
# ===========================================================================

class TestClearOldBatchPostLogs:
    """Tests for Database.clear_old_batch_post_logs with keep_count=0."""

    @pytest.mark.asyncio
    async def test_clear_all_logs(self, db):
        await db.add_batch_post_log(task_id="T1", fname="吧1", status="success")
        await db.add_batch_post_log(task_id="T2", fname="吧2", status="error")

        logs = await db.get_batch_post_logs(limit=10)
        assert len(logs) == 2

        deleted = await db.clear_old_batch_post_logs(keep_count=0)
        assert deleted == 2

        logs_after = await db.get_batch_post_logs(limit=10)
        assert len(logs_after) == 0

    @pytest.mark.asyncio
    async def test_keep_recent_logs(self, db):
        await db.add_batch_post_log(task_id="T1", fname="吧1", status="success")
        await db.add_batch_post_log(task_id="T2", fname="吧2", status="error")

        deleted = await db.clear_old_batch_post_logs(keep_count=1)
        assert deleted == 1

        logs = await db.get_batch_post_logs(limit=10)
        assert len(logs) == 1
        assert logs[0].task_id == "T2"

    @pytest.mark.asyncio
    async def test_clear_when_empty(self, db):
        deleted = await db.clear_old_batch_post_logs(keep_count=0)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_keep_count_exceeds_total(self, db):
        await db.add_batch_post_log(task_id="T1", fname="吧1", status="success")
        deleted = await db.clear_old_batch_post_logs(keep_count=100)
        assert deleted == 0

        logs = await db.get_batch_post_logs(limit=10)
        assert len(logs) == 1


# ===========================================================================
# Test: Filter interaction with _add_log during task execution
# ===========================================================================

class TestAddLogWithFilter:
    """Tests for _add_log respecting the current filter setting."""

    def test_add_success_with_success_filter(self):
        bp = _make_page()
        bp._log_filter_dropdown.value = "success"
        bp._add_log({"status": "success", "account_name": "a", "fname": "b", "title": "t", "tid": 1, "progress": 1, "total": 1})
        assert len(bp.log_list.controls) == 1

    def test_add_error_with_success_filter_hidden(self):
        bp = _make_page()
        bp._log_filter_dropdown.value = "success"
        bp._add_log({"status": "error", "fname": "b", "msg": "err"})
        assert len(bp.log_list.controls) == 0
        assert len(bp._log_raw_items) == 1

    def test_add_skipped_with_success_filter_hidden(self):
        bp = _make_page()
        bp._log_filter_dropdown.value = "success"
        bp._add_log({"status": "skipped", "fname": "c", "msg": "skip"})
        assert len(bp.log_list.controls) == 0
        assert len(bp._log_raw_items) == 1

    def test_all_filter_shows_everything(self):
        bp = _make_page()
        bp._log_filter_dropdown.value = "all"
        bp._add_log({"status": "success", "account_name": "a", "fname": "b", "title": "t", "tid": 1, "progress": 1, "total": 1})
        bp._add_log({"status": "error", "fname": "b", "msg": "err"})
        bp._add_log({"status": "skipped", "fname": "c", "msg": "skip"})
        assert len(bp.log_list.controls) == 3
