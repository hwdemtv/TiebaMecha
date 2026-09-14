"""全域签到核心逻辑综合测试。

覆盖范围:
- _parse_sign_result 全部错误码路径
- update_forum_sign 统计不变量 (Total = Success + Failed)
- check_and_reset_daily_sign 跨天重置 / 断签检测 (回归: 昨日成功不得误清零连续天数)
- sign_all_forums: 封禁/失效/已签 错误码处理
- sign_all_accounts: 代理失效隔离、风控路径日志一致性
- sync_forums_to_db: 取关贴吧标记隐藏
- daemon.reload: 非法时间处理
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tieba_mecha.core.sign import (
    ERR_ALREADY_SIGNED,
    ERR_FORUM_BANNED,
    ERR_FORUM_INVALID,
    _parse_sign_result,
    sign_all_forums,
    sign_all_accounts,
    sync_forums_to_db,
)
from tieba_mecha.db.models import Forum


class FakeSignResponse:
    """模拟 aiotieba sign_forum 返回值"""

    def __init__(self, code=0, msg="", truthy=True):
        self.err = SimpleNamespace(code=code, msg=msg) if code else None
        self._truthy = truthy

    def __bool__(self):
        return self._truthy


def make_client(sign_responses=None):
    """构造 mock aiotieba client, sign_forum 依次返回 sign_responses"""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_threads = AsyncMock(return_value=None)
    if sign_responses is not None:
        client.sign_forum = AsyncMock(side_effect=list(sign_responses))
    else:
        client.sign_forum = AsyncMock(return_value=FakeSignResponse())
    return client


# ========== _parse_sign_result ==========


class TestParseSignResult:
    def test_success(self):
        raw = FakeSignResponse(code=0, truthy=True)
        success, msg, already, invalid, code = _parse_sign_result(raw)
        assert success is True
        assert msg == "签到成功"
        assert already is False
        assert invalid is False
        assert code == 0

    def test_already_signed_160002(self):
        raw = FakeSignResponse(code=ERR_ALREADY_SIGNED, truthy=False)
        success, msg, already, invalid, code = _parse_sign_result(raw)
        assert success is True
        assert msg == "今日已签到"
        assert already is True
        assert invalid is False
        assert code == ERR_ALREADY_SIGNED

    @pytest.mark.parametrize("err_code", ERR_FORUM_INVALID)
    def test_forum_invalid(self, err_code):
        raw = FakeSignResponse(code=err_code, truthy=False)
        success, msg, already, invalid, code = _parse_sign_result(raw)
        assert success is False
        assert str(err_code) in msg
        assert invalid is True
        assert code == err_code

    def test_forum_banned_3250004(self):
        raw = FakeSignResponse(code=ERR_FORUM_BANNED, truthy=False)
        success, msg, already, invalid, code = _parse_sign_result(raw)
        assert success is False
        assert invalid is True
        assert code == ERR_FORUM_BANNED

    def test_generic_failure(self):
        raw = FakeSignResponse(code=999, msg="服务器异常", truthy=False)
        success, msg, already, invalid, code = _parse_sign_result(raw)
        assert success is False
        assert "服务器异常" in msg
        assert invalid is False

    def test_no_err_falsy(self):
        raw = FakeSignResponse(code=0, truthy=False)
        success, msg, already, invalid, code = _parse_sign_result(raw)
        assert success is False
        assert msg == "签到失败"


# ========== update_forum_sign 不变量 ==========


@pytest.mark.asyncio
class TestUpdateForumSignInvariants:
    async def _get_forum(self, db, forum_id):
        async with db.async_session() as session:
            return await session.get(Forum, forum_id)

    async def test_success_increments_once(self, db, sample_account_data):
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name=sample_account_data["name"], bduss=sample_account_data["bduss"])
        forum = await db.add_forum(fid=1, fname="f1", account_id=acc.id)

        await db.update_forum_sign(forum.id, True)
        await db.update_forum_sign(forum.id, True)  # 重复成功 (已签再签)

        f = await self._get_forum(db, forum.id)
        assert f.sign_count == 1
        assert f.history_success == 1
        assert f.history_failed == 0
        assert f.history_total == 1

    async def test_failure_counts_once_then_offset_by_success(self, db, sample_account_data):
        """同日: 失败 2 次只计 1 次, 之后成功冲抵失败"""
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name=sample_account_data["name"], bduss=sample_account_data["bduss"])
        forum = await db.add_forum(fid=1, fname="f1", account_id=acc.id)

        await db.update_forum_sign(forum.id, False)
        await db.update_forum_sign(forum.id, False)  # 重复失败
        f = await self._get_forum(db, forum.id)
        assert f.history_failed == 1

        await db.update_forum_sign(forum.id, True)  # 当日成功 → 冲抵
        f = await self._get_forum(db, forum.id)
        assert f.history_failed == 0
        assert f.history_success == 1
        assert f.history_total == 1
        assert f.is_sign_today is True

    async def test_success_then_failure_same_day(self, db, sample_account_data):
        """同日: 成功后的失败不计数"""
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name=sample_account_data["name"], bduss=sample_account_data["bduss"])
        forum = await db.add_forum(fid=1, fname="f1", account_id=acc.id)

        await db.update_forum_sign(forum.id, True)
        await db.update_forum_sign(forum.id, False)

        f = await self._get_forum(db, forum.id)
        assert f.history_success == 1
        assert f.history_failed == 0
        assert f.is_sign_today is True


# ========== check_and_reset_daily_sign ==========


@pytest.mark.asyncio
class TestCheckAndResetDailySign:
    async def _set_forum_state(self, db, forum_id, **kwargs):
        async with db.async_session() as session:
            forum = await session.get(Forum, forum_id)
            for k, v in kwargs.items():
                setattr(forum, k, v)
            await session.commit()

    async def _make_forum(self, db):
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name="acc_reset", bduss="a" * 192, stoken="b" * 64)
        return await db.add_forum(fid=1, fname="reset_forum", account_id=acc.id)

    async def _get_forum(self, db, forum_id):
        async with db.async_session() as session:
            return await session.get(Forum, forum_id)

    async def test_yesterday_success_keeps_streak(self, db):
        """回归: 昨日签到成功 -> 今日重置签到标记, 但连续天数必须保留"""
        forum = await self._make_forum(db)
        await self._set_forum_state(
            db, forum.id,
            sign_count=5, is_sign_today=True,
            last_sign_status="success",
            last_sign_date=datetime.now() - timedelta(days=1),
        )

        await db.check_and_reset_daily_sign()

        f = await self._get_forum(db, forum.id)
        assert f.is_sign_today is False, "跨天后应清除今日已签标记"
        assert f.last_sign_status == "pending"
        assert f.sign_count == 5, "昨日成功签到, 连续天数不得被误清零"

    async def test_yesterday_failure_breaks_streak(self, db):
        """昨日最终状态为失败 -> 连续天数清零"""
        forum = await self._make_forum(db)
        await self._set_forum_state(
            db, forum.id,
            sign_count=5, is_sign_today=False,
            last_sign_status="failure",
            last_sign_date=datetime.now() - timedelta(days=1),
        )

        await db.check_and_reset_daily_sign()

        f = await self._get_forum(db, forum.id)
        assert f.sign_count == 0

    async def test_gap_over_two_days_breaks_streak(self, db):
        """前天之后未签 -> 连续天数清零"""
        forum = await self._make_forum(db)
        await self._set_forum_state(
            db, forum.id,
            sign_count=5, is_sign_today=False,
            last_sign_status="success",
            last_sign_date=datetime.now() - timedelta(days=2),
        )

        await db.check_and_reset_daily_sign()

        f = await self._get_forum(db, forum.id)
        assert f.sign_count == 0

    async def test_same_day_not_reset(self, db):
        """今日已签 -> 不重置"""
        forum = await self._make_forum(db)
        await self._set_forum_state(
            db, forum.id,
            sign_count=3, is_sign_today=True,
            last_sign_status="success",
            last_sign_date=datetime.now(),
        )

        await db.check_and_reset_daily_sign()

        f = await self._get_forum(db, forum.id)
        assert f.is_sign_today is True
        assert f.sign_count == 3

    async def test_streak_continues_across_sign(self, db):
        """端到端: 昨日成功 -> 今日重置 -> 今日签到成功 -> 连续天数 +1"""
        forum = await self._make_forum(db)
        await self._set_forum_state(
            db, forum.id,
            sign_count=5, is_sign_today=True,
            last_sign_status="success",
            last_sign_date=datetime.now() - timedelta(days=1),
        )

        await db.check_and_reset_daily_sign()
        await db.update_forum_sign(forum.id, True)

        f = await self._get_forum(db, forum.id)
        assert f.sign_count == 6, "连续签到应累积为 6 天"


# ========== sign_all_forums 错误码路径 ==========


@pytest.mark.asyncio
class TestSignAllForumsErrorPaths:
    async def _setup(self, db):
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name="acc_scan", bduss="a" * 192, stoken="b" * 64)
        forum = await db.add_forum(fid=1, fname="scan_forum", account_id=acc.id)
        return acc, forum

    async def _get_forum(self, db, forum_id):
        async with db.async_session() as session:
            return await session.get(Forum, forum_id)

    async def test_banned_forum_marked_not_deleted(self, db):
        """3250004 吧务封禁 -> 熔断标记, 保留记录, 靶场 fail_count 递增"""
        acc, forum = await self._setup(db)
        client = make_client([FakeSignResponse(code=ERR_FORUM_BANNED, truthy=False)])

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(r)

        assert len(results) == 1
        assert results[0].success is False
        f = await self._get_forum(db, forum.id)
        assert f is not None, "封禁贴吧应保留 (熔断模式)"
        assert f.is_banned is True

        from sqlalchemy import select as sa_select
        from tieba_mecha.db.models import TargetPool

        async with db.async_session() as session:
            pool = (await session.execute(sa_select(TargetPool).where(TargetPool.fname == "scan_forum"))).scalar()
        assert pool is not None and pool.fail_count >= 1

    async def test_invalid_forum_deleted(self, db):
        """340006 贴吧失效 -> 自动删除"""
        acc, forum = await self._setup(db)
        client = make_client([FakeSignResponse(code=340006, truthy=False)])

        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for _ in sign_all_forums(db, delay_min=0, delay_max=0):
                    pass

        f = await self._get_forum(db, forum.id)
        assert f is None, "失效贴吧应被删除"

    async def test_already_signed_counts_success(self, db):
        """160002 今日已签 -> 记为成功且只计一次"""
        acc, forum = await self._setup(db)
        client = make_client([
            FakeSignResponse(code=ERR_ALREADY_SIGNED, truthy=False),
            FakeSignResponse(code=ERR_ALREADY_SIGNED, truthy=False),
        ])

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(r)

        assert all(r.success for r in results)
        f = await self._get_forum(db, forum.id)
        assert f.sign_count == 1, "已签状态重复执行不应重复累计连续天数"
        assert f.history_success == 1

    async def test_generic_failure_logged(self, db):
        """普通失败 -> 记日志 + last_sign_status=failure"""
        acc, forum = await self._setup(db)
        client = make_client([FakeSignResponse(code=999, msg="风控拦截", truthy=False)])

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(r)

        assert results[0].success is False
        logs = await db.get_sign_logs(forum_id=forum.id)
        assert len(logs) == 1
        assert logs[0].success is False
        f = await self._get_forum(db, forum.id)
        assert f.last_sign_status == "failure"


# ========== sign_all_accounts 矩阵路径 ==========


@pytest.mark.asyncio
class TestSignAllAccountsMatrix:
    async def _add_account(self, db, name, status="active", proxy_id=None):
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name=name, bduss="a" * 192, stoken="b" * 64, proxy_id=proxy_id)
        if status != "active":
            await db.update_account(acc.id, status=status)
        return acc

    async def test_suspended_proxy_account_isolated(self, db):
        """绑定失效代理的账号被隔离, 不执行签到"""
        proxy = await db.add_proxy(host="127.0.0.1", port=1, protocol="http")
        acc = await self._add_account(db, "acc_proxy", proxy_id=proxy.id)
        await db.add_forum(fid=1, fname="f1", account_id=acc.id)
        await db.update_proxy(proxy.id, is_active=False)

        results = []
        async for r in sign_all_accounts(db, 0, 0, 0, 0):
            results.append(r)

        assert len(results) == 1
        assert results[0]["proxy_status"] == "suspended"
        assert results[0]["success"] is False
        # 贴吧不应产生签到记录
        forums = await db.get_forums(acc.id)
        assert forums[0].last_sign_status == "pending"

    async def test_server_error_yields_and_logs(self, db):
        """回归: TiebaServerError 风控路径应与其他失败一致写入日志并更新状态"""
        from aiotieba.exception import TiebaServerError

        acc = await self._add_account(db, "acc_risky")
        await db.add_forum(fid=1, fname="risky_forum", account_id=acc.id)

        client = make_client()
        client.sign_forum = AsyncMock(side_effect=TiebaServerError(500, "server inner error"))

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_accounts(db, 0, 0, 0, 0):
                    results.append(r)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "风控" in results[0]["message"] or "限流" in results[0]["message"]

        forums = await db.get_forums(acc.id)
        logs = await db.get_sign_logs(forum_id=forums[0].id)
        assert len(logs) == 1, "风控失败应写入签到日志"
        assert logs[0].success is False
        assert forums[0].last_sign_status == "failure"

    async def test_banned_forum_handled_in_matrix(self, db):
        """矩阵模式: 3250004 -> 熔断标记且保留"""
        acc = await self._add_account(db, "acc_banned")
        await db.add_forum(fid=1, fname="banned_forum", account_id=acc.id)
        client = make_client([FakeSignResponse(code=ERR_FORUM_BANNED, truthy=False)])

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_accounts(db, 0, 0, 0, 0):
                    results.append(r)

        assert results[0]["success"] is False
        forums = await db.get_forums(acc.id)
        assert forums and forums[0].is_banned is True


# ========== sync_forums_to_db 隐藏标记 ==========


@pytest.mark.asyncio
class TestSyncForumsHidden:
    async def test_stale_forum_marked_hidden(self, db, sample_account_data):
        """服务器已取关的贴吧应标记 is_hidden, 默认列表不再返回"""
        from tieba_mecha.core.account import add_account
        from tieba_mecha.core.sign import ForumInfo

        acc = await add_account(db=db, name=sample_account_data["name"], bduss=sample_account_data["bduss"])
        # 本地已有 f1(仍在服务器) 和 f2(已被服务器移除)
        await db.add_forum(fid=1, fname="keep_forum", account_id=acc.id)
        await db.add_forum(fid=2, fname="gone_forum", account_id=acc.id)

        server_forums = [ForumInfo(fid=1, fname="keep_forum", is_sign_today=False, sign_count=0)]
        with patch("tieba_mecha.core.sign.get_follow_forums", AsyncMock(return_value=server_forums)):
            count = await sync_forums_to_db(db)

        assert count == 0  # 无新增
        visible = await db.get_forums(acc.id)
        names = {f.fname for f in visible}
        assert "gone_forum" not in names, "已取关贴吧应从默认列表隐藏"
        assert "keep_forum" in names

        all_forums = await db.get_forums(acc.id, include_hidden=True)
        gone = next(f for f in all_forums if f.fname == "gone_forum")
        assert gone.is_hidden is True, "历史数据应保留并标记隐藏"


# ========== daemon.reload ==========


@pytest.mark.asyncio
class TestDaemonReload:
    def _fresh_daemon(self):
        from tieba_mecha.core.daemon import TiebaMechaDaemon

        d = object.__new__(TiebaMechaDaemon)
        d.__init__()
        return d

    async def test_invalid_time_removes_job(self, db):
        """非法时间: 旧任务被移除且不注册新任务 (页面层负责拦截保存)"""
        import json

        d = self._fresh_daemon()
        await db.set_setting("schedule", json.dumps({"enabled": True, "sign_time": "8点"}))
        await d.reload(db)

        assert d.scheduler.get_job(d.sign_job_id) is None

    async def test_valid_time_registers_job(self, db):
        import json

        d = self._fresh_daemon()
        await db.set_setting("schedule", json.dumps({"enabled": True, "sign_time": "08:30"}))
        await d.reload(db)

        job = d.scheduler.get_job(d.sign_job_id)
        assert job is not None
        d.scheduler.remove_job(d.sign_job_id)

    async def test_disabled_removes_job(self, db):
        import json

        d = self._fresh_daemon()
        await db.set_setting("schedule", json.dumps({"enabled": True, "sign_time": "08:30"}))
        await d.reload(db)
        await db.set_setting("schedule", json.dumps({"enabled": False, "sign_time": "08:30"}))
        await d.reload(db)

        assert d.scheduler.get_job(d.sign_job_id) is None
