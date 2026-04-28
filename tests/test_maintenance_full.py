import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass
from typing import List

# 模拟 aiotieba 的返回对象
@dataclass
class MockUserInfo:
    user_id: int = 123456789
    user_name: str = "test_user"

@dataclass
class MockForum:
    fname: str

@dataclass
class MockForumsRes:
    objs: List[MockForum]

@dataclass
class MockThread:
    tid: int
    title: str

@dataclass
class MockThreadsRes:
    objs: List[MockThread]

class TestBioWarming(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 准备 Mock 数据库（所有异步方法用 AsyncMock）
        self.mock_db = MagicMock()
        self.mock_db.update_maint_status = AsyncMock()
        self.mock_db.get_account_by_id = AsyncMock(return_value=MagicMock(user_name="test_user", name="test_acc"))
        self.mock_db.get_forums = AsyncMock(return_value=[])
        self.mock_db.get_proxy = AsyncMock(return_value=None)

        # 准备 Mock 账号数据
        self.mock_account = MagicMock()
        self.mock_account.id = 1
        self.mock_account.bduss = "mock_bduss_encrypted"
        self.mock_account.stoken = "mock_stoken_encrypted"
        self.mock_account.proxy_id = None
        self.mock_account.cuid = "mock_cuid"
        self.mock_account.user_agent = "mock_ua"

        # 准备 Mock 核心组件
        self.patch_decrypt = patch("tieba_mecha.core.account.decrypt_value", side_effect=lambda x: x.replace("_encrypted", ""))
        self.patch_log_info = patch("tieba_mecha.core.maintenance.log_info", new_callable=AsyncMock)
        self.patch_sleep = patch("tieba_mecha.core.maintenance.MaintManager._human_sleep", new_callable=AsyncMock)

        self.patch_decrypt.start()
        self.patch_log_info.start()
        self.patch_sleep.start()

    async def asyncTearDown(self):
        patch.stopall()

    @patch("tieba_mecha.core.maintenance.create_client")
    @patch("tieba_mecha.core.maintenance.get_account_credentials")
    async def test_run_maint_cycle_success(self, mock_get_creds, mock_create_client):
        # 配置 Mock 凭证返回 (id, bduss, stoken, proxy_id, cuid, ua)
        mock_get_creds.return_value = (1, "mock_bduss", "mock_stoken", None, "mock_cuid", "mock_ua")

        # 配置 Mock Client
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_create_client.return_value = mock_client

        # 模拟 Client 接口返回
        mock_client.get_self_info.return_value = MockUserInfo()
        mock_client.get_follow_forums.return_value = MockForumsRes(objs=[MockForum(fname="asoul吧"), MockForum(fname="vtb吧")])
        mock_client.get_threads.return_value = MockThreadsRes(objs=[MockThread(tid=111, title="这是测试帖子1"), MockThread(tid=222, title="这是测试帖子2")])
        mock_client.get_posts.return_value = MagicMock()
        mock_client.agree.return_value = True

        # 执行维护
        from tieba_mecha.core.maintenance import MaintManager
        manager = MaintManager(self.mock_db)

        # 为了保证点赞逻辑被触发，我们可以多跑几次或者 hack random
        with patch("random.random", return_value=0.1): # 强制触发 60% 概率的点赞
            success = await manager.run_maint_cycle(account_id=1)

        # 验证结果
        self.assertTrue(success)

        # 验证 API 调用
        mock_client.get_self_info.assert_called_once()
        mock_client.get_follow_forums.assert_called_once()
        mock_client.get_threads.assert_called_once()
        mock_client.get_posts.assert_called()
        mock_client.agree.assert_called_once_with(unittest.mock.ANY) # tid 是随机抽取的

        # 验证数据库更新 (核心重点：确保使用的是 ID:1 而非 UID:123456789)
        self.mock_db.update_maint_status.assert_called_once_with(1)

        print("\n✅ [测试通过] 养号流程逻辑闭环验证成功。")
        print("✅ [指纹验证] 数据库 ID 传递正确。")

    @patch("tieba_mecha.core.maintenance.create_client")
    @patch("tieba_mecha.core.maintenance.get_account_credentials")
    async def test_run_maint_cycle_proxy_dead(self, mock_get_creds, mock_create_client):
        """代理失效时应跳过，不创建客户端"""
        mock_get_creds.return_value = (1, "mock_bduss", "mock_stoken", 99, "mock_cuid", "mock_ua")

        # 模拟代理失效
        dead_proxy = MagicMock()
        dead_proxy.is_active = False
        self.mock_db.get_proxy = AsyncMock(return_value=dead_proxy)

        from tieba_mecha.core.maintenance import MaintManager
        manager = MaintManager(self.mock_db)

        success = await manager.run_maint_cycle(account_id=1)
        self.assertFalse(success)
        mock_create_client.assert_not_called()

    async def test_run_maint_cycle_no_account_id(self):
        """account_id=None 时应安全返回 False"""
        from tieba_mecha.core.maintenance import MaintManager
        manager = MaintManager(self.mock_db)

        success = await manager.run_maint_cycle(account_id=None)
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
