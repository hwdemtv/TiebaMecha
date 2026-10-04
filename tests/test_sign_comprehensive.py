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
    _get_skip_probability,
    _should_skip_today,
    SIGN_SKIP_DEFAULT,
    SIGN_SKIP_MAX,
    SIGN_SKIP_MESSAGE,
    sign_account_forums,
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

    async def test_yesterday_failure_resets_to_pending(self, db):
        """回归(整改#8): 失败路径只置 last_sign_status 不置 is_sign_today，
        旧重置分支只判 is_sign_today → 失败态跨天残留，混入次日失败账目"""
        forum = await self._make_forum(db)
        await self._set_forum_state(
            db, forum.id,
            sign_count=3, is_sign_today=False,
            last_sign_status="failure",
            last_sign_date=datetime.now() - timedelta(days=1),
        )

        await db.check_and_reset_daily_sign()

        f = await self._get_forum(db, forum.id)
        assert f.last_sign_status == "pending", "昨日失败跨天应回到待签"
        assert f.is_sign_today is False
        assert f.sign_count == 0, "昨日失败应触发断签清零"

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

    async def test_pre_banned_forum_excluded_from_queue(self, db):
        """回归: 已熔断贴吧不进入签到队列, 不再每天重撞 3250004"""
        from tieba_mecha.core.sign import get_sign_stats

        acc, forum = await self._setup(db)
        await db.mark_forum_banned(acc.id, "scan_forum", reason="pre-banned")

        client = make_client()
        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(r)

        assert results == [], "熔断贴吧应被跳过"
        client.sign_forum.assert_not_called()

        # 统计口径一致: 待签总数也不含熔断贴吧
        stats = await get_sign_stats(db)
        assert stats["total"] == 0

        # 默认 get_forums 仍返回熔断记录 (UI 展示用)
        visible = await db.get_forums(acc.id)
        assert len(visible) == 1 and visible[0].is_banned is True


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

    async def test_pre_banned_forum_skipped_in_matrix(self, db):
        """回归: 矩阵全扫跳过已熔断贴吧, 不发起签到请求"""
        acc = await self._add_account(db, "acc_skip")
        await db.add_forum(fid=1, fname="melted_forum", account_id=acc.id)
        await db.mark_forum_banned(acc.id, "melted_forum", reason="pre-banned")

        client = make_client()
        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_accounts(db, 0, 0, 0, 0):
                    results.append(r)

        # 该账号无其他可签贴吧 -> 直接跳过, 不 yield 该贴吧结果
        fnames = [r["fname"] for r in results]
        assert "melted_forum" not in fnames
        client.sign_forum.assert_not_called()


# ========== 拟人化随机跳过 ==========


class TestSkipDice:
    def test_zero_probability_never_skips(self):
        assert _should_skip_today(1, 100, 0.0) is False
        assert _should_skip_today(1, 100, -1.0) is False

    def test_unit_probability_always_skips(self):
        assert _should_skip_today(1, 100, 1.0) is True

    def test_dice_stable_within_same_day(self):
        """同日重复掷骰结果一致：守护定时与手动重跑互不稀释跳过率"""
        first = _should_skip_today(7, 12345, 0.5)
        assert all(_should_skip_today(7, 12345, 0.5) is first for _ in range(10))


