"""守护签到缺席对账（2026-11-03 月报遗留项①）单元测试。

10-21（6号+1341）、10-30（9号+187）两次单日整号缺席实测既无日志也无告警。
对账口径：参与定时签到的账号当日既无任何 is_sign_today 标记、也无 SignLog
记录（成功/失败/拟人跳过均算活动）→ log_warn 告警。
排程：错峰窗结束 + 90min 宽限（覆盖遇锁重试 50min + 跑批时长），每进程每天一次。
"""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tieba_mecha.core import daemon


def _acc(aid=1, name="hwdemtv1", status="active"):
    return SimpleNamespace(id=aid, name=name, status=status)


def _forum(fid=11, signed=False):
    return SimpleNamespace(id=fid, fname=f"f{fid}", is_sign_today=signed)


@pytest.fixture(autouse=True)
def _clear_attendance_state():
    daemon._ATTENDANCE_CHECK_SPAWNED.clear()
    yield
    daemon._ATTENDANCE_CHECK_SPAWNED.clear()


@pytest.mark.asyncio
async def test_attendance_warns_when_account_absent():
    """既无已签标记也无当日日志 → 告警点名账号"""
    absent_acc = _acc()
    db = SimpleNamespace(
        get_scheduled_sign_accounts=AsyncMock(return_value=[absent_acc]),
        get_forums=AsyncMock(return_value=[_forum(signed=False)]),
        has_sign_activity_today=AsyncMock(return_value=False),
    )
    warns = []
    with patch("tieba_mecha.core.daemon.get_db", AsyncMock(return_value=db)), \
         patch("tieba_mecha.core.daemon.log_warn", AsyncMock(side_effect=warns.append)):
        await daemon._sign_attendance_check()

    assert len(warns) == 1
    assert "hwdemtv1" in warns[0]
    assert "缺席" in warns[0]


@pytest.mark.asyncio
async def test_attendance_passes_when_forum_marked_signed():
    """任一贴吧 is_sign_today=True（如手动全签/已签毕）→ 不告警"""
    db = SimpleNamespace(
        get_scheduled_sign_accounts=AsyncMock(return_value=[_acc()]),
        get_forums=AsyncMock(return_value=[_forum(fid=1, signed=False), _forum(fid=2, signed=True)]),
        has_sign_activity_today=AsyncMock(return_value=False),
    )
    warns = []
    with patch("tieba_mecha.core.daemon.get_db", AsyncMock(return_value=db)), \
         patch("tieba_mecha.core.daemon.log_warn", AsyncMock(side_effect=warns.append)):
        await daemon._sign_attendance_check()
    assert warns == []


@pytest.mark.asyncio
async def test_attendance_passes_when_signlog_activity_today():
    """无已签标记但当日有 SignLog（拟人跳过/失败也记账，worker 跑过）→ 不告警"""
    db = SimpleNamespace(
        get_scheduled_sign_accounts=AsyncMock(return_value=[_acc()]),
        get_forums=AsyncMock(return_value=[_forum(signed=False)]),
        has_sign_activity_today=AsyncMock(return_value=True),
    )
    warns = []
    with patch("tieba_mecha.core.daemon.get_db", AsyncMock(return_value=db)), \
         patch("tieba_mecha.core.daemon.log_warn", AsyncMock(side_effect=warns.append)):
        await daemon._sign_attendance_check()
    assert warns == []


@pytest.mark.asyncio
async def test_attendance_skips_terminal_status_and_empty_forums():
    """终态账号与无关注吧账号不参与对账（get_forums 不该被调用）"""
    db = SimpleNamespace(
        get_scheduled_sign_accounts=AsyncMock(
            return_value=[_acc(aid=1, status="banned"), _acc(aid=2, status="suspended_proxy"), _acc(aid=3)]
        ),
        get_forums=AsyncMock(return_value=[]),
        has_sign_activity_today=AsyncMock(return_value=False),
    )
    warns = []
    with patch("tieba_mecha.core.daemon.get_db", AsyncMock(return_value=db)), \
         patch("tieba_mecha.core.daemon.log_warn", AsyncMock(side_effect=warns.append)):
        await daemon._sign_attendance_check()
    assert warns == []
    # 终态账号不查 forums，id=3 查了但为空列表跳过
    assert db.get_forums.await_count == 1


@pytest.mark.asyncio
async def test_attendance_mixed_accounts_names_only_absent():
    """多账号混合：只有缺席者进告警名单"""
    db = SimpleNamespace(
        get_scheduled_sign_accounts=AsyncMock(
            return_value=[_acc(aid=1, name="号1"), _acc(aid=2, name="号2")]
        ),
        get_forums=AsyncMock(
            side_effect=lambda aid, include_banned=False: [_forum(signed=True)] if aid == 1 else [_forum(signed=False)]
        ),
        has_sign_activity_today=AsyncMock(return_value=False),
    )
    warns = []
    with patch("tieba_mecha.core.daemon.get_db", AsyncMock(return_value=db)), \
         patch("tieba_mecha.core.daemon.log_warn", AsyncMock(side_effect=warns.append)):
        await daemon._sign_attendance_check()
    assert len(warns) == 1
    assert "号2" in warns[0] and "号1" not in warns[0]


@pytest.mark.asyncio
async def test_spawn_attendance_check_target_after_window_and_idempotent():
    """排程目标 = sign_time + 错峰窗 + 90min 宽限；已过则退到 now+90min；同日仅排一次"""
    captured = {}
    spawn_count = {"n": 0}

    def fake_create_task(coro):
        spawn_count["n"] += 1
        captured["coro"] = coro  # 暂不运行，稍后手动 await 验证目标时刻
        return None

    db = SimpleNamespace(get_setting=AsyncMock(return_value=json.dumps({"sign_time": "06:45"})))
    with patch("tieba_mecha.core.daemon.get_db", AsyncMock(return_value=db)), \
         patch("tieba_mecha.core.daemon.asyncio.create_task", side_effect=fake_create_task):
        await daemon._spawn_attendance_check(120.0)
        await daemon._spawn_attendance_check(120.0)  # 同日第二次 = no-op

    assert spawn_count["n"] == 1
    assert datetime.now().date() in daemon._ATTENDANCE_CHECK_SPAWNED

    # 运行被捕获的延迟协程，从实际睡眠时长反推目标时刻
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    check_mock = AsyncMock()
    with patch("tieba_mecha.core.daemon.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)), \
         patch("tieba_mecha.core.daemon._sign_attendance_check", check_mock):
        await captured["coro"]

    delay = sleeps[0]
    now = datetime.now()
    expected = now.replace(hour=6, minute=45, second=0, microsecond=0) + timedelta(minutes=120 + 90)
    if expected <= now:
        assert abs(delay - 90 * 60) < 10  # 已过 → 退到 now+90min
    else:
        assert abs(delay - (expected - now).total_seconds()) < 10
    check_mock.assert_awaited_once()
