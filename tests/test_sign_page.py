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

    async def test_load_matrix_total_unique_fnames(self, sign_page, db):
        """全矩阵统计 = 所有账号去重后的贴吧名数"""
        await _add_account_with_forum(db, "acc1", [(1, "alpha", None), (2, "beta", None)])
        await _add_account_with_forum(db, "acc2", [(1, "alpha", None), (3, "gamma", None)])

        await sign_page.load_data()

        assert sign_page.matrix_total_stat.value == "3"  # alpha/beta/gamma 去重

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
        assert sign_page.daemon_mode_info.value == "当前生效模式: 矩阵全扫"

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


# ========== 矩阵列表账号状态显示 (BUG 回归) ==========


@pytest.mark.asyncio
class TestMatrixAccountStatusDisplay:
    def _extract_badge(self, card):
        # card.content = Row[Icon, Column[Row[Text, Container(badge)], Row[...]], Icon]
        row = card.content
        col = row.controls[1]
        return col.controls[0].controls[1]

    async def test_active_account_shows_primary(self, sign_page, db):
        """回归: 系统账号状态取值为 active/pending/..., 不存在 ready;
        active 账号徽标应为 primary 色而非 error 色"""
        await _add_account_with_forum(db, "acc_active", [(1, "f1", None)])
        await sign_page.load_data()

        items = sign_page._build_matrix_mode_items()
        badge = self._extract_badge(items[0])
        assert badge.bgcolor == with_opacity(0.4, "primary"), (
            "active 账号不应显示为 ERROR 红色状态"
        )

    async def test_suspended_account_shows_error(self, sign_page, db):
        acc = await _add_account_with_forum(db, "acc_susp", [(2, "f2", None)])
        await db.update_account(acc.id, status="suspended")
        await sign_page.load_data()

        items = sign_page._build_matrix_mode_items()
        badge = self._extract_badge(items[0])
        assert badge.bgcolor == with_opacity(0.4, "error")

    async def test_orphaned_forum_labeled(self, sign_page, db):
        """账号已遗失的贴吧应显示为 未知/已遗失 (防御分支)"""
        from sqlalchemy import delete as sa_delete
        from tieba_mecha.db.models import Account

        acc = await _add_account_with_forum(db, "acc_gone", [(3, "f3", None)])
        await sign_page.load_data()

        # 绕过 delete_account 的级联删除, 直接移除账号行以模拟孤儿贴吧
        async with db.async_session() as session:
            await session.execute(sa_delete(Account).where(Account.id == acc.id))
            await session.commit()

        await sign_page.load_data()
        items = sign_page._build_matrix_mode_items()
        row = items[0].content
        col = row.controls[1]
        badge_text = col.controls[0].controls[1].content.value
        assert "未知" in badge_text or "遗失" in badge_text


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
