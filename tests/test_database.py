"""Tests for database CRUD operations."""

import pytest
from datetime import datetime

from tieba_mecha.db.crud import Database
from tieba_mecha.db.models import Account, Forum, SignLog, Proxy, AutoRule, Setting


@pytest.mark.asyncio
class TestDatabase:
    """Tests for Database class."""

    async def test_database_initialization(self, temp_db_path):
        """Test database can be initialized."""
        db = Database(temp_db_path)
        import inspect
        print(f"DEBUG: Database class source file: {inspect.getfile(Database)}")
        await db.init_db()
        await db.close()

    async def test_close(self, db):
        """Test database can be closed."""
        await db.close()
        # Should not raise error


@pytest.mark.asyncio
class TestAccountCRUD:
    """Tests for Account CRUD operations."""

    async def test_add_account(self, db):
        """Test adding an account."""
        account = await db.add_account(
            name="test_account",
            bduss="test_bduss",
            stoken="test_stoken",
        )

        assert account.id is not None
        assert account.name == "test_account"
        assert account.bduss == "test_bduss"
        assert account.stoken == "test_stoken"
        assert account.is_active is True  # First account is active

    async def test_add_account_first_is_active(self, db):
        """Test first added account is automatically active."""
        acc1 = await db.add_account(name="first", bduss="bduss1")
        assert acc1.is_active is True

        acc2 = await db.add_account(name="second", bduss="bduss2")
        assert acc2.is_active is False

    async def test_add_account_auto_generates_cuid_and_ua(self, db):
        """Test account creation auto-generates cuid and user_agent."""
        account = await db.add_account(name="test", bduss="bduss")

        assert account.cuid != ""
        assert account.user_agent != ""
        assert "Mozilla" in account.user_agent

    async def test_get_accounts(self, db):
        """Test retrieving all accounts."""
        await db.add_account(name="acc1", bduss="bduss1")
        await db.add_account(name="acc2", bduss="bduss2")

        accounts = await db.get_accounts()

        assert len(accounts) == 2
        assert accounts[0].name == "acc1"
        assert accounts[1].name == "acc2"

    async def test_get_accounts_empty(self, db):
        """Test retrieving accounts when none exist."""
        accounts = await db.get_accounts()
        assert accounts == []

    async def test_get_active_account(self, db):
        """Test retrieving the active account."""
        acc1 = await db.add_account(name="acc1", bduss="bduss1")
        await db.add_account(name="acc2", bduss="bduss2")

        active = await db.get_active_account()

        assert active is not None
        assert active.id == acc1.id
        assert active.name == "acc1"

    async def test_get_active_account_none(self, db):
        """Test retrieving active account when none exists."""
        active = await db.get_active_account()
        assert active is None

    async def test_set_active_account(self, db):
        """Test setting active account."""
        acc1 = await db.add_account(name="acc1", bduss="bduss1")
        acc2 = await db.add_account(name="acc2", bduss="bduss2")

        await db.set_active_account(acc2.id)

        active = await db.get_active_account()
        assert active.id == acc2.id

    async def test_set_active_account_atomic(self, db):
        """Test that only one account is active after switching."""
        acc1 = await db.add_account(name="acc1", bduss="bduss1")
        acc2 = await db.add_account(name="acc2", bduss="bduss2")
        acc3 = await db.add_account(name="acc3", bduss="bduss3")

        await db.set_active_account(acc3.id)

        accounts = await db.get_accounts()
        active_count = sum(1 for a in accounts if a.is_active)
        assert active_count == 1

    async def test_delete_account(self, db):
        """Test deleting an account."""
        acc = await db.add_account(name="to_delete", bduss="bduss")

        result = await db.delete_account(acc.id)
        assert result is True

        accounts = await db.get_accounts()
        assert len(accounts) == 0

    async def test_delete_nonexistent_account(self, db):
        """Test deleting a non-existent account."""
        result = await db.delete_account(999)
        assert result is False

    async def test_delete_active_account_promotes_next(self, db):
        """Test deleting active account promotes next account."""
        acc1 = await db.add_account(name="acc1", bduss="bduss1")
        acc2 = await db.add_account(name="acc2", bduss="bduss2")

        # Delete active account (acc1)
        await db.delete_account(acc1.id)

        # acc2 should now be active
        active = await db.get_active_account()
        assert active.id == acc2.id

    async def test_update_account_status(self, db):
        """Test updating account status."""
        acc = await db.add_account(name="test", bduss="bduss")

        await db.update_account_status(acc.id, "active")

        accounts = await db.get_accounts()
        assert accounts[0].status == "active"
        assert accounts[0].last_verified is not None

    async def test_update_account(self, db):
        """Test updating account fields."""
        acc = await db.add_account(name="test", bduss="bduss")

        updated = await db.update_account(acc.id, name="new_name", user_id=12345)

        assert updated is not None
        assert updated.name == "new_name"
        assert updated.user_id == 12345

    async def test_update_nonexistent_account(self, db):
        """Test updating a non-existent account."""
        result = await db.update_account(999, name="new_name")
        assert result is None

    async def test_get_matrix_accounts(self, db):
        """Test getting matrix accounts (active and not suspended)."""
        acc1 = await db.add_account(name="acc1", bduss="bduss1")
        acc2 = await db.add_account(name="acc2", bduss="bduss2")
        acc3 = await db.add_account(name="acc3", bduss="bduss3")

        # Suspend acc3
        res = await db.update_account(acc3.id, status="suspended_proxy")
        # Verify it's really in DB
        async with db.async_session() as session:
            check = await session.get(Account, acc3.id)
            assert check.status == "suspended_proxy"

        # Deactivate acc1
        await db.set_active_account(acc2.id)

        matrix = await db.get_matrix_accounts()
        # Now Matrix includes all non-suspended accounts (acc1 and acc2)
        assert len(matrix) == 2
        ids = [a.id for a in matrix]
        assert acc2.id in ids
        assert acc1.id in ids

    async def test_get_accounts_by_proxy(self, db):
        """Test getting accounts by proxy ID."""
        acc1 = await db.add_account(name="acc1", bduss="bduss1", proxy_id=1)
        acc2 = await db.add_account(name="acc2", bduss="bduss2", proxy_id=1)
        acc3 = await db.add_account(name="acc3", bduss="bduss3", proxy_id=2)

        accounts = await db.get_accounts_by_proxy(1)

        assert len(accounts) == 2
        assert all(a.proxy_id == 1 for a in accounts)


