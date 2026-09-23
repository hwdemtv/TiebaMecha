"""带链首评（link first reply）回归测试。

架构（2026-09-23 吞评整改）：主帖净文化不带链接，链接存 link_url 字段，
发帖成功后发出带链首评——楼主自评优先（自然行为+发帖号已验证权重），
有失败记录则轮换矩阵号；链接改写去 scheme/拆 ?pwd= 降低外链正则命中。
API 成功≠可见（实测被吞时 reply_num 照样 +1）：发出超过校验延迟仍搜不到
含链楼层即判定被吞，清空 link_reply_at 换号重发，fail_count 达上限放弃；
确认可见后落 link_reply_pid 永久闭环。
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

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


async def _backdate_link_reply(db, mid: int, minutes: int = 10):
    """把 link_reply_at 拨早，越过校验延迟，让下一轮触发可见性校验"""
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        m.link_reply_at = datetime.now() - timedelta(minutes=minutes)
        await session.commit()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """屏蔽首评间隔的真实 asyncio.sleep（20-60s 拟人延迟），保证测试秒级完成"""
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture
def manager(db):
    mgr = AutoBumpManager(db)
    mgr.post_manager.reply_to_thread = AsyncMock(return_value=(True, ""))
    mgr._find_link_reply = AsyncMock(return_value=(True, None))
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
async def test_link_rewrite_strips_scheme_and_extracts_pwd():
    f = AutoBumpManager.format_link_for_share
    assert f("https://pan.baidu.com/s/abc?pwd=xy12") == "pan.baidu.com/s/abc 提取码 xy12"
    assert f("http://pan.baidu.com/s/abc?pwd=xy12") == "pan.baidu.com/s/abc 提取码 xy12"
    assert f("pan.baidu.com/s/abc") == "pan.baidu.com/s/abc"
    # 非百度盘：只去 scheme，不拆参数
    assert f("https://pan.quark.cn/s/abc?pwd=z") == "pan.quark.cn/s/abc?pwd=z"
    assert f(None) == ""
    assert f("  ") == ""


@pytest.mark.asyncio
async def test_first_reply_uses_poster_and_rewrites_link(db, manager):
    poster_id = await _add_account(db, "poster")
    await _add_account(db, "helper")
    mid = await _add_material(db, posted_account_id=poster_id)

    count = await manager.process_link_first_replies()

    assert count == 1
    manager.post_manager.reply_to_thread.assert_awaited_once()
    args = manager.post_manager.reply_to_thread.await_args.args
    assert args[0] == poster_id, "首轮无失败记录时必须楼主自评（最自然的'楼主二楼自取'）"
    assert args[1] == "电影" and args[2] == 123456
    content = args[3]
    assert "pan.baidu.com/s/testlink" in content, "回帖内容必须携带链接"
    assert "https://" not in content, "链接必须去 scheme 后发出"
    assert "提取码" not in content  # testlink 无 pwd 参数

    m = await _get_material(db, mid)
    assert m.link_reply_at is not None
    assert m.link_reply_pid is None, "刚发出尚未校验，pid 必须为空"
    assert m.link_reply_fail_count == 0

    # 发出未满校验延迟：不校验、不重发
    count2 = await manager.process_link_first_replies()
    assert count2 == 0
    assert manager.post_manager.reply_to_thread.await_count == 1


@pytest.mark.asyncio
async def test_verify_confirms_visibility_and_closes_loop(db, manager):
    poster_id = await _add_account(db, "poster")
    mid = await _add_material(db, posted_account_id=poster_id)

    await manager.process_link_first_replies()
    await _backdate_link_reply(db, mid)

    manager._find_link_reply = AsyncMock(return_value=(True, 999))
    count2 = await manager.process_link_first_replies()
    assert count2 == 0
    manager._find_link_reply.assert_awaited_once()
    assert manager._find_link_reply.await_args.args[0] == 123456
    manager.post_manager.reply_to_thread.assert_awaited_once()  # 确认可见后不得重发

    m = await _get_material(db, mid)
    assert m.link_reply_pid == 999
    assert m.link_reply_fail_count == 0

    # 已闭环（pid 落库）的物料不再进入任何阶段
    count3 = await manager.process_link_first_replies()
    assert count3 == 0
    assert manager.post_manager.reply_to_thread.await_count == 1
    assert manager._find_link_reply.await_count == 1


@pytest.mark.asyncio
async def test_verify_swallow_clears_and_retries_with_rotation(db, manager):
    poster_id = await _add_account(db, "poster")
    helper_id = await _add_account(db, "helper")
    mid = await _add_material(db, posted_account_id=poster_id)

    await manager.process_link_first_replies()
    assert manager.post_manager.reply_to_thread.await_args.args[0] == poster_id

    # 越过校验延迟后查不到含链楼层 → 判定被吞
    await _backdate_link_reply(db, mid)
    manager._find_link_reply = AsyncMock(return_value=(True, None))
    count2 = await manager.process_link_first_replies()
    assert count2 == 1, "被吞清空后应立即进入重发"

    m = await _get_material(db, mid)
    assert m.link_reply_fail_count == 1
    assert m.link_reply_at is not None, "重发后应重新记发出时间"
    assert m.link_reply_pid is None

    # 重发换号：不再押注楼主
    args2 = manager.post_manager.reply_to_thread.await_args_list[1].args
    assert args2[0] != poster_id, "有失败记录后必须轮换矩阵号重发"
    assert args2[0] == helper_id

    # 换号重发后校验可见 → 闭环
    await _backdate_link_reply(db, mid)
    manager._find_link_reply = AsyncMock(return_value=(True, 1001))
    await manager.process_link_first_replies()
    m = await _get_material(db, mid)
    assert m.link_reply_pid == 1001


@pytest.mark.asyncio
async def test_verify_swallow_up_to_max_fail_then_gives_up(db, manager):
    await _add_account(db, "poster")
    await _add_account(db, "helper1")
    await _add_account(db, "helper2")
    mid = await _add_material(db)

    await manager.process_link_first_replies()  # 首轮发送（楼主自评）
    await _backdate_link_reply(db, mid)

    for expected_fail in (1, 2, 3):
        await manager.process_link_first_replies()
        m = await _get_material(db, mid)
        assert m.link_reply_fail_count == expected_fail
        if expected_fail < 3:
            assert m.link_reply_at is not None, "未达上限时应继续重发"
            await _backdate_link_reply(db, mid)

    m = await _get_material(db, mid)
    assert m.link_reply_at is None, "达上限后放弃，link_reply_at 清空"
    assert m.link_reply_pid is None

    # 放弃后不再发送、不再校验
    manager.post_manager.reply_to_thread.reset_mock()
    count = await manager.process_link_first_replies()
    assert count == 0
    manager.post_manager.reply_to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_transient_error_keeps_state(db, manager):
    await _add_account(db, "poster")
    mid = await _add_material(db)

    await manager.process_link_first_replies()
    await _backdate_link_reply(db, mid)

    # 楼层查询异常（风控/网络）：不得误判被吞、不得动状态
    manager._find_link_reply = AsyncMock(return_value=(False, None))
    count2 = await manager.process_link_first_replies()
    assert count2 == 0

    m = await _get_material(db, mid)
    assert m.link_reply_at is not None, "查询异常时保持已发出状态待下轮校验"
    assert m.link_reply_fail_count == 0
    manager.post_manager.reply_to_thread.assert_awaited_once()  # 查询异常不得触发重发


@pytest.mark.asyncio
async def test_api_failure_increments_and_gives_up(db, manager):
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
async def test_poster_terminal_falls_back_to_matrix(db, manager):
    poster_id = await _add_account(db, "banned_poster", status="suspended")
    helper_id = await _add_account(db, "helper")
    mid = await _add_material(db, posted_account_id=poster_id)

    count = await manager.process_link_first_replies()

    assert count == 1
    args = manager.post_manager.reply_to_thread.await_args.args
    assert args[0] == helper_id, "楼主不可用（终态）时首轮即回退矩阵号"
    m = await _get_material(db, mid)
    assert m.link_reply_at is not None


@pytest.mark.asyncio
async def test_solo_poster_self_replies(db, manager):
    poster_id = await _add_account(db, "solo_poster")
    mid = await _add_material(db, posted_account_id=poster_id)

    count = await manager.process_link_first_replies()

    assert count == 1
    args = manager.post_manager.reply_to_thread.await_args.args
    assert args[0] == poster_id, "矩阵池只剩原号时应楼主自评"
    m = await _get_material(db, mid)
    assert m.link_reply_at is not None


@pytest.mark.asyncio
async def test_retry_content_carries_rewrite_and_pwd(db, manager):
    """pwd 链接改写后重发内容同样携带提取码尾注"""
    await _add_account(db, "poster")
    await _add_account(db, "helper")
    mid = await _add_material(db, link_url="https://pan.baidu.com/s/xyz?pwd=9q8z")

    await manager.process_link_first_replies()
    content = manager.post_manager.reply_to_thread.await_args.args[3]
    assert "pan.baidu.com/s/xyz 提取码 9q8z" in content
    assert "?pwd=" not in content
    assert "https://" not in content
