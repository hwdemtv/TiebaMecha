"""LaunchConfig 与 PreflightService 单元测试。"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tieba_mecha.web.pages.batch_post.launch_config import LaunchConfig, LaunchConfigError
from tieba_mecha.web.pages.batch_post.preflight import (
    PreflightIssue,
    PreflightService,
    format_duration,
)


@pytest.mark.asyncio
class TestLaunchConfig:
    async def test_merged_fnames_dedup_preserve_order(self):
        cfg = LaunchConfig(
            local_fnames=["吧A", "吧B"],
            global_fnames=["吧B", "吧C", ""],
        )
        assert cfg.get_fnames() == ["吧A", "吧B", "吧C"]

    async def test_dict_roundtrip(self):
        cfg = LaunchConfig(
            account_ids=[1, 2, 3],
            local_fnames=["吧A"],
            global_fnames=["吧B"],
            post_count=10,
            delay_min=60.0,
            delay_max=300.0,
            use_ai=True,
            ai_persona="casual",
            use_schedule=True,
            schedule_type="daily",
            schedule_time=datetime(2026, 9, 18, 10, 30),
            interval_hours=6,
            schedule_day_of_week=2,
            reset_strategy="reuse",
        )
        restored = LaunchConfig.from_dict(cfg.to_dict())
        assert restored.account_ids == [1, 2, 3]
        assert restored.post_count == 10
        assert restored.use_ai is True
        assert restored.schedule_time == datetime(2026, 9, 18, 10, 30)
        assert restored.schedule_day_of_week == 2
        assert restored.reset_strategy == "reuse"

    async def test_from_dict_empty_defaults(self):
        cfg = LaunchConfig.from_dict({})
        assert cfg.account_ids == []
        assert cfg.material_ids is None
        assert cfg.schedule_time is None
        assert cfg.get_fnames() == []


class TestFormatDuration:
    def test_zero(self):
        assert format_duration(0) == "0 分钟"

    def test_minutes(self):
        assert format_duration(300) == "约 5 分钟"

    def test_hours(self):
        assert format_duration(5400) == "约 1 小时 30 分钟"


@pytest.mark.asyncio
class TestPreflightService:
    async def _make_db(self, accounts=None, forums=None, materials=None):
        db = MagicMock()
        db.get_accounts = AsyncMock(return_value=accounts or [])
        db.get_all_unique_forums = AsyncMock(return_value=forums or [])
        db.get_materials = AsyncMock(return_value=materials or [])
        return db

    def _material(self, mid, title="标题", content="正文内容"):
        m = MagicMock()
        m.id, m.title, m.content = mid, title, content
        return m

    def _account(self, aid, status="active", proxy_id=None, name=None):
        acc = MagicMock()
        acc.id, acc.status, acc.proxy_id = aid, status, proxy_id
        acc.user_name, acc.name = name, name
        return acc

    async def test_error_when_no_accounts(self):
        svc = PreflightService(await self._make_db())
        report = await svc.run(LaunchConfig(account_ids=[], local_fnames=["吧A"], post_count=1))
        codes = {i.code for i in report.errors}
        assert "no_accounts" in codes

    async def test_error_when_all_accounts_terminal(self):
        db = await self._make_db(accounts=[self._account(1, status="banned")])
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1], local_fnames=["吧A"], post_count=1))
        assert any(i.code == "no_effective_accounts" for i in report.errors)
        assert report.stats["accounts_effective"] == 0

    async def test_banned_forums_removed_effective_reported(self):
        db = await self._make_db(
            accounts=[self._account(1)],
            forums=[
                {"fname": "安全吧", "is_banned": False, "is_post_target": True},
                {"fname": "封禁吧", "is_banned": True, "is_post_target": False},
                {"fname": "危险吧", "is_banned": False, "is_post_target": False},
            ],
            materials=[self._material(1)],
        )
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(
            account_ids=[1], local_fnames=["安全吧", "封禁吧"],
            global_fnames=["危险吧"], post_count=1))
        assert report.effective_fnames == ["安全吧", "危险吧"]
        assert any(i.code == "forums_banned_removed" for i in report.warnings)
        assert any(i.code == "forums_unsafe" for i in report.warnings)
        assert report.stats["planned_posts"] == 1

    async def test_error_when_no_materials(self):
        db = await self._make_db(
            accounts=[self._account(1)],
            forums=[{"fname": "吧A", "is_banned": False, "is_post_target": True}],
            materials=[],
        )
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1], local_fnames=["吧A"], post_count=5))
        assert any(i.code == "no_materials" for i in report.errors)

    async def test_planned_capped_by_pending_materials(self):
        db = await self._make_db(
            accounts=[self._account(1)],
            forums=[{"fname": "吧A", "is_banned": False, "is_post_target": True}],
            materials=[self._material(i) for i in range(1, 4)],
        )
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1], local_fnames=["吧A"], post_count=10))
        assert report.stats["planned_posts"] == 3
        assert any(i.code == "materials_capped" for i in report.infos)

    async def test_material_quality_scan(self):
        materials = [
            self._material(1, title="", content="正文"),
            self._material(2, title="T", content=""),
            self._material(3, title="T", content="正文"),   # 与 4 完全重复
            self._material(4, title="T", content="正文"),
            self._material(5, title="带链接", content="看 https://example.com 这个"),
        ]
        db = await self._make_db(
            accounts=[self._account(1)],
            forums=[{"fname": "吧A", "is_banned": False, "is_post_target": True}],
            materials=materials,
        )
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(account_ids=[1], local_fnames=["吧A"], post_count=5))
        assert report.stats["materials_missing_title"] == [1]
        assert report.stats["materials_empty_content"] == [2]
        assert report.stats["materials_dup_groups"] == [[3, 4]]
        assert report.stats["materials_with_links"] == 1
        assert any(i.code == "materials_missing_title" for i in report.warnings)
        assert any(i.code == "materials_duplicate" for i in report.warnings)

    async def test_delay_warnings_and_risk_score(self):
        db = await self._make_db(
            accounts=[self._account(1)],
            forums=[{"fname": "吧A", "is_banned": False, "is_post_target": True}],
            materials=[self._material(1)],
        )
        svc = PreflightService(db)
        report = await svc.run(LaunchConfig(
            account_ids=[1], local_fnames=["吧A"], post_count=1,
            delay_min=10, delay_max=5))
        codes = {i.code for i in report.warnings}
        assert "delay_too_short" in codes
        assert "delay_inverted" in codes
        assert report.stats["delay_max_effective"] == 10
        assert report.risk_score >= 3
        assert report.risk_factors

    async def test_free_tier_caps_applied(self):
        db = await self._make_db(
            accounts=[self._account(1), self._account(2)],
            forums=[{"fname": "吧A", "is_banned": False, "is_post_target": True}],
            materials=[self._material(i) for i in range(1, 10)],
        )
        svc = PreflightService(db)
        with patch("tieba_mecha.web.pages.batch_post.preflight.get_auth_manager") as mock_auth:
            mgr = MagicMock()
            mgr.check_local_status = AsyncMock()
            mgr.status = 0  # 非 PRO
            mock_auth.return_value = mgr
            report = await svc.run(LaunchConfig(
                account_ids=[1, 2], local_fnames=["吧A"], post_count=10, use_ai=True))
        assert report.stats["free_tier"] is True
        assert report.stats["ai_effective"] is False
        assert any(i.code == "free_tier_caps" for i in report.warnings)

    async def test_no_proxy_warning(self):
        db = await self._make_db(
            accounts=[self._account(1), self._account(2, proxy_id=7)],
            forums=[{"fname": "吧A", "is_banned": False, "is_post_target": True}],
            materials=[self._material(1)],
        )
        svc = PreflightService(db)
        with patch("tieba_mecha.core.proxy.get_warmup_manager") as mock_wm:
            mgr = MagicMock()
            mgr.needs_warmup = AsyncMock(return_value=False)
            mock_wm.return_value = mgr
            report = await svc.run(LaunchConfig(
                account_ids=[1, 2], local_fnames=["吧A"], post_count=1))
        assert report.stats["accounts_no_proxy"] and len(report.stats["accounts_no_proxy"]) == 1
        assert any(i.code == "accounts_no_proxy" for i in report.warnings)


class TestScanImportPairs:
    def _scan(self, pairs):
        from tieba_mecha.web.pages.batch_post.preflight import scan_import_pairs
        return scan_import_pairs(pairs)

    def test_clean_batch_has_no_warnings(self):
        scan = self._scan([("标题1", "内容1"), ("标题2", "内容2")])
        assert not scan.has_warnings
        assert scan.valid_indices() == [0, 1]

    def test_empty_and_missing_title(self):
        scan = self._scan([("", ""), ("", "只有正文"), ("标题", "内容")])
        assert scan.empty_entries == [0]
        assert scan.missing_title == [1]
        assert scan.valid_indices() == [1, 2]

    def test_overlong_title_invalid(self):
        scan = self._scan([("T" * 501, "内容")])
        assert scan.overlong_title == [0]
        assert scan.valid_indices() == []

    def test_duplicate_groups_and_dedup(self):
        scan = self._scan([
            ("A", "内容X"),
            ("A", "内容X"),          # 完全重复
            ("B", "内容Y"),
            ("B", "内容Y"),          # 完全重复
            ("B", "内容Y"),          # 三连重复
        ])
        assert scan.duplicate_groups == [[0, 1], [2, 3, 4]]
        assert scan.dedup_indices() == [0, 2]

    def test_whitespace_normalized_dup(self):
        scan = self._scan([("A B", "x  y"), ("AB", "xy")])
        assert len(scan.duplicate_groups) == 1

    def test_link_detection(self):
        scan = self._scan([("看这个", "https://example.com 好东西"), ("普通", "无链接")])
        assert scan.with_links == [0]
        assert scan.valid_indices() == [0, 1]