@pytest.mark.asyncio
class TestForumCRUD:
    """Tests for Forum CRUD operations."""

    async def test_add_forum(self, db):
        """Test adding a forum."""
        acc = await db.add_account(name="test", bduss="bduss")

        forum = await db.add_forum(
            fid=12345,
            fname="test_forum",
            account_id=acc.id,
        )

        assert forum.id is not None
        assert forum.fid == 12345
        assert forum.fname == "test_forum"
        assert forum.account_id == acc.id

    async def test_add_forum_duplicate_ignored(self, db):
        """Test adding duplicate forum returns existing."""
        acc = await db.add_account(name="test", bduss="bduss")

        forum1 = await db.add_forum(fid=12345, fname="forum", account_id=acc.id)
        forum2 = await db.add_forum(fid=12345, fname="forum", account_id=acc.id)

        assert forum1.id == forum2.id

    async def test_get_forums(self, db):
        """Test retrieving forums."""
        acc = await db.add_account(name="test", bduss="bduss")
        await db.add_forum(fid=1, fname="forum1", account_id=acc.id)
        await db.add_forum(fid=2, fname="forum2", account_id=acc.id)

        forums = await db.get_forums()

        assert len(forums) == 2

    async def test_get_forums_by_account(self, db):
        """Test retrieving forums for specific account."""
        acc1 = await db.add_account(name="acc1", bduss="bduss1")
        acc2 = await db.add_account(name="acc2", bduss="bduss2")

        await db.add_forum(fid=1, fname="forum1", account_id=acc1.id)
        await db.add_forum(fid=2, fname="forum2", account_id=acc2.id)

        forums = await db.get_forums(acc1.id)

        assert len(forums) == 1
        assert forums[0].fname == "forum1"

    async def test_update_forum_sign(self, db):
        """Test updating forum sign status."""
        acc = await db.add_account(name="test", bduss="bduss")
        forum = await db.add_forum(fid=1, fname="forum", account_id=acc.id)

        await db.update_forum_sign(forum.id, success=True)

        forums = await db.get_forums()
        assert forums[0].is_sign_today is True
        assert forums[0].sign_count == 1

    async def test_update_forum_sign_increments_count(self, db):
        """Test that successful sign increments count."""
        acc = await db.add_account(name="test", bduss="bduss")
        forum = await db.add_forum(fid=1, fname="forum", account_id=acc.id, sign_count=5)

        await db.update_forum_sign(forum.id, success=True)

        forums = await db.get_forums()
        assert forums[0].sign_count == 6

    async def test_reset_daily_sign(self, db):
        """Test resetting daily sign status."""
        acc = await db.add_account(name="test", bduss="bduss")
        forum1 = await db.add_forum(fid=1, fname="forum1", account_id=acc.id)
        forum2 = await db.add_forum(fid=2, fname="forum2", account_id=acc.id)

        await db.update_forum_sign(forum1.id, success=True)
        await db.update_forum_sign(forum2.id, success=True)

        await db.reset_daily_sign()

        forums = await db.get_forums()
        assert all(not f.is_sign_today for f in forums)

    async def test_delete_forum(self, db):
        """Test deleting a forum."""
        acc = await db.add_account(name="test", bduss="bduss")
        forum = await db.add_forum(fid=1, fname="forum", account_id=acc.id)

        result = await db.delete_forum(forum.id)
        assert result is True

        forums = await db.get_forums()
        assert len(forums) == 0