@pytest.mark.asyncio
class TestHumanizedSkip:
    async def _setup(self, db):
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name="acc_skipper", bduss="a" * 192, stoken="b" * 64)
        forum = await db.add_forum(fid=1, fname="skip_forum", account_id=acc.id)
        return acc, forum

    async def _get_forum(self, db, forum_id):
        from tieba_mecha.db.models import Forum

        async with db.async_session() as session:
            return await session.get(Forum, forum_id)

    async def test_full_skip_in_single_flow(self, db):
        """跳过率 1.0(patch 注入, 绕过 clamp): 不发签到请求, 落 SignLog(success=False), 贴吧战绩不动"""
        acc, forum = await self._setup(db)
        client = make_client()

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("tieba_mecha.core.sign._get_skip_probability", AsyncMock(return_value=1.0)):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                        results.append(r)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].message == SIGN_SKIP_MESSAGE
        client.sign_forum.assert_not_called()

        # 审计闭环的关键: 跳过必须落日志, 否则审计永远看到 100% 签到率
        logs = await db.get_sign_logs(forum_id=forum.id)
        assert len(logs) == 1
        assert logs[0].success is False
        assert logs[0].message == SIGN_SKIP_MESSAGE

        f = await self._get_forum(db, forum.id)
        assert f.last_sign_status == "pending", "拟人化跳过不是真失败, 不得污染签到战绩"
        assert f.history_failed == 0
        assert f.sign_count == 0

    async def test_full_skip_in_matrix_flow(self, db):
        """矩阵路径同样跳过且不动战绩"""
        acc, forum = await self._setup(db)
        client = make_client()

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("tieba_mecha.core.sign._get_skip_probability", AsyncMock(return_value=1.0)):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    async for r in sign_all_accounts(db, 0, 0, 0, 0):
                        results.append(r)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert results[0]["message"] == SIGN_SKIP_MESSAGE
        client.sign_forum.assert_not_called()

        f = await self._get_forum(db, forum.id)
        assert f.last_sign_status == "pending"
        assert f.history_failed == 0

    async def test_zero_probability_signs_all(self, db):
        """跳过率 0: 完全保持原有行为"""
        acc, forum = await self._setup(db)
        await db.set_setting("sign_skip_probability", "0")
        client = make_client([FakeSignResponse()])

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(r)

        assert len(results) == 1
        assert results[0].success is True
        client.sign_forum.assert_awaited_once()

    async def test_probability_clamped(self, db):
        """设置异常值时回退/收敛: 超上限截断(1.0 也会被截到 0.2), 非法回退默认, 负数视为关闭"""
        await db.set_setting("sign_skip_probability", "0.9")
        assert await _get_skip_probability(db) == SIGN_SKIP_MAX
        await db.set_setting("sign_skip_probability", "1.0")
        assert await _get_skip_probability(db) == SIGN_SKIP_MAX
        await db.set_setting("sign_skip_probability", "garbage")
        assert await _get_skip_probability(db) == SIGN_SKIP_DEFAULT
        await db.set_setting("sign_skip_probability", "-5")
        assert await _get_skip_probability(db) == 0.0


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
            count = 0
            async for r in sync_forums_to_db(db):
                count += r["added"]

        assert count == 0  # 无新增
        visible = await db.get_forums(acc.id)
        names = {f.fname for f in visible}
        assert "gone_forum" not in names, "已取关贴吧应从默认列表隐藏"
        assert "keep_forum" in names

        all_forums = await db.get_forums(acc.id, include_hidden=True)
        gone = next(f for f in all_forums if f.fname == "gone_forum")
        assert gone.is_hidden is True, "历史数据应保留并标记隐藏"


# ========== 整改批次一：剔除已签 / 按天洗牌 / rollup / 同步生成器 ==========


