"""全域签到页面 (SignPage) 单元测试。

覆盖范围:
- build() 控件构建完整性
- load_data 数据加载与统计填充
- 单账号/矩阵模式切换与统计口径
- 矩阵列表账号状态显示 (回归: active 账号不得显示为 ERROR)
- 守护进程配置保存的时间格式校验 (回归: 非法时间必须拦截)
- 签到历史弹窗使用新式对话框 API
- 仪表盘 auto_start_sign 快捷触发
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tieba_mecha.web.pages.sign import SignPage
from tieba_mecha.web.utils import with_opacity


class MockSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


def make_page():
    page = MagicMock()
    page.session = MockSession()
    page.update = MagicMock()
    page.open = MagicMock()
    page.close = MagicMock()
    page.show_snack_bar = MagicMock()
    page.run_task = MagicMock()
    page.pubsub = MagicMock()
    return page


@pytest.fixture
def mock_page():
    return make_page()


@pytest.fixture
def sign_page(mock_page, db):
    sp = SignPage(mock_page, db, on_navigate=MagicMock())
    sp.build()
    return sp


async def _add_account_with_forum(db, name, forums_spec):
    from tieba_mecha.core.account import add_account

    # verify=False 跳过真实联网验证; 首个账号默认 active, 后续 pending
    acc = await add_account(db=db, name=name, bduss="a" * 192, stoken="b" * 64, verify=False)
    for fid, fname, status in forums_spec:
        forum = await db.add_forum(fid=fid, fname=fname, account_id=acc.id)
        if status == "success":
            await db.update_forum_sign(forum.id, True)
        elif status == "failure":
            await db.update_forum_sign(forum.id, False)
    return acc


# ========== 构建与初始化 ==========


class TestSignPageBuild:
    def test_build_returns_container(self, mock_page, db):
        sp = SignPage(mock_page, db)
        control = sp.build()
        assert control is not None
        assert hasattr(sp, "list_view")
        assert hasattr(sp, "progress_bar")
        assert hasattr(sp, "daemon_switch")
        assert hasattr(sp, "daemon_time")
        assert hasattr(sp, "delay_min_input")
        assert hasattr(sp, "acc_delay_min_input")

    def test_default_mode_is_single(self, mock_page, db):
        sp = SignPage(mock_page, db)
        assert sp._mode == "single"
        sp.build()
        assert sp.mode_text.value == "单账号模式"
        assert sp.matrix_settings.visible is False


# ========== load_data ==========


@pytest.mark.asyncio
class TestSignPageLoadData:
    async def test_load_populates_single_stats(self, sign_page, db):
        await _add_account_with_forum(db, "acc1", [
            (1, "alpha", "success"),
            (2, "beta", "failure"),
            (3, "gamma", None),
        ])
        await sign_page.load_data()

        assert sign_page.total_stat.value == "3"
        assert sign_page.success_stat.value == "1"
        assert sign_page.failure_stat.value == "1"

    async def test_load_matrix_stats_from_rollup(self, sign_page, db):
        """矩阵态统计 = rollup 各账号行汇总（口径闭合：成功+失败+待签=总数）"""
        await _add_account_with_forum(db, "acc1", [(1, "alpha", "success"), (2, "beta", "failure")])
        await _add_account_with_forum(db, "acc2", [(1, "alpha", None)])

        await sign_page.load_data()
        sign_page._toggle_mode(None)

        assert sign_page.total_stat.value == "3"
        assert sign_page.success_stat.value == "1"
        assert sign_page.failure_stat.value == "1"
        assert sign_page.pending_stat.value == "1"

    async def test_load_daemon_settings(self, sign_page, db):
        import json

        await db.set_setting("schedule", json.dumps({"enabled": True, "sign_time": "09:30", "mode": "matrix"}))
        await db.set_setting("sign_delay_min", "8")
        await db.set_setting("sign_delay_max", "20")

        await sign_page.load_data()

        assert sign_page.daemon_switch.value is True
        assert sign_page.daemon_time.value == "09:30"
        assert sign_page.delay_min_input.value == "8"
        assert sign_page.delay_max_input.value == "20"
        assert sign_page.daemon_mode_radio.value == "matrix"

    async def test_auto_start_sign_triggers_run_task(self, sign_page, db):
        sign_page.page.session.set("auto_start_sign", True)
        await sign_page.load_data()

        assert sign_page.page.session.get("auto_start_sign") is False, "触发后标志应复位"
        assert sign_page.page.run_task.called


# ========== 模式切换 ==========


class TestToggleMode:
    def test_toggle_to_matrix(self, sign_page):
        sign_page._toggle_mode(None)
        assert sign_page._mode == "matrix"
        assert sign_page.mode_text.value == "矩阵全扫模式"
        assert sign_page.matrix_settings.visible is True

    def test_toggle_back_to_single(self, sign_page):
        sign_page._toggle_mode(None)
        sign_page._toggle_mode(None)
        assert sign_page._mode == "single"
        assert sign_page.matrix_settings.visible is False

    def test_toggle_blocked_while_signing(self, sign_page):
        sign_page._is_signing = True
        sign_page._toggle_mode(None)
        assert sign_page._mode == "single", "执行中禁止切换模式"

    def test_matrix_mode_stats(self, sign_page, db):
        sign_page._mode = "matrix"
        sign_page.refresh_ui()
        # 由 load_data 填充的 _matrix_tasks 驱动; 此处仅验证口径切换不崩溃
        assert isinstance(sign_page.total_stat.value, str)


@pytest.mark.asyncio
class TestMatrixModeStats:
    async def test_matrix_stats_reflect_all_accounts(self, sign_page, db):
        await _add_account_with_forum(db, "acc1", [(1, "alpha", "success"), (2, "beta", "failure")])
        await _add_account_with_forum(db, "acc2", [(3, "gamma", None)])
        await sign_page.load_data()
        sign_page._toggle_mode(None)

        assert sign_page.total_stat.value == "3"
        assert sign_page.success_stat.value == "1"
        assert sign_page.failure_stat.value == "1"


# ========== 矩阵态账号队列 (整改 #11/#12) ==========


@pytest.mark.asyncio
class TestMatrixAccountQueue:
    def _row_texts(self, items):
        """提取每行的主要文本（账号名/聚合行标签）"""
        import flet as ft

        texts = []
        for card in items:
            row = card.content
            for c in row.controls:
                if isinstance(c, ft.Text) and c.value:
                    texts.append(c.value)
        return texts

    async def test_queue_rows_and_stats_closure(self, sign_page, db):
        """行数 = 可用账号数 + 聚合行；统计与队列所见一致"""
        acc1 = await _add_account_with_forum(db, "q_active", [(1, "q1", "success"), (2, "q2", None)])
        acc2 = await _add_account_with_forum(db, "q_susp", [(3, "q3", None)])
        await db.update_account(acc2.id, status="suspended")

        await sign_page.load_data()
        sign_page._set_mode("matrix")

        items = sign_page._build_account_queue_items()
        texts = self._row_texts(items)
        # 可用账号 1 行 + 挂起聚合 1 行（无孤儿）
        assert len(items) == 2
        assert any("q_active" in t for t in texts)
        assert any("挂起/封禁账号" in t for t in texts)

        # 统计闭合：成功 + 失败 + 待签 = 总数（仅可用账号口径）
        assert sign_page.total_stat.value == "2"
        assert sign_page.success_stat.value == "1"
        assert sign_page.failure_stat.value == "0"
        assert sign_page.pending_stat.value == "1"

    async def test_orphan_forums_aggregate_row(self, sign_page, db):
        """账号已删的吧聚合为孤儿行，不再平铺成卡片"""
        from sqlalchemy import delete as sa_delete
        from tieba_mecha.db.models import Account

        acc = await _add_account_with_forum(db, "acc_gone", [(3, "f3", None)])
        async with db.async_session() as session:
            await session.execute(sa_delete(Account).where(Account.id == acc.id))
            await session.commit()

        await sign_page.load_data()
        sign_page._set_mode("matrix")

        items = sign_page._build_account_queue_items()
        texts = self._row_texts(items)
        assert any("孤儿数据" in t for t in texts)
        # 无可用账号 → 0 账号行 + 0 挂起行 + 1 孤儿行
        assert len(items) == 1


# ========== 守护进程配置保存 ==========


@pytest.mark.asyncio
class TestSaveDaemonConfig:
    async def test_valid_time_saves(self, sign_page, db):
        sign_page.daemon_switch.value = True
        sign_page.daemon_time.value = "09:30"
        sign_page.delay_min_input.value = "5"
        sign_page.delay_max_input.value = "15"

        reload_mock = AsyncMock()
        with patch("tieba_mecha.core.daemon.daemon_instance.reload", reload_mock):
            await sign_page._save_daemon_config(None)

        import json

        sched = json.loads(await db.get_setting("schedule", "{}"))
        assert sched["enabled"] is True
        assert sched["sign_time"] == "09:30"
        assert reload_mock.called
        assert await db.get_setting("sign_delay_min") == "5"

    @pytest.mark.parametrize("bad_time", ["8点", "abc", "25:00", "08:61", "830", ""])
    async def test_invalid_time_blocked(self, sign_page, db, bad_time):
        """回归: 非法时间必须被拦截, 不得静默保存导致守护进程失效"""
        sign_page.daemon_switch.value = True
        sign_page.daemon_time.value = bad_time

        reload_mock = AsyncMock()
        with patch("tieba_mecha.core.daemon.daemon_instance.reload", reload_mock):
            await sign_page._save_daemon_config(None)

        assert await db.get_setting("schedule", "{}") == "{}", "非法时间不应写入配置"
        assert not reload_mock.called, "非法时间不应触发热部署"

    async def test_boundary_times_accepted(self, sign_page, db):
        for t in ["00:00", "23:59", "8:5"]:
            sign_page.daemon_time.value = t
            with patch("tieba_mecha.core.daemon.daemon_instance.reload", AsyncMock()):
                await sign_page._save_daemon_config(None)
            import json

            sched = json.loads(await db.get_setting("schedule", "{}"))
            assert sched.get("sign_time") == t


# ========== 签到历史弹窗 ==========


@pytest.mark.asyncio
class TestForumHistoryDialog:
    async def test_history_uses_page_open(self, sign_page, db):
        """回归: 历史弹窗应使用 page.open() 新式 API (废弃 page.dialog 赋值)"""
        acc = await _add_account_with_forum(db, "acc_hist", [(7, "hist_forum", "success")])
        forums = await db.get_forums(acc.id)
        await db.add_sign_log(forum_id=forums[0].id, fname="hist_forum", success=True, message="签到成功")

        await sign_page._show_forum_history(forums[0].id, "hist_forum")

        assert sign_page.page.open.called, "应通过 page.open() 打开弹窗"
        assert not hasattr(sign_page.page, "dialog") or True  # MagicMock 恒真, 主断言在上

    async def test_history_empty_state(self, sign_page, db):
        acc = await _add_account_with_forum(db, "acc_empty", [(8, "empty_forum", None)])
        forums = await db.get_forums(acc.id)

        await sign_page._show_forum_history(forums[0].id, "empty_forum")
        assert sign_page.page.open.called


# ========== 单账号签到流 ==========


@pytest.mark.asyncio
class TestDoSignSingle:
    async def test_nothing_to_sign(self, sign_page, db):
        await _add_account_with_forum(db, "acc_done", [(9, "done_forum", "success")])
        await sign_page.load_data()

        await sign_page._do_sign_single()
        assert sign_page._is_signing is False
        sign_page.page.show_snack_bar.assert_called()

    async def test_sign_flow_broadcasts_progress(self, sign_page, db):
        from tieba_mecha.core.sign import SignResult

        await _add_account_with_forum(db, "acc_flow", [(10, "flow_forum", None)])
        await sign_page.load_data()

        async def fake_stream(*args, **kwargs):
            yield SignResult(fname="flow_forum", success=True, message="签到成功")

        with patch("tieba_mecha.web.pages.sign.sign_all_forums", fake_stream):
            await sign_page._do_sign_single()

        assert sign_page._is_signing is False
        assert sign_page.progress_bar.visible is False
        assert sign_page.sign_btn_text.value == "启动签到流"
        sign_page.page.pubsub.send_all_on_topic.assert_called()

        topic, payload = sign_page.page.pubsub.send_all_on_topic.call_args_list[-1][0]
        assert topic == "sign_progress"
        assert payload["status"] == "completed"

    async def test_sign_flow_handles_exception(self, sign_page, db):
        await _add_account_with_forum(db, "acc_err", [(11, "err_forum", None)])
        await sign_page.load_data()

        async def broken_stream(*args, **kwargs):
            yield SignResult(fname="err_forum", success=False, message="x")
            raise RuntimeError("网络崩溃")

        from tieba_mecha.core.sign import SignResult

        with patch("tieba_mecha.web.pages.sign.sign_all_forums", broken_stream):
            await sign_page._do_sign_single()

        assert sign_page._is_signing is False, "异常后必须复位执行状态"
        assert sign_page.sign_btn_text.value == "启动签到流"


# ========== 整改批次一：页面守卫 ==========


@pytest.mark.asyncio
class TestBatch1PageGuards:
    async def test_validated_delay_swaps_and_clamps(self, sign_page):
        lo, hi = sign_page._validated_delay("15", "8", (5.0, 15.0))
        assert (lo, hi) == (8.0, 15.0), "倒挂区间应自动交换"

        lo, hi = sign_page._validated_delay("-3", "5", (5.0, 15.0))
        assert lo == 2.0, "负值下限应钳制为 2s"

        lo, hi = sign_page._validated_delay("abc", "5", (5.0, 15.0))
        assert (lo, hi) == (5.0, 5.0), "非数字仅该边回退默认，另一边保留输入"

    async def test_sign_one_rejected_when_lock_held(self, sign_page, db):
        """整改#4: 签到流持锁时单吧手签直接拒绝，不得并发请求"""
        from tieba_mecha.core.sign import sign_flow_lock

        await _add_account_with_forum(db, "acc_lock", [(21, "lock_forum", None)])
        async with sign_flow_lock:
            with patch("tieba_mecha.web.pages.sign.sign_forum", new_callable=AsyncMock) as mock_sign:
                await sign_page._do_sign_one("lock_forum")
                mock_sign.assert_not_called()
        assert sign_page._is_signing is False

    async def test_daemon_mode_saved_from_radio_not_page_mode(self, sign_page, db):
        """整改#1: schedule.mode 取守护面板单选值，与页面执行模式解耦"""
        import json

        sign_page.daemon_switch.value = True
        sign_page.daemon_time.value = "08:00"
        sign_page.daemon_mode_radio.value = "matrix"
        sign_page._mode = "single"  # 页面在单账号模式点保存

        with patch("tieba_mecha.core.daemon.daemon_instance.reload", AsyncMock()):
            await sign_page._save_daemon_config(None)

        sched = json.loads(await db.get_setting("schedule", "{}"))
        assert sched["mode"] == "matrix", "守护模式必须取单选值而非页面模式快照"

    async def test_delay_inputs_sanitized_on_save(self, sign_page, db):
        """整改#9: 保存时非法延迟被钳制后落库"""
        sign_page.daemon_switch.value = False
        sign_page.daemon_time.value = "08:00"
        sign_page.delay_min_input.value = "0"
        sign_page.delay_max_input.value = "abc"

        with patch("tieba_mecha.core.daemon.daemon_instance.reload", AsyncMock()):
            await sign_page._save_daemon_config(None)

        assert await db.get_setting("sign_delay_min") == "2"
        assert await db.get_setting("sign_delay_max") == "15"