@pytest.mark.asyncio
class TestSignLogCRUD:
    """Tests for SignLog CRUD operations."""

    async def test_add_sign_log(self, db):
        """Test adding a sign log."""
        log = await db.add_sign_log(
            forum_id=1,
            fname="test_forum",
            success=True,
            message="签到成功",
        )

        assert log.id is not None
        assert log.forum_id == 1
        assert log.fname == "test_forum"
        assert log.success is True
        assert log.message == "签到成功"

    async def test_get_sign_logs(self, db):
        """Test retrieving sign logs."""
        await db.add_sign_log(forum_id=1, fname="forum1", success=True)
        await db.add_sign_log(forum_id=2, fname="forum2", success=False)

        logs = await db.get_sign_logs()

        assert len(logs) == 2

    async def test_get_sign_logs_limit(self, db):
        """Test sign logs limit."""
        for i in range(20):
            await db.add_sign_log(forum_id=i, fname=f"forum{i}", success=True)

        logs = await db.get_sign_logs(limit=10)
        assert len(logs) == 10


@pytest.mark.asyncio
class TestProxyCRUD:
    """Tests for Proxy CRUD operations."""

    async def test_add_proxy(self, db):
        """Test adding a proxy."""
        proxy = await db.add_proxy(
            host="127.0.0.1",
            port=7890,
            username="user",
            password="pass",
            protocol="http",
        )

        assert proxy.id is not None
        assert proxy.host == "127.0.0.1"
        assert proxy.port == 7890
        assert proxy.protocol == "http"
        assert proxy.is_active is True
        # Password should be encrypted
        assert proxy.password != "pass"

    async def test_add_proxy_no_auth(self, db):
        """Test adding a proxy without authentication."""
        proxy = await db.add_proxy(
            host="127.0.0.1",
            port=7890,
        )

        assert proxy.username == ""
        assert proxy.password == ""

    async def test_get_proxy(self, db):
        """Test retrieving a proxy by ID."""
        created = await db.add_proxy(host="127.0.0.1", port=7890)

        proxy = await db.get_proxy(created.id)

        assert proxy is not None
        assert proxy.host == "127.0.0.1"

    async def test_get_active_proxies(self, db):
        """Test retrieving active proxies."""
        await db.add_proxy(host="proxy1", port=7890)
        await db.add_proxy(host="proxy2", port=7891)

        proxies = await db.get_active_proxies()

        assert len(proxies) == 2

    async def test_mark_proxy_fail(self, db):
        """Test marking proxy failure."""
        proxy = await db.add_proxy(host="127.0.0.1", port=7890)

        await db.mark_proxy_fail(proxy.id)

        updated = await db.get_proxy(proxy.id)
        assert updated.fail_count == 1
        assert updated.is_active is True

    async def test_mark_proxy_fail_deactivates_after_threshold(self, db):
        """Test proxy is deactivated after reaching fail threshold."""
        from tieba_mecha.db.crud import PROXY_FAIL_THRESHOLD

        proxy = await db.add_proxy(host="127.0.0.1", port=7890)

        # Fail the proxy threshold times
        for _ in range(PROXY_FAIL_THRESHOLD):
            await db.mark_proxy_fail(proxy.id)

        updated = await db.get_proxy(proxy.id)
        assert updated.is_active is False

    async def test_delete_proxy(self, db):
        """Test deleting a proxy."""
        proxy = await db.add_proxy(host="127.0.0.1", port=7890)

        result = await db.delete_proxy(proxy.id)
        assert result is True

        deleted = await db.get_proxy(proxy.id)
        assert deleted is None


