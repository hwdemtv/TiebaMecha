"""封禁知识持久化与预检封禁口径测试（2026-10-07 双缺口修复）。

① 空降吧封禁标记重启失忆：mark_forum_banned 只在 forums 表已有该行时落库，
   空降组合的封禁知识原先只活在进程级 _PERMISSION_DENIED 里，重启即丢。
   修复 = settings 黑名单账本（record/get/clear + TTL/FIFO），
   引擎 _build_banned_pairs 读取时合并，解封时联动清理。
② 预检封禁连坐：旧口径全库 max(is_banned)——任一账号（哪怕不在任务里）被封
   就整吧剔除；新口径按任务账号集判定，全封才剔，部分封降级 info 提示。
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tieba_mecha.core.batch_post import BatchPostManager, persist_permission_denied
from tieba_mecha.db.repositories.forum_repo import (
    PERMISSION_DENIED_LEDGER_KEY,
    _PERMISSION_LEDGER_LIMIT,
)
from tieba_mecha.web.pages.batch_post.launch_config import LaunchConfig
from tieba_mecha.web.pages.batch_post.preflight import PreflightService


def _ledger_entry(aid: int, fname: str, reason: str = "测试拦截", ts: datetime | None = None) -> dict:
    return {
        "aid": aid,
        "fname": fname,
        "reason": reason,
        "ts": (ts or datetime.now()).isoformat(timespec="seconds"),
    }


async def _make_account(db, name: str) -> int:
    from tieba_mecha.core.account import encrypt_value

    acc = await db.add_account(name=name, bduss=encrypt_value("a" * 192))
    return acc.id


@pytest.mark.asyncio
class TestPermissionLedger:
    async def test_record_and_get_roundtrip(self, db):
        """落账本后可按账号读回（无 Forum 行的空降组合也能持久化）。"""
        await db.record_permission_denied_ledger(5, "空降吧", "发帖静默拦截(220012)")
        pairs = await db.get_permission_denied_ledger([5])
        assert (5, "空降吧") in pairs

    async def test_get_scoped_by_account(self, db):
        """按账号集过滤：只读任务账号的组合。"""
        await db.record_permission_denied_ledger(5, "吧A", "r")
        await db.record_permission_denied_ledger(7, "吧B", "r")
        assert await db.get_permission_denied_ledger([5]) == [(5, "吧A")]
        assert (7, "吧B") in await db.get_permission_denied_ledger([5, 7])

    async def test_ttl_prunes_expired_entries(self, db):
        """过期条目读取时裁剪（权限限制类封禁可能随等级变化解除，不永久拉黑）。"""
        old = datetime.now() - timedelta(days=31)
        payload = json.dumps([_ledger_entry(5, "老吧", ts=old), _ledger_entry(5, "新吧")])
        await db.set_setting(PERMISSION_DENIED_LEDGER_KEY, payload)
        assert await db.get_permission_denied_ledger([5]) == [(5, "新吧")]

    async def test_fifo_cap_on_write(self, db):
        """写入超上限时 FIFO 裁剪到 _PERMISSION_LEDGER_LIMIT。"""
        flood = [_ledger_entry(i, f"吧{i}") for i in range(_PERMISSION_LEDGER_LIMIT + 5)]
        await db.set_setting(PERMISSION_DENIED_LEDGER_KEY, json.dumps(flood))
        await db.record_permission_denied_ledger(999, "最新吧", "r")
        pairs = await db.get_permission_denied_ledger()
        assert len(pairs) == _PERMISSION_LEDGER_LIMIT
        assert (999, "最新吧") in pairs
        assert (0, "吧0") not in pairs  # 最老的被挤出

    async def test_clear_by_fname_and_account(self, db):
        """解封联动：按吧名清理，可限定单账号。"""
        await db.record_permission_denied_ledger(5, "吧X", "r")
        await db.record_permission_denied_ledger(6, "吧X", "r")
        await db.record_permission_denied_ledger(6, "吧Y", "r")
        removed = await db.clear_permission_denied_ledger(["吧X"], account_id=5)
        assert removed == 1
        assert await db.get_permission_denied_ledger([5]) == []
        assert (6, "吧X") in await db.get_permission_denied_ledger([6])
        removed = await db.clear_permission_denied_ledger(["吧X"])
        assert removed == 1
        assert (6, "吧Y") in await db.get_permission_denied_ledger([6])

    async def test_corrupt_payload_fails_open(self, db):
        """账本损坏静默返回空（fail-open，不阻断引擎/预检）。"""
        await db.set_setting(PERMISSION_DENIED_LEDGER_KEY, "not-json{{{")
        assert await db.get_permission_denied_ledger([5]) == []


@pytest.mark.asyncio
class TestBuildBannedPairsMergesLedger:
    async def test_airdrop_ban_survives_restart(self, db):
        """核心缺口①：空降组合（无 Forum 行）封禁后，新任务构建 banned_pairs 时
        从账本恢复——等价于进程重启后的首次任务。"""
        await db.record_permission_denied_ledger(5, "空降吧", "发帖静默拦截(220012)")
        pm = BatchPostManager(db)
        pairs = await pm._build_banned_pairs([5])
        assert (5, "空降吧") in pairs

    async def test_db_pair_and_ledger_pair_union(self, db):
        """Forum 行封禁与账本兜底并集，且只含任务账号的组合。"""
        aid = await _make_account(db, "号五")
        await db.add_forum(1, "关注吧", aid)
        await db.mark_forum_banned(aid, "关注吧", reason="测试")
        await db.record_permission_denied_ledger(aid, "空降吧", "r")
        await db.record_permission_denied_ledger(777, "别的号吧", "r")

        pm = BatchPostManager(db)
        pairs = await pm._build_banned_pairs([aid])
        assert (aid, "关注吧") in pairs        # Forum 行口径
        assert (aid, "空降吧") in pairs        # 账本兜底口径
        assert (777, "别的号吧") not in pairs  # 非任务账号不混入

    async def test_unban_forum_clears_ledger(self, db):
        """解封联动：签到页解封后账本组合一并清除，否则跨重启兜底继续拦截。"""
        aid = await _make_account(db, "号五")
        await db.add_forum(1, "吧X", aid)
        await db.add_forum(2, "吧Y", aid)
        await db.mark_forum_banned(aid, "吧X", reason="测试")
        await db.record_permission_denied_ledger(aid, "吧X", "r")
        await db.record_permission_denied_ledger(aid, "吧Y", "r")

        assert await db.unban_forum(aid, "吧X") is True
        pairs = await db.get_permission_denied_ledger([aid])
        assert (aid, "吧X") not in pairs
        assert (aid, "吧Y") in pairs

    async def test_unban_globally_clears_ledger_all_accounts(self, db):
        aid5 = await _make_account(db, "号五")
        aid6 = await _make_account(db, "号六")
        await db.add_forum(1, "吧X", aid5)
        await db.add_forum(1, "吧X", aid6)
        await db.mark_forum_banned(aid5, "吧X", reason="测试")
        await db.mark_forum_banned(aid6, "吧X", reason="测试")
        await db.record_permission_denied_ledger(aid5, "吧X", "r")
        await db.record_permission_denied_ledger(aid6, "吧X", "r")
        await db.record_permission_denied_ledger(aid6, "吧Y", "r")

        assert await db.unban_forum_globally("吧X") == 2
        pairs = await db.get_permission_denied_ledger([aid5, aid6])
        assert (aid5, "吧X") not in pairs
        assert (aid6, "吧X") not in pairs
        assert (aid6, "吧Y") in pairs

    async def test_persist_fail_open(self):
        """账本落库失败只吞异常（fail-open），不向发帖链路抛错。"""
        broken = MagicMock()
        broken.record_permission_denied_ledger = AsyncMock(side_effect=RuntimeError("db down"))
        await persist_permission_denied(broken, 5, "吧A", "r")  # 不应抛出


@pytest.mark.asyncio
class TestPreflightBanScoping:
    """预检封禁口径：按任务账号集判定（缺口②）。MagicMock 库，与方法单测同风格。"""

    def _make_db(self, accounts, forums):
        db = MagicMock()
        db.get_accounts = AsyncMock(return_value=accounts)
        db.get_all_unique_forums = AsyncMock(return_value=forums)
        db.get_materials = AsyncMock(return_value=[])
        db.get_banned_forum_pairs = AsyncMock(return_value=[])
        db.get_permission_denied_ledger = AsyncMock(return_value=[])
        db.get_fnames_followed_by_accounts = AsyncMock(return_value=[])
        return db

    def _account(self, aid, status="active"):
        acc = MagicMock()
        acc.id, acc.status, acc.proxy_id = aid, status, 1
        acc.user_name, acc.name = f"号{aid}", f"号{aid}"
        return acc

    def _forum(self, fname, is_banned=False, is_post_target=False):
        return {"fname": fname, "is_banned": is_banned, "is_post_target": is_post_target}

    async def test_partial_ban_keeps_forum_with_info(self):
        """两号任务中一号被封：吧保留（引擎绕开封禁组合派另一号），出 info 提示。"""
        db = self._make_db(
            accounts=[self._account(1), self._account(2)],
            forums=[self._forum("吧A")],
        )
        db.get_banned_forum_pairs = AsyncMock(return_value=[(1, "吧A")])
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1, 2], local_fnames=["吧A"], post_count=1))
        assert report.effective_fnames == ["吧A"]
        assert any(i.code == "forums_banned_partial" for i in report.infos)
        assert not any(i.code == "forums_banned_removed" for i in report.issues)

    async def test_all_effective_banned_removes_forum(self):
        """选中账号全部被封：整吧剔除（与旧行为一致），并给出移除警告。"""
        db = self._make_db(
            accounts=[self._account(1), self._account(2)],
            forums=[self._forum("吧A")],
        )
        db.get_banned_forum_pairs = AsyncMock(return_value=[(1, "吧A"), (2, "吧A")])
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1, 2], local_fnames=["吧A"], post_count=1))
        assert report.effective_fnames == []
        assert any(i.code == "forums_banned_removed" for i in report.warnings)
        assert any(i.code == "no_effective_forums" for i in report.errors)

    async def test_outsider_ban_no_guilt_by_association(self):
        """非任务账号的封禁不连坐：即使查询结果混入任务外组合，覆盖度按任务账号集求交。"""
        db = self._make_db(
            accounts=[self._account(1)],
            forums=[self._forum("吧A")],
        )
        db.get_banned_forum_pairs = AsyncMock(return_value=[(2, "吧A")])
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1], local_fnames=["吧A"], post_count=1))
        assert report.effective_fnames == ["吧A"]
        assert not any(i.code == "forums_banned_removed" for i in report.issues)

    async def test_ledger_ban_counts_toward_removal(self):
        """账本组合（空降封禁）同样计入任务账号的封禁判定。"""
        db = self._make_db(
            accounts=[self._account(1)],
            forums=[self._forum("空降吧")],
        )
        db.get_permission_denied_ledger = AsyncMock(return_value=[(1, "空降吧")])
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1], local_fnames=["空降吧"], post_count=1))
        assert report.effective_fnames == []
        assert any(i.code == "forums_banned_removed" for i in report.warnings)

    async def test_no_effective_accounts_keeps_forums(self):
        """账号全终态（预检已有 error）：不再做封禁剔除，避免空账号集误删全部目标。"""
        db = self._make_db(
            accounts=[self._account(1, status="banned")],
            forums=[self._forum("吧A")],
        )
        db.get_banned_forum_pairs = AsyncMock(return_value=[(1, "吧A")])
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1], local_fnames=["吧A"], post_count=1))
        assert any(i.code == "no_effective_accounts" for i in report.errors)
        assert report.effective_fnames == ["吧A"]

    async def test_ban_query_failure_does_not_block(self):
        """封禁查询失败不阻断预检（fail-open，退化为无封禁剔除）。"""
        db = self._make_db(
            accounts=[self._account(1)],
            forums=[self._forum("吧A")],
        )
        db.get_banned_forum_pairs = AsyncMock(side_effect=RuntimeError("db down"))
        db.get_permission_denied_ledger = AsyncMock(side_effect=RuntimeError("db down"))
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1], local_fnames=["吧A"], post_count=1))
        assert report.effective_fnames == ["吧A"]


def test_ban_branches_persist_ledger():
    """源级钉扎：四类拦截分支都必须调用账本持久化（防回归删除）。"""
    import inspect

    from tieba_mecha.core import batch_post

    source = inspect.getsource(batch_post)
    assert source.count("persist_permission_denied(self.db, account_id, current_target_fname") == 4
    # 本吧封禁分支此前只落库不记进程内黑名单——同任务内会再撞同一组合
    assert 'record_permission_denied(account_id, current_target_fname, "发射检测吧封")' in source
