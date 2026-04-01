"""Tests for tieba_mecha package structure."""

import pytest


class TestPackageStructure:
    """Test that the package is correctly structured."""

    def test_import_package(self):
        """Test that the main package can be imported."""
        import tieba_mecha

        assert hasattr(tieba_mecha, "__version__")

    def test_import_core_modules(self):
        """Test that core modules can be imported."""
        from tieba_mecha.core import account, sign, post, crawl, batch_post, proxy

        assert account is not None
        assert sign is not None
        assert post is not None
        assert crawl is not None
        assert batch_post is not None
        assert proxy is not None

    def test_import_db_modules(self):
        """Test that database modules can be imported."""
        from tieba_mecha.db import models, crud

        assert models is not None
        assert crud is not None
