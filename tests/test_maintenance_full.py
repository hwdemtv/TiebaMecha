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
        # 准备 Mock 数据库
        self.mock_db = MagicMock()
        self.mock_db.update_maint_status = AsyncMock()
        
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
        mock_client.get_posts.assert_called_once()
        mock_client.agree.assert_called_once_with(unittest.mock.ANY) # tid 是随机抽取的
        
        # 验证数据库更新 (核心重点：确保使用的是 ID:1 而非 UID:123456789)
        self.mock_db.update_maint_status.assert_called_once_with(1)
        
        print("\n✅ [测试通过] 养号流程逻辑闭环验证成功。")
        print("✅ [指纹验证] 数据库 ID 传递正确。")

if __name__ == "__main__":
    unittest.main()
