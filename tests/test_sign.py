"""Tests for sign-in functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import asdict

from tieba_mecha.core.sign import SignResult, ForumInfo


class TestSignResult:
    """Tests for SignResult dataclass."""

    def test_sign_result_creation(self):
        """Test SignResult creation with all fields."""
        result = SignResult(
            fname="test_forum",
            success=True,
            message="签到成功",
            sign_count=5,
        )

        assert result.fname == "test_forum"
        assert result.success is True
        assert result.message == "签到成功"
        assert result.sign_count == 5

    def test_sign_result_defaults(self):
        """Test SignResult default values."""
        result = SignResult(fname="test", success=False, message="failed")

        assert result.sign_count == 0

    def test_sign_result_failure(self):
        """Test creating a failure SignResult."""
        result = SignResult(
            fname="forum",
            success=False,
            message="已签到过",
        )

        assert result.success is False
        assert "已签到过" in result.message


class TestForumInfo:
    """Tests for ForumInfo dataclass."""

    def test_forum_info_creation(self):
        """Test ForumInfo creation."""
        info = ForumInfo(
            fid=12345,
            fname="test_forum",
            is_sign_today=True,
            sign_count=10,
        )

        assert info.fid == 12345
        assert info.fname == "test_forum"
        assert info.is_sign_today is True
        assert info.sign_count == 10

    def test_forum_info_defaults(self):
        """Test ForumInfo with minimal args."""
        info = ForumInfo(
            fid=1,
            fname="forum",
            is_sign_today=False,
            sign_count=0,
        )

        assert info.fid == 1
        assert info.is_sign_today is False
        assert info.sign_count == 0


@pytest.mark.asyncio
class TestSignForum:
    """Tests for sign_forum function."""

    async def test_sign_forum_no_credentials(self, db):
        """Test sign_forum returns failure when no credentials."""
        from tieba_mecha.core.sign import sign_forum

        result = await sign_forum(db, "test_forum")

        assert result.success is False
        assert "未找到账号凭证" in result.message

    async def test_sign_forum_success(self, db, sample_account_data, mock_aiotieba_client):
        """Test successful sign_forum."""
        from tieba_mecha.core.sign import sign_forum
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Mock the client to return success
        mock_aiotieba_client.sign_forum = AsyncMock(return_value=MagicMock())

        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            result = await sign_forum(db, "test_forum")

        assert result.success is True
        assert result.message == "签到成功"

    async def test_sign_forum_failure(self, db, sample_account_data, mock_aiotieba_client):
        """Test sign_forum when API returns failure."""
        from tieba_mecha.core.sign import sign_forum
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Mock the client to return None (failure)
        mock_aiotieba_client.sign_forum = AsyncMock(return_value=None)

        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            result = await sign_forum(db, "test_forum")

        assert result.success is False


@pytest.mark.asyncio
class TestGetSignStats:
    """Tests for get_sign_stats function."""

    async def test_get_sign_stats_no_account(self, db):
        """Test get_sign_stats with no account."""
        from tieba_mecha.core.sign import get_sign_stats

        stats = await get_sign_stats(db)

        assert stats["total"] == 0
        assert stats["success"] == 0
        assert stats["failure"] == 0

    async def test_get_sign_stats_no_forums(self, db, sample_account_data):
        """Test get_sign_stats with account but no forums."""
        from tieba_mecha.core.sign import get_sign_stats
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        stats = await get_sign_stats(db)

        assert stats["total"] == 0
        assert stats["success"] == 0

    async def test_get_sign_stats_with_forums(self, db, sample_account_data):
        """Test get_sign_stats with forums."""
        from tieba_mecha.core.sign import get_sign_stats
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Add forums
        forum1 = await db.add_forum(fid=1, fname="forum1", account_id=acc.id)
        forum2 = await db.add_forum(fid=2, fname="forum2", account_id=acc.id)

        # Mark one as signed
        await db.update_forum_sign(forum1.id, success=True)

        stats = await get_sign_stats(db)

        assert stats["total"] == 2
        assert stats["success"] == 1
        assert stats["failure"] == 0


@pytest.mark.asyncio
class TestSyncForumsToDB:
    """Tests for sync_forums_to_db function."""

    async def test_sync_forums_no_account(self, db):
        """Test sync_forums_to_db with no account."""
        from tieba_mecha.core.sign import sync_forums_to_db

        rows = [r async for r in sync_forums_to_db(db)]
        assert rows == []

    async def test_sync_forums_adds_new(self, db, sample_account_data, mock_aiotieba_client):
        """Test sync_forums_to_db adds new forums."""
        from tieba_mecha.core.sign import sync_forums_to_db, get_follow_forums
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Mock user info
        mock_user_info = MagicMock()
        mock_user_info.user_id = 12345
        mock_aiotieba_client.get_self_info = AsyncMock(return_value=mock_user_info)

        # Mock follow forums (Return the ForumInfo object that sync_forums_to_db expects)
        mock_info = ForumInfo(fid=12345, fname="test_forum", is_sign_today=False, sign_count=0)
        
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("tieba_mecha.core.sign.get_follow_forums", AsyncMock(return_value=[mock_info])):
                rows = [r async for r in sync_forums_to_db(db)]

        assert sum(r["added"] for r in rows) == 1


@pytest.mark.asyncio
class TestSignAllForums:
    """Tests for sign_all_forums function."""

    async def test_sign_all_forums_no_account(self, db):
        """Test sign_all_forums with no account."""
        from tieba_mecha.core.sign import sign_all_forums

        results = []
        async for result in sign_all_forums(db):
            results.append(result)

        assert len(results) == 1
        assert results[0].success is False
        assert "未找到活跃账号" in results[0].message

    async def test_sign_all_forums_no_forums(self, db, sample_account_data, mock_aiotieba_client):
        """Test sign_all_forums with account but no forums."""
        from tieba_mecha.core.sign import sign_all_forums
        from tieba_mecha.core.account import add_account

        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            async for result in sign_all_forums(db, delay_min=0, delay_max=0):
                results.append(result)

        # Should complete without error even with no forums
        assert len(results) == 0

    async def test_sign_all_forums_success(self, db, sample_account_data, mock_aiotieba_client):
        """Test sign_all_forums successful execution."""
        from tieba_mecha.core.sign import sign_all_forums
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Add a forum
        await db.add_forum(fid=1, fname="test_forum", account_id=acc.id)

        # Mock sign_forum to succeed
        mock_aiotieba_client.sign_forum = AsyncMock(return_value=MagicMock())

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for result in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(result)

        assert len(results) == 1
        assert results[0].success is True


@pytest.mark.asyncio
class TestSignAllAccounts:
    """Tests for sign_all_accounts matrix function."""

    async def test_sign_all_accounts_no_accounts(self, db):
        """Test sign_all_accounts with no accounts."""
        from tieba_mecha.core.sign import sign_all_accounts

        results = []
        async for result in sign_all_accounts(db):
            results.append(result)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "无可用账号" in results[0]["message"]

    async def test_sign_all_accounts_skips_suspended(self, db, sample_account_data):
        """Test sign_all_accounts skips suspended accounts."""
        from tieba_mecha.core.sign import sign_all_accounts
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Suspend the account
        await db.update_account(acc.id, status="suspended_proxy")
        
        # Ensure account has at least one forum so we can verify if it's actually skipped vs missing forums
        await db.add_forum(fid=1, fname="test_forum", account_id=acc.id)

        results = []
        async for result in sign_all_accounts(db):
            results.append(result)

        # Should report no available accounts
        assert len(results) == 1
        assert results[0]["success"] is False

    async def test_sign_all_accounts_proxy_status(self, db, sample_account_data, mock_aiotieba_client):
        """Test sign_all_accounts reports proxy status correctly."""
        from tieba_mecha.core.sign import sign_all_accounts
        from tieba_mecha.core.account import add_account

        # Create account without proxy
        acc = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        # Add forum
        await db.add_forum(fid=1, fname="test_forum", account_id=acc.id)

        mock_aiotieba_client.sign_forum = AsyncMock(return_value=MagicMock())

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for result in sign_all_accounts(db, delay_min=0, delay_max=0):
                    results.append(result)

        # Should warn about missing proxy
        assert len(results) >= 1
        # The first yield might be the result of a single forum or no accounts
        assert any(r.get("proxy_status") == "missing" for r in results)


@pytest.mark.asyncio
class TestSignLoadFailed300004:
    """300004 数据加载失败处置（2026-11-03 月报遗留项②）。

    10-03~07 电影吧对 187 连日返回 (300004, '加载数据失败')（裸元组文案），
    期间自动流无重试、日志不可读。修复：_parse_sign_result 输出友好文案；
    自动流（矩阵/全扫）遇 300004 短退避重试一次。
    """

    @staticmethod
    def _failed_res(code, msg):
        """模拟 aiotieba 带内嵌 err 的失败响应（err 存在时对象为假值）。

        err 用 namedtuple：aiotieba 的 err str 是元组样式 "(300004, '加载数据失败')"，
        与 _parse_sign_result 兜底文案 str(err) 的真实形态保持一致。
        """
        from collections import namedtuple

        _Err = namedtuple("_Err", ["code", "msg"])

        class _Res:
            def __init__(self):
                self.err = _Err(code, msg)

            def __bool__(self):
                return False

        return _Res()

    async def test_parse_sign_result_300004_friendly_message(self):
        from tieba_mecha.core.sign import _parse_sign_result
        from tieba_mecha.core.risk import ERR_SIGN_LOAD_FAILED

        success, msg, already, invalid, err_code = _parse_sign_result(self._failed_res(ERR_SIGN_LOAD_FAILED, "加载数据失败"))
        assert success is False
        assert msg == "签到数据加载失败 (300004)"
        assert err_code == ERR_SIGN_LOAD_FAILED
        # 其他错误码仍走 str(err) 兜底（元组形态随 Python 版本有差异，只断言含原始消息）
        _, msg2, *_ = _parse_sign_result(self._failed_res(999999, "神秘错误"))
        assert "神秘错误" in msg2
        assert "签到数据加载失败" not in msg2

    async def test_matrix_flow_retries_once_on_300004(self, db, sample_account_data, mock_aiotieba_client):
        """矩阵流首试 300004 → 短退避重试一次 → 重试成功按成功记账"""
        from tieba_mecha.core.sign import sign_account_forums
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db, name=sample_account_data["name"],
            bduss=sample_account_data["bduss"], stoken=sample_account_data["stoken"],
        )
        await db.add_forum(fid=1, fname="电影", account_id=acc.id)

        mock_aiotieba_client.sign_forum = AsyncMock(
            side_effect=[self._failed_res(300004, "加载数据失败"), MagicMock()]
        )

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_account_forums(db, acc.id, delay_min=0, delay_max=0):
                    results.append(r)

        assert len(results) == 1
        assert results[0]["success"] is True
        assert mock_aiotieba_client.sign_forum.await_count == 2

    async def test_matrix_flow_gives_up_after_retry_still_300004(self, db, sample_account_data, mock_aiotieba_client):
        """重试仍 300004 → 按普通失败记账，只多打一次请求不无限重试"""
        from tieba_mecha.core.sign import sign_account_forums
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db, name=sample_account_data["name"],
            bduss=sample_account_data["bduss"], stoken=sample_account_data["stoken"],
        )
        await db.add_forum(fid=1, fname="电影", account_id=acc.id)

        mock_aiotieba_client.sign_forum = AsyncMock(
            return_value=self._failed_res(300004, "加载数据失败")
        )

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_account_forums(db, acc.id, delay_min=0, delay_max=0):
                    results.append(r)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "签到数据加载失败" in results[0]["message"]
        assert mock_aiotieba_client.sign_forum.await_count == 2

    async def test_sign_all_forums_retries_once_on_300004(self, db, sample_account_data, mock_aiotieba_client):
        """全扫流同样重试一次"""
        from tieba_mecha.core.sign import sign_all_forums
        from tieba_mecha.core.account import add_account

        acc = await add_account(
            db=db, name=sample_account_data["name"],
            bduss=sample_account_data["bduss"], stoken=sample_account_data["stoken"],
        )
        await db.add_forum(fid=1, fname="电影", account_id=acc.id)

        mock_aiotieba_client.sign_forum = AsyncMock(
            side_effect=[self._failed_res(300004, "加载数据失败"), MagicMock()]
        )

        results = []
        with patch("tieba_mecha.core.sign.create_client", return_value=mock_aiotieba_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                async for r in sign_all_forums(db, delay_min=0, delay_max=0):
                    results.append(r)

        assert len(results) == 1
        assert results[0].success is True
        assert mock_aiotieba_client.sign_forum.await_count == 2
