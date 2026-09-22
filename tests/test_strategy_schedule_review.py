"""2026-09-22 策略与排期页审查修复回归测试。

覆盖：weekly 首轮对齐所选星期（P1）、预检有效账号集用于建任务（P2）、
终态集合与引擎统一（P2）、短链通道移除无残留。
"""

from datetime import datetime

from tieba_mecha.web.pages.batch_post.launch_config import (
    LaunchConfig,
    calc_next_weekly,
)
from tieba_mecha.web.pages.batch_post.preflight import (
    PreflightReport,
    PreflightService,
    _TERMINAL_ACCOUNT_STATUSES,
)


class TestCalcNextWeekly:
    NOW = datetime(2026, 9, 21, 12, 0)  # 2026-09-21 周一

    def test_monday_select_friday_lands_on_friday(self):
        r = calc_next_weekly(self.NOW, 4, 10, 0)
        assert r == datetime(2026, 9, 25, 10, 0)
        assert r.weekday() == 4

    def test_same_day_future_time_returns_today(self):
        fri_am = datetime(2026, 9, 25, 9, 0)
        r = calc_next_weekly(fri_am, 4, 10, 0)
        assert r == datetime(2026, 9, 25, 10, 0)

    def test_same_day_past_time_rolls_next_week(self):
        fri_pm = datetime(2026, 9, 25, 11, 0)
        r = calc_next_weekly(fri_pm, 4, 10, 0)
        assert r == datetime(2026, 10, 2, 10, 0)

    def test_sunday_select_monday_rolls_to_next_monday(self):
        sun = datetime(2026, 9, 27, 8, 0)  # 周日
        r = calc_next_weekly(sun, 0, 9, 30)
        assert r == datetime(2026, 9, 28, 9, 30)
        assert r.weekday() == 0


class TestPreflightTerminalSetUnified:
    def test_terminal_set_matches_engine(self):
        from tieba_mecha.core.batch_post import TERMINAL_ACCOUNT_STATUSES

        assert _TERMINAL_ACCOUNT_STATUSES == TERMINAL_ACCOUNT_STATUSES
        assert "suspended_proxy" in _TERMINAL_ACCOUNT_STATUSES

    async def test_suspended_proxy_excluded_from_effective(self, db):
        acc1 = await db.add_account(name="ok", bduss="b" * 192, stoken="s" * 64)
        await db.update_account(acc1.id, status="active")
        acc2 = await db.add_account(name="proxied", bduss="c" * 192, stoken="s" * 64)
        await db.update_account(acc2.id, status="suspended_proxy")

        config = LaunchConfig(account_ids=[acc1.id, acc2.id])
        svc = PreflightService(db)
        report = PreflightReport()
        info = await svc._check_accounts(config, report, report.stats)

        assert info["effective"] == [acc1.id]
        assert report.effective_account_ids == [acc1.id]


class TestShortLinkRemoval:
    def test_no_shortlink_residual_in_launch_page(self):
        import inspect

        from tieba_mecha.web.pages import batch_post_page

        source = inspect.getsource(batch_post_page)
        assert "shortlink" not in source.lower()
        assert "SmartLinkConnector" not in source
        assert "_obfuscate_link" not in source

    def test_launch_uses_preflight_effective_accounts(self):
        import inspect

        from tieba_mecha.web.pages import batch_post_page

        source = inspect.getsource(batch_post_page)
        assert "selected_accounts = report.effective_account_ids" in source
