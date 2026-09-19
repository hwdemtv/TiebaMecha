"""get_my_posts 统一视图与新 repo 方法的集成测试（临时 SQLite，无网络）。"""

import asyncio
import os
import tempfile
from datetime import datetime

import pytest

from tieba_mecha.db.crud import Database


@pytest.fixture()
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # 让 init_db 从零建表
    yield path
    if os.path.exists(path):
        os.remove(path)


def _seed(db: Database):
    """构造标准场景：
    - 物料 111: alive, 贴吧 python, 09-18, 有 ThreadRecord(回复3, 非精品)
    - 物料 222: dead(error→疑似删除), 贴吧 linux, 09-19, 无 ThreadRecord
    - 记录 333: 导入帖(回复7, 精品), 贴吧 python, updated_at=今天
    """
    async def run():
        await db.add_account(name="测试甲", bduss="X" * 80, user_name="tester_a")
        a = (await db.get_accounts())[0]

        await db.add_materials_bulk([("标题一号", "正文 www.example.com/1"), ("标题二号", "正文2")])
        mats = await db.get_materials()
        await db.update_material_status(
            mats[0].id, "success", posted_fname="python",
            posted_tid=111, posted_account_id=a.id, posted_time=datetime(2026, 9, 18, 10, 0))
        await db.update_material_status(
            mats[1].id, "success", posted_fname="linux",
            posted_tid=222, posted_account_id=a.id, posted_time=datetime(2026, 9, 19, 9, 0))
        await db.update_material_survival_status(mats[0].id, "alive", "")
        await db.update_material_survival_status(mats[1].id, "dead", "error")

        await db.upsert_thread_records([
            {"tid": 333, "title": "导入的历史帖", "author_name": "tester_a", "author_id": a.id,
             "reply_num": 7, "text": "导入内容", "fname": "python", "is_good": True},
            {"tid": 111, "title": "标题一号", "author_name": "tester_a", "author_id": a.id,
             "reply_num": 3, "text": "正文", "fname": "python", "is_good": False},
        ])
        return a, mats
    return run


def test_get_my_posts_merge_and_filters(db_path):
    async def main():
        db = Database(db_path)
        await db.init_db()
        try:
            a, mats = await _seed(db)()

            # 全量合并：物料 111/222 + 记录 333（111 的记录行被 tid 去重抑制）
            rows = await db.get_my_posts()
            assert sorted(r.tid for r in rows) == [111, 222, 333]
            by_tid = {r.tid: r for r in rows}
            assert by_tid[111].src == "material" and by_tid[111].reply_num == 3
            assert by_tid[333].src == "record" and by_tid[333].is_good is True
            assert by_tid[222].survival_status == "dead" and by_tid[222].death_reason == "error"
            assert by_tid[111].survival_status == "alive"
            assert by_tid[222].post_time == datetime(2026, 9, 19, 9, 0)

            # 账号
            assert len(await db.get_my_posts(account_id=a.id)) == 3
            assert await db.get_my_posts(account_id=9999) == []
            # 存活四态（alive / dead 均含 / suspected / unknown）
            assert [r.tid for r in await db.get_my_posts(survival="alive")] == [111]
            assert [r.tid for r in await db.get_my_posts(survival="suspected")] == [222]
            assert [r.tid for r in await db.get_my_posts(survival="dead")] == [222]
            assert [r.tid for r in await db.get_my_posts(survival="unknown")] == [333]
            # 贴吧 / 精品 / 关键字 / 时间
            assert {r.tid for r in await db.get_my_posts(fname="python")} == {111, 333}
            assert [r.tid for r in await db.get_my_posts(is_good=True)] == [333]
            assert sorted(r.tid for r in await db.get_my_posts(is_good=False)) == [111, 222]
            assert [r.tid for r in await db.get_my_posts(keyword="历史")] == [333]
            # date_from=今天：物料 222 命中；记录 333 (updated_at=今天) 命中；
            # 物料 111 虽被时间过滤，其记录行也被 tid 去重抑制，不产生幽灵行
            assert sorted(r.tid for r in await db.get_my_posts(date_from=datetime(2026, 9, 19))) == [222, 333]
        finally:
            await db.close()

    asyncio.run(main())


