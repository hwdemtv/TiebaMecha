"""Tests for survival analysis feature."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tieba_mecha.db.crud import Database
from tieba_mecha.db.models import MaterialPool, Account


@pytest.mark.asyncio
class TestSurvivalStats:
    """Tests for survival statistics functions."""

    async def test_get_survival_stats_empty(self, db):
        """Test getting survival stats when no materials exist."""
        stats = await db.get_survival_stats()

        assert stats["total"] == 0
        assert stats["alive"] == 0
        assert stats["dead"] == 0
        assert stats["unknown"] == 0

    async def test_get_survival_stats_with_materials(self, db):
        """Test getting survival stats with various materials."""
        # 创建测试物料（通过直接插入数据库）
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            from sqlalchemy import insert

            materials = [
                MaterialPool(
                    title=f"测试标题{i}",
                    content=f"测试内容{i}",
                    status="success",
                    posted_tid=1000 + i,
                    posted_fname="测试吧",
                    survival_status=status,
                )
                for i, status in enumerate(["alive", "alive", "dead", "unknown", "unknown"])
            ]
            session.add_all(materials)
            await session.commit()

        stats = await db.get_survival_stats()

        assert stats["total"] == 5
        assert stats["alive"] == 2
        assert stats["dead"] == 1
        assert stats["unknown"] == 2

    async def test_get_survival_stats_excludes_invalid_tid(self, db):
        """Test that materials without valid posted_tid are excluded."""
        async with db.async_session() as session:
            materials = [
                MaterialPool(
                    title="有效帖子",
                    content="内容",
                    status="success",
                    posted_tid=12345,  # 有效 TID
                    survival_status="alive",
                ),
                MaterialPool(
                    title="无 TID",
                    content="内容",
                    status="success",
                    posted_tid=None,  # 无效
                    survival_status="unknown",
                ),
                MaterialPool(
                    title="TID 为 0",
                    content="内容",
                    status="success",
                    posted_tid=0,  # 无效
                    survival_status="unknown",
                ),
            ]
            session.add_all(materials)
            await session.commit()

        stats = await db.get_survival_stats()

        assert stats["total"] == 1
        assert stats["alive"] == 1


@pytest.mark.asyncio
class TestSurvivalByAccount:
    """Tests for survival statistics grouped by account."""

    async def test_get_survival_by_account_empty(self, db):
        """Test getting survival by account when no materials exist."""
        stats = await db.get_survival_by_account()

        assert stats == []

    async def test_get_survival_by_account_single(self, db):
        """Test getting survival by account with single account."""
        # 创建账号
        acc = await db.add_account(name="测试账号", bduss="b" * 192)

        # 创建物料
        async with db.async_session() as session:
            materials = [
                MaterialPool(
                    title=f"帖子{i}",
                    content="内容",
                    status="success",
                    posted_tid=1000 + i,
                    posted_account_id=acc.id,
                    survival_status=status,
                )
                for i, status in enumerate(["alive", "dead"])
            ]
            session.add_all(materials)
            await session.commit()

        stats = await db.get_survival_by_account()

        assert len(stats) == 1
        assert stats[0]["account_name"] == "测试账号"
        assert stats[0]["total"] == 2
        assert stats[0]["alive"] == 1
        assert stats[0]["dead"] == 1

    async def test_get_survival_by_account_multiple(self, db):
        """Test getting survival by account with multiple accounts."""
        acc1 = await db.add_account(name="账号1", bduss="a" * 192)
        acc2 = await db.add_account(name="账号2", bduss="b" * 192)

        async with db.async_session() as session:
            materials = [
                MaterialPool(
                    title="账号1帖子1",
                    content="内容",
                    status="success",
                    posted_tid=1001,
                    posted_account_id=acc1.id,
                    survival_status="alive",
                ),
                MaterialPool(
                    title="账号2帖子1",
                    content="内容",
                    status="success",
                    posted_tid=1002,
                    posted_account_id=acc2.id,
                    survival_status="dead",
                ),
            ]
            session.add_all(materials)
            await session.commit()

        stats = await db.get_survival_by_account()

        assert len(stats) == 2
        stats_dict = {s["account_id"]: s for s in stats}

        assert stats_dict[acc1.id]["alive"] == 1
        assert stats_dict[acc2.id]["dead"] == 1

    async def test_get_survival_by_account_handles_null_account(self, db):
        """Test that null account_id is handled gracefully."""
        async with db.async_session() as session:
            material = MaterialPool(
                title="无账号帖子",
                content="内容",
                status="success",
                posted_tid=1000,
                posted_account_id=None,  # 无账号
                survival_status="alive",
            )
            session.add(material)
            await session.commit()

        stats = await db.get_survival_by_account()

        assert len(stats) == 1
        assert "账号" in stats[0]["account_name"]


@pytest.mark.asyncio
class TestUpdateMaterialSurvivalStatus:
    """Tests for updating material survival status."""

    async def test_update_survival_status_to_alive(self, db):
        """Test updating survival status to alive."""
        # 创建物料
        async with db.async_session() as session:
            material = MaterialPool(
                title="测试帖子",
                content="内容",
                status="success",
                posted_tid=12345,
                survival_status="unknown",
            )
            session.add(material)
            await session.commit()
            material_id = material.id

        # 更新状态
        await db.update_material_survival_status(material_id, "alive")

        # 验证
        async with db.async_session() as session:
            updated = await session.get(MaterialPool, material_id)
            assert updated.survival_status == "alive"
            assert updated.last_checked_at is not None

    async def test_update_survival_status_with_death_reason(self, db):
        """Test updating survival status with death reason."""
        async with db.async_session() as session:
            material = MaterialPool(
                title="测试帖子",
                content="内容",
                status="success",
                posted_tid=12345,
            )
            session.add(material)
            await session.commit()
            material_id = material.id

        await db.update_material_survival_status(
            material_id, "dead", "deleted_by_user"
        )

        async with db.async_session() as session:
            updated = await session.get(MaterialPool, material_id)
            assert updated.survival_status == "dead"
            assert updated.death_reason == "deleted_by_user"

    async def test_update_nonexistent_material(self, db):
        """Test updating non-existent material does not raise error."""
        # 应该静默处理，不抛出异常
        await db.update_material_survival_status(99999, "dead")


@pytest.mark.asyncio
class TestMaterialPoolModel:
    """Tests for MaterialPool model fields."""

    async def test_material_pool_has_survival_fields(self, db):
        """Test MaterialPool model has all required survival fields."""
        async with db.async_session() as session:
            material = MaterialPool(
                title="测试",
                content="内容",
                status="success",
                posted_tid=12345,
                survival_status="unknown",
                death_reason="",
            )
            session.add(material)
            await session.commit()

            # 验证字段存在且可访问
            assert hasattr(material, "survival_status")
            assert hasattr(material, "death_reason")
            assert hasattr(material, "last_checked_at")

    async def test_material_pool_default_survival_status(self, db):
        """Test MaterialPool default survival_status is 'unknown'."""
        async with db.async_session() as session:
            material = MaterialPool(
                title="测试",
                content="内容",
                status="pending",
            )
            session.add(material)
            await session.commit()

            assert material.survival_status == "unknown"
            assert material.death_reason == ""


class TestCheckPostSurvival:
    """Tests for check_post_survival function."""

    @pytest.mark.asyncio
    async def test_check_post_survival_alive(self):
        """Test checking alive post."""
        from tieba_mecha.core.post import check_post_survival

        mock_response = MagicMock()
        mock_response.forum.fid = 12345

        with patch("aiotieba.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_posts = AsyncMock(return_value=mock_response)

            status, reason = await check_post_survival(12345)

            assert status == "alive"
            assert reason == ""

    @pytest.mark.asyncio
    async def test_check_post_survival_dead_not_found(self):
        """Test checking dead post (not found)."""
        from tieba_mecha.core.post import check_post_survival

        with patch("aiotieba.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_posts = AsyncMock(return_value=None)

            status, reason = await check_post_survival(12345)

            assert status == "dead"

    @pytest.mark.asyncio
    async def test_check_post_survival_dead_exception(self):
        """Test checking post when exception occurs."""
        from tieba_mecha.core.post import check_post_survival

        with patch("aiotieba.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_posts = AsyncMock(side_effect=Exception("Unknown error"))

            status, reason = await check_post_survival(12345)

            assert status == "dead"
            assert reason == "error"

    @pytest.mark.asyncio
    async def test_check_post_survival_deleted_error(self):
        """Test checking post with deleted error message."""
        from tieba_mecha.core.post import check_post_survival

        with patch("aiotieba.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_posts = AsyncMock(
                side_effect=Exception("Thread has been deleted")
            )

            status, reason = await check_post_survival(12345)

            assert status == "dead"
            assert reason == "deleted_by_user"

    @pytest.mark.asyncio
    async def test_check_post_survival_banned_error(self):
        """Test checking post with banned error message."""
        from tieba_mecha.core.post import check_post_survival

        with patch("aiotieba.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_posts = AsyncMock(
                side_effect=Exception("Thread blocked by moderator")
            )

            status, reason = await check_post_survival(12345)

            assert status == "dead"
            assert reason == "banned_by_mod"

    @pytest.mark.asyncio
    async def test_check_post_survival_captcha_error(self):
        """Test checking post with captcha error."""
        from tieba_mecha.core.post import check_post_survival

        with patch("aiotieba.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_posts = AsyncMock(
                side_effect=Exception("需要验证码验证")
            )

            status, reason = await check_post_survival(12345)

            assert status == "dead"
            assert reason == "captcha_required"

    @pytest.mark.asyncio
    async def test_check_post_survival_with_thread_info(self):
        """Test checking post with complete thread information."""
        from tieba_mecha.core.post import check_post_survival

        mock_response = MagicMock()
        mock_response.forum.fid = 12345
        mock_response.thread.title = "测试标题"
        mock_response.thread.reply_num = 10

        with patch("aiotieba.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_posts = AsyncMock(return_value=mock_response)

            status, reason = await check_post_survival(12345)

            assert status == "alive"
            assert reason == ""

    @pytest.mark.asyncio
    async def test_check_post_survival_captcha_in_response(self):
        """Test checking post when response contains captcha."""
        from tieba_mecha.core.post import check_post_survival

        mock_response = MagicMock()
        mock_response.text = "请输入验证码"

        with patch("aiotieba.Client") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_posts = AsyncMock(return_value=mock_response)

            status, reason = await check_post_survival(12345)

            assert status == "dead"
            assert reason == "captcha_required"


@pytest.mark.asyncio
class TestSurvivalRateCalculation:
    """Tests for survival rate calculation logic."""

    async def test_survival_rate_full(self, db):
        """Test 100% survival rate."""
        async with db.async_session() as session:
            materials = [
                MaterialPool(
                    title=f"帖子{i}",
                    content="内容",
                    status="success",
                    posted_tid=1000 + i,
                    survival_status="alive",
                )
                for i in range(5)
            ]
            session.add_all(materials)
            await session.commit()

        stats = await db.get_survival_stats()
        rate = stats["alive"] / stats["total"] * 100

        assert rate == 100.0

    async def test_survival_rate_zero(self, db):
        """Test 0% survival rate."""
        async with db.async_session() as session:
            materials = [
                MaterialPool(
                    title=f"帖子{i}",
                    content="内容",
                    status="success",
                    posted_tid=1000 + i,
                    survival_status="dead",
                )
                for i in range(3)
            ]
            session.add_all(materials)
            await session.commit()

        stats = await db.get_survival_stats()
        rate = stats["alive"] / stats["total"] * 100

        assert rate == 0.0

    async def test_survival_rate_partial(self, db):
        """Test partial survival rate."""
        async with db.async_session() as session:
            materials = [
                MaterialPool(
                    title="存活1",
                    content="内容",
                    status="success",
                    posted_tid=1001,
                    survival_status="alive",
                ),
                MaterialPool(
                    title="存活2",
                    content="内容",
                    status="success",
                    posted_tid=1002,
                    survival_status="alive",
                ),
                MaterialPool(
                    title="阵亡1",
                    content="内容",
                    status="success",
                    posted_tid=1003,
                    survival_status="dead",
                ),
                MaterialPool(
                    title="未知1",
                    content="内容",
                    status="success",
                    posted_tid=1004,
                    survival_status="unknown",
                ),
            ]
            session.add_all(materials)
            await session.commit()

        stats = await db.get_survival_stats()
        rate = stats["alive"] / stats["total"] * 100

        assert rate == 50.0  # 2 out of 4


@pytest.mark.asyncio
class TestCaptchaEvents:
    """Tests for captcha event CRUD operations."""

    async def test_save_captcha_event(self, db):
        """Test saving a captcha event."""
        event_id = await db.save_captcha_event(
            account_id=1,
            task_id=100,
            reason="captcha_required"
        )
        
        assert event_id is not None
        assert event_id > 0

    async def test_get_captcha_events_empty(self, db):
        """Test getting captcha events when none exist."""
        events = await db.get_captcha_events()
        
        assert events == []

    async def test_get_captcha_events_with_data(self, db):
        """Test getting captcha events with data."""
        # 创建测试事件
        await db.save_captcha_event(account_id=1, reason="captcha_required")
        await db.save_captcha_event(account_id=1, reason="rate_limit")
        
        events = await db.get_captcha_events()
        
        assert len(events) == 2

    async def test_get_captcha_events_by_account(self, db):
        """Test getting captcha events filtered by account."""
        await db.save_captcha_event(account_id=1, reason="captcha")
        await db.save_captcha_event(account_id=2, reason="captcha")
        
        events = await db.get_captcha_events(account_id=1)
        
        assert len(events) == 1
        assert events[0]["account_id"] == 1

    async def test_get_captcha_events_by_status(self, db):
        """Test getting captcha events filtered by status."""
        await db.save_captcha_event(reason="captcha")
        
        events = await db.get_captcha_events(status="pending")
        
        assert len(events) == 1
        assert events[0]["status"] == "pending"

    async def test_resolve_captcha_event(self, db):
        """Test resolving a captcha event."""
        event_id = await db.save_captcha_event(reason="captcha")
        
        success = await db.resolve_captcha_event(event_id, resolved_by="manual", notes="User confirmed")
        
        assert success is True
        
        events = await db.get_captcha_events()
        assert events[0]["status"] == "resolved"
        assert events[0]["resolved_by"] == "manual"
        assert events[0]["notes"] == "User confirmed"

    async def test_get_pending_captcha_count(self, db):
        """Test getting pending captcha count."""
        await db.save_captcha_event(reason="captcha")
        await db.save_captcha_event(reason="captcha")
        
        count = await db.get_pending_captcha_count()
        
        assert count == 2

    async def test_clear_resolved_captcha_events(self, db):
        """Test clearing resolved captcha events."""
        event_id = await db.save_captcha_event(reason="captcha")
        await db.resolve_captcha_event(event_id)
        
        count = await db.clear_resolved_captcha_events()
        
        assert count == 1
        
        remaining = await db.get_captcha_events()
        assert len(remaining) == 0


@pytest.mark.asyncio
class TestMaterialsPaginated:
    """Tests for get_materials_paginated."""

    async def test_paginated_empty(self, db):
        """Test paginated query with no data."""
        materials, total = await db.get_materials_paginated()
        assert total == 0
        assert materials == []

    async def test_paginated_page_size(self, db):
        """Test paginated respects page_size."""
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            for i in range(25):
                session.add(MaterialPool(title=f"标题{i}", content=f"内容{i}", status="success", posted_tid=1))
            await session.commit()

        materials, total = await db.get_materials_paginated(page=1, page_size=10)
        assert total == 25
        assert len(materials) == 10

    async def test_paginated_page_2(self, db):
        """Test second page returns remaining items."""
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            for i in range(25):
                session.add(MaterialPool(title=f"标题{i}", content=f"内容{i}", status="success", posted_tid=1))
            await session.commit()

        materials, total = await db.get_materials_paginated(page=2, page_size=10)
        assert total == 25
        assert len(materials) == 10

    async def test_paginated_page_3_partial(self, db):
        """Test last page with partial items."""
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            for i in range(25):
                session.add(MaterialPool(title=f"标题{i}", content=f"内容{i}", status="success", posted_tid=1))
            await session.commit()

        materials, total = await db.get_materials_paginated(page=3, page_size=10)
        assert total == 25
        assert len(materials) == 5

    async def test_paginated_filter_by_survival_status(self, db):
        """Test filtering by survival status."""
        # Create materials with different survival statuses
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            for i in range(5):
                m = MaterialPool(title=f"活{i}", content="c", status="success", posted_tid=1)
                m.survival_status = "alive"
                session.add(m)
            for i in range(3):
                m = MaterialPool(title=f"死{i}", content="c", status="success", posted_tid=1)
                m.survival_status = "dead"
                session.add(m)
            await session.commit()

        alive_mat, alive_total = await db.get_materials_paginated(survival_status="alive")
        assert alive_total == 5
        assert len(alive_mat) == 5

        dead_mat, dead_total = await db.get_materials_paginated(survival_status="dead")
        assert dead_total == 3
        assert len(dead_mat) == 3

    async def test_paginated_filter_by_account(self, db):
        """Test filtering by account_id."""
        acc1 = await db.add_account(name="账号1", bduss="a" * 192)
        acc2 = await db.add_account(name="账号2", bduss="b" * 192)

        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            for i in range(3):
                m = MaterialPool(title=f"A{i}", content="c", status="success", posted_tid=1, posted_account_id=acc1.id)
                m.survival_status = "alive"
                session.add(m)
            for i in range(2):
                m = MaterialPool(title=f"B{i}", content="c", status="success", posted_tid=1, posted_account_id=acc2.id)
                m.survival_status = "alive"
                session.add(m)
            await session.commit()

        mat1, total1 = await db.get_materials_paginated(account_id=acc1.id)
        assert total1 == 3

        mat2, total2 = await db.get_materials_paginated(account_id=acc2.id)
        assert total2 == 2

    async def test_paginated_combined_filters(self, db):
        """Test combining status and account filters."""
        acc = await db.add_account(name="账号", bduss="a" * 192)
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            m1 = MaterialPool(title="活", content="c", status="success", posted_tid=1, posted_account_id=acc.id)
            m1.survival_status = "alive"
            session.add(m1)
            m2 = MaterialPool(title="死", content="c", status="success", posted_tid=1, posted_account_id=acc.id)
            m2.survival_status = "dead"
            session.add(m2)
            await session.commit()

        mat, total = await db.get_materials_paginated(survival_status="alive", account_id=acc.id)
        assert total == 1
        assert mat[0].title == "活"


@pytest.mark.asyncio
class TestGetMaterialIdsByStatus:
    """Tests for get_material_ids_by_status (cross-page select all)."""

    async def test_empty(self, db):
        """Test with no data returns empty list."""
        ids = await db.get_material_ids_by_status(statuses=["pending"])
        assert ids == []

    async def test_returns_ids_only(self, db):
        """Test returns list of ints, not full objects."""
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            for i in range(5):
                session.add(MaterialPool(title=f"标题{i}", content=f"内容{i}", status="pending"))
            await session.commit()

        ids = await db.get_material_ids_by_status(statuses=["pending"])
        assert len(ids) == 5
        assert all(isinstance(i, int) for i in ids)

    async def test_filter_by_status(self, db):
        """Test filtering by multiple statuses."""
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            session.add(MaterialPool(title="待发", content="c", status="pending"))
            session.add(MaterialPool(title="失败", content="c", status="failed"))
            session.add(MaterialPool(title="成功", content="c", status="success"))
            await session.commit()

        pending_ids = await db.get_material_ids_by_status(statuses=["pending", "failed"])
        assert len(pending_ids) == 2

        success_ids = await db.get_material_ids_by_status(statuses=["success"])
        assert len(success_ids) == 1

    async def test_search_text(self, db):
        """Test search_text filtering."""
        async with db.async_session() as session:
            from tieba_mecha.db.models import MaterialPool
            session.add(MaterialPool(title="特殊关键词", content="c", status="pending"))
            session.add(MaterialPool(title="普通标题", content="c", status="pending"))
            await session.commit()

        ids = await db.get_material_ids_by_status(statuses=["pending"], search_text="特殊")
        assert len(ids) == 1
