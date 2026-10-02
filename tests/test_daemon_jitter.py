"""发帖起点日抖动（去指纹）单元测试。

2026-10-02 P2 整改：循环任务每天固定 HH:MM:00 整触发是分钟级恒定的
行为指纹。抖动设计约束：
- seed=(task_id, 锚点) → 同日内重启不重掷、跨日自然漂移
- schedule_time 列只存用户锚点（展示稳定），生效派发时刻走 override 表
- 精确触发守卫与轮询兜底（_filter_due_tasks）按生效时刻判定到期
- once 任务与无锚点场景不抖
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from tieba_mecha.core import daemon


def _task(tid=2, schedule_type="daily", anchor=None):
    return SimpleNamespace(
        id=tid, schedule_type=schedule_type,
        schedule_time=anchor or datetime(2026, 10, 3, 8, 50))


def teardown_function():
    daemon._DISPATCH_OVERRIDES.clear()


def test_jitter_stable_within_cycle_and_across_restart():
    """同 (task, anchor) 偏移稳定：override 命中、清表重启后惰性推导一致"""
    t = _task()
    first = daemon.jittered_dispatch_time(t, t.schedule_time)
    assert daemon._effective_dispatch_time(t) == first
    daemon._DISPATCH_OVERRIDES.clear()  # 模拟 daemon 重启丢表
    assert daemon._effective_dispatch_time(t) == first


def test_jitter_range_and_daily_drift():
    """偏移在 ±10 分钟内，跨日确实漂移（30 天至少出现 2 种偏移）"""
    t = _task()
    seen = set()
    for d in range(30):
        anchor = datetime(2026, 10, 3, 8, 50) + timedelta(days=d)
        eff = daemon.jittered_dispatch_time(t, anchor)
        delta_minutes = (eff - anchor).total_seconds() / 60
        assert -10 <= delta_minutes <= 10
        seen.add(delta_minutes)
    assert len(seen) >= 2


def test_once_task_not_jittered():
    """once 任务不抖、不进 override 表（用户指定时刻即派发时刻）"""
    anchor = datetime(2026, 10, 3, 8, 50)
    t = _task(schedule_type="once", anchor=anchor)
    assert daemon.jittered_dispatch_time(t, anchor) == anchor
    assert t.id not in daemon._DISPATCH_OVERRIDES
    assert daemon._effective_dispatch_time(t) == anchor


def test_effective_time_none_anchor_passthrough():
    t = _task(schedule_type="once", anchor=None)
    t.schedule_time = None
    assert daemon._effective_dispatch_time(t) is None


def test_stale_override_from_reused_task_id_ignored():
    """同 id 陈旧 override 必须作废：任务删除后 SQLite rowid 复用同 id，
    旧任务的 override 若被采信会拦住新任务的派发（workflow 回归案例）"""
    t = _task(tid=1, anchor=datetime(2026, 10, 3, 8, 50))
    # 旧周期/旧任务残留：偏差远超 ±10 分钟抖动窗
    daemon._DISPATCH_OVERRIDES[1] = datetime(2026, 10, 4, 20, 50)
    eff = daemon._effective_dispatch_time(t)
    assert abs((eff - t.schedule_time).total_seconds()) <= 600  # 重算的合法抖动


def test_once_task_ignores_lingering_override():
    """once 任务永不查 override 表（同 id 循环任务的残留不推迟一次性派发）"""
    anchor = datetime(2026, 10, 3, 8, 50)
    daemon._DISPATCH_OVERRIDES[7] = anchor + timedelta(hours=5)
    t = _task(tid=7, schedule_type="once", anchor=anchor)
    assert daemon._effective_dispatch_time(t) == anchor


def test_filter_due_tasks_skips_jittered_future():
    """轮询兜底：锚点已到但生效时刻被抖动后移的任务不参与本轮派发

    override 与锚点偏差须落在 ±10 分钟抖动窗内才被采信（一致性校验）。"""
    now = datetime(2026, 10, 3, 8, 50)
    future_due = _task(tid=1, anchor=now - timedelta(minutes=2))   # 锚点已到
    daemon.jittered_dispatch_time(future_due, future_due.schedule_time)
    # 窗内合法 override，后移到未来
    daemon._DISPATCH_OVERRIDES[future_due.id] = now + timedelta(minutes=8)

    past_once = _task(tid=2, schedule_type="once", anchor=now - timedelta(minutes=30))
    no_anchor = _task(tid=3, schedule_type="daily")
    no_anchor.schedule_time = None

    due = daemon._filter_due_tasks([future_due, past_once, no_anchor], now)
    assert past_once in due and no_anchor in due
    assert future_due not in due
