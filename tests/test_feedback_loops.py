"""反馈闭环与风控持久化测试。

覆盖：
- Auto-Bump bump_last_date 回归（每日一次判重）
- FailureCircuitBreaker 跨实例/跨任务持久化
- ContentSimilarityDetector 从发帖日志回种
- 行为审计评分数学修复 + audit_and_govern 权重治理
- 存活反馈：死亡原因分流 / 存活样本查询 / 治理入口 / AI few-shot 注入与相似度自检
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from tieba_mecha.core.batch_post import ContentSimilarityDetector, FailureCircuitBreaker
from tieba_mecha.core.ai_optimizer import AIOptimizer, SELF_CHECK_SIMILARITY_THRESHOLD, _bigram_jaccard


# ========================================================================
# Auto-Bump：bump_last_date 字段回归
# ========================================================================

class TestAutoBumpDateField:
    def test_update_uses_persisted_field(self):
        """自顶成功后必须写 bump_last_date（曾误写 last_date，动态属性不落库，
        导致 scheduled/matrix_loop 模式"今日已执行"判重失效、同日重复自顶）。"""
        import inspect
        from tieba_mecha.core import batch_post

        source = inspect.getsource(batch_post)
        assert "mat.last_date" not in source, "不得写入模型上不存在的 last_date 属性"
        assert "mat.bump_last_date = today" in source


# ========================================================================
# FailureCircuitBreaker 持久化
# ========================================================================

class TestFailureCircuitBreakerPersistence:
    @pytest.mark.asyncio
    async def test_state_survives_new_instance(self, db):
        """熔断状态跨实例存续：新任务回种后仍处于熔断中"""
        await db.add_account(name="breaker-acc", bduss="x" * 200)

        breaker1 = FailureCircuitBreaker(max_consecutive_failures=5, base_cooldown=30, db=db, scope="post")
        await breaker1.load()
        for _ in range(5):
            triggered = await breaker1.record_failure(1)
        assert triggered is True
        assert breaker1.is_in_cooldown(1) is True

        # 模拟下一个任务：全新实例，从库回种
        breaker2 = FailureCircuitBreaker(max_consecutive_failures=5, base_cooldown=30, db=db, scope="post")
        await breaker2.load()
        assert breaker2.is_in_cooldown(1) is True, "熔断状态应跨实例持久化"

    @pytest.mark.asyncio
    async def test_success_clears_persisted_state(self, db):
        await db.add_account(name="breaker-acc2", bduss="x" * 200)

        breaker1 = FailureCircuitBreaker(max_consecutive_failures=3, base_cooldown=60, db=db, scope="post")
        await breaker1.load()
        for _ in range(3):
            await breaker1.record_failure(1)
        assert breaker1.is_in_cooldown(1) is True

        await breaker1.record_success(1)

        breaker2 = FailureCircuitBreaker(max_consecutive_failures=3, base_cooldown=60, db=db, scope="post")
        await breaker2.load()
        assert breaker2.is_in_cooldown(1) is False

    @pytest.mark.asyncio
    async def test_scopes_are_independent(self, db):
        """post 与 follow 场景独立计数，互不干扰"""
        await db.add_account(name="scope-acc", bduss="x" * 200)
        post_breaker = FailureCircuitBreaker(max_consecutive_failures=3, db=db, scope="post")
        await post_breaker.load()
        for _ in range(3):
            await post_breaker.record_failure(1)

        follow_breaker = FailureCircuitBreaker(max_consecutive_failures=3, db=db, scope="follow")
        await follow_breaker.load()
        assert follow_breaker.is_in_cooldown(1) is False

    @pytest.mark.asyncio
    async def test_memory_mode_without_db(self):
        """不传 db 时保持纯内存行为（原语义）"""
        breaker = FailureCircuitBreaker(max_consecutive_failures=3, base_cooldown=30)
        await breaker.load()  # 无 db 应为空操作
        for _ in range(3):
            triggered = await breaker.record_failure(42)
        assert triggered is True
        assert breaker.is_in_cooldown(42) is True
        await breaker.record_success(42)
        assert breaker.is_in_cooldown(42) is False


# ========================================================================
# ContentSimilarityDetector 日志回种
# ========================================================================

class TestSimilaritySeeding:
    @pytest.mark.asyncio
    async def test_seed_from_batch_post_logs(self, db):
        """任务结束后新检测器能从发帖日志恢复相似度历史（24h 回溯跨任务生效）"""
        long_text = "这是一段足够长的测试内容用来计算字符级二元组相似度，需要超过最小长度限制才有统计意义。"
        await db.add_batch_post_log(
            task_id="t1", fname="测试吧", status="success",
            account_id=1, title="标题甲", tid=1001,
            data={"content": long_text},
        )

        detector = ContentSimilarityDetector(similarity_threshold=0.7, window_hours=24.0)
        seeded = await detector.seed_from_db(db)
        assert seeded == 1

        passed, similarity = await detector.check("标题甲", long_text)
        assert passed is False and similarity > 0.7

        passed2, _ = await detector.check("完全不同的标题", "南辕北辙的内容写法差异巨大完全不相干的一段文字描述。")
        assert passed2 is True

    @pytest.mark.asyncio
    async def test_seed_ignores_old_logs(self, db):
        """窗口外的日志不回种"""
        from tieba_mecha.db.models import BatchPostLog
        async with db.async_session() as session:
            session.add(BatchPostLog(
                task_id="t0", fname="旧吧", status="success", title="旧标题",
                data_json=json.dumps({"content": "很旧的发帖内容".join([""] * 30)}),
                created_at=datetime.now() - timedelta(hours=48),
            ))
            await session.commit()

        detector = ContentSimilarityDetector(window_hours=24.0)
        seeded = await detector.seed_from_db(db)
        assert seeded == 0


# ========================================================================
# 行为审计：评分数学 + 自动治理
# ========================================================================

class TestRiskScoreMath:
    def test_max_risk_reaches_ten(self):
        """全维度最高档 → 10 分（修复前理论满分仅 2.8，5.0 阈值永不触发）"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        auditor = BehaviorAuditor(db=None)
        score = auditor._calculate_risk_score(
            sign_rate=0.3,                              # 真实成功率极低 → 2.0*0.2
            sign_attempts=10,
            sign_hour_distribution={"9": 19, "10": 1},  # 签到峰时 95% → 档位取 max 2.0
            hour_distribution={"10": 10},               # 发帖高度集中 → 3.0*0.25
            content_variety=0.3,                        # 重复度高 → 3.0*0.25
            avg_interval=1.0,
            proxy_fails=15,
        )
        assert score == pytest.approx(10.0)

    def test_clean_behavior_is_zero(self):
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        auditor = BehaviorAuditor(db=None)
        score = auditor._calculate_risk_score(
            sign_rate=1.0,                       # 真实成功率 100% 是健康态，不再扣分
            sign_attempts=30,
            sign_hour_distribution={"3": 5, "8": 5, "12": 5, "18": 5, "23": 5},
            hour_distribution={"9": 3, "14": 3, "21": 3},
            content_variety=0.95,
            avg_interval=30.0,
            proxy_fails=0,
        )
        assert score == pytest.approx(0.0)

    def test_moderate_risk_reaches_threshold(self):
        """中等风险组合应能达到 5.0 治理阈值"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        auditor = BehaviorAuditor(db=None)
        score = auditor._calculate_risk_score(
            sign_rate=0.9,
            sign_attempts=30,
            sign_hour_distribution={"9": 25, "10": 5},  # 签到峰时 >0.8 → 2.0*0.2
            hour_distribution={"2": 8, "3": 1},  # 高度集中 → 3.0*0.25
            content_variety=0.4,                 # 重复度高 → 3.0*0.25
            avg_interval=10.0,
            proxy_fails=0,
        )
        assert score >= 5.0

    def test_high_success_rate_no_longer_scores(self):
        """口径重构：成功率高≠行为规律，不再计分（旧版 >0.95 即取最高档）"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        auditor = BehaviorAuditor(db=None)
        score = auditor._calculate_risk_score(
            sign_rate=1.0,
            sign_attempts=50,
            sign_hour_distribution={"9": 2, "11": 2, "15": 2, "20": 2},
            hour_distribution={"9": 3, "14": 3, "21": 3},
            content_variety=1.0,
            avg_interval=30.0,
            proxy_fails=0,
        )
        assert score == pytest.approx(0.0)

    def test_work_hours_concentration_scores(self):
        """工作时间集中（≥5 帖且 >90%）计入时间维度 2.0/3.0 档"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        auditor = BehaviorAuditor(db=None)
        # 6 帖分布在 10/12/17 点，峰时 2/6=0.33 不触发峰时档 → 仅工作时间档生效
        score = auditor._calculate_risk_score(
            hour_distribution={"10": 2, "12": 2, "17": 2},
            content_variety=1.0,
        )
        expected = 2.0 * 0.25 / 2.8 * 10
        assert score == pytest.approx(expected)

    def test_work_hours_small_sample_no_score(self):
        """样本 <5 帖：工作时间集中不计分（2-3 帖触发纯属噪声）"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        auditor = BehaviorAuditor(db=None)
        score = auditor._calculate_risk_score(
            hour_distribution={"10": 1, "12": 1},
            content_variety=1.0,
        )
        assert score == pytest.approx(0.0)


