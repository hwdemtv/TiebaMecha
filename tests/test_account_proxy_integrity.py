"""账号 / 代理数据完整性回归（2026-11-24 全项目体检整改）

- 代理预热保护从未生效：预热判断读 Account.proxy_bind_time，但模型里根本没有这一列，
  全项目没有任何地方写入它，needs_warmup 恒为 False（预检的「预热期账号」列表恒为空）。
- 删账号有残留且 ID 会被复用：项目没有外键，accounts 主键不是 AUTOINCREMENT——删掉最大
  ID 的账号后再新增会拿到同一个 ID（封号后重加很常见），残留的熔断状态会被新账号继承
  （「勿反复手动解 1 号熔断」这类事故的温床），旧循环任务的账号池也会把新号拉进去。
- 删代理不解绑账号：accounts.proxy_id 悬空；同样因为 ID 复用，批量删再批量加时原账号
  会被静默绑到新代理上（出口 IP 突变）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from tieba_mecha.core.proxy import get_warmup_manager
from tieba_mecha.db.crud import Database


async def _set_bind_time(db, account_id, when):
    async with db.async_session() as session:
        from tieba_mecha.db.models import Account

        acc = await session.get(Account, account_id)
        acc.proxy_bind_time = when
        await session.commit()


# ---------- 代理预热 ----------

async def test_binding_a_proxy_starts_warmup(db):
    acc = await db.add_account(name="warm", bduss="X" * 80)
    warmup = get_warmup_manager()
    assert (await db.get_account_by_id(acc.id)).proxy_bind_time is None
    assert not await warmup.needs_warmup(db, acc.id)          # 未绑代理：无所谓预热

    proxy = await db.add_proxy("127.0.0.1", 18888, protocol="http")
    before = datetime.now()
    await db.update_account(acc.id, proxy_id=proxy.id)

    assert await warmup.needs_warmup(db, acc.id)              # 刚换绑：进入 48h 预热期
    assert 47 < await warmup.get_remaining_hours(db, acc.id) <= 48
    assert before <= (await db.get_account_by_id(acc.id)).proxy_bind_time <= datetime.now()


async def test_resaving_same_proxy_does_not_restart_warmup(db):
    """编辑资料时原样保存代理，不应把已经熬过的预热期清零"""
    proxy = await db.add_proxy("127.0.0.1", 18888, protocol="http")
    acc = await db.add_account(name="warm2", bduss="X" * 80, proxy_id=proxy.id)
    three_days_ago = datetime.now() - timedelta(days=3)
    await _set_bind_time(db, acc.id, three_days_ago)

    await db.update_account(acc.id, proxy_id=proxy.id, name="改个备注")
    await db.update_account(acc.id, post_weight=7)             # 不带 proxy_id 的更新更不该动它

    assert not await get_warmup_manager().needs_warmup(db, acc.id)
    assert (await db.get_account_by_id(acc.id)).proxy_bind_time == three_days_ago


async def test_rebinding_to_another_proxy_restarts_warmup(db):
    p1 = await db.add_proxy("127.0.0.1", 18888, protocol="http")
    p2 = await db.add_proxy("127.0.0.1", 18889, protocol="http")
    acc = await db.add_account(name="warm3", bduss="X" * 80, proxy_id=p1.id)
    await _set_bind_time(db, acc.id, datetime.now() - timedelta(days=3))
    assert not await get_warmup_manager().needs_warmup(db, acc.id)

    await db.update_account(acc.id, proxy_id=p2.id)           # 换到另一个出口 IP

    assert await get_warmup_manager().needs_warmup(db, acc.id)


async def test_unbinding_clears_warmup(db):
    proxy = await db.add_proxy("127.0.0.1", 18888, protocol="http")
    acc = await db.add_account(name="warm4", bduss="X" * 80, proxy_id=proxy.id)
    assert await get_warmup_manager().needs_warmup(db, acc.id)

    await db.update_account(acc.id, proxy_id=None)

    row = await db.get_account_by_id(acc.id)
    assert row.proxy_id is None and row.proxy_bind_time is None
    assert not await get_warmup_manager().needs_warmup(db, acc.id)


async def test_proxy_bind_time_is_not_directly_writable(db):
    """绑定时刻只由换绑动作驱动：不允许通过 update_account 的字段白名单随手改"""
    acc = await db.add_account(name="warm5", bduss="X" * 80)
    await db.update_account(acc.id, proxy_bind_time=datetime.now() - timedelta(days=9))
    assert (await db.get_account_by_id(acc.id)).proxy_bind_time is None


async def test_posting_flow_skips_accounts_in_warmup_period(db):
    """预热期内的账号不进入发射：物料顺延、保持待发；过了预热期照常发出"""
    from tests.test_post_launch_classification import FID, FNAME, _client, _engine, _task

    from tieba_mecha.core.account import add_account
    from tieba_mecha.core.batch_post import BatchPostManager

    proxy = await db.add_proxy("127.0.0.1", 18888, protocol="http")
    acc = await add_account(db=db, name="warm6", bduss="b" * 192, stoken="s" * 64,
                            proxy_id=proxy.id, verify=False)
    await db.add_forum(fid=FID, fname=FNAME, account_id=acc.id)
    await db.add_materials_bulk([("预热期标题", "预热期内容")])
    task = _task([acc.id], 1)

    with _engine(_client(), {"err_code": 0, "data": {"tid": 1}}) as env:
        updates = [u async for u in BatchPostManager(db).execute_task(task)]

    env.commit.assert_not_awaited()                           # 预热期内：一条发帖请求都没发
    assert "预热期" in " ".join(u["msg"] for u in updates)
    assert (await db.get_materials())[0].status == "pending"  # 物料不被记失败

    await _set_bind_time(db, acc.id, datetime.now() - timedelta(days=3))
    with _engine(_client(), {"err_code": 0, "data": {"tid": 2}}) as env:
        updates = [u async for u in BatchPostManager(db).execute_task(task)]

    env.commit.assert_awaited_once()
    assert updates[0]["status"] == "success"


async def test_warmup_expires_after_48_hours(db):
    proxy = await db.add_proxy("127.0.0.1", 18888, protocol="http")
    acc = await db.add_account(name="warm7", bduss="X" * 80, proxy_id=proxy.id)
    await _set_bind_time(db, acc.id, datetime.now() - timedelta(hours=49))
    warmup = get_warmup_manager()
    assert not await warmup.needs_warmup(db, acc.id)
    assert await warmup.get_remaining_hours(db, acc.id) == 0.0


async def test_migration_adds_proxy_bind_time_to_legacy_database(temp_db_path):
    """上一版本创建的库没有这一列：init_db 必须补上、保留数据、且重复执行无副作用"""
    # 先按当前结构建库并写入一个账号，再把新列删掉 = 还原成旧版本留下的库
    legacy = Database(temp_db_path)
    await legacy.init_db()
    await legacy.add_account(name="legacy", bduss="X" * 80)
    await legacy.close()
    conn = sqlite3.connect(temp_db_path)
    conn.execute("ALTER TABLE accounts DROP COLUMN proxy_bind_time")
    conn.commit()
    conn.close()

    database = Database(temp_db_path)
    try:
        await database.init_db()
        await database.init_db()                               # 幂等
        conn = sqlite3.connect(temp_db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
        conn.close()
        assert "proxy_bind_time" in columns
        row = await database.get_account_by_id(1)
        assert row.name == "legacy" and row.proxy_bind_time is None   # 数据保留；存量账号不追溯进预热期
    finally:
        await database.close()


# ---------- 删账号：清残留，ID 复用不继承 ----------

async def _breaker_rows(db, account_id):
    async with db.async_session() as session:
        from sqlalchemy import select

        from tieba_mecha.db.models import BreakerState

        rows = (await session.execute(select(BreakerState).where(BreakerState.account_id == account_id))).scalars().all()
        return [(r.scope, r.fail_streak) for r in rows]


async def _seed_breaker(db, account_id):
    async with db.async_session() as session:
        from tieba_mecha.db.models import BreakerState

        session.add(BreakerState(account_id=account_id, scope="post", fail_streak=5,
                                 last_failure_at=datetime.now(),
                                 breaker_until=datetime.now() + timedelta(hours=6), trigger_times_json="[]"))
        await session.commit()


async def test_deleting_account_clears_breaker_state_and_task_pool(db):
    acc = await db.add_account(name="doomed", bduss="X" * 80)
    other = await db.add_account(name="keeper", bduss="Y" * 80)
    await _seed_breaker(db, acc.id)
    await _seed_breaker(db, other.id)
    task = await db.add_batch_task(
        fname="f", titles_json="[]", contents_json="[]", strategy="round_robin", total=1,
        accounts_json=json.dumps([acc.id, other.id]),
    )

    assert await db.delete_account(acc.id) is True

    assert await _breaker_rows(db, acc.id) == []
    assert await _breaker_rows(db, other.id) == [("post", 5)]   # 不误伤别的账号
    assert json.loads((await db.get_batch_task(task.id)).accounts_json) == [other.id]


async def test_reused_account_id_does_not_inherit_cooling_breaker(db):
    """删掉最大 ID 的账号再加一个，SQLite 会复用同一个 ID：新账号不得一出生就在熔断中"""
    from tieba_mecha.core.batch_post import FailureCircuitBreaker

    acc = await db.add_account(name="banned_old", bduss="X" * 80)
    await _seed_breaker(db, acc.id)
    breaker = FailureCircuitBreaker(db=db, scope="post")
    await breaker.load()
    assert breaker.is_in_cooldown(acc.id)                      # 前提：旧账号确实在熔断

    await db.delete_account(acc.id)
    fresh = await db.add_account(name="fresh_new", bduss="Z" * 80)
    assert fresh.id == acc.id                                  # 前提：ID 被复用

    breaker = FailureCircuitBreaker(db=db, scope="post")
    await breaker.load()
    assert not breaker.is_in_cooldown(fresh.id)


# ---------- 删代理：解绑账号 ----------

async def test_deleting_proxy_unbinds_accounts(db):
    proxy = await db.add_proxy("127.0.0.1", 18888, protocol="http")
    acc = await db.add_account(name="bound", bduss="X" * 80, proxy_id=proxy.id)
    unbound = await db.add_account(name="unbound", bduss="Y" * 80)

    assert await db.delete_proxy(proxy.id) is True

    assert await db.get_proxy(proxy.id) is None
    row = await db.get_account_by_id(acc.id)
    assert row.proxy_id is None and row.proxy_bind_time is None   # 回退直连，也不再处于预热期
    assert (await db.get_account_by_id(unbound.id)).proxy_id is None


async def test_reused_proxy_id_does_not_capture_old_accounts(db):
    """删掉最大 ID 的代理再加一个，SQLite 复用同一个 ID：原账号不得被静默绑到新代理上"""
    p1 = await db.add_proxy("1.1.1.1", 1080, protocol="socks5")
    acc = await db.add_account(name="p_bound", bduss="X" * 80, proxy_id=p1.id)
    await db.delete_proxy(p1.id)

    p2 = await db.add_proxy("2.2.2.2", 1080, protocol="socks5")
    assert p2.id == p1.id                                       # 前提：ID 被复用
    assert (await db.get_account_by_id(acc.id)).proxy_id is None


async def test_deleting_missing_proxy_or_account_is_a_no_op(db):
    assert await db.delete_proxy(424242) is False
    assert await db.delete_account(424242) is False
