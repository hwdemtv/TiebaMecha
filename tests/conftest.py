"""Pytest configuration and fixtures for TiebaMecha tests."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

# Add project source to path before any imports
_project_root = Path(__file__).parent.parent
_src_path = _project_root / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

import pytest
import pytest_asyncio

# Set test environment variables before importing any app modules
os.environ["TIEBA_MECHA_SALT"] = "a" * 64  # 64 hex chars = 32 bytes
os.environ["TIEBA_MECHA_SECRET_KEY"] = "b" * 64


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    """Create a temporary database file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    # Cleanup
    if db_path.exists():
        try:
            db_path.unlink()
        except PermissionError:
            # Windows 上新写的 .db 可能被杀软/索引器短暂锁定，也可能是进程内泄漏的
            # aiosqlite 连接线程：先短暂重试（前者可解），仍失败则取证再抛。
            # 被取消的后台任务会留下不经连接池记账的孤儿连接（SQLAlchemy 2.0.48：查询结果
            # 已回投、任务尚未恢复时被取消，session.close 不归还该连接），它只随 GC 关闭——
            # 主动回收一轮让孤儿连接的线程开始关闭，再进重试循环（2026-11-24 体检整改：
            # 此前守护类用例每次收尾都在这里确定性报 PermissionError）
            import gc
            import time
            gc.collect()
            for _ in range(5):
                time.sleep(0.2)
                try:
                    db_path.unlink()
                    break
                except PermissionError:
                    continue
            else:
                import threading

                # psutil 是可选依赖（未在 pyproject 声明）：缺失时不能盖掉原始的 PermissionError
                hits = []
                try:
                    import psutil

                    hits = [f.path for f in psutil.Process().open_files() if Path(f.path) == db_path]
                except ImportError:
                    pass
                threads = [t.name for t in threading.enumerate() if "connection_worker" in t.name.lower()]
                print(f"\n[CONFTEST-DBG] unlink仍失败 hits={hits} aiosqlite_threads={threads}")
                raise


@pytest.fixture(autouse=True)
def _offline_account_verification(monkeypatch):
    """常规门禁禁止真实账号验证联网（autouse）。

    core.account.add_account / refresh_account 默认走 verify_account，会用传入的 BDUSS
    真实请求百度 get_self_info——全量套件此前每次跑批向百度发出 64 次请求（全是假
    BDUSS，均被拒）。这里统一桩成确定性的离线失败：返回值与「百度拒绝无效凭据」时
    完全一致，账号照样落 invalid: 状态，离线与有网结果相同。
    需要真实验证逻辑的用例可向本夹具要回原函数后自行 monkeypatch。
    """
    from tieba_mecha.core import account as account_module

    real_verify = account_module.verify_account

    async def _offline_verify(bduss, stoken="", **kwargs):
        return False, 0, "", "无法获取用户信息，请检查凭证是否有效"

    monkeypatch.setattr(account_module, "verify_account", _offline_verify)
    return real_verify


@pytest_asyncio.fixture
async def db(temp_db_path: Path) -> AsyncGenerator:
    """Create a test database instance."""
    from tieba_mecha.db.crud import Database

    database = Database(temp_db_path)
    await database.init_db()
    # 拟人化跳过率默认 0.08 会让签到流测试概率性翻车（骰子命中时行为改变），
    # 测试环境固定为 0；跳过行为本身由 TestHumanizedSkip 显式设置概率覆盖
    await database.set_setting("sign_skip_probability", "0")
    # 守护错峰默认 90 分钟会让守护路径测试走排程分支（甚至 spawn 实时 worker），
    # 测试环境固定为 0（串行旧路径）；错峰行为由 daemon 套路的错峰用例显式设置窗口覆盖
    await database.set_setting("sign_stagger_minutes", "0")
    yield database
    # 先取消本测试 loop 内残留的后台任务（错峰 worker / 批量派发等 daemon
    # spawn 的 task 会在 teardown 的 await 窗口被调度、摸库检出连接），再关库：
    # 保证 engine dispose 时没有会话仍在途中，Windows 上临时 db 文件不被占用
    try:
        loop = asyncio.get_running_loop()
        strays = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        for t in strays:
            t.cancel()
        if strays:
            await asyncio.gather(*strays, return_exceptions=True)
    except RuntimeError:
        pass  # 无运行中 loop（纯同步收尾）时无事可做
    await database.close()


@pytest.fixture
def mock_aiotieba_client() -> MagicMock:
    """Create a mock aiotieba client."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_self_info = AsyncMock()
    client.sign_forum = AsyncMock()
    client.get_follow_forums = AsyncMock(return_value=[])
    client.add_thread = AsyncMock()
    return client


@pytest.fixture
def sample_account_data() -> dict:
    """Sample account data for testing."""
    # BDUSS 必须是 192 字符
    return {
        "name": "test_account",
        "bduss": "a" * 192,  # 192 个字符
        "stoken": "b" * 64,  # STOKEN 通常 64 字符
        "user_id": 12345678,
        "user_name": "test_user",
    }


@pytest.fixture
def sample_forum_data() -> dict:
    """Sample forum data for testing."""
    return {
        "fid": 12345,
        "fname": "test_forum",
        "sign_count": 10,
    }


@pytest.fixture
def sample_proxy_data() -> dict:
    """Sample proxy data for testing."""
    return {
        "host": "127.0.0.1",
        "port": 7890,
        "username": "test_user",
        "password": "test_pass",
        "protocol": "http",
    }
