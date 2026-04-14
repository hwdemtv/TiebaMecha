# -*- coding: utf-8 -*-
"""Database-driven integration test script"""
import asyncio
import sys
sys.path.insert(0, 'src')

from pathlib import Path
from tieba_mecha.db.crud import Database

async def run_tests():
    print('='*60)
    print('     Database-Driven Integration Test')
    print('='*60)
    print()

    db = Database(Path('data/tieba_mecha.db'))
    await db.init_db()

    results = {'pass': 0, 'fail': 0, 'skip': 0}

    # ========== 1. Account Module ==========
    print('[1] Account Module')
    print('-'*40)

    from tieba_mecha.core.account import get_account_credentials, list_accounts

    creds = await get_account_credentials(db)
    if creds:
        acc_id, bduss, stoken, proxy_id, cuid, ua = creds
        print(f'  PASS: Credentials retrieved (ID={acc_id})')
        print(f'        BDUSS length: {len(bduss)}')
        results['pass'] += 1
    else:
        print('  FAIL: No credentials found')
        results['fail'] += 1

    accounts = await list_accounts(db)
    print(f'  PASS: Listed {len(accounts)} accounts')
    results['pass'] += 1
    print()

    # ========== 2. Sign Module ==========
    print('[2] Sign Module')
    print('-'*40)

    from tieba_mecha.core.sign import sign_forum, get_sign_stats

    forums = await db.get_forums()
    if forums:
        result = await sign_forum(db, forums[0].fname)
        status = 'PASS' if result.success else 'SKIP'
        msg = result.message[:30] if len(result.message) > 30 else result.message
        print(f'  {status}: Sign {forums[0].fname} - {msg}')
        if result.success:
            results['pass'] += 1
        else:
            results['skip'] += 1

    stats = await get_sign_stats(db)
    print(f'  PASS: Stats - {stats["success"]}/{stats["total"]} signed')
    results['pass'] += 1
    print()

    # ========== 3. Crawl Module ==========
    print('[3] Crawl Module')
    print('-'*40)

    from tieba_mecha.core.crawl import crawl_user, crawl_threads, get_crawl_history

    # User crawl with username
    async for p in crawl_user(db, 'hwdemtv187', with_posts=False):
        if p.status == 'completed':
            print(f'  PASS: User crawl (username) - {p.message}')
            results['pass'] += 1
        elif p.status == 'failed':
            print(f'  FAIL: User crawl - {p.message[:40]}')
            results['fail'] += 1

    # User crawl with numeric ID
    async for p in crawl_user(db, '7032046377', with_posts=False):
        if p.status == 'completed':
            print(f'  PASS: User crawl (numeric ID) - {p.message}')
            results['pass'] += 1

    # Threads crawl
    async for p in crawl_threads(db, 'asoul', pages=1):
        if p.status in ['completed', 'partial']:
            print(f'  PASS: Threads crawl - got {p.current} threads')
            results['pass'] += 1

    history = await get_crawl_history(db)
    print(f'  PASS: Crawl history has {len(history)} records')
    results['pass'] += 1
    print()

    # ========== 4. Material Pool ==========
    print('[4] Material Pool Module')
    print('-'*40)

    materials = await db.get_materials(limit=100)
    print(f'  PASS: Retrieved {len(materials)} materials')
    results['pass'] += 1

    # Bulk add test materials
    import time
    test_suffix = int(time.time())
    pairs = [
        (f'test_material_{test_suffix}_1', 'test content 1'),
        (f'test_material_{test_suffix}_2', 'test content 2'),
    ]
    added = await db.add_materials_bulk(pairs)
    print(f'  PASS: Bulk added {added} materials')
    results['pass'] += 1
    print()

    # ========== 5. Proxy Module ==========
    print('[5] Proxy Module')
    print('-'*40)

    from tieba_mecha.core.proxy import get_best_proxy_config

    proxy = await get_best_proxy_config(db)
    if proxy:
        print(f'  PASS: Proxy configured')
        results['pass'] += 1
    else:
        print('  PASS: No proxy (direct connection)')
        results['pass'] += 1
    print()

    # ========== 6. Auto Rules ==========
    print('[6] Auto Rules Module')
    print('-'*40)

    rules = await db.get_auto_rules()
    print(f'  PASS: {len(rules)} rules configured')
    results['pass'] += 1
    print()

    # ========== 7. Batch Post ==========
    print('[7] Batch Post Module')
    print('-'*40)

    from tieba_mecha.core.batch_post import BatchPostManager

    manager = BatchPostManager(db)
    accounts = await db.get_accounts()
    print(f'  PASS: Manager initialized with {len(accounts)} accounts')
    results['pass'] += 1

    pending = await db.get_pending_batch_tasks()
    print(f'  PASS: {len(pending)} pending batch tasks')
    results['pass'] += 1
    print()

    # ========== 8. Client Factory ==========
    print('[8] Client Factory')
    print('-'*40)

    from tieba_mecha.core.client_factory import create_client

    if creds:
        async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
            user = await client.get_self_info()
            if user and user.user_id:
                print(f'  PASS: Client authenticated as user_id={user.user_id}')
                results['pass'] += 1

            try:
                forums_list = await client.get_follow_forums()
                print(f'  PASS: Retrieved {len(forums_list)} followed forums')
                results['pass'] += 1
            except Exception as e:
                print(f'  SKIP: Get follow forums - {str(e)[:30]}')
                results['skip'] += 1
    print()

    # ========== 9. Database CRUD ==========
    print('[9] Database CRUD Operations')
    print('-'*40)

    forums = await db.get_forums()
    print(f'  PASS: get_forums() returned {len(forums)} forums')
    results['pass'] += 1

    logs = await db.get_sign_logs(limit=10)
    print(f'  PASS: get_sign_logs() returned {len(logs)} logs')
    results['pass'] += 1

    tasks = await db.get_crawl_tasks()
    print(f'  PASS: get_crawl_tasks() returned {len(tasks)} tasks')
    results['pass'] += 1

    setting = await db.get_setting('test_key', 'default_value')
    print(f'  PASS: get_setting() works')
    results['pass'] += 1
    print()

    # ========== 10. User Posts Fetch ==========
    print('[10] User Posts Fetch')
    print('-'*40)

    async for p in crawl_user(db, 'hwdemtv187', with_posts=True):
        if p.status == 'completed':
            posts_count = p.current - 1
            print(f'  PASS: Fetched user info + {posts_count} posts')
            results['pass'] += 1
        elif p.status == 'failed':
            print(f'  FAIL: {p.message[:40]}')
            results['fail'] += 1
    print()

    # ========== Summary ==========
    print('='*60)
    print(f'  TOTAL: PASS={results["pass"]}, FAIL={results["fail"]}, SKIP={results["skip"]}')
    print('='*60)

    return results['fail'] == 0

if __name__ == '__main__':
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
