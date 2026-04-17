"""Tests for forum follow and account selection logic."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from tieba_mecha.core.batch_post import BatchPostManager
from tieba_mecha.db.models import Forum, TargetPool


@pytest.mark.asyncio
class TestFollowForumsBulk:
    """Tests for the bulk follow functionality."""

    async def test_follow_forums_bulk_success(self, db):
        """测试批量关注成功的情况"""
        from tieba_mecha.core.account import encrypt_value, get_account_credentials

        # 准备账号数据
        enc_bduss = encrypt_value("a" * 192)
        acc1 = await db.add_account(name="acc1", bduss=enc_bduss)

        # Mock 客户端
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.follow_forum = AsyncMock(return_value=None)

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client):
            pm = BatchPostManager(db)

            # 执行批量关注
            result = await pm.follow_forums_bulk(
                fnames=["new_forum_1", "new_forum_2"],
                account_ids=[acc1.id]
            )

            # 验证结果结构
            assert "success" in result
            assert "failed" in result
            assert "skipped" in result

            # 验证成功关注的数量
            assert len(result["success"]) == 2
            assert all(item["account_id"] == acc1.id for item in result["success"])

            # 验证数据库是否添加了关注记录
            forums = await db.get_forums()
            fnames = [f.fname for f in forums]
            assert "new_forum_1" in fnames
            assert "new_forum_2" in fnames

    async def test_follow_forums_bulk_skip_already_followed(self, db):
        """测试批量关注时跳过已关注的吧"""
        from tieba_mecha.core.account import encrypt_value

        # 准备账号数据并已关注一个吧
        enc_bduss = encrypt_value("a" * 192)
        acc1 = await db.add_account(name="acc1", bduss=enc_bduss)
        await db.add_forum(fid=1, fname="already_followed", account_id=acc1.id)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.follow_forum = AsyncMock(return_value=None)

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client):
            pm = BatchPostManager(db)

            # 尝试关注两个吧，其中一个已关注
            result = await pm.follow_forums_bulk(
                fnames=["already_followed", "new_forum"],
                account_ids=[acc1.id]
            )

            # 已关注的应该被跳过
            assert len(result["skipped"]) == 1
            assert result["skipped"][0]["fname"] == "already_followed"
            assert result["skipped"][0]["reason"] == "已关注"

            # 新吧应该成功
            assert len(result["success"]) == 1
            assert result["success"][0]["fname"] == "new_forum"

    async def test_follow_forums_bulk_no_accounts(self, db):
        """测试无可用账号时的情况"""
        pm = BatchPostManager(db)

        result = await pm.follow_forums_bulk(
            fnames=["some_forum"],
            account_ids=[]  # 空账号列表
        )

        assert len(result["failed"]) == 1
        assert result["failed"][0]["reason"] == "无可用账号"

    async def test_follow_forums_bulk_blacklisted_account(self, db):
        """测试账号被吧拉黑时的情况"""
        from tieba_mecha.core.account import encrypt_value

        enc_bduss = encrypt_value("a" * 192)
        acc1 = await db.add_account(name="acc1", bduss=enc_bduss)

        # 模拟被拉黑的错误
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.follow_forum = AsyncMock(side_effect=Exception("400013: 被拉黑"))

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client):
            pm = BatchPostManager(db)

            result = await pm.follow_forums_bulk(
                fnames=["banned_forum"],
                account_ids=[acc1.id]
            )

            # 应该标记为失败并记录拉黑原因
            assert len(result["failed"]) == 1
            assert result["failed"][0]["fname"] == "banned_forum"
            assert "拉黑" in result["failed"][0]["reason"]

            # 验证数据库中是否标记为封禁
            forums = await db.get_forums()
            banned_forums = [f for f in forums if f.is_banned]
            assert len(banned_forums) == 1
            assert banned_forums[0].fname == "banned_forum"


@pytest.mark.asyncio
class TestPickOptimalAccount:
    """Tests for the optimal account selection logic."""

    async def test_pick_account_skip_banned_forum(self, db):
        """测试选择账号时跳过已被该吧封禁的账号"""
        from tieba_mecha.core.account import encrypt_value

        # 准备两个账号，都关注了同一个吧
        enc_bduss1 = encrypt_value("a" * 192)
        enc_bduss2 = encrypt_value("b" * 192)
        acc1 = await db.add_account(name="acc1", bduss=enc_bduss1)
        acc2 = await db.add_account(name="acc2", bduss=enc_bduss2)

        # 账号1关注了 target_forum 且设为发帖目标
        forum1 = await db.add_forum(fid=1, fname="target_forum", account_id=acc1.id)
        await db.toggle_forum_post_target(forum1.id, True)

        # 账号2也关注了 target_forum 但被吧务封禁了
        forum2 = await db.add_forum(fid=2, fname="target_forum", account_id=acc2.id)
        await db.toggle_forum_post_target(forum2.id, True)
        await db.mark_forum_banned(acc2.id, "target_forum", "吧务封禁")

        from tieba_mecha.core.batch_post import BatchPostTask

        pm = BatchPostManager(db)
        task = BatchPostTask(
            id="test",
            fname="target_forum",
            accounts=[acc1.id, acc2.id],
            strategy="weighted"
        )

        # 多次选择，应该只选 acc1
        selected_ids = set()
        for _ in range(10):
            selected = await pm._pick_optimal_account_for_target(
                task, "target_forum", 0, [(acc1.id, 5), (acc2.id, 5)]
            )
            selected_ids.add(selected)

        # 验证只选中了 acc1（未被封禁的）
        assert selected_ids == {acc1.id}

    async def test_pick_account_all_banned(self, db):
        """测试当所有原生账号都被封禁时，回落到其他策略"""
        from tieba_mecha.core.account import encrypt_value

        enc_bduss1 = encrypt_value("a" * 192)
        enc_bduss2 = encrypt_value("b" * 192)
        acc1 = await db.add_account(name="acc1", bduss=enc_bduss1)
        acc2 = await db.add_account(name="acc2", bduss=enc_bduss2)

        # 两个账号都关注了 target_forum 但都被封禁
        forum1 = await db.add_forum(fid=1, fname="target_forum", account_id=acc1.id)
        await db.toggle_forum_post_target(forum1.id, True)
        await db.mark_forum_banned(acc1.id, "target_forum", "封禁1")

        forum2 = await db.add_forum(fid=2, fname="target_forum", account_id=acc2.id)
        await db.toggle_forum_post_target(forum2.id, True)
        await db.mark_forum_banned(acc2.id, "target_forum", "封禁2")

        from tieba_mecha.core.batch_post import BatchPostTask

        pm = BatchPostManager(db)
        task = BatchPostTask(
            id="test",
            fname="target_forum",
            accounts=[acc1.id, acc2.id],
            strategy="round_robin"  # 使用轮询策略作为回退
        )

        # 应该回落到 round_robin 策略
        selected = await pm._pick_optimal_account_for_target(
            task, "target_forum", 0, [(acc1.id, 5), (acc2.id, 5)]
        )

        # 因为原生号全被封禁，会回落到 round_robin，选择 acc1
        assert selected in [acc1.id, acc2.id]

    async def test_pick_account_no_native_follow(self, db):
        """测试没有原生关注账号时的情况"""
        from tieba_mecha.core.account import encrypt_value

        enc_bduss = encrypt_value("a" * 192)
        acc1 = await db.add_account(name="acc1", bduss=enc_bduss)

        # 账号没有关注 target_forum
        await db.add_forum(fid=1, fname="other_forum", account_id=acc1.id)

        from tieba_mecha.core.batch_post import BatchPostTask

        pm = BatchPostManager(db)
        task = BatchPostTask(
            id="test",
            fname="target_forum",
            accounts=[acc1.id],
            strategy="round_robin"
        )

        # 没有原生号，回落到 round_robin
        selected = await pm._pick_optimal_account_for_target(
            task, "target_forum", 0, [(acc1.id, 5)]
        )

        # 应该使用轮询策略选择的账号
        assert selected == acc1.id