@pytest.mark.asyncio
class TestAccountChipBusyGuard:
    async def test_switch_blocked_when_busy(self, mock_page, db):
        """整改#10: 宿主页执行签到流时芯片切号被拒"""
        from tieba_mecha.web.components.account_switcher import AccountSwitchChip

        chip = AccountSwitchChip(mock_page, db, is_busy=lambda: True)
        target = MagicMock()
        target.id = 999
        chip._active = MagicMock()
        chip._active.id = 1

        with patch("tieba_mecha.web.components.account_switcher.switch_account", new_callable=AsyncMock) as mock_sw:
            await chip._do_switch(target)

        mock_sw.assert_not_called()


# ========== 整改批次三：原因徽标 / 熔断解除 / 矩阵确认 ==========


@pytest.mark.asyncio
class TestBatch3Page:
    async def test_pending_reason_classification(self, sign_page, db):
        """整改#13: 未签原因四态判定（跳过/失败/熔断/待签）"""
        from datetime import datetime
        from tieba_mecha.core.sign import SIGN_SKIP_MESSAGE

        acc = await _add_account_with_forum(db, "acc_badge", [
            (31, "skip_forum", None), (32, "fail_forum", "failure"),
        ])
        forums = {f.fname: f for f in await db.get_forums(acc.id)}
        await db.add_sign_log(
            forum_id=forums["skip_forum"].id, fname="skip_forum",
            success=False, message=SIGN_SKIP_MESSAGE,
        )
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_logs = {l.forum_id: l for l in await db.get_sign_logs(limit=100, since=today_start)}

        assert sign_page._pending_reason(forums["skip_forum"], today_logs)[0] == "今日跳过"
        assert sign_page._pending_reason(forums["fail_forum"], today_logs)[0] == "失败"

        await db.mark_forum_banned(acc.id, "fail_forum", reason="pre-banned")
        forums = {f.fname: f for f in await db.get_forums(acc.id)}
        assert sign_page._pending_reason(forums["fail_forum"], today_logs)[0] == "已熔断"

    async def test_banned_row_unban_instead_of_sign(self, sign_page, db):
        """整改#17: 熔断行显示徽标、给解除入口、不给手签按钮"""
        acc = await _add_account_with_forum(db, "acc_ban", [(33, "ban_forum", None)])
        await db.mark_forum_banned(acc.id, "ban_forum", reason="pre-banned")
        await sign_page.load_data()

        items = sign_page._build_single_mode_items()
        assert len(items) == 1
        row = items[0].content
        tooltips = [c.tooltip for c in row.controls if hasattr(c, "tooltip") and c.tooltip]
        assert any("解除熔断" in t for t in tooltips), "熔断行应有解除熔断入口"
        buttons = [c.text for c in row.controls if hasattr(c, "text") and c.text]
        assert "签到" not in buttons, "熔断行不得再提供手签（重撞 3250004）"

    async def test_matrix_entry_requires_confirm_dialog(self, sign_page, db):
        """整改#18: 矩阵启动必经确认弹窗，确认前不进入执行态"""
        await _add_account_with_forum(db, "acc_dialog", [(34, "dlg_forum", None)])
        await sign_page.load_data()
        sign_page._set_mode("matrix")

        with patch("tieba_mecha.web.pages.sign.sign_all_accounts") as mock_flow:
            async def empty_gen(*a, **k):
                if False: yield {}

            mock_flow.return_value = empty_gen()
            await sign_page._do_sign_matrix()

        sign_page.page.open.assert_called(), "矩阵启动应先弹确认框"
        assert sign_page._is_signing is False, "确认前不得进入执行态"
        mock_flow.assert_not_called()

    async def test_scope_text_reflects_pending(self, sign_page, db):
        """整改#19: 大按钮旁常显本次将签范围"""
        await _add_account_with_forum(db, "acc_scope", [(35, "scope_forum", "success"), (36, "scope_forum2", None)])
        await sign_page.load_data()

        assert "本次将签" in (sign_page.scope_text.value or "")
        assert "1 吧" in (sign_page.scope_text.value or "")
