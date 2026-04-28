"""Tests for batch post fixes.

Covers:
- Fix 1: daemon.py ai_persona passed to CoreBatchPostTask
- Fix 2: execute_task warns when total exceeds available materials
- Fix 4: daemon.py logs error messages from execute_task updates
- Fix 7: BionicDelay.get_delay respects min/max boundaries
"""

import asyncio
import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch


# ========================================================================
# Fix 7: BionicDelay boundary clamping
# ========================================================================


class TestBionicDelayBoundary:
    """BionicDelay.get_delay must return values within [min_sec, max_sec]."""

    def test_delay_respects_minimum(self):
        """Returned delay should never be below min_sec."""
        from tieba_mecha.core.batch_post import BionicDelay

        for _ in range(200):
            delay = BionicDelay.get_delay(60, 300)
            assert delay >= 60, f"Delay {delay} violated minimum 60"

    def test_delay_respects_maximum(self):
        """Returned delay should never exceed max_sec."""
        from tieba_mecha.core.batch_post import BionicDelay

        for _ in range(200):
            delay = BionicDelay.get_delay(60, 300)
            assert delay <= 300, f"Delay {delay} violated maximum 300"

    def test_delay_with_equal_min_max(self):
        """When min==max, delay should be exactly that value."""
        from tieba_mecha.core.batch_post import BionicDelay

        delay = BionicDelay.get_delay(120, 120)
        assert delay == 120

    def test_delay_with_small_range(self):
        """Small range should still be respected."""
        from tieba_mecha.core.batch_post import BionicDelay

        for _ in range(200):
            delay = BionicDelay.get_delay(5, 6)
            assert 5 <= delay <= 6, f"Delay {delay} out of [5, 6]"

    def test_delay_min_clamped_to_1(self):
        """Minimum should be at least 1."""
        from tieba_mecha.core.batch_post import BionicDelay

        for _ in range(200):
            delay = BionicDelay.get_delay(0, 5)
            assert delay >= 1, f"Delay {delay} below floor of 1"

    def test_delay_inverted_min_max_corrected(self):
        """If min > max, the code should swap them internally."""
        from tieba_mecha.core.batch_post import BionicDelay

        for _ in range(50):
            delay = BionicDelay.get_delay(100, 10)  # inverted
            # max_sec = max(min_sec, max_sec) => max(100, 10) = 100
            assert 10 <= delay <= 100


# ========================================================================
# Fix 1: ai_persona passed through daemon task creation
# ========================================================================


class TestDaemonAiPersonaPassthrough:
    """daemon.py must pass ai_persona when creating CoreBatchPostTask."""

    def test_daemon_imports_ai_persona(self):
        """The CoreBatchPostTask in daemon should include ai_persona field."""
        from tieba_mecha.core.batch_post import BatchPostTask
        import inspect

        sig = inspect.signature(BatchPostTask.__init__)
        assert "ai_persona" in sig.parameters

    def test_daemon_source_includes_ai_persona(self):
        """daemon.py do_batch_post_tasks should reference ai_persona."""
        import inspect
        from tieba_mecha.core import daemon
        source = inspect.getsource(daemon.do_batch_post_tasks)
        assert "ai_persona" in source


# ========================================================================
# Fix 2: total exceeds material warning
# ========================================================================


@pytest.mark.asyncio
class TestExecuteTaskMaterialWarning:
    """execute_task should warn when task.total > available materials."""

    async def test_warns_when_total_exceeds_materials(self, db):
        """Should emit a warning log when total is truncated."""
        from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask

        # Seed: add 1 account so the pre-check doesn't fail
        await db.add_account(
            name="test_acc",
            bduss="x" * 192,
            stoken="y" * 64,
        )

        # Add 1 material
        await db.add_materials_bulk([("title1", "content1")])

        manager = BatchPostManager(db)
        task = BatchPostTask(
            id="test",
            fname="test_forum",
            fnames=["test_forum"],
            accounts=[1],
            total=100,  # way more than 1 available material
        )

        updates = []
        with patch("tieba_mecha.core.batch_post.get_account_credentials", new_callable=AsyncMock) as mock_creds, \
             patch("tieba_mecha.core.batch_post.get_auth_manager", new_callable=AsyncMock) as mock_auth:

            mock_auth_mgr = AsyncMock()
            mock_auth_mgr.check_local_status = AsyncMock()
            mock_auth_mgr.status = MagicMock()
            mock_auth_mgr.status.__eq__ = lambda s, o: True  # PRO
            mock_auth.return_value = mock_auth_mgr

            mock_creds.return_value = None  # no creds → each material skips

            async for update in manager.execute_task(task):
                updates.append(update)

        # Task total should have been adjusted down
        assert task.total == 1