class TestAuditGovernance:
    @pytest.mark.asyncio
    async def test_high_risk_account_weight_reduced(self, db):
        """高风险账号 post_weight 自动下调并写入 weight_history"""
        acc = await db.add_account(name="risk-acc", bduss="x" * 200)

        fake_report = [{
            "account_id": acc.id, "account_name": "risk-acc",
            "risk_score": 7.5, "alerts": ["发帖时间过于集中"], "recommendations": [],
            "stats": {},
        }]
        with patch("tieba_mecha.core.behavior_audit.audit_all_accounts", new=AsyncMock(return_value=fake_report)):
            from tieba_mecha.core.behavior_audit import audit_and_govern
            await audit_and_govern(db)

        account = await db.get_account_by_id(acc.id)
        assert account.post_weight == 2  # 默认 5 - 3

        history = await db.get_weight_history(acc.id)
        assert any(h.source == "behavior_audit" for h in history)

    @pytest.mark.asyncio
    async def test_auto_adjust_can_be_disabled(self, db):
        acc = await db.add_account(name="safe-acc", bduss="x" * 200)
        await db.set_setting("audit_auto_adjust_weight", "false")

        fake_report = [{
            "account_id": acc.id, "account_name": "safe-acc",
            "risk_score": 9.0, "alerts": [], "recommendations": [], "stats": {},
        }]
        with patch("tieba_mecha.core.behavior_audit.audit_all_accounts", new=AsyncMock(return_value=fake_report)):
            from tieba_mecha.core.behavior_audit import audit_and_govern
            await audit_and_govern(db)

        account = await db.get_account_by_id(acc.id)
        assert account.post_weight == 5  # 未调整


