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
            sign_rate=0.99,
            hour_distribution={"10": 10},
            content_variety=0.3,
            avg_interval=1.0,
            proxy_fails=15,
        )
        assert score == pytest.approx(10.0)

    def test_clean_behavior_is_zero(self):
        from tieba_mecha.core.behavior_audit import BehaviorAuditor
        auditor = BehaviorAuditor(db=None)
        score = auditor._calculate_risk_score(
            sign_rate=0.8,
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
            sign_rate=0.99,                    # 签到过规律 → 2.0*0.2
            hour_distribution={"2": 8, "3": 1},  # 高度集中 → 3.0*0.25
            content_variety=0.4,               # 重复度高 → 3.0*0.25
            avg_interval=10.0,
            proxy_fails=0,
        )
        assert score >= 5.0


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
