"""授权本地缓存判定逻辑测试（check_local_status 宽限期机制）。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tieba_mecha.core.auth import AuthStatus, LicenseManager


def make_lm(db) -> LicenseManager:
    lm = LicenseManager.__new__(LicenseManager)
    lm.db = db
    lm.status = AuthStatus.FREE
    lm.license_info = {}
    lm._hwid = None
    lm._initialized = True
    return lm


def make_db(settings: dict):
    async def get_setting(key, default=""):
        return settings.get(key, default)

    async def set_setting(key, value):
        settings[key] = value

    return type("FakeDB", (), {"get_setting": AsyncMock(side_effect=get_setting),
                               "set_setting": AsyncMock(side_effect=set_setting)})()


@pytest.mark.asyncio
async def test_no_key_is_free():
    lm = make_lm(make_db({}))
    assert await lm.check_local_status() is AuthStatus.FREE


@pytest.mark.asyncio
async def test_key_without_cache_is_free():
    """有 key 但从未在线验证通过 → 不再直通 PRO。"""
    lm = make_lm(make_db({"license_key": "SOME-KEY"}))
    assert await lm.check_local_status() is AuthStatus.FREE


@pytest.mark.asyncio
async def test_fresh_cache_grants_pro():
    lm = make_lm(make_db({
        "license_key": "SOME-KEY",
        "license_cached_status": "pro",
        "license_last_verified_at": datetime.now().isoformat(),
    }))
    assert await lm.check_local_status() is AuthStatus.PRO


@pytest.mark.asyncio
async def test_stale_cache_falls_back_to_free():
    lm = make_lm(make_db({
        "license_key": "SOME-KEY",
        "license_cached_status": "pro",
        "license_last_verified_at": (datetime.now() - timedelta(hours=100)).isoformat(),
    }))
    assert await lm.check_local_status() is AuthStatus.FREE


@pytest.mark.asyncio
async def test_corrupt_timestamp_is_free():
    lm = make_lm(make_db({
        "license_key": "SOME-KEY",
        "license_cached_status": "pro",
        "license_last_verified_at": "not-a-date",
    }))
    assert await lm.check_local_status() is AuthStatus.FREE


@pytest.mark.asyncio
async def test_cache_write_and_revoke():
    db = make_db({"license_key": "SOME-KEY"})
    lm = make_lm(db)

    await lm._cache_auth_result(ok=True)
    assert db.get_setting.side_effect is not None
    settings = db.set_setting.call_args_list
    written = {c.args[0]: c.args[1] for c in settings}
    assert written["license_cached_status"] == "pro"
    assert "license_last_verified_at" in written
    assert await lm.check_local_status() is AuthStatus.PRO

    await lm._cache_auth_result(ok=False)
    assert await lm.check_local_status() is AuthStatus.FREE
