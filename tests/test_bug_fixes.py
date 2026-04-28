"""Unit tests for the latest round of bug fixes.

Covers:
- Fix 1: auto_rule.py missing get_account_credentials import
- Fix 2: notification.py wrong Database import in clear_all_read
- Fix 3: app.py duplicate batch_post scheduler removed
- Fix 4: daemon.py bare except -> except Exception
- Fix 5: post.py docstring position + duplicate import httpx
- Fix 6: link_manager.py unused self.client removed
- Fix 7: start_web.py hardcoded fallback key replaced
- Fix 8: sign.py sign_all_accounts single client per account
- Fix 9-11: bare except -> except Exception in batch_post_page/crawl/sign pages
"""

import ast
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.insert(0, str(SRC)) if str(SRC) not in sys.path else None


# ========================================================================
# Fix 1: auto_rule.py — missing get_account_credentials import
# ========================================================================


class TestAutoRuleImport:
    """auto_rule.py must import get_account_credentials from .account."""

    def test_get_account_credentials_is_imported(self):
        """Import should resolve without NameError."""
        from tieba_mecha.core.auto_rule import get_account_credentials
        assert callable(get_account_credentials)

    def test_apply_rules_function_exists(self):
        """apply_rules_to_threads should be importable (imports resolve)."""
        from tieba_mecha.core.auto_rule import apply_rules_to_threads
        assert callable(apply_rules_to_threads)


# ========================================================================
# Fix 2: notification.py — clear_all_read no longer imports from .auth
# ========================================================================


class TestNotificationImport:
    """notification.py clear_all_read must not import Database from .auth."""

    def test_notification_module_imports_cleanly(self):
        """Module import should not trigger circular/wrong import."""
        from tieba_mecha.core import notification
        assert hasattr(notification, "NotificationManager")

    def test_clear_all_read_no_auth_import(self):
        """clear_all_read source should not reference 'from .auth import Database'."""
        import inspect
        from tieba_mecha.core.notification import NotificationManager
        source = inspect.getsource(NotificationManager.clear_all_read)
        assert "from .auth import Database" not in source


# ========================================================================
# Fix 3: app.py — _batch_scheduler removed
# ========================================================================


class TestAppNoDuplicateScheduler:
    """app.py must not contain _batch_scheduler to avoid dual scheduling."""

    def test_no_batch_scheduler_method(self):
        """TiebaMechaApp should not have _batch_scheduler."""
        from tieba_mecha.web.app import TiebaMechaApp
        assert not hasattr(TiebaMechaApp, "_batch_scheduler")

    def test_app_module_has_no_batch_scheduler_def(self):
        """Source of app.py should not define _batch_scheduler."""
        import inspect
        from tieba_mecha.web import app as app_module
        source = inspect.getsource(app_module)
        assert "def _batch_scheduler" not in source


# ========================================================================
# Fix 4: daemon.py — bare except replaced with except Exception
# ========================================================================


class TestDaemonBareExceptFixed:
    """daemon.py should not contain bare except: clauses."""

    def test_no_bare_except_in_source(self):
        """Source inspection: no bare 'except:' in daemon.py."""
        import inspect
        from tieba_mecha.core import daemon
        source = inspect.getsource(daemon)
        # 'except:' with no exception type (excluding 'except Exception:')
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped == "except:" or stripped.startswith("except: "):
                pytest.fail(f"Found bare except in daemon.py: {stripped}")


# ========================================================================
# Fix 5: post.py — docstring before imports, no duplicate httpx
# ========================================================================


class TestPostAddThreadFix:
    """add_thread should have proper docstring and no duplicate import httpx."""

    def test_add_thread_has_docstring(self):
        """add_thread.__doc__ should be the 发帖 docstring, not None."""
        from tieba_mecha.core.post import add_thread
        assert add_thread.__doc__ is not None
        assert "发帖" in add_thread.__doc__

    def test_no_duplicate_httpx_import(self):
        """Source of add_thread should only have one import httpx."""
        import inspect
        from tieba_mecha.core.post import add_thread
        source = inspect.getsource(add_thread)
        count = source.count("import httpx")
        assert count <= 1, f"Found {count} 'import httpx' in add_thread, expected <= 1"

    def test_no_import_asyncio_in_function(self):
        """add_thread should not re-import asyncio (module-level already)."""
        import inspect
        from tieba_mecha.core.post import add_thread
        source = inspect.getsource(add_thread)
        assert "import asyncio" not in source


