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
    mgr._find_link_reply = AsyncMock(return_value=("empty", None))
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
    assert "pan.baidu.com/s/testlink" in content.replace("删", ""), "回帖内容必须携带链接（剥删字后可还原）"
    assert "删" in content, "百度盘链接必须经过删字暗号化（打断特征串）"
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
async def test_no_selfreply_config_skips_poster_rotation(db, manager):
    """link_reply_no_selfreply 排除楼主自评：其帖子首评首轮直接轮换其他矩阵号。

    回归背景：2026-10-04 实证 hwdemtv187 楼主自评首评 4/4 被百度系统硬吞
    （作者视角不可见+reply_num 残影），同吧同内容其他号存活。
    """
    poster_id = await _add_account(db, "poster")     # 矩阵池序在前，无配置时会被选为楼主自评
    await _add_account(db, "helper")
    await db.set_setting("link_reply_no_selfreply", str(poster_id))
    mid = await _add_material(db, posted_account_id=poster_id)

    count = await manager.process_link_first_replies()

    assert count == 1
    args = manager.post_manager.reply_to_thread.await_args.args
    assert args[0] != poster_id, "被排除楼主自评的账号，其帖子首评不得由楼主自评发出"
    m = await _get_material(db, mid)
    assert m.link_reply_at is not None

    # 配置清空后行为回归：楼主自评优先恢复
    await db.set_setting("link_reply_no_selfreply", "")
    mid2 = await _add_material(db, posted_account_id=poster_id, posted_tid=654321)
    count2 = await manager.process_link_first_replies()
    assert count2 == 1
    args2 = manager.post_manager.reply_to_thread.await_args_list[-1].args
    assert args2[0] == poster_id, "配置清空后必须恢复楼主自评优先"


@pytest.mark.asyncio
async def test_verify_confirms_visibility_and_closes_loop(db, manager):
    poster_id = await _add_account(db, "poster")
    mid = await _add_material(db, posted_account_id=poster_id)

    await manager.process_link_first_replies()
    await _backdate_link_reply(db, mid)

    manager._find_link_reply = AsyncMock(return_value=("found", 999))
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
    manager._find_link_reply = AsyncMock(return_value=("empty", None))
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
    manager._find_link_reply = AsyncMock(return_value=("found", 1001))
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
    manager._find_link_reply = AsyncMock(return_value=("error", None))
    count2 = await manager.process_link_first_replies()
    assert count2 == 0

    m = await _get_material(db, mid)
    assert m.link_reply_at is not None, "查询异常时保持已发出状态待下轮校验"
    assert m.link_reply_fail_count == 0
    manager.post_manager.reply_to_thread.assert_awaited_once()  # 查询异常不得触发重发


@pytest.mark.asyncio
async def test_dead_thread_skips_swallow_judgement(db, manager):
    """主帖阵亡（get_posts 内嵌错误码 350008/4）：不得判被吞、不动状态、不重发。

    回归背景：2026-10-04 #626 主帖阵亡后三次"被吞"误判白耗 3 次换号重发——
    主帖搜不到楼层与回复被吞同表象，必须先分清。
    """
    await _add_account(db, "poster")
    mid = await _add_material(db)

    await manager.process_link_first_replies()
    await _backdate_link_reply(db, mid)

    manager._find_link_reply = AsyncMock(return_value=("dead", None))
    count2 = await manager.process_link_first_replies()
    assert count2 == 0

    m = await _get_material(db, mid)
    assert m.link_reply_at is not None, "主帖阵亡时保持已发出状态，不触发被吞清空"
    assert m.link_reply_fail_count == 0, "主帖阵亡不得累计 fail_count"
    manager.post_manager.reply_to_thread.assert_awaited_once()  # 不得重发（重发也只会再失败）


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
    """pwd 链接改写后重发内容同样携带提取码尾注（删字暗号化不破坏还原性）"""
    await _add_account(db, "poster")
    await _add_account(db, "helper")
    mid = await _add_material(db, link_url="https://pan.baidu.com/s/xyz?pwd=9q8z")

    await manager.process_link_first_replies()
    content = manager.post_manager.reply_to_thread.await_args.args[3]
    clean = content.replace("删", "")
    assert "pan.baidu.com/s/xyz 提取码 9q8z" in clean
    assert "?pwd=" not in clean
    assert "https://" not in clean


# ---- 删字暗号化（2026-10-08 晚四账号带链首评全吞、模式疑似被拉黑的变体实验）----


