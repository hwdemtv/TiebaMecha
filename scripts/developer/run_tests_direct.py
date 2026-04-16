#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct test runner that bypasses encoding issues."""

import sys
import os

# Set encoding before any imports
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Force UTF-8 output
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add project to path
project_root = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(project_root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Set test environment variables
os.environ["TIEBA_MECHA_SALT"] = "a" * 64
os.environ["TIEBA_MECHA_SECRET_KEY"] = "b" * 64


def check_dependencies():
    """Check if required dependencies are installed."""
    required = ['sqlalchemy', 'aiosqlite', 'cryptography']
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def run_tests():
    """Run tests using unittest for maximum compatibility."""
    import tempfile
    import asyncio
    from pathlib import Path

    print("=" * 60)
    print("TiebaMecha Unit Tests")
    print("=" * 60)

    # Check dependencies first
    missing = check_dependencies()
    if missing:
        print(f"\n[!] Missing dependencies: {', '.join(missing)}")
        print(f"[!] Please install with: pip install {' '.join(missing)}")
        return 1

    passed = 0
    failed = 0

    # Test 1: Package imports
    print("\n[1] Testing package imports...")
    try:
        from tieba_mecha.db import models, crud
        from tieba_mecha.core import account
        print("    [PASS] All modules imported successfully")
        passed += 1
    except Exception as e:
        print(f"    [FAIL] Import failed: {e}")
        failed += 1
        return 1

    # Test 2: Encryption
    print("\n[2] Testing encryption/decryption...")
    try:
        from tieba_mecha.core.account import encrypt_value, decrypt_value

        original = "test_secret_123"
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)

        assert encrypted != original, "Encrypted value should differ from original"
        assert decrypted == original, "Decryption should recover original"
        print("    [PASS] Encryption/decryption working correctly")
        passed += 1
    except Exception as e:
        print(f"    [FAIL] Encryption test failed: {e}")
        failed += 1

    # Test 3: Cookie parsing
    print("\n[3] Testing cookie parsing...")
    try:
        from tieba_mecha.core.account import parse_cookie

        cookie = "BDUSS=abc123; STOKEN=xyz789; other=value"
        bduss, stoken = parse_cookie(cookie)

        assert bduss == "abc123", f"Expected 'abc123', got '{bduss}'"
        assert stoken == "xyz789", f"Expected 'xyz789', got '{stoken}'"
        print("    [PASS] Cookie parsing working correctly")
        passed += 1
    except Exception as e:
        print(f"    [FAIL] Cookie parsing test failed: {e}")
        failed += 1

    # Test 4: Database operations
    print("\n[4] Testing database operations...")
    try:
        async def test_db():
            from tieba_mecha.db.crud import Database

            # Create temp database
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = Path(f.name)

            db = Database(db_path)
            await db.init_db()

            # Test account operations
            acc = await db.add_account(name="test", bduss="test_bduss", stoken="test_stoken")
            assert acc.id is not None
            assert acc.name == "test"
            assert acc.is_active is True  # First account is active

            # Test get accounts
            accounts = await db.get_accounts()
            assert len(accounts) == 1

            # Test add second account
            acc2 = await db.add_account(name="test2", bduss="bduss2")
            assert acc2.is_active is False  # Second account not active

            # Test switch account
            await db.set_active_account(acc2.id)
            active = await db.get_active_account()
            assert active.id == acc2.id

            # Test forum operations
            forum = await db.add_forum(fid=12345, fname="test_forum", account_id=acc.id)
            assert forum.id is not None
            assert forum.fname == "test_forum"

            forums = await db.get_forums(acc.id)
            assert len(forums) == 1

            # Test settings
            await db.set_setting("test_key", "test_value")
            value = await db.get_setting("test_key")
            assert value == "test_value"

            # Test proxy
            proxy = await db.add_proxy(host="127.0.0.1", port=7890)
            assert proxy.id is not None
            assert proxy.host == "127.0.0.1"

            await db.close()

            # Cleanup
            db_path.unlink(missing_ok=True)

            return True

        result = asyncio.run(test_db())
        if result:
            print("    [PASS] Database operations working correctly")
            passed += 1
    except Exception as e:
        print(f"    [FAIL] Database test failed: {e}")
        import traceback
        traceback.print_exc()
        failed += 1

    # Test 5: Rate limiter
    print("\n[5] Testing rate limiter...")
    try:
        from tieba_mecha.core.batch_post import RateLimiter

        limiter = RateLimiter(rpm=8)  # 保守值
        assert limiter.rpm == 8
        assert limiter.timestamps == []

        async def test_limiter():
            # Should allow first few requests immediately
            for _ in range(5):
                await limiter.wait_if_needed()
            assert len(limiter.timestamps) == 5

        asyncio.run(test_limiter())
        print("    [PASS] Rate limiter working correctly")
        passed += 1
    except Exception as e:
        print(f"    [FAIL] Rate limiter test failed: {e}")
        failed += 1

    # Test 6: Batch post task
    print("\n[6] Testing batch post task...")
    try:
        from tieba_mecha.core.batch_post import BatchPostTask, BatchPostManager

        task = BatchPostTask(
            id="test-1",
            fname="single_forum",
            fnames=["forum1", "forum2"],
            titles=["Title 1"],
            contents=["Content"],
            accounts=[1, 2, 3],
            strategy="round_robin",
        )

        # Test get_fnames prefers fnames over fname
        fnames = task.get_fnames()
        assert fnames == ["forum1", "forum2"], f"Expected ['forum1', 'forum2'], got {fnames}"

        # Test fallback to fname
        task2 = BatchPostTask(id="test-2", fname="fallback_forum")
        assert task2.get_fnames() == ["fallback_forum"]

        # Test weighted choice
        manager = BatchPostManager(db=None)
        weights = [(1, 9), (2, 1)]
        results = [manager._weighted_choice(weights) for _ in range(100)]
        count_1 = results.count(1)
        assert count_1 > 70, f"Weighted choice should prefer higher weight, got {count_1}/100 for weight 9"

        print("    [PASS] Batch post task working correctly")
        passed += 1
    except Exception as e:
        print(f"    [FAIL] Batch post task test failed: {e}")
        failed += 1

    # Test 7: Sign result and forum info
    print("\n[7] Testing sign dataclasses...")
    try:
        from tieba_mecha.core.sign import SignResult, ForumInfo

        result = SignResult(fname="test", success=True, message="OK", sign_count=5)
        assert result.fname == "test"
        assert result.success is True
        assert result.sign_count == 5

        info = ForumInfo(fid=123, fname="forum", is_sign_today=False, sign_count=0)
        assert info.fid == 123
        assert info.fname == "forum"

        print("    [PASS] Sign dataclasses working correctly")
        passed += 1
    except Exception as e:
        print(f"    [FAIL] Sign dataclasses test failed: {e}")
        failed += 1

    # Test 8: Account info
    print("\n[8] Testing account info...")
    try:
        from tieba_mecha.core.account import AccountInfo

        info = AccountInfo(
            id=1,
            name="test",
            user_id=123,
            user_name="testuser",
            is_active=True,
            status="active",
        )

        assert info.id == 1
        assert info.name == "test"
        assert info.status == "active"
        assert info.cuid == ""  # default

        print("    [PASS] Account info working correctly")
        passed += 1
    except Exception as e:
        print(f"    [FAIL] Account info test failed: {e}")
        failed += 1

    # Test 9: Proxy fail threshold
    print("\n[9] Testing proxy failure threshold...")
    try:
        async def test_proxy_fail():
            from tieba_mecha.db.crud import Database, PROXY_FAIL_THRESHOLD

            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = Path(f.name)

            db = Database(db_path)
            await db.init_db()

            proxy = await db.add_proxy(host="127.0.0.1", port=7890)

            # Fail until threshold
            for _ in range(PROXY_FAIL_THRESHOLD):
                await db.mark_proxy_fail(proxy.id)

            updated = await db.get_proxy(proxy.id)
            assert updated.is_active is False, "Proxy should be deactivated after threshold failures"

            await db.close()
            db_path.unlink(missing_ok=True)
            return True

        result = asyncio.run(test_proxy_fail())
        if result:
            print("    [PASS] Proxy failure threshold working correctly")
            passed += 1
    except Exception as e:
        print(f"    [FAIL] Proxy failure threshold test failed: {e}")
        failed += 1

    # Test 10: Account suspension for proxy
    print("\n[10] Testing account suspension for proxy...")
    try:
        async def test_suspension():
            from tieba_mecha.db.crud import Database

            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                db_path = Path(f.name)

            db = Database(db_path)
            await db.init_db()

            proxy = await db.add_proxy(host="127.0.0.1", port=7890)
            acc1 = await db.add_account(name="acc1", bduss="b1", proxy_id=proxy.id)
            acc2 = await db.add_account(name="acc2", bduss="b2", proxy_id=proxy.id)

            # Suspend accounts
            suspended = await db.suspend_accounts_for_proxy(proxy.id, "Test suspension")
            assert len(suspended) == 2

            accounts = await db.get_accounts()
            assert all(a.status == "suspended_proxy" for a in accounts)

            # Restore accounts
            restored = await db.restore_accounts_for_proxy(proxy.id)
            assert len(restored) == 2

            accounts = await db.get_accounts()
            assert all(a.status == "active" for a in accounts)

            await db.close()
            db_path.unlink(missing_ok=True)
            return True

        result = asyncio.run(test_suspension())
        if result:
            print("    [PASS] Account suspension working correctly")
            passed += 1
    except Exception as e:
        print(f"    [FAIL] Account suspension test failed: {e}")
        failed += 1

    # Summary
    print("\n" + "=" * 60)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