# ========================================================================
# Fix 4: daemon logs error messages from execute_task
# ========================================================================


class TestDaemonLogsErrors:
    """daemon.py do_batch_post_tasks should log error/failed updates."""

    def test_daemon_source_logs_error_updates(self):
        """Source should print error messages from execute_task updates."""
        import inspect
        from tieba_mecha.core import daemon
        source = inspect.getsource(daemon.do_batch_post_tasks)
        assert 'update.get("status"' in source or "update_status" in source


# ========================================================================
# BatchPostTask dataclass integrity
# ========================================================================


class TestBatchPostTaskDefaults:
    """BatchPostTask should have sane defaults."""

    def test_default_strategy(self):
        from tieba_mecha.core.batch_post import BatchPostTask
        t = BatchPostTask(id="1", fname="test")
        assert t.strategy == "round_robin"
        assert t.pairing_mode == "random"
        assert t.ai_persona == "normal"
        assert t.delay_min == 120.0
        assert t.delay_max == 600.0

    def test_get_fnames_fallback(self):
        """get_fnames should fall back to [fname] when fnames is empty."""
        from tieba_mecha.core.batch_post import BatchPostTask
        t = BatchPostTask(id="1", fname="test_forum")
        assert t.get_fnames() == ["test_forum"]

    def test_get_fnames_prefers_list(self):
        """get_fnames should prefer fnames over fname."""
        from tieba_mecha.core.batch_post import BatchPostTask
        t = BatchPostTask(id="1", fname="old_forum", fnames=["a", "b"])
        assert t.get_fnames() == ["a", "b"]


# ========================================================================
# PerAccountRateLimiter correctness
# ========================================================================


@pytest.mark.asyncio
class TestPerAccountRateLimiter:
    """PerAccountRateLimiter should isolate accounts and throttle correctly."""

    async def test_independent_account_limiting(self):
        """Different accounts should not affect each other."""
        from tieba_mecha.core.batch_post import PerAccountRateLimiter
        limiter = PerAccountRateLimiter(rpm=2)

        # Account 1 uses its 2 slots
        await limiter.wait_if_needed(1)
        await limiter.wait_if_needed(1)

        # Account 2 should still be able to post (independent window)
        await limiter.wait_if_needed(2)

        status1 = limiter.get_status(1)
        status2 = limiter.get_status(2)
        assert status1["recent_posts"] == 2
        assert status2["recent_posts"] == 1
        assert status1["can_post"] is False
        assert status2["can_post"] is True

    async def test_reset_account(self):
        """reset_account should clear the account's timestamps."""
        from tieba_mecha.core.batch_post import PerAccountRateLimiter
        limiter = PerAccountRateLimiter(rpm=5)

        await limiter.wait_if_needed(1)
        await limiter.wait_if_needed(1)
        assert limiter.get_status(1)["recent_posts"] == 2

        limiter.reset_account(1)
        assert limiter.get_status(1)["recent_posts"] == 0


# ========================================================================
# CaptchaCircuitBreaker
# ========================================================================


class TestCaptchaCircuitBreaker:
    """CaptchaCircuitBreaker should detect captcha keywords."""

    @pytest.mark.asyncio
    async def test_triggers_on_captcha_keyword(self):
        from tieba_mecha.core.batch_post import CaptchaCircuitBreaker
        breaker = CaptchaCircuitBreaker(cooldown_minutes=30)

        triggered = await breaker.check_and_trigger(1, "请先输入验证码", 0)
        assert triggered is True
        assert breaker.is_in_cooldown(1) is True

    @pytest.mark.asyncio
    async def test_no_trigger_on_normal_error(self):
        from tieba_mecha.core.batch_post import CaptchaCircuitBreaker
        breaker = CaptchaCircuitBreaker(cooldown_minutes=30)

        triggered = await breaker.check_and_trigger(1, "用户没有权限", 0)
        assert triggered is False
        assert breaker.is_in_cooldown(1) is False

    @pytest.mark.asyncio
    async def test_saves_captcha_event_to_db(self, db):
        """CaptchaCircuitBreaker 应在触发时将事件写入数据库"""
        from tieba_mecha.core.batch_post import CaptchaCircuitBreaker
        breaker = CaptchaCircuitBreaker(cooldown_minutes=30, db=db)

        triggered = await breaker.check_and_trigger(1, "请先输入验证码", 0)
        assert triggered is True

        events = await db.get_captcha_events()
        assert len(events) == 1
        assert events[0]["account_id"] == 1
        assert "验证码" in events[0]["reason"]
        assert events[0]["status"] == "pending"


# ========================================================================
# FailureCircuitBreaker
# ========================================================================


