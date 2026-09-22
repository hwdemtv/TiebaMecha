"""2026-09-21 hwdemtv3 全吧封禁事故回归测试。

事故根因：账号被判定 banned 后，发帖调度链路（轮询取号/死锁检测/账号预检）
与养号链路均不认终态状态，封禁号被照常派发（20:50 任务遭 220012 拦截）、
照常 BioWarming 养号（封禁号持续产生行为记录）。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tieba_mecha.core.batch_post import (
    TERMINAL_ACCOUNT_STATUSES,
    BatchPostManager,
    BatchPostTask,
)


def _bduss() -> str:
    return "b" * 192


class TestTerminalStatusConstant:
    def test_terminal_statuses_complete(self):
        assert TERMINAL_ACCOUNT_STATUSES == frozenset(
            {"banned", "suspended", "expired", "suspended_proxy"}
        )


class TestMaintAccountFiltering:
    """养号队列必须排除终态账号：封禁号继续养号等于给风控递证据。"""

    async def _enable_maint(self, db, name: str, status: str):
        acc = await db.add_account(name=name, bduss=_bduss(), stoken="s" * 64)
        await db.update_account(acc.id, is_maint_enabled=True, status=status)
        return acc

    @pytest.mark.parametrize("status", ["banned", "suspended", "suspended_proxy", "expired"])
    async def test_terminal_statuses_excluded_from_maint(self, db, status):
        acc = await self._enable_maint(db, f"acc_{status}", status)

        maint_ids = [a.id for a in await db.get_maint_accounts()]

        assert acc.id not in maint_ids

    async def test_healthy_account_still_in_maint(self, db):
        acc = await self._enable_maint(db, "acc_ok", "active")

        maint_ids = [a.id for a in await db.get_maint_accounts()]

        assert acc.id in maint_ids


class TestBanStatusLinkage:
    """update_account_status 判定封禁时联动关闭养号开关（心跳/手动验证/发帖拦截共用收口）。"""

    async def test_banned_disables_maint_flag(self, db):
        acc = await db.add_account(name="acc", bduss=_bduss(), stoken="s" * 64)
        await db.update_account(acc.id, is_maint_enabled=True)

        await db.update_account_status(acc.id, "banned")

        updated = await db.get_account_by_id(acc.id)
        assert updated.status == "banned"
        assert updated.is_maint_enabled is False

    async def test_non_banned_keeps_maint_flag(self, db):
        acc = await db.add_account(name="acc", bduss=_bduss(), stoken="s" * 64)
        await db.update_account(acc.id, is_maint_enabled=True)

        await db.update_account_status(acc.id, "active")

        updated = await db.get_account_by_id(acc.id)
        assert updated.is_maint_enabled is True


class TestBatchPoolEntryFilter:
    """任务执行入口应剔除终态账号；账号池全灭时快速失败，不得进入发帖循环。"""

    def _task(self, account_ids: list[int]) -> BatchPostTask:
        return BatchPostTask(
            id="t",
            fname="f",
            fnames=["f"],
            accounts=account_ids,
            strategy="strict_round_robin",
            total=5,
        )

    async def _run(self, manager: BatchPostManager, task: BatchPostTask) -> list[dict]:
        with patch("tieba_mecha.core.batch_post.get_auth_manager", new_callable=AsyncMock) as mock_auth:
            mock_auth_mgr = AsyncMock()
            mock_auth_mgr.check_local_status = AsyncMock()
            mock_auth_mgr.status = MagicMock()
            mock_auth_mgr.status.__eq__ = lambda s, o: True  # PRO
            mock_auth.return_value = mock_auth_mgr
            return [u async for u in manager.execute_task(task)]

    async def test_all_banned_pool_fails_fast(self, db):
        acc = await db.add_account(name="banned_acc", bduss=_bduss(), stoken="s" * 64)
        await db.update_account(acc.id, status="banned")

        updates = await self._run(BatchPostManager(db), self._task([acc.id]))

        assert updates and updates[0]["status"] == "failed"
        assert "账号全部不可用" in updates[0]["msg"]

    async def test_unknown_account_id_dropped(self, db):
        updates = await self._run(BatchPostManager(db), self._task([9999]))

        assert updates and updates[0]["status"] == "failed"
        assert "账号全部不可用" in updates[0]["msg"]

    async def test_mixed_pool_drops_banned_and_proceeds(self, db):
        ok = await db.add_account(name="ok_acc", bduss=_bduss(), stoken="s" * 64)
        banned = await db.add_account(name="banned_acc", bduss="c" * 192, stoken="s" * 64)
        await db.update_account(banned.id, status="banned")

        task = self._task([ok.id, banned.id])
        updates = await self._run(BatchPostManager(db), task)

        # 空物料库 → 应走到物料检查（账号过滤已放行健康号），且任务池只剩健康号
        assert updates[0]["status"] == "failed"
        assert "物料池为空" in updates[0]["msg"]
        assert task.accounts == [ok.id]


class TestMidRunBanEviction:
    """发帖拦截判定全吧封禁时必须同步剔除内存态：只写库不清内存，同一任务后续物料仍会选中该账号。"""

    def test_ban_branch_evicts_in_memory_state(self):
        import inspect

        from tieba_mecha.core import batch_post

        source = inspect.getsource(batch_post.BatchPostManager)
        assert "task.accounts = [a for a in task.accounts if a != account_id]" in source
        assert "account_map.pop(account_id, None)" in source
        assert "aw[0] != account_id" in source