# ========================================================================
# 存活反馈闭环
# ========================================================================

class TestSurvivalFeedback:
    @pytest.fixture(autouse=True)
    async def _seed_forum(self, db):
        """准备一个火力目标贴吧 + 三个已发物料"""
        self.db = db
        self.acc = await db.add_account(name="surv-acc", bduss="x" * 200)
        await db.add_forum(fid=1001, fname="存活测试吧", account_id=self.acc.id)
        async with db.async_session() as session:
            from tieba_mecha.db.models import Forum
            from sqlalchemy import update as sa_update
            await session.execute(
                sa_update(Forum).where(Forum.fname == "存活测试吧").values(is_post_target=True)
            )
            await session.commit()

    async def _add_posted_material(self, title, status, reason="", posted_hours_ago=72):
        # add_materials_bulk 按内容去重且只返回计数，内容需唯一，id 事后按标题查询
        await self.db.add_materials_bulk([(title, f"{title}的正文内容，" * 5)])
        from tieba_mecha.db.models import MaterialPool
        from sqlalchemy import select
        async with self.db.async_session() as session:
            mid = (await session.execute(
                select(MaterialPool.id)
                .where(MaterialPool.title == title)
                .order_by(MaterialPool.id.desc())
                .limit(1)
            )).scalar_one_or_none()
        assert mid is not None, f"物料未插入: {title}"
        posted_time = datetime.now() - timedelta(hours=posted_hours_ago)
        await self.db.update_material_status(
            mid, "success",
            posted_fname="存活测试吧", posted_tid=9000 + mid,
            posted_account_id=self.acc.id, posted_time=posted_time,
        )
        await self.db.update_material_survival_status(mid, status, reason)
        return mid

    @pytest.mark.asyncio
    async def test_death_reason_routing(self, db):
        """吧务删除关停火力目标；系统删除（内容问题）不关停"""
        await self._add_posted_material("吧务删的帖子", "dead", "deleted_by_mod")
        await self._add_posted_material("系统删的帖子", "dead", "deleted_by_system")

        # 吧务删除在存活探测时已联动封吧；auto_sync 再跑一遍同步幂等
        await db.auto_sync_post_target()

        from tieba_mecha.db.models import Forum
        from sqlalchemy import select
        async with db.async_session() as session:
            forum = (await session.execute(
                select(Forum).where(Forum.fname == "存活测试吧")
            )).scalar_one()
        assert forum.is_post_target is False
        assert forum.is_banned is True

    @pytest.mark.asyncio
    async def test_system_delete_alone_does_not_close_forum(self, db):
        """仅系统删除（无吧务风险）不应关闭火力目标，走内容策略路由"""
        await self._add_posted_material("又被系统删了", "dead", "deleted_by_system")

        await db.auto_sync_post_target()

        from tieba_mecha.db.models import Forum
        from sqlalchemy import select
        async with db.async_session() as session:
            forum = (await session.execute(
                select(Forum).where(Forum.fname == "存活测试吧")
            )).scalar_one()
        assert forum.is_post_target is True

    @pytest.mark.asyncio
    async def test_get_survival_examples(self, db):
        """存活样本：正例来自存活 ≥48h 的帖子，反例来自系统删除"""
        await self._add_posted_material("存活良好的标题写法", "alive", posted_hours_ago=96)
        await self._add_posted_material("被系统干掉的标题", "dead", "deleted_by_system", posted_hours_ago=24)

        examples = await db.get_survival_examples(fname="存活测试吧")
        assert "存活良好的标题写法" in examples["positive"]
        assert "被系统干掉的标题" in examples["negative"]

    @pytest.mark.asyncio
    async def test_death_reason_stats(self, db):
        await self._add_posted_material("甲", "dead", "deleted_by_system")
        await self._add_posted_material("乙", "dead", "deleted_by_system")
        stats = await db.get_death_reason_stats(days=14)
        assert stats.get("deleted_by_system", 0) >= 2

    @pytest.mark.asyncio
    async def test_run_survival_governance_alerts(self, db):
        """系统删除聚集 → 告警 + 通知"""
        await self._add_posted_material("帖一", "dead", "deleted_by_system")
        await self._add_posted_material("帖二", "dead", "deleted_by_system")
        await self._add_posted_material("帖三", "dead", "deleted_by_system")

        from tieba_mecha.core.survival_feedback import run_survival_governance
        result = await run_survival_governance(db)

        assert result["alerted"] is True
        assert result["death_stats"].get("deleted_by_system", 0) >= 3

        notifications = await db.get_all_notifications(limit=10)
        assert any("系统删除" in (n.title or "") for n in notifications)