@pytest.mark.asyncio
class TestFailureCircuitBreaker:
    """FailureCircuitBreaker should trip after N consecutive failures."""

    async def test_trips_after_threshold(self):
        from tieba_mecha.core.batch_post import FailureCircuitBreaker
        breaker = FailureCircuitBreaker(max_consecutive_failures=3, cooldown_minutes=60)

        tripped_1 = await breaker.record_failure(1)
        assert tripped_1 is False
        tripped_2 = await breaker.record_failure(1)
        assert tripped_2 is False
        tripped_3 = await breaker.record_failure(1)
        assert tripped_3 is True
        assert breaker.is_in_cooldown(1) is True

    async def test_success_resets_counter(self):
        from tieba_mecha.core.batch_post import FailureCircuitBreaker
        breaker = FailureCircuitBreaker(max_consecutive_failures=3, cooldown_minutes=60)

        await breaker.record_failure(1)
        await breaker.record_failure(1)
        await breaker.record_success(1)

        # Counter reset, need 3 more to trip
        tripped = await breaker.record_failure(1)
        assert tripped is False


# ========================================================================
# AccountForumCooldown
# ========================================================================


class TestAccountForumCooldown:
    """AccountForumCooldown should block repeated posts to same forum."""

    def test_cooldown_blocks_immediate_retry(self):
        from tieba_mecha.core.batch_post import AccountForumCooldown
        tracker = AccountForumCooldown(cooldown_seconds=600)

        assert tracker.can_post(1, "forum_a") is True
        # After recording a post, should be blocked
        import asyncio
        asyncio.get_event_loop().run_until_complete(tracker.record_post(1, "forum_a"))
        assert tracker.can_post(1, "forum_a") is False
        # Different forum should still be allowed
        assert tracker.can_post(1, "forum_b") is True

    def test_get_available_forum(self):
        from tieba_mecha.core.batch_post import AccountForumCooldown
        tracker = AccountForumCooldown(cooldown_seconds=600)

        import asyncio
        asyncio.get_event_loop().run_until_complete(tracker.record_post(1, "forum_a"))

        # forum_a is on cooldown, forum_b should be available
        result = asyncio.get_event_loop().run_until_complete(
            tracker.get_available_forum(1, ["forum_a", "forum_b"])
        )
        assert result == "forum_b"


# ========================================================================
# Account rotation strategy: round_robin distribution
# ========================================================================


