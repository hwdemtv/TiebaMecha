"""任务预检服务（干跑）：不发帖，只校验账号/代理/目标贴吧/物料是否可用。

预检结果是两个 UI 的单源数据：
- 启动前确认摘要（账号 n · 贴吧 m · 物料 k · 预计发布 x 次）
- "干跑预检"按钮的完整体检报告

口径必须与 core/batch_post.execute_task 保持一致：
- 终态账号（banned/suspended/expired）不参与执行 → 排除出有效账号池
- 发布次数 = post_count，上限为 min(待发物料数, Free 配额)
- Free 版：单账号、单次 ≤3 帖、AI 关闭
- 凌晨 1-6 点时段延迟 ×2（TimeWindowDispatcher）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from ....core.auth import get_auth_manager, AuthStatus

# 与引擎 _EXCLUDED_STATUSES 保持一致：完全排除出加权池的终态状态
_TERMINAL_ACCOUNT_STATUSES = {"banned", "suspended", "expired"}
_STATUS_LABELS = {
    "active": "可用",
    "banned": "封禁",
    "expired": "失效",
    "error": "异常",
    "suspended_proxy": "代理暂停",
    "unknown": "未验证",
}
_QUIET_START, _QUIET_END = 1, 6  # 凌晨 1-6 点高风险时段（与 TimeWindowDispatcher 一致）
# 链接风险判定（与 web/pages/posts/helpers._LINK_RE 语义不同且**有意为之**：
# 此处是"含链接风险"的宽松判定（含 t.cn 短链与常见裸域名，命中即计数警告），
# helpers.extract_links 是"精确提取 http(s) 链接"（供展示/逐条列出）。
# 两者请勿互相替换。
_URL_RE = re.compile(r"https?://|t\.cn/|[-A-Za-z0-9.]{4,}\.(?:com|cn|net|top|xyz|me|cc)\b")


@dataclass
class PreflightIssue:
    """一条预检结论。level: error=拦截 / warning=显著警告 / info=提示。"""

    level: str
    code: str
    message: str


@dataclass
class PreflightReport:
    issues: list[PreflightIssue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    risk_score: float = 0.0
    risk_factors: list[str] = field(default_factory=list)
    # 预检后的有效目标（剔除已封禁贴吧），启动时应以此为准
    effective_fnames: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def infos(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.level == "info"]


def format_duration(seconds: float) -> str:
    """把秒数格式化为人类可读时长。"""
    if seconds <= 0:
        return "0 分钟"
    total_min = int(seconds // 60)
    if total_min < 1:
        return "不足 1 分钟"
    hours, minutes = divmod(total_min, 60)
    if hours <= 0:
        return f"约 {minutes} 分钟"
    return f"约 {hours} 小时 {minutes} 分钟"


# MaterialPool.title 为 String(500)，超过即无效
_IMPORT_TITLE_MAX = 500


@dataclass
class ImportScanReport:
    """物料导入预扫结果（索引进 pairs 原始列表）。"""

    total: int = 0
    empty_entries: list[int] = field(default_factory=list)      # 标题正文全空
    missing_title: list[int] = field(default_factory=list)      # 标题为空
    overlong_title: list[int] = field(default_factory=list)     # 标题超 DB 上限
    duplicate_groups: list[list[int]] = field(default_factory=list)
    with_links: list[int] = field(default_factory=list)         # 含链接/短链

    @property
    def has_warnings(self) -> bool:
        return bool(
            self.empty_entries or self.missing_title or self.overlong_title
            or self.duplicate_groups or self.with_links
        )

    def valid_indices(self) -> list[int]:
        """可导入的条目（非空、标题合规），重复项默认保留。"""
        bad = set(self.empty_entries) | set(self.overlong_title)
        return [i for i in range(self.total) if i not in bad]

    def dedup_indices(self) -> list[int]:
        """在有效项基础上每组重复只保留第一条。"""
        dup_extra = {i for group in self.duplicate_groups for i in group[1:]}
        return [i for i in self.valid_indices() if i not in dup_extra]


def scan_import_pairs(pairs: list[tuple[str, str]]) -> ImportScanReport:
    """对导入前的 (标题, 正文) 列表做质量扫描。纯函数，不触 UI/DB。"""
    report = ImportScanReport(total=len(pairs))
    seen: dict[str, int] = {}
    groups: dict[str, list[int]] = {}

    for idx, (title, content) in enumerate(pairs):
        t = (title or "").strip()
        c = (content or "").strip()
        if not t and not c:
            report.empty_entries.append(idx)
            continue
        if not t:
            report.missing_title.append(idx)
        if len(t) > _IMPORT_TITLE_MAX:
            report.overlong_title.append(idx)
        if _URL_RE.search(t) or _URL_RE.search(c):
            report.with_links.append(idx)
        key = f"{_normalize_text(t)}|{_normalize_text(c)}"
        if key:
            if key in seen:
                groups[key].append(idx)
            else:
                seen[key] = idx
                groups[key] = [idx]
    report.duplicate_groups = [g for g in groups.values() if len(g) > 1]
    return report


def _is_quiet_hour(dt: datetime) -> bool:
    return _QUIET_START <= dt.hour < _QUIET_END


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _bigrams(text: str) -> set[str]:
    return {text[i: i + 2] for i in range(len(text) - 1)} if len(text) >= 2 else {text} if text else set()


def _similarity(a: str, b: str) -> float:
    bg1, bg2 = _bigrams(a), _bigrams(b)
    if not bg1 or not bg2:
        return 1.0 if a == b else 0.0
    return len(bg1 & bg2) / len(bg1 | bg2)


class PreflightService:
    """组合引擎既有信号，输出结构化预检报告。只读，不产生任何发帖行为。"""

    # 物料扫描上限，防止超大池拖慢预检
    SCAN_CAP = 300
    # 高相似判定的两两比对样本上限
    SIMILARITY_SAMPLE_CAP = 150

    def __init__(self, db):
        self.db = db

    async def run(self, config) -> PreflightReport:
        """执行完整预检，返回结构化报告。"""
        report = PreflightReport()
        stats = report.stats

        accounts_info = await self._check_accounts(config, report, stats)
        fnames = await self._check_forums_async(config, report, stats)
        report.effective_fnames = fnames
        materials = await self._check_materials(config, report, stats)
        await self._check_quota_async(config, report, stats, accounts_info["effective"])
        self._check_schedule_and_delay(config, report, stats)
        self._scan_material_content(materials, report, stats)
        self._estimate(config, report, stats, accounts_info)
        self._score(config, report, stats, accounts_info)
        return report

    # ------------------------------------------------------------------
    async def _check_accounts(self, config, report: PreflightReport, stats: dict) -> dict:
        selected = list(dict.fromkeys(config.account_ids))
        stats["accounts_selected"] = len(selected)
        by_status: dict[str, list[str]] = {}
        effective_ids: list[int] = []
        no_proxy: list[str] = []
        warmup: list[str] = []

        all_accounts = {a.id: a for a in await self.db.get_accounts()} if selected else {}
        for aid in selected:
            acc = all_accounts.get(aid)
            if acc is None:
                by_status.setdefault("失效(已删除)", []).append(f"ID:{aid}")
                continue
            label = acc.user_name or acc.name or f"账号{acc.id}"
            if acc.status in _TERMINAL_ACCOUNT_STATUSES:
                by_status.setdefault(_STATUS_LABELS.get(acc.status, acc.status), []).append(label)
            else:
                effective_ids.append(aid)
                if not acc.proxy_id:
                    no_proxy.append(label)

        # 代理预热期账号本次不会发帖（引擎逐账号跳过）
        if effective_ids:
            from ....core.proxy import get_warmup_manager
            warmup_mgr = get_warmup_manager()
            for aid in effective_ids:
                try:
                    if await warmup_mgr.needs_warmup(self.db, aid):
                        acc = all_accounts.get(aid)
                        warmup.append(acc.user_name or acc.name or f"账号{aid}")
                except Exception:
                    pass  # 预热查询失败不阻断预检

        stats["accounts_effective"] = len(effective_ids)
        stats["accounts_excluded"] = {k: v for k, v in by_status.items()}
        stats["accounts_no_proxy"] = no_proxy
        stats["accounts_warmup"] = warmup

        if not selected:
            report.issues.append(PreflightIssue("error", "no_accounts", "未选择任何执行账号"))
        elif not effective_ids:
            detail = "、".join(f"{k} {len(v)} 个" for k, v in by_status.items())
            report.issues.append(PreflightIssue(
                "error", "no_effective_accounts", f"所选账号全部不可用（{detail}），无有效兵力"))
        else:
            if by_status:
                detail = "、".join(f"{k} {len(v)} 个" for k, v in by_status.items())
                report.issues.append(PreflightIssue(
                    "info", "accounts_excluded", f"{len(selected) - len(effective_ids)} 个账号不参与执行（{detail}），引擎已自动排除"))
            if warmup:
                report.issues.append(PreflightIssue(
                    "warning", "accounts_warmup",
                    f"{len(warmup)} 个账号处于代理预热期，本次不会发帖：{'、'.join(warmup)}"))
            if no_proxy:
                report.issues.append(PreflightIssue(
                    "warning", "accounts_no_proxy",
                    f"{len(no_proxy)} 个账号未绑定代理（裸连），风控暴露面大：{'、'.join(no_proxy[:5])}{'…' if len(no_proxy) > 5 else ''}"))
        return {"effective": effective_ids, "no_proxy": no_proxy, "all": all_accounts}

    # ------------------------------------------------------------------
    async def _check_forums_async(self, config, report: PreflightReport, stats: dict) -> list[str]:
        merged = config.get_fnames()
        stats["forums_selected"] = len(merged)
        if not merged:
            report.issues.append(PreflightIssue("error", "no_forums", "未选择任何目标贴吧"))
            return []

        all_forums = await self.db.get_all_unique_forums()
        valid = {f["fname"]: f for f in all_forums if not f["is_banned"]}
        safe = {f["fname"] for f in all_forums if f["is_post_target"]}

        effective = [fn for fn in merged if fn in valid]
        banned_removed = [fn for fn in merged if fn not in valid]
        unsafe = [fn for fn in effective if fn not in safe]

        stats["forums_effective"] = effective
        stats["forums_banned_removed"] = banned_removed
        stats["forums_unsafe"] = unsafe

        if banned_removed:
            report.issues.append(PreflightIssue(
                "warning", "forums_banned_removed",
                f"{len(banned_removed)} 个已封禁/失效贴吧将被自动移除：{'、'.join(banned_removed[:8])}{'…' if len(banned_removed) > 8 else ''}"))
        if not effective:
            report.issues.append(PreflightIssue("error", "no_effective_forums", "目标贴吧全部已封禁/失效"))
        if unsafe:
            report.issues.append(PreflightIssue(
                "warning", "forums_unsafe",
                f"{len(unsafe)} 个非安全贴吧（未标记为发帖目标），可能被拦截：{'、'.join(unsafe[:8])}{'…' if len(unsafe) > 8 else ''}"))
        return effective

    # ------------------------------------------------------------------
    async def _check_materials(self, config, report: PreflightReport, stats: dict) -> list:
        pending = await self.db.get_materials(status="pending", limit=None)
        stats["materials_pending"] = len(pending)
        if not pending:
            report.issues.append(PreflightIssue(
                "error", "no_materials", "排期池无待发(pending)物料，请先导入或回炉重置"))
            return []

        planned = min(config.post_count, len(pending)) if config.post_count > 0 else 0
        if config.post_count <= 0:
            report.issues.append(PreflightIssue(
                "error", "invalid_post_count", "发帖数量未设置（total=0）"))
            planned = 0
        elif planned < config.post_count:
            report.issues.append(PreflightIssue(
                "info", "materials_capped",
                f"待发物料仅 {len(pending)} 条，少于计划的 {config.post_count} 帖，实际将发布 {planned} 帖"))
        stats["planned_posts"] = planned
        return pending

    # ------------------------------------------------------------------
    async def _check_quota_async(self, config, report: PreflightReport, stats: dict, effective_ids: list) -> None:
        """Free 版配额截断，口径与引擎一致；结果写入 stats 供摘要展示。"""
        try:
            am = await get_auth_manager()
            _ = await am.check_local_status()
            is_pro = am.status == AuthStatus.PRO
        except Exception:
            is_pro = False  # 授权态未知按 Free 保守处理
        stats["free_tier"] = not is_pro

        if not is_pro:
            caps = []
            if len(effective_ids) > 1:
                caps.append("仅保留 1 个账号")
            if config.post_count > 3:
                caps.append(f"单次任务上限 3 帖（计划 {config.post_count}）")
            if config.use_ai:
                caps.append("AI 改写不可用，已自动关闭")
            stats["ai_effective"] = False
            if caps:
                report.issues.append(PreflightIssue(
                    "warning", "free_tier_caps", f"Free 版配额限制：{'；'.join(caps)}"))
        else:
            stats["ai_effective"] = config.use_ai

    # ------------------------------------------------------------------
    def _check_schedule_and_delay(self, config, report: PreflightReport, stats: dict) -> None:
        stats["mode"] = "scheduled" if config.use_schedule else "immediate"
        now = datetime.now()
        quiet_dt: datetime | None = None
        if config.use_schedule:
            stats["schedule_type"] = config.schedule_type
            if config.schedule_time:
                stats["schedule_time"] = config.schedule_time.strftime("%Y-%m-%d %H:%M")
                if _is_quiet_hour(config.schedule_time):
                    quiet_dt = config.schedule_time
        elif _is_quiet_hour(now):
            quiet_dt = now

        if quiet_dt is not None:
            report.issues.append(PreflightIssue(
                "warning", "quiet_hours",
                f"{'计划执行时间' if config.use_schedule else '当前时间'}处于凌晨 1-6 点高风险时段，引擎将自动启用双倍延迟"))

        if config.delay_min < 30:
            report.issues.append(PreflightIssue(
                "warning", "delay_too_short",
                f"发帖间隔过短（最短 {config.delay_min:g} 秒），同 IP 高频发布极易触发风控，建议 ≥120 秒"))
        elif config.delay_min < 60:
            report.issues.append(PreflightIssue(
                "warning", "delay_short",
                f"发帖间隔偏短（最短 {config.delay_min:g} 秒），建议 ≥120 秒"))

        if config.delay_max < config.delay_min:
            report.issues.append(PreflightIssue(
                "warning", "delay_inverted",
                f"间隔上限（{config.delay_max:g}s）小于下限（{config.delay_min:g}s），将按下限执行"))
            stats["delay_max_effective"] = config.delay_min
        else:
            stats["delay_max_effective"] = config.delay_max

    # ------------------------------------------------------------------
    def _scan_material_content(self, materials, report: PreflightReport, stats: dict) -> None:
        """物料质量扫描：缺标题/空正文/批内重复/含链接。"""
        sample = materials[: self.SCAN_CAP]
        missing_title, empty_content, with_links = [], [], 0
        seen: dict[str, int] = {}
        dup_groups: dict[str, list[int]] = {}

        for m in sample:
            title = (m.title or "").strip()
            content = (m.content or "").strip()
            if not title:
                missing_title.append(m.id)
            if not content:
                empty_content.append(m.id)
            if _URL_RE.search(title) or _URL_RE.search(content):
                with_links += 1
            key = f"{_normalize_text(title)}|{_normalize_text(content)}"
            if key:
                if key in seen:
                    dup_groups.setdefault(key, [seen[key]]).append(m.id)
                else:
                    seen[key] = m.id

        stats["materials_missing_title"] = missing_title
        stats["materials_empty_content"] = empty_content
        stats["materials_with_links"] = with_links
        stats["materials_dup_groups"] = [ids for ids in dup_groups.values()]

        if missing_title:
            report.issues.append(PreflightIssue(
                "warning", "materials_missing_title",
                f"{len(missing_title)} 条物料缺标题（ID: {missing_title[:10]}{'…' if len(missing_title) > 10 else ''}），将仅发正文"))
        if empty_content:
            report.issues.append(PreflightIssue(
                "warning", "materials_empty_content",
                f"{len(empty_content)} 条物料正文为空（ID: {empty_content[:10]}{'…' if len(empty_content) > 10 else ''}）"))
        if dup_groups:
            total_dup = sum(len(g) - 1 for g in dup_groups.values())
            report.issues.append(PreflightIssue(
                "warning", "materials_duplicate",
                f"发现 {len(dup_groups)} 组完全重复的物料（合计多余 {total_dup} 条），同内容重复投放易触发风控"))

        # 高相似（非完全相同）检测：限制样本量做两两比对
        sim_pairs = self._find_high_similarity(sample)
        stats["materials_high_similarity_pairs"] = sim_pairs
        if sim_pairs:
            report.issues.append(PreflightIssue(
                "info", "materials_similar",
                f"{len(sim_pairs)} 对物料内容高度相似（≥90%），建议开启 AI 改写或增差异"))

    def _find_high_similarity(self, sample) -> list[tuple[int, int]]:
        texts = [
            _normalize_text(f"{m.title}{m.content}")
            for m in sample[: self.SIMILARITY_SAMPLE_CAP]
        ]
        pairs: list[tuple[int, int]] = []
        for i in range(len(texts)):
            if not texts[i]:
                continue
            for j in range(i + 1, len(texts)):
                if texts[i] == texts[j]:
                    continue  # 完全重复已单独统计
                if abs(len(texts[i]) - len(texts[j])) > 0.3 * max(len(texts[i]), 1):
                    continue  # 长度差过大直接跳过，省去 bigram 计算
                if _similarity(texts[i], texts[j]) >= 0.9:
                    pairs.append((sample[i].id, sample[j].id))
                    if len(pairs) >= 20:
                        return pairs
        return pairs

    # ------------------------------------------------------------------
    def _estimate(self, config, report: PreflightReport, stats: dict, accounts_info: dict) -> None:
        planned = stats.get("planned_posts", 0)
        d_min = config.delay_min
        d_max = stats.get("delay_max_effective", config.delay_max)
        # 凌晨时段双倍延迟（与引擎一致）
        multiplier = 2 if _is_quiet_hour(datetime.now()) else 1
        est_min = planned * d_min * multiplier
        est_max = planned * d_max * multiplier
        stats["estimated_duration"] = (format_duration(est_min), format_duration(est_max))
        stats["delay_multiplier"] = multiplier

    # ------------------------------------------------------------------
    def _score(self, config, report: PreflightReport, stats: dict, accounts_info: dict) -> None:
        """任务风险评分 0-10：仅展示，不拦截启动。"""
        score = 0.0
        factors: list[str] = []

        d_min = config.delay_min
        if d_min < 30:
            score += 3
            factors.append(f"间隔过短({d_min:g}s) +3")
        elif d_min < 60:
            score += 1.5
            factors.append(f"间隔偏短({d_min:g}s) +1.5")

        if stats.get("delay_multiplier", 1) > 1:
            score += 2
            factors.append("凌晨高风险时段 +2")

        unsafe = stats.get("forums_unsafe") or []
        if unsafe:
            add = min(2.0, 0.5 * len(unsafe))
            score += add
            factors.append(f"含 {len(unsafe)} 个非安全贴吧 +{add:g}")

        effective_n = stats.get("accounts_effective", 0)
        no_proxy = accounts_info.get("no_proxy") or []
        if effective_n > 0:
            naked_ratio = len(no_proxy) / effective_n
            if naked_ratio > 0.5:
                score += 2
                factors.append("过半账号裸连 +2")
            elif no_proxy:
                score += 1
                factors.append(f"{len(no_proxy)} 个账号裸连 +1")
        if effective_n == 1:
            score += 1.5
            factors.append("单账号执行 +1.5")

        planned = stats.get("planned_posts", 0)
        if planned > 50:
            score += 1.5
            factors.append(f"单次投放 {planned} 帖 +1.5")
        elif planned > 20:
            score += 0.75
            factors.append(f"单次投放 {planned} 帖 +0.75")

        if stats.get("materials_dup_groups"):
            score += 1
            factors.append("存在重复物料 +1")

        report.risk_score = round(min(10.0, score), 1)
        report.risk_factors = factors
