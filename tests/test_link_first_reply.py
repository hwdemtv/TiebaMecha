"""带链首评（link first reply）回归测试。

架构（2026-09-22 内容池整改）：主帖净文化不带链接，链接存 link_url 字段，
发帖成功后由矩阵号（排除发帖原号）在楼中楼发出带链首评；
失败累计 link_reply_fail_count，达上限放弃。
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from tieba_mecha.core.batch_post import AutoBumpManager
from tieba_mecha.db.models import MaterialPool


async def _add_account(db, name: str, status: str = "active") -> int:
    acc = await db.add_account(name=name, bduss="b" * 192, stoken="s" * 64)
    await db.update_account(acc.id, status=status)
    return acc.id


async def _add_material(db, **overrides) -> int:
    fields = dict(
        title="测试标题",
        content="测试正文，两百字级别的真实观影感受。",
        status="success",
        posted_fname="电影",
        posted_tid=123456,
        posted_account_id=1,
        posted_time=datetime.now() - timedelta(hours=2),
        link_url="https://pan.baidu.com/s/testlink",
    )
    fields.update(overrides)
    async with db.async_session() as session:
        m = MaterialPool(**fields)
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return m.id


async def _get_material(db, mid: int) -> MaterialPool:
    async with db.async_session() as session:
        return await session.get(MaterialPool, mid)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """屏蔽首评间隔的真实 asyncio.sleep（20-60s 拟人延迟），保证测试秒级完成"""
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture
def manager(db):
    mgr = AutoBumpManager(db)
    mgr.post_manager.reply_to_thread = AsyncMock(return_value=(True, ""))
    return mgr


@pytest.mark.asyncio
async def test_add_materials_bulk_persists_link_url(db):
    ok = await db.add_materials_bulk([("标题A", "正文A", "https://pan.baidu.com/s/abc")])
    assert ok == 1
    async with db.async_session() as session:
        from sqlalchemy import select
        m = (await session.execute(select(MaterialPool).where(MaterialPool.title == "标题A"))).scalars().first()
        assert m.link_url == "https://pan.baidu.com/s/abc"

    ok = await db.add_materials_bulk([("标题B", "正文B")])
    assert ok == 1
    async with db.async_session() as session:
        from sqlalchemy import select
        m = (await session.execute(select(MaterialPool).where(MaterialPool.title == "标题B"))).scalars().first()
        assert m.link_url is None


@pytest.mark.asyncio
async def test_first_reply_success_marks_and_excludes_poster(db, manager):
    poster_id = await _add_account(db, "poster")
    await _add_account(db, "helper")
    mid = await _add_material(db, posted_account_id=poster_id)

    count = await manager.process_link_first_replies()

    assert count == 1
    manager.post_manager.reply_to_thread.assert_awaited_once()
    args = manager.post_manager.reply_to_thread.await_args.args
    assert args[0] != poster_id, "首评必须由非发帖原号发出"
    assert args[1] == "电影" and args[2] == 123456
    assert "pan.baidu.com/s/testlink" in args[3], "回帖内容必须携带链接"

    m = await _get_material(db, mid)
    assert m.link_reply_at is not None
    assert m.link_reply_fail_count == 0

    # 已发过首评的物料不再进入扫描
    count2 = await manager.process_link_first_replies()
    assert count2 == 0


@pytest.mark.asyncio
async def test_first_reply_failure_increments_and_gives_up(db, manager):
    await _add_account(db, "poster")
    await _add_account(db, "helper")
    manager.post_manager.reply_to_thread = AsyncMock(return_value=(False, "帖子已被删除"))
    mid = await _add_material(db)

    count = await manager.process_link_first_replies()
    assert count == 0
    m = await _get_material(db, mid)
    assert m.link_reply_at is None
    assert m.link_reply_fail_count == 1

    # 累计到上限后不再尝试
    async with db.async_session() as session:
        mrow = await session.get(MaterialPool, mid)
        mrow.link_reply_fail_count = 2
        await session.commit()
    count2 = await manager.process_link_first_replies()
    assert count2 == 0
    m = await _get_material(db, mid)
    assert m.link_reply_fail_count == 3  # 第3次失败后到达上限

    manager.post_manager.reply_to_thread.reset_mock()
    count3 = await manager.process_link_first_replies()
    assert count3 == 0
    manager.post_manager.reply_to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_filters(db, manager):
    await _add_account(db, "poster")
    await _add_account(db, "helper")

    # 太新鲜（<5分钟延迟窗）
    await _add_material(db, posted_time=datetime.now() - timedelta(minutes=1))
    # 太陈旧（>48h）
    await _add_material(db, posted_time=datetime.now() - timedelta(hours=49))
    # 无链接
    await _add_material(db, link_url=None)
    # 非成功状态
    await _add_material(db, status="pending")

    count = await manager.process_link_first_replies()
    assert count == 0
    manager.post_manager.reply_to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_matrix_pool_skips(db, manager):
    # 不建任何账号 → 矩阵池为空
    await _add_material(db)
    count = await manager.process_link_first_replies()
    assert count == 0
    manager.post_manager.reply_to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_to_poster_when_solo_account(db, manager):
    poster_id = await _add_account(db, "solo_poster")
    mid = await _add_material(db, posted_account_id=poster_id)

    count = await manager.process_link_first_replies()

    assert count == 1
    args = manager.post_manager.reply_to_thread.await_args.args
    assert args[0] == poster_id, "矩阵池只剩原号时应回退为楼主自评"
    m = await _get_material(db, mid)
    assert m.link_reply_at is not None
