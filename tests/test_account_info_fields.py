"""AccountInfo 字段完整性与凭据解密失败可见性（2026-11-24 全项目体检整改）

- AccountInfo 此前没有 is_sign_scheduled 字段，账号页用 getattr(acc, 'is_sign_scheduled', True)
  读取——默认值永远生效，已退出定时签到的账号在界面上仍显示「定时：开启」，用户无法从界面
  判断守护到底会不会签这个号（守护实际按数据库排程）。
- get_account_credentials 解密失败时静默返回 None，与「账号不存在」无法区分：加密密钥变更后
  所有账号都显示「未找到凭证」却查不出原因。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tieba_mecha.core.account as account_mod  # noqa: E402
from tieba_mecha.core.account import (  # noqa: E402
    AccountInfo,
    add_account,
    get_account_credentials,
    list_accounts,
    refresh_account,
)


async def _two_accounts(db):
    on = await add_account(db=db, name="sched_on", bduss="a" * 192, stoken="b" * 64, verify=False)
    off = await add_account(db=db, name="sched_off", bduss="c" * 192, stoken="d" * 64, verify=False)
    await db.update_account(off.id, is_sign_scheduled=False)
    return on, off


def test_account_info_declares_the_field():
    assert "is_sign_scheduled" in AccountInfo.__dataclass_fields__
    assert AccountInfo.__dataclass_fields__["is_sign_scheduled"].default is True   # 新账号默认参与


async def test_list_accounts_reports_real_sign_scheduled_flag(db):
    await _two_accounts(db)

    flags = {info.name: info.is_sign_scheduled for info in await list_accounts(db)}

    assert flags == {"sched_on": True, "sched_off": False}


async def test_refresh_account_keeps_sign_scheduled_flag(db):
    _, off = await _two_accounts(db)

    info = await refresh_account(db, off.id)

    assert info is not None and info.is_sign_scheduled is False


async def test_added_account_defaults_to_scheduled(db):
    info = await add_account(db=db, name="fresh", bduss="a" * 192, stoken="b" * 64, verify=False)
    assert info.is_sign_scheduled is True


async def test_decrypt_failure_is_logged_without_leaking_credentials(db, monkeypatch):
    acc = await add_account(db=db, name="broken", bduss="a" * 192, stoken="b" * 64, verify=False)

    def _boom(_value):
        raise RuntimeError("InvalidToken")

    monkeypatch.setattr(account_mod, "decrypt_value", _boom)
    log_warn = AsyncMock()
    with patch("tieba_mecha.core.logger.log_warn", log_warn):
        creds = await get_account_credentials(db, acc.id)

    assert creds is None                                   # 既有行为：按无凭据处理
    message = str(log_warn.await_args.args[0])
    assert f"账号 [{acc.id}]" in message and "RuntimeError" in message
    assert "a" * 20 not in message                         # 日志里不得出现凭据内容