# ========================================================================
# 存活检测统一入口：定时轮转 + 手动按钮共用（run_survival_check）
# ========================================================================

class TestSurvivalDetection:
    async def _add_posted(self, title, posted_hours_ago=72):
        """造一个已发帖成功的物料，返回 (mid, tid)"""
        await self.db.add_materials_bulk([(title, f"{title}的正文内容，" * 5)])
        from tieba_mecha.db.models import MaterialPool
        from sqlalchemy import select
        async with self.db.async_session() as session:
            mid = (await session.execute(
                select(MaterialPool.id)
                .where(MaterialPool.title == title)
                .order_by(MaterialPool.id.desc())
                .limit(1)
            )).scalar_one_or_none()
        assert mid is not None, f"物料未插入: {title}"
        tid = 9000 + mid
        await self.db.update_material_status(
            mid, "success",
            posted_fname="存活测试吧", posted_tid=tid,
            posted_account_id=1, posted_time=datetime.now() - timedelta(hours=posted_hours_ago),
        )
        return mid, tid

    async def _set_checked(self, mid, hours_ago):
        from tieba_mecha.db.models import MaterialPool
        from sqlalchemy import update as sa_update
        async with self.db.async_session() as session:
            await session.execute(
                sa_update(MaterialPool).where(MaterialPool.id == mid)
                .values(last_checked_at=datetime.now() - timedelta(hours=hours_ago))
            )
            await session.commit()

    async def _get_material(self, mid):
        from tieba_mecha.db.models import MaterialPool
        async with self.db.async_session() as session:
            return await session.get(MaterialPool, mid)

    @pytest.mark.asyncio
    async def test_writes_verdict_and_counts(self, db, monkeypatch):
        """alive/dead 结论写入 survival_status/death_reason 并推进检测时间"""
        self.db = db
        mid1, tid1 = await self._add_posted("轮转甲帖")
        mid2, tid2 = await self._add_posted("轮转乙帖")

        async def fake_check(tid):
            return {tid1: ("alive", ""), tid2: ("dead", "deleted_unknown")}[tid]

        monkeypatch.setattr("tieba_mecha.core.post.check_post_survival", fake_check)
        from tieba_mecha.core.survival_feedback import run_survival_check
        result = await run_survival_check(db)

        assert result["skipped"] is False
        assert (result["total"], result["checked"], result["alive"], result["dead"]) == (2, 2, 1, 1)
        assert result["unknown"] == 0 and result["failed"] == 0
        m1, m2 = await self._get_material(mid1), await self._get_material(mid2)
        assert m1.survival_status == "alive"
        assert m2.survival_status == "dead" and m2.death_reason == "deleted_unknown"
        assert m1.last_checked_at is not None and m2.last_checked_at is not None

    @pytest.mark.asyncio
    async def test_unknown_keeps_status_but_advances_rotation(self, db, monkeypatch):
        """unknown（验证码/网络拦截）不写存活档案，仅推进 last_checked_at 防轮转卡头"""
        self.db = db
        mid, tid = await self._add_posted("验证码钉子户")
        await db.update_material_survival_status(mid, "alive")
        checked_before = (await self._get_material(mid)).last_checked_at

        async def fake_check(tid_):
            return "unknown", "captcha_required"

        monkeypatch.setattr("tieba_mecha.core.post.check_post_survival", fake_check)
        from tieba_mecha.core.survival_feedback import run_survival_check
        result = await run_survival_check(db)

        assert result["unknown"] == 1
        m = await self._get_material(mid)
        assert m.survival_status == "alive"  # 原档案保留
        assert m.last_checked_at is not None and m.last_checked_at > checked_before

    @pytest.mark.asyncio
    async def test_rotation_recent_first_then_stale(self, db):
        """轮转排序：近 7 天新帖优先；老帖内从未测过 > 最久未测"""
        self.db = db
        mid_old_checked, _ = await self._add_posted("老帖已测", posted_hours_ago=24 * 30)
        await self._set_checked(mid_old_checked, hours_ago=1)
        mid_old_never, _ = await self._add_posted("老帖从未测", posted_hours_ago=24 * 30)
        mid_recent, _ = await self._add_posted("新帖昨发", posted_hours_ago=24)
        await self._set_checked(mid_recent, hours_ago=1)

        picked = await db.get_materials_for_survival_check(limit=1)
        assert [m.id for m in picked] == [mid_recent]

        order = await db.get_materials_for_survival_check()
        assert [m.id for m in order] == [mid_recent, mid_old_never, mid_old_checked]

    @pytest.mark.asyncio
    async def test_lock_skips_concurrent_run(self, db, monkeypatch):
        """已有一轮在跑时直接 skipped，不并发打 API"""
        self.db = db

        async def fail_check(tid):
            raise AssertionError("互斥期间不应发起检测")

        monkeypatch.setattr("tieba_mecha.core.post.check_post_survival", fail_check)
        from tieba_mecha.core.survival_feedback import _survival_check_lock, run_survival_check
        async with _survival_check_lock:
            result = await run_survival_check(db)
        assert result["skipped"] is True

    @pytest.mark.asyncio
    async def test_daemon_task_respects_settings(self, db, monkeypatch):
        """daemon 任务读 settings 开关与批次；禁用时完全不探测"""
        import tieba_mecha.core.daemon as daemon_mod
        import tieba_mecha.core.survival_feedback as sf_mod
        self.db = db

        calls = {}

        async def fake_run(check_db, limit=None, **kw):
            calls["limit"] = limit
            return {"skipped": False, "total": 0}

        async def fake_get_db():
            return db

        monkeypatch.setattr(daemon_mod, "get_db", fake_get_db)
        monkeypatch.setattr(sf_mod, "run_survival_check", fake_run)

        await db.set_setting("survival_check_enabled", "true")
        await db.set_setting("survival_check_batch", "55")
        await daemon_mod.do_survival_check_task()
        assert calls.get("limit") == 55

        await db.set_setting("survival_check_enabled", "false")
        calls.clear()
        await daemon_mod.do_survival_check_task()
        assert not calls