def test_count_duplicate_titles(db_path):
    async def main():
        db = Database(db_path)
        await db.init_db()
        try:
            await db.add_materials_bulk([("标题一号", "a"), ("标题二号", "b")])
            assert await db.count_duplicate_titles("标题一号") == 1
            assert await db.count_duplicate_titles("不存在的标题") == 0
        finally:
            await db.close()

    asyncio.run(main())


def test_set_material_auto_bump(db_path):
    async def main():
        db = Database(db_path)
        await db.init_db()
        try:
            await db.add_materials_bulk([("标题一号", "a")])
            m = (await db.get_materials())[0]
            assert await db.set_material_auto_bump(m.id, True) is True
            assert (await db.get_materials_by_ids([m.id]))[0].is_auto_bump is True
            assert await db.set_material_auto_bump(m.id, False) is True
            assert (await db.get_materials_by_ids([m.id]))[0].is_auto_bump is False
            assert await db.set_material_auto_bump(99999, True) is False
        finally:
            await db.close()

    asyncio.run(main())


def test_bump_log_write_and_query(db_path):
    """自顶流水：逐次写入成功/失败，按物料/TID 过滤、时间倒序。"""
    async def main():
        db = Database(db_path)
        await db.init_db()
        try:
            await db.add_materials_bulk([("标题一号", "a")])
            m = (await db.get_materials())[0]

            id1 = await db.add_bump_log(m.id, 111, fname="python",
                                        account_id=1, account_name="甲", content="顶一下", success=True)
            id2 = await db.add_bump_log(m.id, 111, fname="python",
                                        account_id=2, account_name="乙", content="顶二下",
                                        success=False, message="吧务封禁: 测试错误")
            id3 = await db.add_bump_log(None, 222, success=True)
            assert id1 and id2 and id3

            logs = await db.get_bump_logs(material_id=m.id)
            assert {l.id for l in logs} == {id1, id2}
            failed = [l for l in logs if not l.success]
            assert len(failed) == 1 and "封禁" in failed[0].message
            assert failed[0].account_name == "乙"

            by_tid = await db.get_bump_logs(tid=222)
            assert [l.id for l in by_tid] == [id3]
            assert by_tid[0].material_id is None

            assert len(await db.get_bump_logs()) == 3
            assert len(await db.get_bump_logs(limit=2)) == 2
        finally:
            await db.close()

    asyncio.run(main())


def test_register_posted_material_inserts_success_row(db_path):
    """发布登记：单行直插 success + posted 字段，返回自增 ID；不做内容去重。"""
    async def main():
        db = Database(db_path)
        await db.init_db()
        acc = await db.add_account(name="甲", bduss="X" * 80, user_name="a")

        mid1 = await db.register_posted_material(
            "新帖标题", "新帖正文", posted_fname="python",
            posted_tid=999, posted_account_id=acc.id, posted_time=datetime(2026, 9, 19, 12, 0))
        assert mid1 > 0

        mats = await db.get_materials()
        assert len(mats) == 1
        m = mats[0]
        assert m.id == mid1
        assert m.status == "success"
        assert m.posted_tid == 999
        assert m.posted_fname == "python"
        assert m.posted_account_id == acc.id

        # 追踪登记不做去重：同内容再次登记应产生第二行（各自对应一次发帖）
        mid2 = await db.register_posted_material(
            "新帖标题", "新帖正文", posted_fname="python",
            posted_tid=1000, posted_account_id=acc.id)
        assert mid2 not in (0, mid1)
        assert len(await db.get_materials()) == 2

    asyncio.run(main())
