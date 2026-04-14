import pytest
import pytest_asyncio
from tieba_mecha.db.crud import Database
from tieba_mecha.core.account import encrypt_value

@pytest_asyncio.fixture
async def matrix_db(db):
    # 初始化一些账号数据
    # 账号 A 关注: 贴吧1, 贴吧2
    # 账号 B 关注: 贴吧2, 贴吧3
    
    bduss = encrypt_value("dummy_bduss")
    
    acc_a = await db.add_account(name="AccA", bduss=bduss, user_id=1, user_name="UserA")
    acc_b = await db.add_account(name="AccB", bduss=bduss, user_id=2, user_name="UserB")
    
    # 关注关系
    await db.add_forum(fid=101, fname="贴吧1", account_id=acc_a.id)
    await db.add_forum(fid=102, fname="贴吧2", account_id=acc_a.id)
    
    await db.add_forum(fid=102, fname="贴吧2", account_id=acc_b.id)
    await db.add_forum(fid=103, fname="贴吧3", account_id=acc_b.id)
    
    return db

@pytest.mark.asyncio
async def test_get_forum_matrix_stats(matrix_db):
    stats = await matrix_db.get_forum_matrix_stats()
    
    # 检查总吧数 (去重后应该是 3 个)
    assert len(stats) == 3
    
    # 检查排序 (贴吧2 应该在第一位，因为有两个号关注)
    assert stats[0]['fname'] == "贴吧2"
    assert stats[0]['account_count'] == 2
    assert "AccA" in stats[0]['account_names']
    assert "AccB" in stats[0]['account_names']
    
    # 检查单号关注的吧
    bar1 = next(s for s in stats if s['fname'] == "贴吧1")
    assert bar1['account_count'] == 1
    assert bar1['account_names'] == "AccA"

@pytest.mark.asyncio
async def test_bulk_update_target_group(matrix_db):
    # 批量锁定标的并分类
    fnames = ["贴吧1", "贴吧2", "新吧"]
    await matrix_db.bulk_update_target_group(fnames, "游戏/二次元")
    
    stats = await matrix_db.get_forum_matrix_stats()
    
    # 吧库总数应该是 4 个 (原3个 + 1个新吧)
    assert len(stats) == 4
    
    for s in stats:
        if s['fname'] in fnames:
            assert s['is_target'] is True
            assert s['post_group'] == "游戏/二次元"
        else:
            assert s['is_target'] is False

@pytest.mark.asyncio
async def test_target_pool_sync_with_no_account(matrix_db):
    # 测试那些在 TargetPool 中但没有任何号关注的贴吧是否也被统计进来
    await matrix_db.upsert_target_pools(["孤独贴吧"], "空降兵")
    
    stats = await matrix_db.get_forum_matrix_stats()
    lonely = next(s for s in stats if s['fname'] == "孤独贴吧")
    
    assert lonely['account_count'] == 0
    assert lonely['is_target'] is True
    assert lonely['post_group'] == "空降兵"
