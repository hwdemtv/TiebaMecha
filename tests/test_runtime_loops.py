"""进程级后台循环的启动时序回归（2026-11-24 全项目体检整改）

NotificationManager 由首个浏览器会话的 _full_initialize 创建，而 web/runtime.py 的后台循环
随进程自启（systemd / 容器 lifespan），早于任何会话。此前两个循环在启动瞬间拿不到管理器：

- _notification_sync_loop 直接 return，而 ensure_started 的 _started 已置位、后续会话不会再
  拉起它——生产环境里远程通知同步从不自动运行，只能手动点刷新；
- _update_checker_loop 首次检查发现新版本却不推送，要等 24 小时后的下一轮。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tieba_mecha.core.notification as notification_mod  # noqa: E402
import tieba_mecha.core.updater as updater_mod  # noqa: E402
import tieba_mecha.web.runtime as runtime  # noqa: E402


@pytest.fixture
def fast_sleep():
    """把循环里的等待压缩成立即让出（真实让出事件循环，保证其它任务能被调度）"""
    real_sleep = asyncio.sleep

    async def _fast(seconds, *args, **kwargs):
        await real_sleep(0)

    with patch("tieba_mecha.web.runtime.asyncio.sleep", _fast):
        yield


def _manager(sync_result=0):
    return SimpleNamespace(
        set_license_config=MagicMock(),
        sync_remote_notifications=AsyncMock(return_value=sync_result),
        push=AsyncMock(),
    )


def _db():
    db = MagicMock()
    db.get_setting = AsyncMock(return_value="")
    return db


async def _finish(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_sync_loop_waits_for_manager_created_by_first_session(monkeypatch, fast_sleep):
    polls = {"n": 0}
    manager = _manager(sync_result=2)
    synced = asyncio.Event()

    async def _sync():
        synced.set()
        return 2

    manager.sync_remote_notifications = _sync

    def _get_manager():
        polls["n"] += 1
        return manager if polls["n"] > 3 else None     # 前 3 次还没有会话创建出管理器

    monkeypatch.setattr(notification_mod, "get_notification_manager", _get_manager)

    task = asyncio.create_task(runtime._notification_sync_loop(_db()))
    await asyncio.wait_for(synced.wait(), timeout=2)
    await _finish(task)

    assert polls["n"] == 4                               # 第 4 次拿到即开始同步
    manager.set_license_config.assert_called()


async def test_sync_loop_exits_with_error_if_manager_never_appears(monkeypatch, fast_sleep):
    monkeypatch.setattr(notification_mod, "get_notification_manager", lambda: None)
    log_error = AsyncMock()
    with patch("tieba_mecha.core.logger.log_error", log_error):
        await asyncio.wait_for(runtime._notification_sync_loop(_db()), timeout=2)

    assert log_error.await_count == 1
    assert "通知管理器" in str(log_error.await_args.args[0])


async def test_sync_loop_starts_immediately_when_manager_exists(monkeypatch, fast_sleep):
    manager = _manager()
    synced = asyncio.Event()

    async def _sync():
        synced.set()
        return 0

    manager.sync_remote_notifications = _sync
    monkeypatch.setattr(notification_mod, "get_notification_manager", lambda: manager)

    task = asyncio.create_task(runtime._notification_sync_loop(_db()))
    await asyncio.wait_for(synced.wait(), timeout=2)
    await _finish(task)

    manager.set_license_config.assert_called()          # 不经等待，立即同步


async def test_update_found_at_boot_is_pushed_once_manager_appears(monkeypatch, fast_sleep):
    polls = {"n": 0}
    manager = _manager()
    pushed = asyncio.Event()

    async def _push(**kwargs):
        pushed.set()

    manager.push = AsyncMock(side_effect=_push)

    def _get_manager():
        polls["n"] += 1
        return manager if polls["n"] > 2 else None

    monkeypatch.setattr(notification_mod, "get_notification_manager", _get_manager)
    release = SimpleNamespace(tag_name="v9.9.9", version="9.9.9", published_at=datetime.now(), html_url="https://example.invalid")
    updater = SimpleNamespace(should_check_update=AsyncMock(return_value=True), check_update=AsyncMock(return_value=release))
    monkeypatch.setattr(updater_mod, "get_update_manager", lambda: updater)

    task = asyncio.create_task(runtime._update_checker_loop(_db()))
    await asyncio.wait_for(pushed.wait(), timeout=2)
    await _finish(task)

    assert manager.push.await_count >= 1               # 修复前：这次「发现新版本」被丢掉，一次都不会推
    assert "v9.9.9" in manager.push.await_args.kwargs["title"]