@pytest.mark.asyncio
class TestSettingsCRUD:
    """Tests for Settings CRUD operations."""

    async def test_set_and_get_setting(self, db):
        """Test setting and getting a value."""
        await db.set_setting("test_key", "test_value")

        value = await db.get_setting("test_key")
        assert value == "test_value"

    async def test_get_setting_default(self, db):
        """Test getting non-existent setting returns default."""
        value = await db.get_setting("nonexistent", "default_value")
        assert value == "default_value"

    async def test_update_setting(self, db):
        """Test updating an existing setting."""
        await db.set_setting("key", "value1")
        await db.set_setting("key", "value2")

        value = await db.get_setting("key")
        assert value == "value2"


@pytest.mark.asyncio
class TestAutoRuleCRUD:
    """Tests for AutoRule CRUD operations."""

    async def test_add_auto_rule(self, db):
        """Test adding an auto rule."""
        rule = await db.add_auto_rule(
            fname="test_forum",
            rule_type="keyword",
            pattern="spam",
            action="delete",
        )

        assert rule.id is not None
        assert rule.fname == "test_forum"
        assert rule.rule_type == "keyword"
        assert rule.pattern == "spam"
        assert rule.action == "delete"
        assert rule.is_active is True

    async def test_get_auto_rules(self, db):
        """Test retrieving auto rules."""
        await db.add_auto_rule(fname="forum1", rule_type="keyword", pattern="a")
        await db.add_auto_rule(fname="forum2", rule_type="regex", pattern="b")

        rules = await db.get_auto_rules()
        assert len(rules) == 2

    async def test_get_auto_rules_by_forum(self, db):
        """Test retrieving rules for specific forum."""
        await db.add_auto_rule(fname="forum1", rule_type="keyword", pattern="a")
        await db.add_auto_rule(fname="forum2", rule_type="keyword", pattern="b")

        rules = await db.get_auto_rules(fname="forum1")
        assert len(rules) == 1
        assert rules[0].fname == "forum1"

    async def test_toggle_rule(self, db):
        """Test toggling rule active state."""
        rule = await db.add_auto_rule(fname="forum", rule_type="keyword", pattern="test")

        await db.toggle_rule(rule.id, is_active=False)

        rules = await db.get_auto_rules()
        assert rules[0].is_active is False

    async def test_delete_auto_rule(self, db):
        """Test deleting an auto rule."""
        rule = await db.add_auto_rule(fname="forum", rule_type="keyword", pattern="test")

        result = await db.delete_auto_rule(rule.id)
        assert result is True

        rules = await db.get_auto_rules()
        assert len(rules) == 0


@pytest.mark.asyncio
class TestAccountProxySuspension:
    """Tests for account suspension related to proxy failures."""

    async def test_suspend_accounts_for_proxy(self, db):
        """Test suspending accounts when proxy fails."""
        proxy = await db.add_proxy(host="127.0.0.1", port=7890)
        acc1 = await db.add_account(name="acc1", bduss="bduss1", proxy_id=proxy.id)
        acc2 = await db.add_account(name="acc2", bduss="bduss2", proxy_id=proxy.id)

        suspended = await db.suspend_accounts_for_proxy(proxy.id, "代理失效")

        assert len(suspended) == 2
        for acc in suspended:
            assert acc.status == "suspended_proxy"
            assert acc.suspended_reason == "代理失效"

    async def test_restore_accounts_for_proxy(self, db):
        """Test restoring accounts when proxy recovers."""
        proxy = await db.add_proxy(host="127.0.0.1", port=7890)
        acc = await db.add_account(name="acc", bduss="bduss", proxy_id=proxy.id)

        await db.suspend_accounts_for_proxy(proxy.id, "代理失效")
        restored = await db.restore_accounts_for_proxy(proxy.id)

        assert len(restored) == 1
        assert restored[0].status == "active"
        assert restored[0].suspended_reason == ""