# ========================================================================
# Fix 6: link_manager.py — self.client and close() removed
# ========================================================================


class TestLinkManagerCleanup:
    """SmartLinkConnector should not create unused self.client or have close()."""

    def test_no_self_client_in_init(self):
        """__init__ should not set self.client."""
        from tieba_mecha.core.link_manager import SmartLinkConnector
        import inspect
        init_source = inspect.getsource(SmartLinkConnector.__init__)
        assert "self.client" not in init_source

    def test_no_close_method(self):
        """SmartLinkConnector should not have close() method."""
        from tieba_mecha.core.link_manager import SmartLinkConnector
        assert not hasattr(SmartLinkConnector, "close")

    def test_methods_use_own_client(self):
        """test_connection should create its own client via async with."""
        from tieba_mecha.core.link_manager import SmartLinkConnector
        import inspect
        source = inspect.getsource(SmartLinkConnector.test_connection)
        assert "async with httpx.AsyncClient()" in source


# ========================================================================
# Fix 7: start_web.py — no hardcoded fallback key
# ========================================================================


class TestStartWebNoHardcodedKey:
    """start_web.py should not contain hardcoded fallback keys."""

    def test_no_cyber_mecha_secret(self):
        """Should not contain 'cyber_mecha_secret_777'."""
        source = (Path(__file__).parent.parent / "start_web.py").read_text(encoding="utf-8")
        assert "cyber_mecha_secret_777" not in source

    def test_no_fallback_secret_key(self):
        """Should not contain 'fallback_secret_key'."""
        source = (Path(__file__).parent.parent / "start_web.py").read_text(encoding="utf-8")
        assert '"fallback_secret_key"' not in source
        assert "'fallback_secret_key'" not in source

    def test_uses_secrets_module(self):
        """Should use secrets.token_hex for random key generation."""
        source = (Path(__file__).parent.parent / "start_web.py").read_text(encoding="utf-8")
        assert "secrets" in source and "token_hex" in source


# ========================================================================
# Fix 8: sign.py — sign_all_accounts uses single client per account
# ========================================================================