def test_obfuscate_link_for_reply_inserts_single_del():
    from tieba_mecha.core.batch_post import AutoBumpManager as M

    canonical = "pan.baidu.com/s/1XIiMN6daT6rmpB0CpI2JMA 提取码 soee"
    for _ in range(50):
        out = M.obfuscate_link_for_reply(canonical)
        assert out.replace("删", "") == canonical, "剥删字必须还原原文"
        assert out.count("删") == 1, "恰好插一个删字"
        del_pos = out.index("删")
        url_end = canonical.find(" ")
        assert del_pos >= len("pan.baidu.com/s/"), "删字落在 /s/ 之后的路径段"
        assert del_pos <= url_end, "删字不越过 URL 末尾"
        assert "删" not in out[out.index(" 提取码"):], "提取码尾注不得被污染"
    # 仅百度盘陪绑：夸克等未标记网盘原样返回
    quark = "pan.quark.cn/s/abc?pwd=z"
    assert M.obfuscate_link_for_reply(quark) == quark
    assert M.obfuscate_link_for_reply("") == ""


def test_floor_contains_link_strips_del():
    from tieba_mecha.core.batch_post import AutoBumpManager as M

    token = "pan.baidu.com/s/abc 提取码 xy12"
    assert M._floor_contains_link("忘了说，资源在这里 pan.baidu删.com/s/abc 提取码 xy12", token)
    assert M._floor_contains_link("忘了说，资源在这里 pan.baidu.com/s/a删bc 提取码 xy12", token)
    assert M._floor_contains_link("忘了说，资源在这里 pan.baidu.com/s/abc 提取码 xy12", token)
    assert not M._floor_contains_link("无关楼层内容", token)
    assert not M._floor_contains_link(None, token)
    assert not M._floor_contains_link("任意文本", "")


@pytest.mark.asyncio
async def test_repost_resets_stale_link_reply_state(db):
    """10-02 物料616事故：reuse 重发落新 posted_tid 时旧首评闭环记录不清，
    首评调度按 link_reply_at IS NULL 捞候选会永久跳过该物料——新帖没有首评。"""
    mid = await _add_material(db)
    # 首轮闭环：首评已发出且确认可见（挂在旧帖上）
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        m.link_reply_at = datetime.now() - timedelta(days=2)
        m.link_reply_pid = 153984149016
        await session.commit()

    # 重发：新帖落库 → 首评状态必须归零重装载
    await db.update_material_status(
        mid, "success", posted_fname="电视剧资源", posted_tid=999,
        posted_account_id=1, posted_time=datetime.now(),
    )

    m = await _get_material(db, mid)
    assert m.posted_tid == 999
    assert m.link_reply_at is None
    assert m.link_reply_pid is None
    assert m.link_reply_fail_count == 0


@pytest.mark.asyncio
async def test_reset_to_pending_clears_link_reply_state(db):
    """手动/批量重置为待发：旧帖首评记录随行作废，重发后可重新获得首评。"""
    mid = await _add_material(db)
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        m.link_reply_at = datetime.now() - timedelta(days=2)
        m.link_reply_pid = 42
        m.link_reply_fail_count = 1
        await session.commit()

    await db.update_material_status(mid, "pending")

    m = await _get_material(db, mid)
    assert m.link_reply_at is None
    assert m.link_reply_pid is None
    assert m.link_reply_fail_count == 0


# ---- 账号级风控（2026-10-08 hwdemtv1 秒删事故整改）：手动剔除 + 被吞熔断 ----


async def _get_ledger(db) -> dict:
    import json as _json
    raw = await db.get_setting("link_reply_breaker_ledger", "")
    return _json.loads(raw) if raw else {}


@pytest.mark.asyncio
async def test_exclude_accounts_removed_from_pool(db, manager):
    """link_reply_exclude_accounts：命中者全程不参与首评（楼主自评+轮换都轮不到）。"""
    poster_id = await _add_account(db, "poster")
    helper_id = await _add_account(db, "helper")
    await db.set_setting("link_reply_exclude_accounts", str(poster_id))
    mid = await _add_material(db, posted_account_id=poster_id)

    count = await manager.process_link_first_replies()

    assert count == 1
    args = manager.post_manager.reply_to_thread.await_args.args
    assert args[0] == helper_id, "被剔除账号的帖子首评不得由本人自评发出"
    m = await _get_material(db, mid)
    assert m.link_reply_at is not None


