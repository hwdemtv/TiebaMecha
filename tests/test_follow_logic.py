"""Tests for forum follow and account selection logic."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from tieba_mecha.core.batch_post import BatchPostManager
from tieba_mecha.db.models import Forum, TargetPool


def _make_mock_client():
    """创建带 get_forum mock 的客户端（每个贴吧返回不同 fid）"""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.follow_forum = AsyncMock(return_value=None)

    # get_forum 根据贴吧名返回不同 fid（err=None 表示接口成功）
    _fid_counter = [10000]
    async def _mock_get_forum(fname):
        info = MagicMock()
        _fid_counter[0] += 1
        info.fid = _fid_counter[0]
        info.err = None
        return info
    mock_client.get_forum = _mock_get_forum
    return mock_client


def _err_resp(msg: str):
    """模拟 aiotieba BoolResponse：API 失败不抛异常，错误挂在 .err 属性上"""
    resp = MagicMock()
    resp.err = RuntimeError(msg)
    return resp


@pytest.mark.asyncio
class TestFollowForumsBulk:
    """Tests for the bulk follow functionality."""

    async def test_follow_forums_bulk_success(self, db):
        """测试批量关注成功的情况"""
        from tieba_mecha.core.account import encrypt_value, get_account_credentials

        # 准备账号数据
        enc_bduss = encrypt_value("a" * 192)
        acc1 = await db.add_account(name="acc1", bduss=enc_bduss)

        mock_client = _make_mock_client()

        # 多贴吧场景下相邻操作间会真实拟人休眠，测试中打补丁跳过
        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock):
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

        mock_client = _make_mock_client()

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
        mock_client = _make_mock_client()
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
class TestFollowErrSemantics:
    """P0/P1 回归：aiotieba API 失败不抛异常（错误挂 .err），必须据此判定成败与分类"""

    async def _make_account(self, db, name: str):
        from tieba_mecha.core.account import encrypt_value
        return await db.add_account(name=name, bduss=encrypt_value("a" * 192))

    async def test_follow_err_response_is_failure(self, db):
        """关注 API 失败（.err 挂错而非抛异常）不得计为成功，也不得写库"""
        acc1 = await self._make_account(db, "err_fol_acc")
        mock_client = _make_mock_client()
        mock_client.follow_forum = AsyncMock(return_value=_err_resp("9999: 服务器开小差了"))

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock):
            pm = BatchPostManager(db)
            result = await pm.follow_forums_bulk(["flaky_bar"], account_ids=[acc1.id])

        assert len(result["failed"]) == 1
        assert result["success"] == []
        assert all(f.fname != "flaky_bar" for f in await db.get_forums())

    async def test_unfollow_err_response_keeps_record(self, db):
        """取关 API 失败时：计为失败，本地关注记录与靶场均保留"""
        acc1 = await self._make_account(db, "err_unf_acc")
        await db.add_forum(fid=1, fname="sticky_bar", account_id=acc1.id)
        await db.upsert_target_pools(["sticky_bar"], "test")

        mock_client = _make_mock_client()
        mock_client.unfollow_forum = AsyncMock(return_value=_err_resp("9999: 服务器开小差了"))

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client):
            pm = BatchPostManager(db)
            res = await pm.unfollow_forums_bulk(["sticky_bar"])

        assert len(res["failed"]) == 1
        assert res["success"] == []
        assert len(await db.get_forums()) == 1
        assert len(await db.get_all_target_pools_raw()) == 1

    async def test_unfollow_not_followed_is_idempotent(self, db):
        """取关遇“尚未关注”类错误：按幂等成功处理并清理本地记录与靶场"""
        acc1 = await self._make_account(db, "ghost_acc")
        await db.add_forum(fid=2, fname="ghost_bar", account_id=acc1.id)
        await db.upsert_target_pools(["ghost_bar"], "test")

        mock_client = _make_mock_client()
        mock_client.unfollow_forum = AsyncMock(return_value=_err_resp("340008: 您尚未关注该吧"))

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client):
            pm = BatchPostManager(db)
            res = await pm.unfollow_forums_bulk(["ghost_bar"])

        assert len(res["success"]) == 1
        assert res["failed"] == []
        assert len(await db.get_forums()) == 0
        assert len(await db.get_all_target_pools_raw()) == 0

    async def test_follow_already_followed_err_classifies_as_skip(self, db):
        """API 返回“已关注”：归入 skipped 而非 failed，且补齐本地记录（不计熔断）"""
        acc1 = await self._make_account(db, "dup_srv_acc")
        mock_client = _make_mock_client()
        mock_client.follow_forum = AsyncMock(return_value=_err_resp("330005: 你已关注该吧"))

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client):
            pm = BatchPostManager(db)
            result = await pm.follow_forums_bulk(["dup_server_bar"], account_ids=[acc1.id])

        assert result["failed"] == []
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "已关注"
        fnames = [f.fname for f in await db.get_forums(acc1.id, include_hidden=True)]
        assert "dup_server_bar" in fnames

    async def test_follow_success_resets_stale_flags(self, db):
        """重新关注成功后，滞后的 is_hidden/is_banned 标记必须复位"""
        acc1 = await self._make_account(db, "revive_acc")
        await db.add_forum(fid=1, fname="revive_bar", account_id=acc1.id)

        # 模拟 sync 标记过的滞后状态：服务端曾取关（hidden）+ 曾被拉黑（banned）
        from sqlalchemy import select
        from tieba_mecha.db.models import Forum
        async with db.async_session() as session:
            forum = (await session.execute(
                select(Forum).where(Forum.fname == "revive_bar", Forum.account_id == acc1.id)
            )).scalar_one()
            forum.is_hidden = True
            forum.is_banned = True
            forum.ban_reason = "旧封禁"
            await session.commit()

        mock_client = _make_mock_client()
        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock):
            pm = BatchPostManager(db)
            result = await pm.follow_forums_bulk(["revive_bar"], account_ids=[acc1.id])

        assert len(result["success"]) == 1
        forums = {f.fname: f for f in await db.get_forums(acc1.id, include_hidden=True)}
        revived = forums["revive_bar"]
        assert revived.is_hidden is False
        assert revived.is_banned is False
        assert revived.ban_reason == ""


class TestFollowRobustness:
    """检查报告修复回归：并发闸门 / 输入防护 / 拉黑重试 / 熔断口径 / 异常落账 / 补齐口径"""

    async def _make_account(self, db, name: str):
        from tieba_mecha.core.account import encrypt_value
        return await db.add_account(name=name, bduss=encrypt_value("a" * 192))

    async def test_concurrent_gate_rejects_second_run(self, db):
        """闸门被占用时第二次调用应 fail-fast 拒绝，且不发出任何关注请求"""
        from tieba_mecha.core import batch_post as bp

        acc1 = await self._make_account(db, "gate_acc")
        mock_client = _make_mock_client()

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client):
            pm = BatchPostManager(db)
            assert bp._follow_flow_gate.try_lock() is True
            try:
                result = await pm.follow_forums_bulk(["gate_bar"], account_ids=[acc1.id])
                assert len(result["failed"]) == 1
                assert result["failed"][0]["reason"] == "已有批量关注/取关任务在执行"
                assert mock_client.follow_forum.await_count == 0
            finally:
                bp._follow_flow_gate.unlock()

            # 闸门释放后恢复正常执行
            result2 = await pm.follow_forums_bulk(["gate_bar"], account_ids=[acc1.id])
            assert len(result2["success"]) == 1

    async def test_unfollow_gate_shared_with_follow(self, db):
        """关注与取关共用同一把闸门：取关执行中发起关注应被拒绝"""
        from tieba_mecha.core import batch_post as bp

        pm = BatchPostManager(db)
        assert bp._follow_flow_gate.try_lock() is True
        try:
            result = await pm.unfollow_forums_bulk(["some_bar"])
            assert result["failed"][0]["reason"] == "已有批量关注/取关任务在执行"
        finally:
            bp._follow_flow_gate.unlock()

    async def test_empty_fnames_is_noop(self, db):
        """空贴吧列表应直接返回且不创建客户端"""
        acc1 = await self._make_account(db, "empty_acc")
        mock_client = _make_mock_client()

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client) as mc:
            pm = BatchPostManager(db)
            result = await pm.follow_forums_bulk([], account_ids=[acc1.id])

        assert result == {"success": [], "failed": [], "skipped": []}
        mc.assert_not_called()

    async def test_duplicate_fnames_dedup(self, db):
        """重复的贴吧名应去重，只关注一次"""
        acc1 = await self._make_account(db, "dup_input_acc")
        mock_client = _make_mock_client()

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock):
            pm = BatchPostManager(db)
            result = await pm.follow_forums_bulk(["dup_in_bar", "dup_in_bar"], account_ids=[acc1.id])

        assert mock_client.follow_forum.await_count == 1
        assert len(result["success"]) == 1

    async def test_banned_pair_skipped_without_retry(self, db):
        """已标记拉黑的 (账号, 吧) 对不应重试关注，直接跳过"""
        acc1 = await self._make_account(db, "banned_acc")
        await db.add_forum(fid=9, fname="bl_retry_bar", account_id=acc1.id)
        await db.mark_forum_banned(acc1.id, "bl_retry_bar", reason="历史拉黑")

        mock_client = _make_mock_client()
        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client), \
             patch("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock):
            pm = BatchPostManager(db)
            result = await pm.follow_forums_bulk(["bl_retry_bar"], account_ids=[acc1.id])

        mock_client.follow_forum.assert_not_called()
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "该吧已拉黑，跳过重试"

    async def test_blacklist_does_not_trip_failure_breaker(self, db):
        """拉黑（吧级处置）不计入连续失败熔断：breaker_state 不产生 follow 记录"""
        from sqlalchemy import select

        from tieba_mecha.db.models import BreakerState

        acc1 = await self._make_account(db, "bl_breaker_acc")
        mock_client = _make_mock_client()
        mock_client.follow_forum = AsyncMock(side_effect=Exception("400013: 被拉黑"))

        with patch("tieba_mecha.core.batch_post.create_client", return_value=mock_client):
            pm = BatchPostManager(db)
            result = await pm.follow_forums_bulk(["bl_breaker_bar"], account_ids=[acc1.id])

        assert len(result["failed"]) == 1
        async with db.async_session() as session:
            rows = (await session.execute(
                select(BreakerState).where(BreakerState.scope == "follow")
            )).scalars().all()
        assert rows == [], "拉黑失败不应写入熔断状态"

    async def test_client_exception_records_remaining_fnames(self, db):
        """客户端创建失败时，该账号未尝试的 (账号, 吧) 对必须落入 skipped"""
        acc1 = await self._make_account(db, "boom_acc")

        with patch("tieba_mecha.core.batch_post.create_client", side_effect=Exception("boom")):
            pm = BatchPostManager(db)
            result = await pm.follow_forums_bulk(["boom_a", "boom_b"], account_ids=[acc1.id])

        skipped_names = {s["fname"] for s in result["skipped"]}
        assert skipped_names == {"boom_a", "boom_b"}
        assert all(s["reason"] == "客户端异常中断" for s in result["skipped"])

    async def test_complement_follow_pair_semantics(self, db):
        """批量补齐口径：只关注了部分选中贴吧的账号必须返回参与补齐，全关注的排除"""
        acc1 = await self._make_account(db, "part_acc")   # 只关注 A
        acc2 = await self._make_account(db, "part_acc2")  # 只关注 B
        acc3 = await self._make_account(db, "full_acc")   # A、B 全关注
        await db.add_forum(fid=1, fname="补A", account_id=acc1.id)
        await db.add_forum(fid=2, fname="补B", account_id=acc2.id)
        await db.add_forum(fid=3, fname="补A", account_id=acc3.id)
        await db.add_forum(fid=4, fname="补B", account_id=acc3.id)

        missing = await db.get_accounts_not_following_any_forums(["补A", "补B"])
        missing_ids = {a.id for a in missing}

        assert acc1.id in missing_ids, "只关注 A 的账号缺失 B，应参与补齐"
        assert acc2.id in missing_ids, "只关注 B 的账号缺失 A，应参与补齐"
        assert acc3.id not in missing_ids, "全关注的账号不应参与补齐"

    async def test_captcha_breaker_load_seeds_cooldown(self, db):
        """验证码熔断跨运行生效：历史 captcha 事件应在新实例 load 后进入冷却"""
        from tieba_mecha.core.batch_post import CaptchaCircuitBreaker

        acc1 = await self._make_account(db, "cap_load_acc")
        await db.save_captcha_event(account_id=acc1.id, event_type="captcha", reason="测试触发")

        breaker = CaptchaCircuitBreaker(cooldown_minutes=30, db=db)
        await breaker.load()

        assert breaker.is_in_cooldown(acc1.id) is True
        assert breaker.is_in_cooldown((acc1.id or 0) + 9999) is False


@pytest.mark.asyncio
class TestPickOptimalAccount:
    """Tests for the optimal account selection logic."""

    async def _build_maps(self, db, task):
        """构建 native_map 和 followed_map 用于测试"""
        pm = BatchPostManager(db)
        native_map = await pm._build_native_account_map(task.accounts)
        followed_map = await pm._build_followed_account_map(task.accounts)
        return native_map, followed_map

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
        native_map, followed_map = await self._build_maps(db, task)

        # 多次选择，应该只选 acc1
        selected_ids = set()
        for _ in range(10):
            selected = await pm._pick_optimal_account_for_target(
                task, "target_forum", 0, [(acc1.id, 5), (acc2.id, 5)],
                native_map, followed_map
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
        native_map, followed_map = await self._build_maps(db, task)

        # 应该回落到 round_robin 策略
        selected = await pm._pick_optimal_account_for_target(
            task, "target_forum", 0, [(acc1.id, 5), (acc2.id, 5)],
            native_map, followed_map
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
        native_map, followed_map = await self._build_maps(db, task)

        # 没有原生号，回落到 round_robin
        selected = await pm._pick_optimal_account_for_target(
            task, "target_forum", 0, [(acc1.id, 5)],
            native_map, followed_map
        )

        # 应该使用轮询策略选择的账号
        assert selected == acc1.id
