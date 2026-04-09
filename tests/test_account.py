"""Tests for account management functionality."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tieba_mecha.core.account import (
    encrypt_value,
    decrypt_value,
    get_encryption_key,
    parse_cookie,
    add_account,
    get_account_credentials,
    list_accounts,
    switch_account,
    remove_account,
    AccountInfo,
)


class TestEncryption:
    """Tests for encryption and decryption functions."""

    def test_get_encryption_key_returns_bytes(self):
        """Test that get_encryption_key returns bytes."""
        key = get_encryption_key()
        assert isinstance(key, bytes)
        assert len(key) > 0

    def test_encrypt_decrypt_roundtrip(self):
        """Test that encryption and decryption work correctly."""
        original = "test_secret_value_123"
        encrypted = encrypt_value(original)

        # Encrypted value should be different from original
        assert encrypted != original

        # Decryption should recover original value
        decrypted = decrypt_value(encrypted)
        assert decrypted == original

    def test_encrypt_different_values_produce_different_outputs(self):
        """Test that different inputs produce different encrypted outputs."""
        value1 = "secret1"
        value2 = "secret2"

        encrypted1 = encrypt_value(value1)
        encrypted2 = encrypt_value(value2)

        assert encrypted1 != encrypted2

    def test_encrypt_same_value_produces_different_outputs(self):
        """Test that encrypting the same value twice produces different outputs (due to IV)."""
        value = "same_secret"

        encrypted1 = encrypt_value(value)
        encrypted2 = encrypt_value(value)

        # Fernet uses IV, so same plaintext should produce different ciphertext
        assert encrypted1 != encrypted2

        # But both should decrypt to the same value
        assert decrypt_value(encrypted1) == value
        assert decrypt_value(encrypted2) == value

    def test_decrypt_invalid_value_raises_error(self):
        """Test that decrypting an invalid value raises an error."""
        with pytest.raises(Exception):
            decrypt_value("invalid_encrypted_value")


class TestParseCookie:
    """Tests for cookie parsing functionality."""

    def test_parse_cookie_extracts_bduss(self):
        """Test that BDUSS is correctly extracted from cookie string."""
        cookie = "BDUSS=abc123def456; other=value"
        bduss, stoken = parse_cookie(cookie)
        assert bduss == "abc123def456"
        assert stoken == ""

    def test_parse_cookie_extracts_stoken(self):
        """Test that STOKEN is correctly extracted from cookie string."""
        cookie = "BDUSS=abc123; STOKEN=xyz789; other=value"
        bduss, stoken = parse_cookie(cookie)
        assert bduss == "abc123"
        assert stoken == "xyz789"

    def test_parse_cookie_handles_missing_stoken(self):
        """Test that missing STOKEN returns empty string."""
        cookie = "BDUSS=abc123"
        bduss, stoken = parse_cookie(cookie)
        assert bduss == "abc123"
        assert stoken == ""

    def test_parse_cookie_handles_empty_string(self):
        """Test that empty cookie string returns empty values."""
        bduss, stoken = parse_cookie("")
        assert bduss == ""
        assert stoken == ""

    def test_parse_cookie_handles_malformed_cookie(self):
        """Test that malformed cookie returns empty values."""
        bduss, stoken = parse_cookie("not_a_valid_cookie")
        assert bduss == ""
        assert stoken == ""

    def test_parse_cookie_handles_semicolon_after_value(self):
        """Test parsing with semicolon after the value."""
        cookie = "BDUSS=value_with_semicolon; STOKEN=another;"
        bduss, stoken = parse_cookie(cookie)
        assert bduss == "value_with_semicolon"
        assert stoken == "another"


class TestAccountInfo:
    """Tests for AccountInfo dataclass."""

    def test_account_info_creation(self):
        """Test that AccountInfo can be created with all fields."""
        info = AccountInfo(
            id=1,
            name="test",
            user_id=123,
            user_name="testuser",
            is_active=True,
            status="active",
            cuid="abc123",
            user_agent="Mozilla/5.0",
            proxy_id=None,
        )

        assert info.id == 1
        assert info.name == "test"
        assert info.user_id == 123
        assert info.user_name == "testuser"
        assert info.is_active is True
        assert info.status == "active"
        assert info.cuid == "abc123"
        assert info.user_agent == "Mozilla/5.0"
        assert info.proxy_id is None

    def test_account_info_default_values(self):
        """Test that AccountInfo has correct default values."""
        info = AccountInfo(
            id=1,
            name="test",
            user_id=123,
            user_name="testuser",
            is_active=True,
        )

        assert info.status == "unknown"
        assert info.cuid == ""
        assert info.user_agent == ""
        assert info.proxy_id is None


@pytest.mark.asyncio
class TestAccountManagement:
    """Tests for account management functions."""

    async def test_add_account(self, db, sample_account_data):
        """Test adding a new account."""
        account = await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        assert account.id is not None
        assert account.name == sample_account_data["name"]
        # First account should be active
        assert account.is_active is True

    async def test_add_multiple_accounts(self, db):
        """Test adding multiple accounts."""
        acc1 = await add_account(db, "account1", "bduss1", "stoken1")
        acc2 = await add_account(db, "account2", "bduss2", "stoken2")

        assert acc1.is_active is True  # First account is active
        assert acc2.is_active is False  # Second account is not active

    async def test_get_account_credentials(self, db, sample_account_data):
        """Test retrieving account credentials."""
        await add_account(
            db=db,
            name=sample_account_data["name"],
            bduss=sample_account_data["bduss"],
            stoken=sample_account_data["stoken"],
        )

        creds = await get_account_credentials(db)

        assert creds is not None
        acc_id, bduss, stoken, proxy_id, cuid, ua = creds
        assert bduss == sample_account_data["bduss"]
        assert stoken == sample_account_data["stoken"]

    async def test_get_account_credentials_no_account(self, db):
        """Test getting credentials when no account exists."""
        creds = await get_account_credentials(db)
        assert creds is None

    async def test_list_accounts(self, db):
        """Test listing all accounts."""
        await add_account(db, "acc1", "bduss1", "stoken1")
        await add_account(db, "acc2", "bduss2", "stoken2")

        accounts = await list_accounts(db)

        assert len(accounts) == 2
        assert accounts[0].name == "acc1"
        assert accounts[1].name == "acc2"

    async def test_switch_account(self, db):
        """Test switching active account."""
        acc1 = await add_account(db, "acc1", "bduss1", "stoken1")
        acc2 = await add_account(db, "acc2", "bduss2", "stoken2")

        # acc1 should be active initially
        assert acc1.is_active is True

        # Switch to acc2
        result = await switch_account(db, acc2.id)
        assert result is True

        # Verify acc2 is now active
        creds = await get_account_credentials(db)
        assert creds is not None
        _, bduss, _, _, _, _ = creds
        assert bduss == "bduss2"

    async def test_remove_account(self, db):
        """Test removing an account."""
        acc = await add_account(db, "to_delete", "bduss", "stoken")

        result = await remove_account(db, acc.id)
        assert result is True

        accounts = await list_accounts(db)
        assert len(accounts) == 0

    async def test_remove_nonexistent_account(self, db):
        """Test removing a non-existent account returns False."""
        result = await remove_account(db, 999)
        assert result is False

    async def test_remove_active_account_promotes_next(self, db):
        """Test that removing active account promotes next account."""
        acc1 = await add_account(db, "acc1", "bduss1", "stoken1")
        acc2 = await add_account(db, "acc2", "bduss2", "stoken2")

        # Remove active account (acc1)
        await remove_account(db, acc1.id)

        # acc2 should now be active
        accounts = await list_accounts(db)
        assert len(accounts) == 1
        assert accounts[0].is_active is True