@pytest.mark.asyncio
async def test_exclude_all_accounts_skips_send(db, manager):
    """剔除后矩阵池为空：本轮跳过发送，不得报错不得发帖。"""
    poster_id = await _add_account(db, "poster")
    await db.set_setting("link_reply_exclude_accounts", str(poster_id))
    await _add_material(db, posted_account_id=poster_id)

    count = await manager.process_link_first_replies()

    assert count == 0
    manager.post_manager.reply_to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_breaker_trips_after_threshold_and_blocks(db, manager):
    """被吞熔断：同一账号窗口内累计达阈值击数即冷却退池，后续物料轮不到它。"""
    solo_id = await _add_account(db, "solo")
    mid1 = await _add_material(db, posted_account_id=solo_id, posted_tid=111)

    # 第1轮：自评发出 → 判被吞 → 记击1、立即换号重发（池只剩自己）
    assert await manager.process_link_first_replies() == 1
    await _backdate_link_reply(db, mid1)
    await manager.process_link_first_replies()
    m = await _get_material(db, mid1)
    assert m.link_reply_fail_count == 1
    ledger = await _get_ledger(db)
    assert len(ledger["strikes"][str(solo_id)]) == 1
    assert str(mid1) in ledger["pending"], "换号重发后 pending 应指向新发出账号"

    # 第2轮：再被吞 → 记击2 达阈值 → 熔断退池；同轮发送阶段池空跳过（fail_count 停在 2，
    # 冷却到期且未超 48h 窗时会自然重试——优雅降级而非放弃）
    await _backdate_link_reply(db, mid1)
    count = await manager.process_link_first_replies()
    assert count == 0, "熔断生效轮：发送阶段池空，不得再发"
    m = await _get_material(db, mid1)
    assert m.link_reply_fail_count == 2
    ledger = await _get_ledger(db)
    assert len(ledger["strikes"][str(solo_id)]) == 2
    assert str(mid1) not in ledger["pending"], "被吞判定后 pending 必须清掉"

    # 后续物料：solo 处于冷却 → 剔除后池空 → 不发送
    manager.post_manager.reply_to_thread.reset_mock()
    mid2 = await _add_material(db, posted_account_id=solo_id, posted_tid=222)
    count = await manager.process_link_first_replies()
    assert count == 0
    manager.post_manager.reply_to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_breaker_cooldown_expires(db, manager):
    """冷却到期自动恢复：窗口内击数仍在但超过冷却期即不再拦截。"""
    from tieba_mecha.core.batch_post import AutoBumpManager as _M
    aid = 7
    now = datetime.now()
    strikes = {str(aid): [
        (now - timedelta(hours=20)).isoformat(timespec="seconds"),
        (now - timedelta(hours=19)).isoformat(timespec="seconds"),
    ]}
    hot, cnt, _ = _M._breaker_blocked(aid, strikes, now, threshold=2, window_h=48, cooldown_h=24)
    assert hot is True and cnt == 2, "19h 前被吞、24h 冷却期还剩 5h → 仍冷却中"

    hot_late, cnt_late, _ = _M._breaker_blocked(aid, strikes, now + timedelta(hours=6), threshold=2, window_h=48, cooldown_h=24)
    assert hot_late is False and cnt_late == 2, "末击 25h 后冷却已过 → 恢复资格（窗口内击数仍在但不续期）"

    hot_old, cnt_old, _ = _M._breaker_blocked(aid, {"7": [(now - timedelta(hours=100)).isoformat()]}, now, 2, 48, 24)
    assert hot_old is False and cnt_old == 0, "窗口(48h)外的击数不计数"


@pytest.mark.asyncio
async def test_confirm_visible_clears_pending_no_strike(db, manager):
    """确认可见：清 pending 不记击；被吞只击实际发出账号（账本 pending 归因）。"""
    poster_id = await _add_account(db, "poster")
    mid = await _add_material(db, posted_account_id=poster_id)

    await manager.process_link_first_replies()  # poster 自评发出
    ledger = await _get_ledger(db)
    assert ledger["pending"][str(mid)]["aid"] == poster_id

    await _backdate_link_reply(db, mid)
    manager._find_link_reply = AsyncMock(return_value=("found", 777))
    await manager.process_link_first_replies()

    ledger = await _get_ledger(db)
    assert str(mid) not in ledger["pending"], "确认可见必须清 pending"
    assert ledger["strikes"] == {}, "可见不得记击"


@pytest.mark.asyncio
async def test_breaker_disabled_skips_strike_accounting(db, manager):
    """link_reply_breaker_enabled=false：纯旧行为，不记账不熔断。"""
    await _add_account(db, "poster")
    await db.set_setting("link_reply_breaker_enabled", "false")
    mid = await _add_material(db)

    await manager.process_link_first_replies()
    await _backdate_link_reply(db, mid)
    await manager.process_link_first_replies()  # 判被吞+换号重发

    ledger = await _get_ledger(db)
    assert ledger.get("strikes", {}) == {} and ledger.get("pending", {}) == {}, "开关关闭不得留任何账本痕迹"
    m = await _get_material(db, mid)
    assert m.link_reply_fail_count == 1, "被吞判定与重发本身不受开关影响"