# ========================================================================
# AI 改写增强：相似度自检 + 存活 few-shot
# ========================================================================

class _FakeResp:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload, ensure_ascii=False)


class _FakeSession:
    """按顺序返回预置的响应载荷"""

    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.requests: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append(json or {})
        content = self._contents.pop(0)
        resp = _FakeResp({"choices": [{"message": {"content": content}}]})

        class _CM:
            async def __aenter__(self_inner):
                return resp

            async def __aexit__(self_inner, *args):
                return False

        return _CM()

    async def close(self):
        pass


def _make_optimizer(session_payloads: list[str]) -> tuple[AIOptimizer, _FakeSession]:
    db = AsyncMock()
    db.get_setting = AsyncMock(side_effect=lambda k, d: {
        "ai_api_key": "test-key",
        "ai_base_url": "https://api.test/v1/",
        "ai_model": "test-model",
    }.get(k, d))
    optimizer = AIOptimizer(db)
    session = _FakeSession(session_payloads)
    optimizer._get_session = AsyncMock(return_value=session)
    return optimizer, session


ORIGINAL_CONTENT = "这份小学二年级数学教辅资料覆盖全学期知识点，包含同步练习与单元测试卷，适合课后巩固复习使用，高清可打印。"


def _ai_json(title: str, content: str) -> str:
    return json.dumps({"title": title, "content": content}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _stub_pro_and_rate_limit():
    stub = AsyncMock()
    stub.status = 1
    with patch("tieba_mecha.core.auth.get_auth_manager", new=AsyncMock(return_value=stub)), \
         patch.object(AIOptimizer, "_wait_for_rate_limit", new=AsyncMock()):
        yield


class TestAISimilaritySelfCheck:
    def test_bigram_jaccard_basics(self):
        long_a = "这是一段足够长的文本内容用于相似度计算的单元测试样例甲。" * 2
        assert _bigram_jaccard(long_a, long_a) == pytest.approx(1.0)
        long_b = "南辕北辙风马牛不相及的另一段完全无关文本内容书写方式差异巨大示例乙。" * 2
        assert _bigram_jaccard(long_a, long_b) < 0.3
        assert _bigram_jaccard("短", "短") == 0.0  # 过短不具判断意义

    @pytest.mark.asyncio
    async def test_lazy_rewrite_rejected_after_retry(self):
        """两次都返回近似原文 → 放弃改写（回退原文），报相似度过高"""
        lazy = _ai_json("改写后标题", ORIGINAL_CONTENT)  # 内容与原文一致 = 摆烂
        optimizer, session = _make_optimizer([lazy, lazy])

        ok, t, c, err = await optimizer.optimize_post(
            "原标题", ORIGINAL_CONTENT, persona="normal"
        )
        assert ok is False
        assert t == "原标题" and c == ORIGINAL_CONTENT
        assert "相似度过高" in err
        assert len(session.requests) == 2  # 重试了一次

    @pytest.mark.asyncio
    async def test_retry_with_stronger_instruction(self):
        """首次摆烂 → 强化指令重试 → 第二次真正改写则接受"""
        lazy = _ai_json("改写后标题", ORIGINAL_CONTENT)
        good = _ai_json("全新标题写法", "围绕二年级数学的同步练习册，编排循序渐进，单元卷齐全，打印清晰，家长辅导好帮手，内容组织方式完全不同。")
        optimizer, session = _make_optimizer([lazy, good])

        ok, t, c, err = await optimizer.optimize_post(
            "原标题", ORIGINAL_CONTENT, persona="normal"
        )
        assert ok is True, err
        assert "全新标题写法" == t
        # 第二次请求应带强化指令
        assert "过于相似" in session.requests[1]["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_survival_examples_injected_into_prompt(self):
        """存活样本（正例/反例标题）注入 user prompt"""
        good = _ai_json("新标题", "完全重写的另一种表述与结构的内容文本，信息量等价但措辞与句式彻底变化，避免任何连续相同表述出现。")
        optimizer, session = _make_optimizer([good])

        ok, _, _, err = await optimizer.optimize_post(
            "原标题", ORIGINAL_CONTENT, persona="normal",
            survival_examples={
                "positive": ["存活标题示例一", "存活标题示例二"],
                "negative": ["被删标题示例"],
            },
        )
        assert ok is True, err
        prompt = session.requests[0]["messages"][1]["content"]
        assert "存活标题示例一" in prompt
        assert "被删标题示例" in prompt
        assert "严禁抄袭" in prompt

    @pytest.mark.asyncio
    async def test_no_survival_section_without_examples(self):
        good = _ai_json("新标题", "完全重写的另一种表述与结构的内容文本，信息量等价但措辞与句式彻底变化，避免任何连续相同表述出现。")
        optimizer, session = _make_optimizer([good])

        await optimizer.optimize_post("原标题", ORIGINAL_CONTENT, persona="normal")
        assert "存活参考" not in session.requests[0]["messages"][1]["content"]


# ========================================================================
# 行为审计口径重构：签到率剔除跳过行 / 签到时间规律性 / 工作时间告警样本门槛
# ========================================================================

class TestAuditSignRateSemantics:
    """签到率口径重构：跳过行剔除分母，"过于规律"改由签到时间分布衡量"""

    async def _make_account_with_forum(self, db):
        acc = await db.add_account(name="audit-acc", bduss="x" * 200)
        forum = await db.add_forum(fid=1, fname="审计吧", account_id=acc.id)
        return acc, forum

    async def _insert_signs(self, db, forum, rows):
        from tieba_mecha.db.models import SignLog
        base = datetime.now() - timedelta(days=2)  # 相对日期：勿用硬编码，7天窗口滑动后fixture会老化出窗
        async with db.async_session() as session:
            for success, message, hour in rows:
                session.add(SignLog(forum_id=forum.id, fname=forum.fname, success=success,
                                    message=message, signed_at=base.replace(hour=hour, minute=30, second=0, microsecond=0)))
            await session.commit()

    @pytest.mark.asyncio
    async def test_skip_rows_excluded_from_rate(self, db):
        from tieba_mecha.core.sign import SIGN_SKIP_MESSAGE
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        acc, forum = await self._make_account_with_forum(db)
        rows = [(True, "签到成功", 9)] * 8 + [(False, SIGN_SKIP_MESSAGE, 9)] * 2
        await self._insert_signs(db, forum, rows)

        report = await BehaviorAuditor(db).analyze_account(acc.id, days=7)
        stats = report["stats"]
        assert stats["sign_attempts"] == 8
        assert stats["sign_skips"] == 2
        assert stats["sign_rate"] == pytest.approx(1.0)  # 真实成功率 100%，跳过不扣分
        assert stats["sign_skip_ratio"] == pytest.approx(0.2)
        assert not any("签到" in a for a in report["alerts"])

    @pytest.mark.asyncio
    async def test_low_real_success_alert_direction(self, db):
        """真实成功率低 = 疑似被风控，建议方向是降频查代理而非加大签到"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        acc, forum = await self._make_account_with_forum(db)
        rows = [(True, "签到成功", 9)] * 2 + [(False, "风控拒绝", 9)] * 8
        await self._insert_signs(db, forum, rows)

        report = await BehaviorAuditor(db).analyze_account(acc.id, days=7)
        assert report["stats"]["sign_rate"] == pytest.approx(0.2)
        assert any("疑似被风控拦截" in a for a in report["alerts"])
        assert any("切勿加大签到力度" in r for r in report["recommendations"])

    @pytest.mark.asyncio
    async def test_sign_hour_concentration_alert(self, db):
        """成功签到集中在同一小时（≥20 次样本）→ 规律性告警并计分"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        acc, forum = await self._make_account_with_forum(db)
        rows = [(True, "签到成功", 9)] * 25 + [(True, "签到成功", 14)] * 3
        await self._insert_signs(db, forum, rows)

        report = await BehaviorAuditor(db).analyze_account(acc.id, days=7)
        assert any("签到时间过于集中" in a for a in report["alerts"])
        assert report["risk_score"] > 0

    @pytest.mark.asyncio
    async def test_sign_hour_concentration_needs_sample(self, db):
        """成功签到样本 <20 次不触发规律性告警"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        acc, forum = await self._make_account_with_forum(db)
        rows = [(True, "签到成功", 9)] * 10
        await self._insert_signs(db, forum, rows)

        report = await BehaviorAuditor(db).analyze_account(acc.id, days=7)
        assert not any("签到时间过于集中" in a for a in report["alerts"])

    @pytest.mark.asyncio
    async def test_inactive_proxy_excluded_from_stats(self, db):
        """已停用代理的累计失败数不再计入审计（陈史对后续风险无意义）"""
        proxy = await db.add_proxy(host="127.0.0.1", port=1080,
                                   username="", password="", protocol="socks5")
        for _ in range(11):
            await db.mark_proxy_fail(proxy.id)
        acc = await db.add_account(name="px-acc", bduss="z" * 200, proxy_id=proxy.id)

        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        report = await BehaviorAuditor(db).analyze_account(acc.id, days=7)
        # 阈值 10 次后代理已停用 → 不计入
        assert "proxy_fail_count" not in report["stats"]


class TestAuditWorkHoursSampleGuard:
    """工作时间告警样本门槛（≥5 帖才有统计意义）"""

    async def _insert_posts(self, db, acc, hours):
        from tieba_mecha.db.models import BatchPostLog
        base = datetime.now() - timedelta(days=2)  # 相对日期：勿用硬编码，7天窗口滑动后fixture会老化出窗
        async with db.async_session() as session:
            for i, h in enumerate(hours):
                session.add(BatchPostLog(task_id=f"t{i}", account_id=acc.id, account_name=acc.name,
                                         fname="吧", title=f"标题{i}", tid=1000 + i, status="success",
                                         created_at=base.replace(hour=h, minute=0, second=0, microsecond=0)))
            await session.commit()

    @pytest.mark.asyncio
    async def test_small_sample_no_alert(self, db):
        """2 帖全在 9-18 点：不再触发"工作时间集中"告警（旧版即误触发）"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        acc = await db.add_account(name="post-acc", bduss="y" * 200)
        await self._insert_posts(db, acc, [10, 14])

        report = await BehaviorAuditor(db).analyze_account(acc.id, days=7)
        assert not any("工作时间" in a for a in report["alerts"])

    @pytest.mark.asyncio
    async def test_sufficient_sample_alerts_and_scores(self, db):
        """6 帖全在 9-18 点：触发告警且计入风险评分"""
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        acc = await db.add_account(name="post-acc2", bduss="y" * 200)
        await self._insert_posts(db, acc, [10, 11, 12, 14, 16, 17])

        report = await BehaviorAuditor(db).analyze_account(acc.id, days=7)
        assert any("工作时间" in a for a in report["alerts"])
        assert report["risk_score"] > 0