@pytest.mark.asyncio
class TestSignAllAccountsSingleClient:
    """sign_all_accounts should create one client per account, not per forum."""

    async def test_single_client_per_account(self):
        """create_client should be called once per account, not per forum."""
        from tieba_mecha.core import sign

        mock_db = AsyncMock()
        mock_account = MagicMock()
        mock_account.id = 1
        mock_account.name = "acc1"
        mock_account.proxy_id = None
        mock_db.get_matrix_accounts = AsyncMock(return_value=[mock_account])

        mock_forum1 = MagicMock()
        mock_forum1.id = 10
        mock_forum1.fname = "forum_a"
        mock_forum1.sign_count = 0
        mock_forum2 = MagicMock()
        mock_forum2.id = 11
        mock_forum2.fname = "forum_b"
        mock_forum2.sign_count = 0
        mock_db.get_forums = AsyncMock(return_value=[mock_forum1, mock_forum2])
        mock_db.add_sign_log = AsyncMock()
        mock_db.update_forum_sign = AsyncMock()

        mock_client = AsyncMock()
        mock_sign_result = MagicMock()
        mock_sign_result.__bool__ = lambda s: True
        mock_sign_result.err = None
        mock_client.sign_forum = AsyncMock(return_value=mock_sign_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("tieba_mecha.core.sign.get_account_credentials", new_callable=AsyncMock) as mock_creds, \
             patch("tieba_mecha.core.sign.create_client", new_callable=AsyncMock) as mock_create:

            mock_creds.return_value = (1, "bduss", "stoken", None, "cuid", "ua")
            mock_create.return_value = mock_client

            results = []
            async for r in sign.sign_all_accounts(mock_db, delay_min=0, delay_max=0, acc_delay_min=0, acc_delay_max=0):
                results.append(r)

            # 2 forums should have been signed
            assert len(results) == 2
            assert all(r["success"] for r in results)

            # Key assertion: create_client called only ONCE (1 account), not twice (2 forums)
            assert mock_create.call_count == 1


# ========================================================================
# Fix 9-11: bare except -> except Exception in UI pages
# ========================================================================


class TestNoBareExceptInPages:
    """UI page files should not contain bare except: clauses."""

    @pytest.mark.parametrize("module_path", [
        "tieba_mecha.web.pages.batch_post_page",
        "tieba_mecha.web.pages.crawl",
        "tieba_mecha.web.pages.sign",
    ])
    def test_no_bare_except(self, module_path):
        """Source should not contain bare 'except:' (without exception type)."""
        import importlib
        mod = importlib.import_module(module_path)
        import inspect
        source = inspect.getsource(mod)
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped == "except:" or (stripped.startswith("except:") and stripped[7:8] not in ("E", " ")):
                # Allow "except Exception:" etc.
                pass
            # Simpler check: line is exactly 'except:' or 'except: pass' with no type
            if stripped in ("except:", "except: pass"):
                pytest.fail(f"Found bare except in {module_path} line {i}: {stripped}")


# ========================================================================
# Fix 3: Verify daemon still has do_batch_post_tasks (not removed)
# ========================================================================


class TestDaemonStillHasBatchPost:
    """daemon.py should still have do_batch_post_tasks (we removed the duplicate in app.py, not daemon)."""

    def test_do_batch_post_tasks_exists(self):
        from tieba_mecha.core.daemon import do_batch_post_tasks
        assert callable(do_batch_post_tasks)

    def test_daemon_has_scheduler(self):
        from tieba_mecha.core.daemon import TiebaMechaDaemon
        d = TiebaMechaDaemon()
        assert hasattr(d, "scheduler")


# ========================================================================
# Fix 5: post.py add_thread — verify httpx is available where needed
# ========================================================================


@pytest.mark.asyncio
class TestPostAddThreadHttpxAvailable:
    """add_thread should still be able to use httpx at runtime."""

    async def test_add_thread_returns_on_no_creds(self):
        """Without credentials, add_thread should return gracefully, not crash on import."""
        mock_db = AsyncMock()
        with patch("tieba_mecha.core.post.get_account_credentials", new_callable=AsyncMock) as mock_creds:
            mock_creds.return_value = None
            from tieba_mecha.core.post import add_thread
            success, msg, tid = await add_thread(mock_db, "test_forum", "title", "content")
            assert success is False
            assert "未找到账号凭证" in msg
            assert tid == 0


# ========================================================================
# Integration: notification clear_all_read with proper DB
# ========================================================================


@pytest.mark.asyncio
class TestNotificationClearAllRead:
    """clear_all_read should work without import errors."""

    async def test_clear_all_read_returns_zero_when_no_db(self):
        """With no db set, should return 0 gracefully."""
        from tieba_mecha.core.notification import NotificationManager
        NotificationManager._instance = None  # reset singleton
        nm = NotificationManager(db=None)
        result = await nm.clear_all_read()
        assert result == 0

    async def test_clear_all_read_with_empty_db(self, db):
        """With empty notifications table, should return 0."""
        from tieba_mecha.core.notification import NotificationManager
        NotificationManager._instance = None  # reset singleton
        nm = NotificationManager(db=db)
        result = await nm.clear_all_read()
        assert result == 0

    async def test_clear_all_read_clears_read_notifications(self, db):
        """Should delete only read notifications."""
        from tieba_mecha.core.notification import NotificationManager
        NotificationManager._instance = None  # reset singleton
        nm = NotificationManager(db=db)

        # Add some notifications
        await db.add_notification(type="info", title="t1", message="m1")
        n2 = await db.add_notification(type="info", title="t2", message="m2")
        await db.mark_notification_read(n2.id)

        # Clear all read
        result = await nm.clear_all_read()
        assert result == 1

        # Verify: only 1 unread should remain
        remaining = await db.get_unread_notifications()
        assert len(remaining) == 1
        assert remaining[0].title == "t1"


# ========================================================================
# Integration: SmartLinkConnector works without self.client
# ========================================================================


class TestSmartLinkConnectorWorks:
    """SmartLinkConnector should work properly without self.client."""

    def test_instantiation_does_not_create_httpx_client(self):
        """__init__ should not create any httpx client."""
        mock_db = AsyncMock()
        from tieba_mecha.core.link_manager import SmartLinkConnector
        connector = SmartLinkConnector(mock_db)
        # Should not have client attribute
        assert not hasattr(connector, "client")

    @pytest.mark.asyncio
    async def test_get_active_shortlinks(self):
        """get_active_shortlinks should work with cached data."""
        mock_db = AsyncMock()
        mock_db.get_setting = AsyncMock(return_value='[{"shortCode":"ABC123","seoTitle":"Test"}]')

        from tieba_mecha.core.link_manager import SmartLinkConnector
        connector = SmartLinkConnector(mock_db)
        links = await connector.get_active_shortlinks()
        assert len(links) == 1
        assert links[0]["shortCode"] == "ABC123"
