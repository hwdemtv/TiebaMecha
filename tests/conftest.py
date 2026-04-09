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
        db_path.unlink()


@pytest_asyncio.fixture
async def db(temp_db_path: Path) -> AsyncGenerator:
    """Create a test database instance."""
    from tieba_mecha.db.crud import Database

    database = Database(temp_db_path)
    await database.init_db()
    yield database
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
