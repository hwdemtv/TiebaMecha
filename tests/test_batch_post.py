"""Tests for batch post functionality."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tieba_mecha.core.batch_post import RateLimiter, BatchPostTask, BatchPostManager


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_rate_limiter_initialization(self):
        """Test RateLimiter initializes correctly."""
        limiter = RateLimiter(rpm=8)  # 实际默认 RPM 为 8（保守值）
        assert limiter.rpm == 8
        assert limiter.timestamps == []

    def test_rate_limiter_custom_rpm(self):
        """Test RateLimiter with custom RPM."""
        limiter = RateLimiter(rpm=30)
        assert limiter.rpm == 30

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_under_limit(self):
        """Test that requests under the limit are allowed immediately."""
        limiter = RateLimiter(rpm=5)

        # Should not wait for first few requests
        for _ in range(5):
            await limiter.wait_if_needed()

        assert len(limiter.timestamps) == 5

    @pytest.mark.asyncio
    async def test_rate_limiter_timestamp_cleanup(self):
        """Test that old timestamps are cleaned up."""
        limiter = RateLimiter(rpm=8)  # 保守值

        # Add some "old" timestamps (simulate time passing)
        import time
        old_time = time.time() - 120  # 2 minutes ago
        limiter.timestamps = [old_time, old_time + 30]

        # Trigger a wait check
        await limiter.wait_if_needed()

        # Old timestamps should be cleaned
        assert len(limiter.timestamps) == 1


class TestBatchPostTask:
    """Tests for BatchPostTask dataclass."""

    def test_batch_post_task_creation(self):
        """Test BatchPostTask creation with all fields."""
        task = BatchPostTask(
            id="test-task-1",
            fname="test_forum",
            titles=["Title 1", "Title 2"],
            contents=["Content 1"],
            accounts=[1, 2, 3],
            strategy="round_robin",
            total=10,
        )

        assert task.id == "test-task-1"
        assert task.fname == "test_forum"
        assert task.fnames == []
        assert task.titles == ["Title 1", "Title 2"]
        assert task.contents == ["Content 1"]
        assert task.accounts == [1, 2, 3]
        assert task.strategy == "round_robin"
        assert task.total == 10
        assert task.status == "pending"
        assert task.progress == 0

    def test_batch_post_task_default_values(self):
        """Test BatchPostTask default values."""
        task = BatchPostTask(id="test", fname="forum")

        assert task.fnames == []
        assert task.titles == []
        assert task.contents == []
        assert task.accounts == []
        assert task.strategy == "round_robin"
        assert task.delay_min == 60.0
        assert task.delay_max == 300.0
        assert task.use_ai is False
        assert task.status == "pending"
        assert task.progress == 0
        assert task.total == 0
        assert task.start_time is None

    def test_get_fnames_with_fnames_list(self):
        """Test get_fnames returns fnames when set."""
        task = BatchPostTask(
            id="test",
            fname="single_forum",
            fnames=["forum1", "forum2", "forum3"],
        )

        result = task.get_fnames()
        assert result == ["forum1", "forum2", "forum3"]

    def test_get_fnames_fallback_to_fname(self):
        """Test get_fnames falls back to fname when fnames is empty."""
        task = BatchPostTask(id="test", fname="single_forum")

        result = task.get_fnames()
        assert result == ["single_forum"]

    def test_get_fnames_empty_when_no_forum(self):
        """Test get_fnames returns empty list when no forum specified."""
        task = BatchPostTask(id="test", fname="")

        result = task.get_fnames()
        assert result == []

    def test_weight_override_default(self):
        """Test weight_override defaults to empty dict."""
        task = BatchPostTask(id="test", fname="forum")
        assert task.weight_override == {}


class TestBatchPostManager:
    """Tests for BatchPostManager class."""

    def test_batch_post_manager_initialization(self):
        """Test BatchPostManager initializes correctly."""
        manager = BatchPostManager(db=MagicMock())
        assert manager.db is not None
        assert manager._active_tasks == {}

    def test_weighted_choice_basic(self):
        """Test _weighted_choice returns a valid account ID."""
        manager = BatchPostManager(db=MagicMock())
        accounts_with_weights = [(1, 5), (2, 3), (3, 2)]

        result = manager._weighted_choice(accounts_with_weights)

        assert result in [1, 2, 3]

    def test_weighted_choice_distribution(self):
        """Test _weighted_choice roughly follows weight distribution."""
        manager = BatchPostManager(db=MagicMock())
        # Account 1 has weight 9, account 2 has weight 1
        accounts_with_weights = [(1, 9), (2, 1)]

        # Run many times and count results
        results = [manager._weighted_choice(accounts_with_weights) for _ in range(1000)]
        count_1 = results.count(1)
        count_2 = results.count(2)

        # Account 1 should be chosen much more often
        assert count_1 > count_2
        # Roughly 90% should be account 1 (allow some variance)
        assert count_1 > 800

    def test_weighted_choice_single_account(self):
        """Test _weighted_choice with single account."""
        manager = BatchPostManager(db=MagicMock())
        accounts_with_weights = [(42, 5)]

        result = manager._weighted_choice(accounts_with_weights)
        assert result == 42

    @pytest.mark.asyncio
    async def test_pick_account_round_robin(self):
        """Test _pick_account with round_robin strategy."""
        manager = BatchPostManager(db=MagicMock())
        task = BatchPostTask(
            id="test",
            fname="forum",
            accounts=[10, 20, 30],
            strategy="round_robin",
        )
        weights = [(10, 1), (20, 1), (30, 1)]

        # Round robin should cycle through accounts
        assert await manager._pick_account(task, 0, weights) == 10
        assert await manager._pick_account(task, 1, weights) == 20
        assert await manager._pick_account(task, 2, weights) == 30
        assert await manager._pick_account(task, 3, weights) == 10  # Wraps around

    @pytest.mark.asyncio
    async def test_pick_account_random(self):
        """Test _pick_account with random strategy."""
        manager = BatchPostManager(db=MagicMock())
        task = BatchPostTask(
            id="test",
            fname="forum",
            accounts=[10, 20, 30],
            strategy="random",
        )
        weights = [(10, 1), (20, 1), (30, 1)]

        # Random should always return a valid account ID
        for _ in range(10):
            result = await manager._pick_account(task, 0, weights)
            assert result in [10, 20, 30]

    @pytest.mark.asyncio
    async def test_pick_account_weighted(self):
        """Test _pick_account with weighted strategy."""
        manager = BatchPostManager(db=MagicMock())
        task = BatchPostTask(
            id="test",
            fname="forum",
            accounts=[10, 20, 30],
            strategy="weighted",
        )
        # Account 30 has highest weight
        weights = [(10, 1), (20, 1), (30, 8)]

        # Weighted should prefer account 30
        results = [await manager._pick_account(task, i, weights) for i in range(100)]
        count_30 = results.count(30)

        assert count_30 > 60  # Should be chosen most often

    @pytest.mark.asyncio
    async def test_pick_account_unknown_strategy_defaults_to_round_robin(self):
        """Test _pick_account with unknown strategy defaults to round_robin."""
        manager = BatchPostManager(db=MagicMock())
        task = BatchPostTask(
            id="test",
            fname="forum",
            accounts=[10, 20, 30],
            strategy="unknown_strategy",
        )
        weights = [(10, 1), (20, 1), (30, 1)]

        # Should behave like round_robin
        assert await manager._pick_account(task, 0, weights) == 10
        assert await manager._pick_account(task, 1, weights) == 20

    @pytest.mark.asyncio
    async def test_build_weighted_accounts_with_override(self):
        """Test _build_weighted_accounts uses weight_override."""
        mock_db = MagicMock()
        mock_db.get_accounts = AsyncMock(return_value=[])

        manager = BatchPostManager(db=mock_db)
        task = BatchPostTask(
            id="test",
            fname="forum",
            accounts=[1, 2, 3],
            weight_override={1: 10, 2: 5},  # Override weights for accounts 1 and 2
        )

        result = await manager._build_weighted_accounts(task)

        # Check that override weights are applied
        weights_dict = dict(result)
        assert weights_dict[1] == 10
        assert weights_dict[2] == 5
        # Account 3 should have default weight (from DB, which returns no accounts, so 5)
        assert weights_dict[3] == 5

    @pytest.mark.asyncio
    async def test_build_weighted_accounts_clamps_weights(self):
        """Test _build_weighted_accounts clamps weights to 1-10 range."""
        mock_db = MagicMock()
        mock_db.get_accounts = AsyncMock(return_value=[])

        manager = BatchPostManager(db=mock_db)
        task = BatchPostTask(
            id="test",
            fname="forum",
            accounts=[1, 2],
            weight_override={1: 100, 2: -5},  # Out of range weights
        )

        result = await manager._build_weighted_accounts(task)
        weights_dict = dict(result)

        # Weights should be clamped
        assert weights_dict[1] == 10  # Clamped to max
        assert weights_dict[2] == 1   # Clamped to min

    @pytest.mark.asyncio
    async def test_execute_task_no_forums(self):
        """Test execute_task yields failure when no forums specified."""
        mock_db = MagicMock()
        manager = BatchPostManager(db=mock_db)

        task = BatchPostTask(id="test", fname="", fnames=[], total=5)

        results = []
        async for result in manager.execute_task(task):
            results.append(result)

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert "未指定目标贴吧" in results[0]["msg"]

    @pytest.mark.asyncio
    async def test_execute_task_no_accounts(self):
        """Test execute_task yields failure when no accounts available."""
        mock_db = MagicMock()
        mock_db.get_active_account = AsyncMock(return_value=None)
        manager = BatchPostManager(db=mock_db)

        task = BatchPostTask(id="test", fname="test_forum", total=5)

        results = []
        async for result in manager.execute_task(task):
            results.append(result)

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert "未找到可用账号" in results[0]["msg"]