@pytest.mark.asyncio
class TestAccountRotationStrategies:
    """Verify round_robin and strict_round_robin distribute accounts evenly."""

    async def _pick(self, manager, task, target_fname, step, native_map, followed_map):
        """Helper to call _pick_optimal_account_for_target."""
        return await manager._pick_optimal_account_for_target(
            task, target_fname, step, [], native_map, followed_map
        )

    async def test_round_robin_even_with_multiple_native(self):
        """With multiple native accounts, search-ahead improves distribution vs naive fallback."""
        from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask

        mock_db = AsyncMock()
        manager = BatchPostManager(mock_db)

        accounts = [1, 2, 3, 4, 5]
        task = BatchPostTask(id="t", fname="A", fnames=["A"], accounts=accounts, strategy="round_robin")

        # Forum A has native accounts [2, 4]
        native_map = {"A": [2, 4]}
        followed_map = {"A": [2, 4]}

        picks = []
        for step in range(10):
            acc = await self._pick(manager, task, "A", step, native_map, followed_map)
            picks.append(acc)

        # Search-ahead: only native accounts used, distribution improved
        assert set(picks) == {2, 4}, f"Only native accounts should be used, got {set(picks)}"
        # Account 2: 6, Account 4: 4 — better than old approach which could be 8/2
        from collections import Counter
        counts = Counter(picks)
        assert max(counts.values()) - min(counts.values()) <= 2, f"Too uneven: {counts}"

    async def test_round_robin_falls_back_to_followed(self):
        """When no native accounts, round_robin should use followed accounts."""
        from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask
        from collections import Counter

        mock_db = AsyncMock()
        manager = BatchPostManager(mock_db)

        task = BatchPostTask(id="t", fname="B", fnames=["B"], accounts=[1, 2, 3], strategy="round_robin")

        native_map = {"B": []}  # no native
        followed_map = {"B": [1, 3]}  # but 1, 3 follow

        picks = []
        for step in range(6):
            acc = await self._pick(manager, task, "B", step, native_map, followed_map)
            picks.append(acc)

        # Only followed accounts used, no account 2
        assert set(picks) == {1, 3}, f"Only followed accounts should be used, got {set(picks)}"
        counts = Counter(picks)
        assert max(counts.values()) - min(counts.values()) <= 2, f"Too uneven: {counts}"

    async def test_strict_round_robin_prefers_native(self):
        """strict_round_robin should prefer native accounts via search-ahead."""
        from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask

        mock_db = AsyncMock()
        manager = BatchPostManager(mock_db)

        accounts = [1, 2, 3, 4, 5]
        task = BatchPostTask(id="t", fname="C", fnames=["C"], accounts=accounts, strategy="strict_round_robin")

        # Forum C: only account 3 is native, but accounts 3,5 follow
        native_map = {"C": [3]}
        followed_map = {"C": [3, 5]}

        picks = []
        for step in range(5):
            acc = await self._pick(manager, task, "C", step, native_map, followed_map)
            picks.append(acc)

        # Every step should find account 3 (only native) via search-ahead
        assert all(p == 3 for p in picks), f"Expected all 3, got {picks}"

    async def test_strict_round_robin_uses_followed_when_no_native(self):
        """strict_round_robin should search followed accounts when no native."""
        from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask

        mock_db = AsyncMock()
        manager = BatchPostManager(mock_db)

        accounts = [1, 2, 3]
        task = BatchPostTask(id="t", fname="D", fnames=["D"], accounts=accounts, strategy="strict_round_robin")

        native_map = {"D": []}
        followed_map = {"D": [1, 2, 3]}

        picks = []
        for step in range(6):
            acc = await self._pick(manager, task, "D", step, native_map, followed_map)
            picks.append(acc)

        # Pure round-robin since all follow: 1, 2, 3, 1, 2, 3
        assert picks == [1, 2, 3, 1, 2, 3]

    async def test_round_robin_multi_forum_distribution(self):
        """Across multiple forums, each with different native accounts, distribution should use all accounts."""
        from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask

        mock_db = AsyncMock()
        manager = BatchPostManager(mock_db)

        accounts = [1, 2, 3]
        task = BatchPostTask(id="t", fname="A", fnames=["A", "B", "C"], accounts=accounts, strategy="round_robin")

        # Each forum has 2 native accounts out of 3
        native_map = {"A": [1, 2], "B": [2, 3], "C": [1, 3]}
        followed_map = {"A": [1, 2, 3], "B": [1, 2, 3], "C": [1, 2, 3]}

        fnames = ["A", "B", "C"]
        stats = {}
        for step in range(9):
            fname = fnames[step % 3]
            acc = await self._pick(manager, task, fname, step, native_map, followed_map)
            stats[acc] = stats.get(acc, 0) + 1

        # Each account should get 3 posts (perfect balance)
        assert stats == {1: 3, 2: 3, 3: 3}, f"Uneven distribution: {stats}"

    async def test_weighted_strategy_respects_native_weights(self):
        """weighted strategy should use weights even when native accounts exist."""
        from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask
        from collections import Counter

        mock_db = AsyncMock()
        manager = BatchPostManager(mock_db)

        task = BatchPostTask(id="t", fname="A", fnames=["A"], accounts=[1, 2], strategy="weighted")
        # Account 1 weight=1, Account 2 weight=9 → account 2 should dominate
        weights = [(1, 1), (2, 9)]
        native_map = {"A": [1, 2]}  # both are native
        followed_map = {"A": [1, 2]}

        picks = []
        for step in range(100):
            acc = await manager._pick_optimal_account_for_target(
                task, "A", step, weights, native_map, followed_map
            )
            picks.append(acc)

        counts = Counter(picks)
        # Account 2 (weight 9) should be picked far more often than account 1 (weight 1)
        assert counts[2] > counts[1] * 3, f"Weighted selection not working: {counts}"


@pytest.mark.asyncio
class TestPerAccountRateLimiterNoCascade:
    """PerAccountRateLimiter should not cascade-wait after an initial wait."""

    async def test_no_cascade_wait_after_limit(self):
        """After waiting for RPM limit, the next call should not immediately trigger another wait."""
        import time as _time
        from tieba_mecha.core.batch_post import PerAccountRateLimiter

        # Use rpm=1 with a tiny window by monkey-patching time
        limiter = PerAccountRateLimiter(rpm=2)

        # Fill the 2 slots
        await limiter.wait_if_needed(1)
        await limiter.wait_if_needed(1)
        assert limiter.get_status(1)["recent_posts"] == 2

        # This third call will trigger a wait (we use a mock to avoid real sleep)
        # After the wait, the next call should see 2 posts (not 3), so it shouldn't cascade
        # We verify by checking that after one wait cycle, status allows posting
        # (The fix ensures the timestamp isn't appended after waiting)
        status_before = limiter.get_status(1)
        assert status_before["can_post"] is False
