"""get_forum_risk_stats 聚合查询测试：覆盖账号/发帖/删帖/封禁。"""

import pytest
from sqlalchemy import select

from tieba_mecha.db.crud import Database


@pytest.mark.asyncio
async def test_forum_risk_stats_aggregation(db: Database):
    acc_a = await db.add_account("acc_a", "a" * 192, "s" * 64)
    acc_b = await db.add_account("acc_b", "b" * 192, "s" * 64)

    # 吧X：两个账号关注（覆盖2），安全；历史发 3 帖删 2（删帖率 67% → 高风险）
    await db.add_forum(fid=1, fname="吧X", account_id=acc_a.id)
    await db.add_forum(fid=1, fname="吧X", account_id=acc_b.id)
    # 吧Y：单账号关注，封禁
    await db.add_forum(fid=2, fname="吧Y", account_id=acc_a.id)
    # 吧Z：无发帖记录
    await db.add_forum(fid=3, fname="吧Z", account_id=acc_b.id)

    async with db.async_session() as session:
        from tieba_mecha.db.models import Forum as DBForum
        for forum in (await session.execute(
            select(DBForum).where(DBForum.fname == "吧X")
        )).scalars().all():
            forum.is_post_target = True
        for forum in (await session.execute(
            select(DBForum).where(DBForum.fname == "吧Y")
        )).scalars().all():
            forum.is_banned = True
        await session.commit()

    # 已发记录：吧X 3 帖（2 dead 1 alive），吧Y 1 帖 dead
    async with db.async_session() as session:
        from tieba_mecha.db.models import MaterialPool
        rows = [
            MaterialPool(title="t", content="c", status="success", posted_fname="吧X",
                         survival_status="dead", posted_tid=1),
            MaterialPool(title="t", content="c", status="success", posted_fname="吧X",
                         survival_status="dead", posted_tid=2),
            MaterialPool(title="t", content="c", status="success", posted_fname="吧X",
                         survival_status="alive", posted_tid=3),
            MaterialPool(title="t", content="c", status="success", posted_fname="吧Y",
                         survival_status="dead", posted_tid=4),
            # 未发帖物料不参与聚合
            MaterialPool(title="t", content="c", status="pending"),
        ]
        session.add_all(rows)
        await session.commit()

    stats = {s["fname"]: s for s in await db.get_forum_risk_stats()}

    assert stats["吧X"]["cover_accounts"] == 2
    assert stats["吧X"]["posted_count"] == 3
    assert stats["吧X"]["dead_count"] == 2
    assert stats["吧X"]["dead_rate"] == round(2 / 3, 3)
    assert stats["吧X"]["is_post_target"] is True
    assert stats["吧X"]["is_banned"] is False

    assert stats["吧Y"]["cover_accounts"] == 1
    assert stats["吧Y"]["is_banned"] is True
    assert stats["吧Y"]["posted_count"] == 1

    assert stats["吧Z"]["posted_count"] == 0
    assert stats["吧Z"]["dead_rate"] == 0.0

    assert "吧Z" in stats
    assert len(stats) == 3


@pytest.mark.asyncio
async def test_forum_risk_stats_empty_db(db: Database):
    assert await db.get_forum_risk_stats() == []
