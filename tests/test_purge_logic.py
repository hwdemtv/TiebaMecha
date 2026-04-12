"""Tests for forum purge and unfollow logic."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from tieba_mecha.core.batch_post import BatchPostManager
from tieba_mecha.db.models import Forum, TargetPool

@pytest.mark.asyncio
class TestPurgeLogic:
    """Tests for the global purge and bulk unfollow functionality."""

    async def test_db_purge_methods(self, db):
        """测试数据库层的清理方法"""
        # 1. 准备测试数据
        acc1 = await db.add_account(name="acc1", bduss="a"*192)
        acc2 = await db.add_account(name="acc2", bduss="b"*192)
        
        # 关注关系
        await db.add_forum(fid=1, fname="purge_me", account_id=acc1.id)
        await db.add_forum(fid=2, fname="keep_me", account_id=acc1.id)
        await db.add_forum(fid=3, fname="purge_me", account_id=acc2.id)
        
        # 靶场池
        await db.upsert_target_pools(["purge_me", "keep_me"], "test_group")
        
        # 2. 验证 get_account_ids_following_forums
        affected_accs = await db.get_account_ids_following_forums(["purge_me"])
        assert sorted(affected_accs) == sorted([acc1.id, acc2.id])
        
        # 3. 执行清理：delete_forum_memberships_globally
        del_count = await db.delete_forum_memberships_globally(["purge_me"])
        assert del_count == 2
        
        remaining_forums = await db.get_forums()
        assert len(remaining_forums) == 1
        assert remaining_forums[0].fname == "keep_me"
        
        # 4. 执行清理：delete_target_pool_by_fnames
        del_target_count = await db.delete_target_pool_by_fnames(["purge_me"])
        assert del_target_count == 1
        
        remaining_targets = await db.get_all_target_pools_raw()
        assert len(remaining_targets) == 1
        assert remaining_targets[0].fname == "keep_me"

    @patch("tieba_mecha.core.batch_post.create_client")
    async def test_unfollow_forums_bulk_logic(self, mock_create_client, db):
        """测试 BatchPostManager 中的批量取关业务逻辑"""
        from tieba_mecha.core.account import encrypt_value, get_account_credentials
        
        # 准备数据：必须手动加密，因为 get_account_credentials 会尝试解密
        enc_bduss = encrypt_value("a"*192)
        acc1 = await db.add_account(name="acc1", bduss=enc_bduss)
        await db.add_forum(fid=1, fname="target_bar", account_id=acc1.id)
        await db.upsert_target_pools(["target_bar"], "test")
        
        # 调试断言：确保在测试环境下加密/解密链路是通的
        creds = await get_account_credentials(db, acc1.id)
        assert creds is not None, "测试环境解密失败，请检查 TIEBA_MECHA_SALT/SECRET_KEY 环境变量"
        assert creds[1] == "a"*192
        
        # Mock 客户端
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.unfollow = AsyncMock(return_value=None)
        mock_create_client.return_value = mock_client
        
        pm = BatchPostManager(db)
        
        # 执行取关
        success = await pm.unfollow_forums_bulk(["target_bar"])
        
        assert success is True
        # 验证是否调用了取关 API
        mock_client.unfollow.assert_called_with("target_bar")
        
        # 验证数据库是否已清理
        assert len(await db.get_forums()) == 0
        assert len(await db.get_all_target_pools_raw()) == 0

    @patch("tieba_mecha.core.batch_post.create_client")
    async def test_unfollow_no_accounts_logic(self, mock_create_client, db):
        """测试在没有任何账号关注时，该功能是否依然能清理数据库并安全退出"""
        # 准备数据：只在靶场池有，但没账号关注
        await db.upsert_target_pools(["lonely_bar"], "test")
        
        pm = BatchPostManager(db)
        
        # 执行取关
        success = await pm.unfollow_forums_bulk(["lonely_bar"])
        
        assert success is True
        # 验证由于没账号关注，所以不会创建客户端
        mock_create_client.assert_not_called()
        
        # 验证数据库最终还是被清理了
        assert len(await db.get_all_target_pools_raw()) == 0
