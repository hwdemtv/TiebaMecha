"""进程级自动化运行时（web/runtime.py）的语义测试。

验证要点：
- ensure_started 幂等（二次调用不叠加任务）
- 启动的任务被强引用持有（不随"会话"消失——用显式取消模拟会话外的
  其它取消源缺失场景；真正的会话隔离由 create_task 语义保证）
- set_ui_hook 的钩子异常被吞掉（会话断开时 UI 刷新失败不影响自动化）
"""

import asyncio

import pytest

from tieba_mecha.web import runtime
from tieba_mecha.web.runtime import AutomationManager


class FakeDB:
    async def get_setting(self, key, default=""):
        return default

    async def get_accounts(self):
        return []


@pytest.fixture
def fresh_manager(monkeypatch):
    """每个用例重置运行时状态并 stub 掉 daemon 启动（避免真实调度器/网络）。"""
    monkeypatch.setattr(runtime, "_started", False)
    monkeypatch.setattr(runtime, "_tasks", [])
    monkeypatch.setattr(runtime, "_ui_hook", None)

    started_flags = []

    class FakeDaemon:
        async def start(self):
            started_flags.append(True)
            await asyncio.sleep(3600)

    import tieba_mecha.core.daemon as daemon_mod
    monkeypatch.setattr(daemon_mod, "daemon_instance", FakeDaemon())

    # 四个循环替换为可观测的挂起协程
    async def _hang(name):
        await asyncio.sleep(3600)

    monkeypatch.setattr(runtime, "_account_heartbeat_loop", lambda db: _hang("hb"))
    monkeypatch.setattr(runtime, "_proxy_monitor_loop", lambda db: _hang("pm"))
    monkeypatch.setattr(runtime, "_notification_sync_loop", lambda db: _hang("ns"))
    monkeypatch.setattr(runtime, "_update_checker_loop", lambda db: _hang("uc"))
    return started_flags


@pytest.mark.asyncio
async def test_ensure_started_runs_once(fresh_manager):
    db = FakeDB()
    assert await AutomationManager.ensure_started(db) is True
    await asyncio.sleep(0)  # 让事件循环调度已创建的任务
    await asyncio.sleep(0)
    assert len(fresh_manager) == 1          # daemon 启动一次
    assert len(runtime._tasks) == 5         # daemon + 4 循环
    assert AutomationManager.is_running() is True

    # 幂等：二次调用不重复启动
    assert await AutomationManager.ensure_started(db) is False
    assert len(fresh_manager) == 1
    assert len(runtime._tasks) == 5


@pytest.mark.asyncio
async def test_tasks_survive_unrelated_cancellation(fresh_manager):
    """模拟"启动自动化的那个协程"被取消（旧架构中等于会话断开）：
    自动化任务本身不应受影响。"""
    db = FakeDB()

    async def fake_session_that_started_automation():
        await AutomationManager.ensure_started(db)
        # 模拟会话断开：启动者自身被取消
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await fake_session_that_started_automation()

    await asyncio.sleep(0)  # 让事件循环处理一次任务调度
    # 全部自动化任务仍在运行
    assert all(not t.done() for t in runtime._tasks)
    assert AutomationManager.is_running() is True

    # 清理
    for t in runtime._tasks:
        t.cancel()


@pytest.mark.asyncio
async def test_ui_hook_exception_is_swallowed(fresh_manager):
    """UI 钩子（如通知铃刷新）抛异常时不得影响自动化流程。"""
    async def bad_hook():
        raise RuntimeError("session dead")

    AutomationManager.set_ui_hook(bad_hook)
    await runtime._notify_ui_refresh()  # 不应抛出
    AutomationManager.set_ui_hook(None)
    await runtime._notify_ui_refresh()  # 无钩子同样安全