@pytest.mark.asyncio
class TestBatch1CoreFixes:
    async def _add_account_with_forums(self, db, n, signed_indices=()):
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name="acc_fix", bduss="a" * 192, stoken="b" * 64)
        await db.set_setting("sign_skip_probability", "0")
        for i in range(n):
            f = await db.add_forum(fid=i + 1, fname=f"forum_{i:02d}", account_id=acc.id)
            if i in signed_indices:
                await db.update_forum_sign(f.id, True)
        return acc

    async def test_already_signed_excluded_from_queue(self, db):
        """整改#3: 今日已签的吧不发请求、不落新日志（重跑秒级空扫）"""
        acc = await self._add_account_with_forums(db, 3, signed_indices=(0, 2))
        client = make_client()

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(r)

        assert [r.fname for r in results] == ["forum_01"]
        client.sign_forum.assert_awaited_once()
        logs = await db.get_sign_logs(limit=50)
        assert len(logs) == 1, "已签吧不得产生新日志"

    async def _reset_unsigned(self, db, acc_id):
        """把账号名下所有吧复位为未签（模拟同日同集合重跑前的状态）"""
        from sqlalchemy import update as sa_update
        from tieba_mecha.db.models import Forum

        async with db.async_session() as session:
            await session.execute(
                sa_update(Forum).where(Forum.account_id == acc_id)
                .values(is_sign_today=False, last_sign_status="pending")
            )
            await session.commit()

    async def test_order_stable_same_day_and_seeded(self, db):
        """整改#6: 同账号同日同集合 → 同序，且等于按天种子的洗牌结果"""
        import random as _random
        from datetime import date

        acc = await self._add_account_with_forums(db, 8)
        client = make_client()

        async def run_once():
            results = []
            with patch("tieba_mecha.core.sign.create_client", return_value=client):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                        results.append(r)
            return [r.fname for r in results]

        first = await run_once()
        await self._reset_unsigned(db, acc.id)  # 复位后同集合重跑
        second = await run_once()
        assert first == second, "同日重跑顺序应一致"

        expected = [f"forum_{i:02d}" for i in range(8)]
        _random.Random(f"order:{acc.id}:{date.today().isoformat()}").shuffle(expected)
        assert first == expected, "顺序应等于按天种子的确定性洗牌"

    async def test_order_changes_across_days(self, db):
        """整改#6: 跨天顺序必不同（消除字母序指纹）"""
        import random as _random
        from datetime import date as real_date, timedelta

        acc = await self._add_account_with_forums(db, 8)
        client = make_client()

        def expected_order(d):
            order = [f"forum_{i:02d}" for i in range(8)]
            _random.Random(f"order:{acc.id}:{d.isoformat()}").shuffle(order)
            return order

        async def run_on(fake_today):
            fake_date = MagicMock()
            fake_date.today.return_value = fake_today
            results = []
            with patch("tieba_mecha.core.sign.create_client", return_value=client), \
                 patch("tieba_mecha.core.sign.date", fake_date), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(r)
            return [r.fname for r in results]

        base = real_date(2026, 1, 1)
        other = next(
            d for d in (base + timedelta(days=k) for k in range(1, 10))
            if expected_order(d) != expected_order(base)
        )
        assert await run_on(other) == expected_order(other)
        await self._reset_unsigned(db, acc.id)
        assert await run_on(base) == expected_order(base)
        assert expected_order(other) != expected_order(base)


    async def test_manual_run_bypasses_skip_dice(self, db):
        """整改#14: ignore_skip=True 时跳过率 1.0 也全部照签（手动=明确意图）；
        守护路径默认掷骰不变（由 TestHumanizedSkip 覆盖）"""
        acc = await self._add_account_with_forums(db, 2)
        client = make_client()

        with patch("tieba_mecha.core.sign.create_client", return_value=client), \
             patch("tieba_mecha.core.sign._get_skip_probability", AsyncMock(return_value=1.0)), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            results = [r async for r in sign_all_forums(db, delay_min=0, delay_max=0, ignore_skip=True)]

        assert len(results) == 2 and all(r.success for r in results)
        assert client.sign_forum.call_count == 2

    async def test_stop_event_aborts_between_forums(self, db):
        """整改#16: 停止事件置位后下个循环顶即退出，不再签后续贴吧"""
        await self._add_account_with_forums(db, 3)
        client = make_client()
        stop = asyncio.Event()
        collected = []

        with patch("tieba_mecha.core.sign.create_client", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            async for r in sign_all_forums(db, delay_min=0, delay_max=0, stop_event=stop):
                collected.append(r)
                stop.set()

        assert len(collected) == 1, "首个吧之后应立即中止"
        client.sign_forum.assert_awaited_once()

    async def test_stop_event_prevents_account_switch(self, db):
        """整改#16: 矩阵流账号切换延迟被中止后不再进入下一账号"""
        from tieba_mecha.core.account import add_account

        for i in range(2):
            acc = await add_account(db=db, name=f"acc_stop{i}", bduss="a" * 192, stoken="b" * 64)
            await db.add_forum(fid=i + 1, fname=f"stop_forum_{i}", account_id=acc.id)
        await db.set_setting("sign_skip_probability", "0")

        client = make_client()
        stop = asyncio.Event()
        names = []

        with patch("tieba_mecha.core.sign.create_client", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            async for r in sign_all_accounts(db, 0, 0, 0, 0, stop_event=stop):
                names.append(r.get("account_name"))
                stop.set()

        assert len(names) == 1, "第一账号第一个吧后应中止，不切换账号"


@pytest.mark.asyncio
class TestSignRollup:
    async def test_rollup_accounts_and_aggregates(self, db):
        """共享基建: 可用账号逐行账目 + 挂起/孤儿聚合，口径闭合"""
        from sqlalchemy import delete as sa_delete
        from tieba_mecha.core.account import add_account
        from tieba_mecha.db.models import Account as AccModel

        acc1 = await add_account(db=db, name="roll_a", bduss="a" * 192, stoken="b" * 64)
        for fid, fname in [(1, "r1"), (2, "r2")]:
            f = await db.add_forum(fid=fid, fname=fname, account_id=acc1.id)
            await db.update_forum_sign(f.id, True)
        f3 = await db.add_forum(fid=3, fname="r3", account_id=acc1.id)
        await db.add_forum(fid=4, fname="r4", account_id=acc1.id)
        await db.mark_forum_banned(acc1.id, "r4", reason="pre-banned")
        await db.update_forum_sign(f3.id, False)  # 今日失败

        acc2 = await add_account(db=db, name="roll_s", bduss="a" * 192, stoken="b" * 64)
        await db.update_account(acc2.id, status="suspended")
        await db.add_forum(fid=5, fname="r5", account_id=acc2.id)

        acc3 = await add_account(db=db, name="roll_gone", bduss="a" * 192, stoken="b" * 64)
        await db.add_forum(fid=6, fname="r6", account_id=acc3.id)
        async with db.async_session() as session:
            await session.execute(sa_delete(AccModel).where(AccModel.id == acc3.id))
            await session.commit()

        rollup = await db.get_sign_rollup_by_account()
        ids = [a["account_id"] for a in rollup["accounts"]]
        assert acc1.id in ids and acc2.id not in ids

        row = next(a for a in rollup["accounts"] if a["account_id"] == acc1.id)
        assert row["total"] == 3
        assert row["signed"] == 2
        assert row["failed_today"] == 1
        assert row["pending"] == 0
        assert row["banned"] == 1
        assert rollup["suspended_forums"] == 1
        assert rollup["orphan_forums"] == 1


@pytest.mark.asyncio
class TestSyncGenerator:
    async def test_sync_yields_per_account_with_inter_delay(self, db):
        """整改#5: 逐账号 yield 账目；账号间有随机延迟且末账号后不加"""
        from tieba_mecha.core.account import add_account
        from tieba_mecha.core.sign import ForumInfo, SYNC_ACC_DELAY_MIN, SYNC_ACC_DELAY_MAX

        for i in range(2):
            await add_account(db=db, name=f"sync_acc{i}", bduss="a" * 192, stoken="b" * 64)

        async def fake_follow(db_, account_id=None):
            return [ForumInfo(fid=100 + account_id, fname=f"forum_{account_id}", is_sign_today=False, sign_count=0)]

        sleeps = []

        async def fake_sleep(sec):
            sleeps.append(sec)

        with patch("tieba_mecha.core.sign.get_follow_forums", side_effect=fake_follow), \
             patch("asyncio.sleep", fake_sleep):
            rows = [r async for r in sync_forums_to_db(db)]

        assert len(rows) == 2
        assert all(r["added"] == 1 for r in rows)
        assert len(sleeps) == 1, "2 账号只在切换间睡一次"
        assert SYNC_ACC_DELAY_MIN <= sleeps[0] <= SYNC_ACC_DELAY_MAX


# ========== daemon.reload ==========


@pytest.mark.asyncio
class TestDaemonReload:
    def _fresh_daemon(self):
        from tieba_mecha.core.daemon import TiebaMechaDaemon

        d = object.__new__(TiebaMechaDaemon)
        d.__init__()
        return d

    async def test_invalid_time_no_job_registered(self, db):
        """非法时间且原本无任务: 不注册新任务 (页面层负责拦截保存)"""
        import json

        d = self._fresh_daemon()
        await db.set_setting("schedule", json.dumps({"enabled": True, "sign_time": "8点"}))
        await d.reload(db)

        assert d.scheduler.get_job(d.sign_job_id) is None

    async def test_invalid_time_preserves_existing_job(self, db):
        """回归: 非法时间不得移除已注册任务 -- 先删后建会在解析失败时静默丢失定时签到"""
        import json

        d = self._fresh_daemon()
        await db.set_setting("schedule", json.dumps({"enabled": True, "sign_time": "08:30"}))
        await d.reload(db)
        assert d.scheduler.get_job(d.sign_job_id) is not None

        # 写入非法时间后重载, 旧任务应原样保留
        await db.set_setting("schedule", json.dumps({"enabled": True, "sign_time": "25:99"}))
        await d.reload(db)

        job = d.scheduler.get_job(d.sign_job_id)
        assert job is not None, "解析失败时应保留原有任务"
        assert str(job.trigger.fields[5]) == "8"  # Hour 仍为 08:30
        assert str(job.trigger.fields[6]) == "30"

    async def test_corrupted_schedule_json_preserves_existing_job(self, db):
        """回归: schedule 为损坏 JSON 时跳过重载, 不影响现有任务"""
        import json

        d = self._fresh_daemon()
        await db.set_setting("schedule", json.dumps({"enabled": True, "sign_time": "08:30"}))
        await d.reload(db)

        await db.set_setting("schedule", "{not-json")
        await d.reload(db)
        assert d.scheduler.get_job(d.sign_job_id) is not None

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


@pytest.mark.asyncio
class TestMatrixAccountForumsErrorPaths:
    """矩阵流 sign_account_forums（矩阵全扫与守护错峰的生产路径）失效码处理"""

    async def _setup(self, db):
        from tieba_mecha.core.account import add_account

        acc = await add_account(db=db, name="acc_matrix", bduss="a" * 192, stoken="b" * 64)
        forum = await db.add_forum(fid=2, fname="matrix_forum", account_id=acc.id)
        return acc, forum

    async def test_invalid_forum_removed_single_warn(self, db):
        """340006 失效 -> 自动删除 + 移除 WARN 恰好一条（回归：invalid 分支与统一失败分支曾同文双记）"""
        acc, forum = await self._setup(db)
        client = make_client([FakeSignResponse(code=340006, truthy=False)])

        with patch("tieba_mecha.core.sign.create_client", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch("tieba_mecha.core.sign.log_warn", new_callable=AsyncMock) as mock_warn:
                    async for _ in sign_account_forums(db, account_id=acc.id, delay_min=0, delay_max=0):
                        pass

        async with db.async_session() as session:
            assert await session.get(Forum, forum.id) is None, "失效贴吧应被删除"
        warn_msgs = [str(c.args[0]) for c in mock_warn.await_args_list]
        removed = [m for m in warn_msgs if "已自动移除" in m]
        assert len(removed) == 1, f"失效移除 WARN 应恰好 1 条，实际: {warn_msgs}"
