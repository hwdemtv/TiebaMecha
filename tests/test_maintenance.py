"""Tests for Maintenance (BioWarming) functionality."""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tieba_mecha.core.maintenance import MaintManager, do_warming


def _make_db(settings: dict | None = None):
    """构造带内存配置存储的 mock Database，返回 (db, store, saved)。"""
    db = MagicMock()
    store = dict(settings or {})
    saved = {}

    async def get_setting(key, default=""):
        return store.get(key, default)

    async def set_setting(key, value):
        saved[key] = value

    db.get_setting = AsyncMock(side_effect=get_setting)
    db.set_setting = AsyncMock(side_effect=set_setting)
    db.upsert_target_pools = AsyncMock(return_value=1)
    return db, store, saved


class TestMaintManager:
    """Tests for MaintManager class."""

    def test_maint_manager_initialization(self):
        """Test MaintManager initializes correctly."""
        mock_db = MagicMock()
        manager = MaintManager(mock_db)
        assert manager.db == mock_db

    @pytest.mark.asyncio
    async def test_run_maint_cycle_no_credentials(self):
        """Test run_maint_cycle handles missing credentials."""
        mock_db = MagicMock()
        manager = MaintManager(mock_db)

        with patch('tieba_mecha.core.maintenance.get_account_credentials', AsyncMock(return_value=None)):
            result = await manager.run_maint_cycle(account_id=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_run_maint_cycle_success(self):
        """Test run_maint_cycle executes successfully."""
        mock_db = MagicMock()
        mock_db.update_maint_status = AsyncMock()
        # [页面/核心演进] run_maint_cycle 现会查询账号显示名, db mock 需提供异步方法
        mock_db.get_account_by_id = AsyncMock(return_value=MagicMock(user_name="test_user", name="test_acc"))
        mock_db.get_forums = AsyncMock(return_value=[])
        mock_db.get_proxy = AsyncMock(return_value=None)
        manager = MaintManager(mock_db)

        # Mock credentials
        mock_creds = (1, "test_bduss", "test_stoken", None, "test_cuid", "test_ua")

        # Mock client
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_self_info = AsyncMock(return_value=MagicMock(user_id=12345))
        # err 必须显式为 None: MagicMock 自动生成的 err 属性是 truthy, 会被判为接口错误走空列表回退
        mock_client.get_follow_forums = AsyncMock(return_value=MagicMock(err=None, objs=[
            MagicMock(fname="test_forum")
        ]))
        mock_client.get_threads = AsyncMock(return_value=MagicMock(objs=[
            MagicMock(tid=123456, title="Test Thread Title")
        ]))
        mock_client.get_posts = AsyncMock()
        mock_client.agree = AsyncMock()
        # [行为演进] 防检测搜索 (15% 概率) 与破冰关注会调用这两个接口
        mock_client.search_threads = AsyncMock()
        mock_client.follow_forum = AsyncMock()

        with patch('tieba_mecha.core.maintenance.get_account_credentials', AsyncMock(return_value=mock_creds)):
            with patch('tieba_mecha.core.maintenance.create_client', AsyncMock(return_value=mock_client)):
                # 拟真停顿会真实睡眠, 打桩跳过
                with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
                    result = await manager.run_maint_cycle(account_id=1)

        assert result is True
        mock_db.update_maint_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_maint_cycle_empty_forum_list(self):
        """空关注列表 -> 回退本地数据库 -> 本地也为空时进入冷启动公域探索, 完成一轮维护返回 True。

        (行为演进: 旧逻辑直接返回 False, 现已改为 DEFAULT_PUBLIC_FORUMS 破冰探索)
        """
        mock_db = MagicMock()
        mock_db.update_maint_status = AsyncMock()
        mock_db.get_account_by_id = AsyncMock(return_value=MagicMock(user_name="test_user", name="test_acc"))
        mock_db.get_forums = AsyncMock(return_value=[])  # 本地数据库也无关注列表

        manager = MaintManager(mock_db)

        mock_creds = (1, "test_bduss", "test_stoken", None, "test_cuid", "test_ua")

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_self_info = AsyncMock(return_value=MagicMock(user_id=12345))
        mock_client.get_follow_forums = AsyncMock(return_value=MagicMock(objs=[]))
        mock_client.get_threads = AsyncMock(return_value=MagicMock(objs=[]))

        with patch('tieba_mecha.core.maintenance.get_account_credentials', AsyncMock(return_value=mock_creds)):
            with patch('tieba_mecha.core.maintenance.create_client', AsyncMock(return_value=mock_client)):
                with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
                    result = await manager.run_maint_cycle(account_id=1)

        assert result is True, "空关注列表应触发冷启动公域探索并完成维护"

    @pytest.mark.asyncio
    async def test_run_maint_cycle_exception_handling(self):
        """Test run_maint_cycle handles exceptions gracefully."""
        mock_db = MagicMock()
        manager = MaintManager(mock_db)

        with patch('tieba_mecha.core.maintenance.get_account_credentials', AsyncMock(side_effect=Exception("Test error"))):
            result = await manager.run_maint_cycle(account_id=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_human_sleep(self):
        """Test _human_sleep adds random delay."""
        import time

        mock_db = MagicMock()
        manager = MaintManager(mock_db)

        start = time.time()
        await manager._human_sleep(0.1, 0.2)
        elapsed = time.time() - start

        # Should have slept between 0.1 and 0.2 seconds
        assert 0.08 <= elapsed <= 0.3  # Allow some margin


class TestDoWarming:
    """Tests for do_warming convenience function."""

    @pytest.mark.asyncio
    async def test_do_warming_calls_manager(self):
        """Test do_warming calls MaintManager.run_maint_cycle."""
        mock_db = MagicMock()

        with patch('tieba_mecha.core.maintenance.MaintManager.run_maint_cycle', AsyncMock(return_value=True)) as mock_run:
            result = await do_warming(mock_db, account_id=1)

        assert result is True
        mock_run.assert_called_once_with(1)


class TestAutofollowNoBawu:
    """无吧主吧机会性关注 (_try_autofollow_no_bawu)。"""

    @pytest.mark.asyncio
    async def test_disabled_is_noop(self):
        """开关关闭时不应发起任何发现/关注请求。"""
        db, _, _ = _make_db({"maint_autofollow_enabled": "false"})
        manager = MaintManager(db)
        client = MagicMock()
        client.get_square_forums = AsyncMock()

        with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
            await manager._try_autofollow_no_bawu(client, 1, "acc", set())

        client.get_square_forums.assert_not_called()
        client.get_square_forums.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_follows_no_bawu_forum_and_records(self):
        """开关开启+概率命中时：关注无吧主吧、写记账、入靶场池无吧主分组。"""
        db, _, saved = _make_db({
            "maint_autofollow_enabled": "true",
            "maint_autofollow_prob": "1.0",
        })
        manager = MaintManager(db)
        cand = MagicMock(fname="无主吧", is_followed=False, member_num=2000)
        client = MagicMock()
        client.get_square_forums = AsyncMock(return_value=MagicMock(err=None, objs=[cand]))
        client.get_forum = AsyncMock(return_value=MagicMock(err=None, has_bawu=False, member_num=2000))
        client.follow_forum = AsyncMock(return_value=MagicMock(err=None))

        with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
            await manager._try_autofollow_no_bawu(client, 1, "acc", set())

        client.follow_forum.assert_awaited_once_with("无主吧")
        db.upsert_target_pools.assert_awaited_once_with(["无主吧"], group="无吧主")
        assert json.loads(saved["maint_autofollow_state"]) == {"1": ["无主吧"]}

    @pytest.mark.asyncio
    async def test_skips_forum_with_bawu(self):
        """候选吧都有吧务时不应关注任何吧。"""
        db, _, saved = _make_db({"maint_autofollow_enabled": "true", "maint_autofollow_prob": "1.0"})
        manager = MaintManager(db)
        cands = [MagicMock(fname=f"有主吧{i}", is_followed=False, member_num=2000) for i in range(3)]
        client = MagicMock()
        client.get_square_forums = AsyncMock(return_value=MagicMock(err=None, objs=cands))
        client.get_forum = AsyncMock(return_value=MagicMock(err=None, has_bawu=True, member_num=2000))
        client.follow_forum = AsyncMock()

        with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
            await manager._try_autofollow_no_bawu(client, 1, "acc", set())

        client.follow_forum.assert_not_called()
        client.get_forum.assert_awaited()  # 3 个候选都被复检过
        assert saved == {}
        db.upsert_target_pools.assert_not_called()

    @pytest.mark.asyncio
    async def test_member_num_filter(self):
        """吧人数低于下限的候选应在发现阶段被过滤，不发起复检请求。"""
        db, _, _ = _make_db({"maint_autofollow_enabled": "true", "maint_autofollow_prob": "1.0"})
        manager = MaintManager(db)
        cand = MagicMock(fname="迷你吧", is_followed=False, member_num=100)
        client = MagicMock()
        client.get_square_forums = AsyncMock(return_value=MagicMock(err=None, objs=[cand]))
        client.get_forum = AsyncMock()
        client.follow_forum = AsyncMock()

        with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
            await manager._try_autofollow_no_bawu(client, 1, "acc", set())

        client.get_forum.assert_not_called()
        client.follow_forum.assert_not_called()

    @pytest.mark.asyncio
    async def test_respects_per_account_cap(self):
        """记账达到单账号上限后不再发现/关注。"""
        db, store, _ = _make_db({"maint_autofollow_enabled": "true", "maint_autofollow_prob": "1.0"})
        store["maint_autofollow_state"] = json.dumps({"1": [f"吧{i}" for i in range(10)]})
        manager = MaintManager(db)
        client = MagicMock()
        client.get_square_forums = AsyncMock()

        with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
            await manager._try_autofollow_no_bawu(client, 1, "acc", set())

        client.get_square_forums.assert_not_called()

    @pytest.mark.asyncio
    async def test_follow_error_aborts_without_ledger(self):
        """关注接口报错（上限/违规吧）时终止本轮，不写记账不入池。"""
        db, _, saved = _make_db({"maint_autofollow_enabled": "true", "maint_autofollow_prob": "1.0"})
        manager = MaintManager(db)
        cand = MagicMock(fname="无主吧", is_followed=False, member_num=2000)
        client = MagicMock()
        client.get_square_forums = AsyncMock(return_value=MagicMock(err=None, objs=[cand]))
        client.get_forum = AsyncMock(return_value=MagicMock(err=None, has_bawu=False, member_num=2000))
        client.follow_forum = AsyncMock(return_value=MagicMock(err=Exception("follow limit")))

        with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
            await manager._try_autofollow_no_bawu(client, 1, "acc", set())

        assert saved == {}
        db.upsert_target_pools.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_config_is_noop(self):
        """配置数值非法时应静默跳过，不抛异常。"""
        db, _, _ = _make_db({
            "maint_autofollow_enabled": "true",
            "maint_autofollow_prob": "abc",
        })
        manager = MaintManager(db)
        client = MagicMock()
        client.get_square_forums = AsyncMock()

        with patch('tieba_mecha.core.maintenance.MaintManager._human_sleep', AsyncMock()):
            await manager._try_autofollow_no_bawu(client, 1, "acc", set())

        client.get_square_forums.assert_not_called()
