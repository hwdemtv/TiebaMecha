"""自顶（自动回帖）配置与调度行为测试（2026-11-24 改写）

改写前的问题：31 个用例里 11 个零断言（只调用不断言），另有一批把生产逻辑抄进测试再
断言自己（本地重写一份 validate_max_count、自定义一份 BUMP_TEMPLATES 等）——生产代码
怎么变它们都不会红。正因如此，「次数上限在引擎里根本没生效」一直没被发现：界面按
estimate_next_bump 显示「已达次数上限」，引擎的 once 分支却直接放行。

现在全部直接驱动生产代码：真实的 SQLite + AutoBumpManager + make_templated_bump_content +
BatchPostPage._save_bump_config，只把「真正回帖到贴吧」「AI 生成」「成功后的随机等待」
三处外部副作用打桩。
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tieba_mecha.core.batch_post import (
    BUMP_EMOJIS,
    BUMP_FALLBACK_TEMPLATES,
    AutoBumpManager,
    make_templated_bump_content,
)
from tieba_mecha.db.crud import Database
from tieba_mecha.web.pages.batch_post_page import BatchPostPage
from tieba_mecha.web.pages.posts.helpers import estimate_next_bump


# ---------- 设置项读写 ----------

class TestBumpSettingsCrud:
    """自顶配置项在数据库里的读写。"""

    async def test_set_and_get_max_bump_count(self, db: Database):
        await db.set_setting("max_bump_count", "50")
        assert await db.get_setting("max_bump_count") == "50"

    async def test_set_and_get_bump_cooldown(self, db: Database):
        await db.set_setting("bump_cooldown_minutes", "120")
        assert await db.get_setting("bump_cooldown_minutes") == "120"

    async def test_set_and_get_matrix_enabled(self, db: Database):
        await db.set_setting("bump_matrix_enabled", "1")
        assert await db.get_setting("bump_matrix_enabled") == "1"
        await db.set_setting("bump_matrix_enabled", "0")
        assert await db.get_setting("bump_matrix_enabled") == "0"

    async def test_bump_settings_update_overwrites(self, db: Database):
        await db.set_setting("max_bump_count", "20")
        await db.set_setting("max_bump_count", "100")
        await db.set_setting("bump_cooldown_minutes", "45")
        await db.set_setting("bump_cooldown_minutes", "180")
        assert await db.get_setting("max_bump_count") == "100"
        assert await db.get_setting("bump_cooldown_minutes") == "180"


# ---------- 巡检行为的测试脚手架 ----------

FNAME, TID = "自顶测试吧", 9001


async def _seed(db, *, bump_count=0, last_bumped_at=None, is_auto_bump=True, n_helpers=2):
    """一个发帖号 + 若干帮顶号 + 一条已发布物料"""
    poster = await db.add_account(name="poster", bduss="X" * 80, user_name="u_poster")
    helpers = [await db.add_account(name=f"helper{i}", bduss="Y" * 80, user_name=f"u_helper{i}")
               for i in range(n_helpers)]
    mid = await db.register_posted_material(
        "测试标题", "测试正文", posted_fname=FNAME, posted_tid=TID, posted_account_id=poster.id)
    async with db.async_session() as session:
        from tieba_mecha.db.models import MaterialPool

        m = await session.get(MaterialPool, mid)
        m.is_auto_bump = is_auto_bump
        m.bump_count = bump_count
        m.last_bumped_at = last_bumped_at
        await session.commit()
    return SimpleNamespace(mid=mid, poster=poster, helpers=helpers)


async def _material(db, mid):
    async with db.async_session() as session:
        from tieba_mecha.db.models import MaterialPool

        m = await session.get(MaterialPool, mid)
        # 脱离 session 后仍可读取的快照
        return SimpleNamespace(bump_count=m.bump_count, last_bumped_at=m.last_bumped_at,
                               bump_last_date=m.bump_last_date)


async def _set_last_bumped(db, mid, when):
    async with db.async_session() as session:
        from tieba_mecha.db.models import MaterialPool

        m = await session.get(MaterialPool, mid)
        m.last_bumped_at = when
        await session.commit()


async def _config(db, *, matrix="1", cooldown="45", max_count="20", ai_content="0"):
    await db.set_setting("bump_matrix_enabled", matrix)
    await db.set_setting("bump_cooldown_minutes", cooldown)
    await db.set_setting("max_bump_count", max_count)
    # 关掉 AI 生成：本套用例只验证调度，不向 AI 网关发请求
    await db.set_setting("bump_ai_content", ai_content)


async def _run_cycle(db, reply_result=(True, "")):
    """跑一轮自顶巡检，返回回帖桩（记录每次调用的 账号/吧/TID/内容）"""
    manager = AutoBumpManager(db)
    reply = AsyncMock(return_value=reply_result)
    with patch.object(manager.post_manager, "reply_to_thread", reply), \
         patch("tieba_mecha.core.batch_post.asyncio.sleep", new_callable=AsyncMock):
        await manager.process_all_candidates()
    return reply


# ---------- 巡检行为 ----------

class TestBumpCycle:
    """一轮自顶巡检对单条物料的完整处理。"""

    async def test_bumps_eligible_material_with_non_poster_account(self, db):
        seeded = await _seed(db)
        await _config(db)

        reply = await _run_cycle(db)

        reply.assert_awaited_once()
        account_id, fname, tid, content = reply.await_args.args
        assert account_id in {h.id for h in seeded.helpers}   # 同号自顶风险极高，必须换号
        assert (fname, tid) == (FNAME, TID)
        assert content and "分享好物" not in content
        m = await _material(db, seeded.mid)
        assert m.bump_count == 1 and m.last_bumped_at is not None
        assert m.bump_last_date == date.today()
        logs = await db.get_bump_logs(material_id=seeded.mid)
        assert len(logs) == 1 and logs[0].success is True and logs[0].account_id == account_id

    async def test_failed_bump_is_logged_but_not_counted(self, db):
        seeded = await _seed(db)
        await _config(db)

        reply = await _run_cycle(db, reply_result=(False, "吧务封禁"))

        reply.assert_awaited_once()
        m = await _material(db, seeded.mid)
        assert m.bump_count == 0 and m.last_bumped_at is None    # 失败不消耗次数
        logs = await db.get_bump_logs(material_id=seeded.mid)
        assert len(logs) == 1 and logs[0].success is False and "吧务封禁" in logs[0].message

    async def test_auto_bump_switch_off_disables_material(self, db):
        await _seed(db, is_auto_bump=False)
        await _config(db)
        (await _run_cycle(db)).assert_not_awaited()

    async def test_matrix_disabled_skips_bump(self, db):
        """未开启矩阵协同：没有别的号可用，宁可不顶也不让原帖号自顶。"""
        seeded = await _seed(db)
        await _config(db, matrix="0")

        (await _run_cycle(db)).assert_not_awaited()
        assert (await _material(db, seeded.mid)).bump_count == 0

    async def test_ai_content_is_used_when_enabled(self, db):
        """开启 AI 生成时用模型返回的内容（模型调用打桩）"""
        await _seed(db)
        await _config(db, ai_content="1")

        manager_gen = AsyncMock(return_value=(True, "AI 生成的自顶内容", ""))
        with patch("tieba_mecha.core.ai_optimizer.AIOptimizer.generate_bump_content", manager_gen):
            reply = await _run_cycle(db)
        assert reply.await_args.args[3] == "AI 生成的自顶内容"

    async def test_ai_failure_falls_back_to_template(self, db):
        await _seed(db)
        await _config(db, ai_content="1")

        with patch("tieba_mecha.core.ai_optimizer.AIOptimizer.generate_bump_content",
                   AsyncMock(return_value=(False, "", "网关超时"))):
            reply = await _run_cycle(db)
        content = reply.await_args.args[3]
        assert content and "网关超时" not in content


class TestBumpCooldown:
    """冷却时间窗：上次自顶后 N 分钟内不再自顶。"""

    @pytest.mark.parametrize("cooldown", [45, 120, 10])
    async def test_cooldown_window_gates_second_bump(self, db, cooldown):
        seeded = await _seed(db)
        await _config(db, cooldown=str(cooldown))

        await _run_cycle(db)
        assert (await _material(db, seeded.mid)).bump_count == 1

        await _run_cycle(db)                                   # 立刻再跑：冷却未到
        assert (await _material(db, seeded.mid)).bump_count == 1

        await _set_last_bumped(db, seeded.mid, datetime.now() - timedelta(minutes=cooldown + 1))
        await _run_cycle(db)                                   # 冷却已过
        assert (await _material(db, seeded.mid)).bump_count == 2

    async def test_configured_cooldown_value_is_honored(self, db):
        """同一条「30 分钟前自顶过」的物料：冷却 120 时不顶，改成冷却 10 后就顶"""
        await _seed(db, last_bumped_at=datetime.now() - timedelta(minutes=30))
        await _config(db, cooldown="120")
        (await _run_cycle(db)).assert_not_awaited()

        await _config(db, cooldown="10")
        (await _run_cycle(db)).assert_awaited_once()

    async def test_zero_cooldown_setting_falls_back_to_safe_default(self, db):
        """冷却被写成 0/负数时不能退化成「每轮巡检都顶」"""
        await _seed(db, last_bumped_at=datetime.now() - timedelta(minutes=1))
        await _config(db, cooldown="0")
        (await _run_cycle(db)).assert_not_awaited()


class TestBumpCountLimit:
    """once 模式的累计次数上限（改写前引擎从不执行该上限）。"""

    async def test_default_limit_20_stops_at_twenty(self, db):
        seeded = await _seed(db, bump_count=19)
        await _config(db)

        await _run_cycle(db)
        assert (await _material(db, seeded.mid)).bump_count == 20   # 第 20 次：最后一次

        await _set_last_bumped(db, seeded.mid, None)
        (await _run_cycle(db)).assert_not_awaited()                  # 第 21 次：不再自顶
        assert (await _material(db, seeded.mid)).bump_count == 20
        assert len(await db.get_bump_logs(material_id=seeded.mid)) == 1

    @pytest.mark.parametrize("limit", [5, 50, 100])
    async def test_custom_limit_is_honored(self, db, limit):
        seeded = await _seed(db, bump_count=limit - 1)
        await _config(db, max_count=str(limit))

        (await _run_cycle(db)).assert_awaited_once()
        assert (await _material(db, seeded.mid)).bump_count == limit

        await _set_last_bumped(db, seeded.mid, None)
        (await _run_cycle(db)).assert_not_awaited()

    async def test_material_already_over_limit_is_never_bumped(self, db):
        await _seed(db, bump_count=25)
        await _config(db)
        (await _run_cycle(db)).assert_not_awaited()

    async def test_non_positive_limit_setting_falls_back_to_default(self, db):
        """上限被写成 0 时不能把所有物料一刀切成「已达上限」"""
        await _seed(db)
        await _config(db, max_count="0")
        (await _run_cycle(db)).assert_awaited_once()

    @pytest.mark.parametrize("limit, count", [(20, 0), (20, 19), (20, 20), (20, 25), (5, 5), (50, 49), (100, 100)])
    def test_engine_and_post_list_agree_on_limit(self, limit, count):
        """帖子列表显示「已达次数上限」当且仅当引擎确实不再自顶（两处口径一致）"""
        manager = AutoBumpManager(db=None)
        should_bump, reason = manager._should_bump_this_cycle(SimpleNamespace(bump_mode="once", bump_count=count), limit)
        label = estimate_next_bump(True, "once", bump_count=count, max_bump_count=limit)

        assert should_bump is (count < limit)
        assert ("已达次数上限" in label) is (count >= limit)
        if not should_bump:
            assert reason == f"已达次数上限({limit}次)" == label

    def test_limit_applies_to_once_mode_only(self):
        """scheduled / matrix_loop 模式按「每日一次」约束，不受累计次数上限影响"""
        manager = AutoBumpManager(db=None)
        over = SimpleNamespace(bump_mode="scheduled", bump_count=999, bump_last_date=None, bump_hour=0, bump_duration_days=0)
        assert manager._should_bump_this_cycle(over, 20) == (True, "scheduled")
        today_done = SimpleNamespace(bump_mode="scheduled", bump_count=0, bump_last_date=date.today(), bump_hour=0, bump_duration_days=0)
        assert manager._should_bump_this_cycle(today_done, 20) == (False, "今日已执行")


# ---------- 账号轮换（真实选号函数） ----------

class TestAccountRotation:
    def _pool(self, ids):
        return [SimpleNamespace(id=i) for i in ids]

    def _material(self, *, posted_account_id, bump_count=0, bump_mode="once", **extra):
        return SimpleNamespace(id=1, posted_account_id=posted_account_id, bump_count=bump_count,
                               bump_mode=bump_mode, **extra)

    def test_rotation_excludes_poster_and_cycles_through_helpers(self):
        manager = AutoBumpManager(db=None)
        pool = self._pool([1, 2, 3, 4])
        picks = [manager._select_account_for_bump(self._material(posted_account_id=2, bump_count=n), pool)
                 for n in range(9)]
        assert 2 not in picks
        assert picks == [1, 3, 4, 1, 3, 4, 1, 3, 4]

    def test_single_account_pool_has_no_one_to_bump(self):
        manager = AutoBumpManager(db=None)
        assert manager._select_account_for_bump(self._material(posted_account_id=7), self._pool([7])) is None
        assert manager._select_account_for_bump(self._material(posted_account_id=7), []) is None

    def test_matrix_loop_rotates_material_account_list(self):
        manager = AutoBumpManager(db=None)
        pool = self._pool([1, 3, 4])
        picks = [manager._select_account_for_bump(
            self._material(posted_account_id=1, bump_mode="matrix_loop",
                           bump_account_ids=json.dumps([4, 3]), bump_account_index=k), pool)
            for k in range(5)]
        assert picks == [4, 3, 4, 3, 4]

    def test_matrix_loop_falls_back_when_list_account_left_the_pool(self):
        manager = AutoBumpManager(db=None)
        mat = self._material(posted_account_id=1, bump_mode="matrix_loop",
                             bump_account_ids=json.dumps([99]), bump_account_index=0, bump_count=1)
        assert manager._select_account_for_bump(mat, self._pool([1, 3, 4])) == 4   # 回退按 bump_count 轮换


# ---------- 自顶内容（真实模板引擎） ----------

class TestBumpContentEngine:
    LONG_TITLE = "东川笃哉作品集免费下载快来收藏"

    def test_content_is_short_clean_text(self):
        longest = 8 + len(" 还行，") + max(map(len, BUMP_FALLBACK_TEMPLATES)) + 1 + max(map(len, BUMP_EMOJIS))
        with_prefix = 0
        for seed in range(300):
            random.seed(seed)
            content = make_templated_bump_content(self.LONG_TITLE)
            lowered = content.lower()
            for banned in ("分享好物", "免费下载", "资源已取", "http", "【", "】"):
                assert banned not in lowered, f"seed={seed} 自顶内容含推广/外链特征: {content}"
            assert len(content) <= longest
            assert content.endswith(tuple(BUMP_EMOJIS)) and any(t in content for t in BUMP_FALLBACK_TEMPLATES)
            if " 还行，" in content:
                with_prefix += 1
                assert content.startswith(self.LONG_TITLE[:8] + " 还行，")   # 标题关键词截断 8 字
        assert 0 < with_prefix < 300   # 20% 概率的前缀分支确实被走到、且不是每次都带

    def test_no_title_means_no_prefix(self):
        random.seed(7)
        for _ in range(100):
            assert " 还行，" not in make_templated_bump_content("")


# ---------- 设置页保存（真实的 _save_bump_config） ----------

class _FakePage:
    def __init__(self):
        self.controls, self.overlay = [], []
        self.update = MagicMock()
        self.open = MagicMock()
        self.close = MagicMock()
        self.run_task = MagicMock()


def _page():
    db = MagicMock()
    db.set_setting = AsyncMock()
    bp = BatchPostPage(page=_FakePage(), db=db)
    bp._show_snackbar = MagicMock()
    bp._refresh_material_table = AsyncMock()
    bp.bump_max_count_field = SimpleNamespace(value=None)
    bp.bump_cooldown_field = SimpleNamespace(value=None)
    bp.bump_matrix_switch = SimpleNamespace(value=False)
    bp.use_ai_switch = SimpleNamespace(value=True)
    bp.use_schedule = SimpleNamespace(value=True)
    bp.bump_ai_content_switch = SimpleNamespace(value=True)
    return bp


def _saved(bp) -> dict:
    return {call.args[0]: call.args[1] for call in bp.db.set_setting.await_args_list}


class TestBumpConfigSave:
    """设置页「保存自顶配置」：输入钳制与落库。"""

    @pytest.mark.parametrize("typed, saved", [("5", "5"), ("50", "50"), ("100", "100"),
                                              ("200", "100"), ("0", "5"), ("abc", "20"), (None, "20")])
    async def test_max_count_clamped_5_to_100(self, typed, saved):
        bp = _page()
        bp.bump_max_count_field.value = typed
        await bp._save_bump_config(None)
        assert _saved(bp)["max_bump_count"] == saved
        if typed in ("abc", None):
            # 解析失败时输入框被回写成缺省值，用户能看到实际生效的配置
            assert bp.bump_max_count_field.value == saved

    @pytest.mark.parametrize("typed, saved", [("10", "10"), ("720", "720"), ("1440", "1440"),
                                              ("2000", "1440"), ("5", "10"), ("abc", "45"), (None, "45")])
    async def test_cooldown_clamped_10_to_1440(self, typed, saved):
        bp = _page()
        bp.bump_cooldown_field.value = typed
        await bp._save_bump_config(None)
        assert _saved(bp)["bump_cooldown_minutes"] == saved

    @pytest.mark.parametrize("enabled, stored", [(True, "1"), (False, "0")])
    async def test_switches_stored_as_flags(self, enabled, stored):
        bp = _page()
        bp.bump_matrix_switch.value = enabled
        bp.bump_ai_content_switch.value = enabled
        await bp._save_bump_config(None)
        settings = _saved(bp)
        assert settings["bump_matrix_enabled"] == stored
        assert settings["bump_ai_content"] == stored
