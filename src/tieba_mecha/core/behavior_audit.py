"""行为审计模块：分析账号行为模式，检测异常并提供改进建议"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from ..db.crud import Database
from .logger import log_info, log_warn


class BehaviorAuditor:
    """
    行为审计器：通过分析账号近期的操作日志，检测行为模式异常，
    提供针对性的风险降低建议。

    检测维度：
    - 签到率异常（过于规律）
    - 发帖时间集中度过高
    - 内容重复度
    - 连续操作间隔是否自然
    """

    def __init__(self, db: Database):
        self.db = db

    async def analyze_account(self, account_id: int, days: int = 7) -> dict[str, Any]:
        """
        分析指定账号近 N 天的行为数据，返回审计报告。

        Returns:
            {
                "account_id": int,
                "risk_score": float (0-10, 越高越危险),
                "alerts": list[str],
                "recommendations": list[str],
                "stats": dict
            }
        """
        stats = await self._collect_stats(account_id, days)
        alerts = []
        recommendations = []

        # 维度 1：签到健康度（sign_rate 为剔除跳过后的真实成功率）
        sign_rate = stats.get("sign_rate", 0)
        attempts = stats.get("sign_attempts", 0)
        if attempts > 0:
            if sign_rate < 0.4:
                alerts.append(
                    f"签到真实成功率仅 {sign_rate:.0%}（{attempts} 次真实尝试），疑似被风控拦截"
                )
                recommendations.append("检查代理与网络环境，必要时降低该账号的操作频率，切勿加大签到力度")
            elif sign_rate < 0.7:
                alerts.append(f"签到真实成功率偏低 ({sign_rate:.0%})")
                recommendations.append("更换代理 IP 或降低签到频率，观察是否被针对性风控")

        # 跳过比例异常（跳过是保护行为，但过高说明账号实际活跃不足）
        skip_ratio = stats.get("sign_skip_ratio", 0)
        if skip_ratio > 0.5:
            alerts.append(f"签到跳过比例 {skip_ratio:.0%}，实际签到频次偏低")
            recommendations.append("检查拟人化跳过概率配置，或减少同日重复签到任务")

        # 签到时间规律性（防指纹核心指标：成功签到集中在同一小时是机器特征；
        # 跳过率本身测不了"过于规律"，改由时间分布衡量）
        sign_hours = stats.get("sign_hour_distribution", {})
        sign_success_total = sum(sign_hours.values())
        if sign_success_total >= 20:
            sign_peak = max(sign_hours.values()) / sign_success_total
            if sign_peak > 0.6:
                peak_hour = max(sign_hours, key=lambda k: sign_hours[k])
                alerts.append(f"签到时间过于集中：{peak_hour} 时占 {sign_peak:.0%}")
                recommendations.append("启用签到错峰调度，把每日签到窗口拉开到多个时段")

        # 维度 2：发帖时间分布
        hour_dist = stats.get("post_hour_distribution", {})
        if hour_dist:
            total_posts = sum(hour_dist.values())
            if total_posts >= 3:
                peak_ratio = max(hour_dist.values()) / total_posts
                if peak_ratio > 0.5:
                    peak_hour = max(hour_dist, key=hour_dist.get)
                    alerts.append(f"发帖时间过于集中：{peak_hour} 时占 {peak_ratio:.0%}")
                    recommendations.append("将发帖分散到更多时段，避免固定时间发帖")

            # 检查是否只在工作时间发帖（样本 ≥5 才有统计意义，2-3 帖触发纯属噪声）
            work_hours_posts = sum(v for k, v in hour_dist.items() if 9 <= int(k) <= 18)
            if total_posts >= 5 and work_hours_posts / total_posts > 0.9:
                alerts.append("几乎所有帖子都在工作时间 (9-18点) 发出")
                recommendations.append("增加晚间和周末的发帖比例，更贴近真实用户行为")

        # 维度 3：内容多样性
        content_variety = stats.get("content_unique_ratio", 1.0)
        if content_variety < 0.6:
            alerts.append(f"内容重复度过高 ({content_variety:.0%} 唯一)")
            recommendations.append("使用更多 AI 人格或手动增加文案变体")
        elif content_variety < 0.8:
            recommendations.append("内容多样性一般，可考虑增加文案素材库的丰富度")

        # 维度 4：操作间隔自然度
        avg_interval = stats.get("avg_post_interval_minutes", 0)
        if avg_interval > 0 and avg_interval < 5:
            alerts.append(f"平均发帖间隔仅 {avg_interval:.1f} 分钟，过于密集")
            recommendations.append("增大发帖间隔至 3-10 分钟，或启用 BionicDelay 拟人化延迟")

        # 维度 5：代理健康度
        proxy_fail_count = stats.get("proxy_fail_count", 0)
        if proxy_fail_count > 5:
            alerts.append(f"代理失败 {proxy_fail_count} 次，可能已被标记")
            recommendations.append("更换代理 IP 或使用住宅代理")

        # 计算风险评分 (0-10)
        risk_score = self._calculate_risk_score(
            sign_rate=sign_rate,
            sign_attempts=attempts,
            sign_hour_distribution=sign_hours,
            hour_distribution=hour_dist,
            content_variety=content_variety,
            avg_interval=avg_interval,
            proxy_fails=proxy_fail_count,
        )

        return {
            "account_id": account_id,
            "risk_score": round(risk_score, 1),
            "alerts": alerts,
            "recommendations": recommendations,
            "stats": stats,
        }

    async def _collect_stats(self, account_id: int, days: int) -> dict[str, Any]:
        """收集账号的行为统计数据"""
        stats: dict[str, Any] = {}

        try:
            async with self.db.async_session() as session:
                from sqlalchemy import select, func
                from ..db.models import SignLog, BatchPostLog, Forum

                cutoff = datetime.now() - timedelta(days=days)

                # 签到统计：拟人化跳过是防指纹保护行为（sign.py 落 success=False），
                # 不计入成功率分母——否则"签到率"实为 1-跳过概率，语义失效
                from .sign import SIGN_SKIP_MESSAGE
                sign_rows = (await session.execute(
                    select(SignLog.success, SignLog.message, SignLog.signed_at)
                    .join(Forum, SignLog.forum_id == Forum.id)
                    .where(
                        Forum.account_id == account_id,
                        SignLog.signed_at >= cutoff
                    )
                )).all()
                total = len(sign_rows)
                skips = sum(1 for r in sign_rows if r.message == SIGN_SKIP_MESSAGE)
                success = sum(1 for r in sign_rows if r.success)
                attempts = total - skips
                stats["total_signs"] = total
                stats["sign_skips"] = skips
                stats["sign_attempts"] = attempts
                stats["successful_signs"] = success
                # 真实成功率：仅统计真实尝试（跳过行剔除）
                stats["sign_rate"] = success / attempts if attempts > 0 else 0.0
                stats["sign_skip_ratio"] = skips / total if total > 0 else 0.0
                # 成功签到时间分布（跳过不触碰百度侧，失败无行为意义，
                # 只有成功签到才是账号在百度留下的行为指纹）
                sign_hours: dict[str, int] = {}
                for r in sign_rows:
                    if r.success and r.signed_at:
                        hour = str(r.signed_at.hour)
                        sign_hours[hour] = sign_hours.get(hour, 0) + 1
                stats["sign_hour_distribution"] = sign_hours

                # 发帖统计
                post_stmt = select(BatchPostLog).where(
                    BatchPostLog.account_id == account_id,
                    BatchPostLog.created_at >= cutoff,
                    BatchPostLog.status == "success"
                )
                post_result = await session.execute(post_stmt)
                posts = post_result.scalars().all()

                if posts:
                    stats["total_posts"] = len(posts)

                    # 时间分布
                    hour_dist: dict[str, int] = {}
                    for p in posts:
                        if p.created_at:
                            hour = str(p.created_at.hour)
                            hour_dist[hour] = hour_dist.get(hour, 0) + 1
                    stats["post_hour_distribution"] = hour_dist

                    # 内容唯一性（基于标题）
                    unique_titles = set(p.title for p in posts if p.title)
                    stats["unique_titles"] = len(unique_titles)
                    stats["content_unique_ratio"] = len(unique_titles) / max(len(posts), 1)

                    # 操作间隔
                    timestamps = sorted([p.created_at for p in posts if p.created_at])
                    if len(timestamps) >= 2:
                        intervals = [
                            (timestamps[i+1] - timestamps[i]).total_seconds() / 60
                            for i in range(len(timestamps) - 1)
                        ]
                        stats["avg_post_interval_minutes"] = sum(intervals) / len(intervals)
                    else:
                        stats["avg_post_interval_minutes"] = 0

                # 代理健康度：fail_count 是全生命周期累计值（无时间戳日志可回溯），
                # 只统计仍在轮换中的 active 代理——已停用代理的陈史对后续风险无意义
                account = await self.db.get_account_by_id(account_id)
                if account and account.proxy_id:
                    proxy = await self.db.get_proxy(account.proxy_id)
                    if proxy and proxy.is_active:
                        stats["proxy_fail_count"] = proxy.fail_count

        except Exception as e:
            stats["error"] = str(e)
            await log_warn(f"收集账号 {account_id} 行为数据失败: {e}")

        return stats

    def _calculate_risk_score(
        self,
        sign_rate: float = 0,
        hour_distribution: dict = None,
        content_variety: float = 1.0,
        avg_interval: float = 0,
        proxy_fails: int = 0,
        sign_attempts: int = 0,
        sign_hour_distribution: dict = None,
    ) -> float:
        """
        综合风险评分 (0-10)，各维度加权：
        - 签到健康度：20%（档位上限 2.0，取真实成功率档与签到时间规律档的较重者；
          sign_rate 为剔除拟人化跳过后的真实成功率）
        - 发帖时间集中度：25%（档位上限 3.0，发帖峰时档与工作时间集中档取较重者，
          工作时间档需样本 ≥5 帖）
        - 内容重复度：25%（档位上限 3.0）
        - 操作间隔：15%（档位上限 3.0）
        - 代理健康：15%（档位上限 3.0，仅统计仍在轮换中的 active 代理）

        每维度按危险程度取档位分，加权求和后按理论满分归一到 0-10。
        （历史 bug：曾直接累加 档位分×权重，理论满分仅 2.8，
        与 0-10 量表声明及 5.0 高风险阈值不符，高风险告警永不触发。）
        """
        raw = 0.0

        # 签到健康度评分（真实成功率与签到时间规律性，取较重档）
        sign_dim = 0.0
        if sign_attempts > 0:
            if sign_rate < 0.4:
                sign_dim = 2.0
            elif sign_rate < 0.7:
                sign_dim = 1.5
        if sign_hour_distribution:
            sign_total = sum(sign_hour_distribution.values())
            if sign_total >= 20:
                sign_peak = max(sign_hour_distribution.values()) / sign_total
                if sign_peak > 0.8:
                    sign_dim = max(sign_dim, 2.0)
                elif sign_peak > 0.6:
                    sign_dim = max(sign_dim, 1.0)
        raw += sign_dim * 0.20

        # 发帖时间集中度评分（峰时占比与工作时间集中，取较重档；样本过小不计）
        if hour_distribution:
            total = sum(hour_distribution.values())
            if total >= 3:
                post_dim = 0.0
                peak_ratio = max(hour_distribution.values()) / total
                if peak_ratio > 0.6:
                    post_dim = 3.0
                elif peak_ratio > 0.4:
                    post_dim = 1.5
                work_ratio = sum(v for k, v in hour_distribution.items() if 9 <= int(k) <= 18) / total
                if total >= 5 and work_ratio > 0.9:
                    post_dim = max(post_dim, 2.0)
                raw += post_dim * 0.25

        # 内容重复度评分
        if content_variety < 0.5:
            raw += 3.0 * 0.25
        elif content_variety < 0.7:
            raw += 2.0 * 0.25
        elif content_variety < 0.85:
            raw += 1.0 * 0.25

        # 操作间隔评分
        if 0 < avg_interval < 3:
            raw += 3.0 * 0.15
        elif 0 < avg_interval < 5:
            raw += 1.5 * 0.15

        # 代理健康评分
        if proxy_fails > 10:
            raw += 3.0 * 0.15
        elif proxy_fails > 5:
            raw += 2.0 * 0.15
        elif proxy_fails > 2:
            raw += 1.0 * 0.15

        # 理论满分 = Σ(各维度档位上限 × 权重) = 2.8
        max_raw = 2.0 * 0.20 + 3.0 * 0.25 + 3.0 * 0.25 + 3.0 * 0.15 + 3.0 * 0.15
        return min(10.0, raw / max_raw * 10)


async def audit_all_accounts(db: Database, days: int = 7) -> list[dict[str, Any]]:
    """
    审计所有矩阵账号，返回按风险评分降序排列的报告列表。
    """
    accounts = await db.get_matrix_accounts()
    if not accounts:
        return []

    auditor = BehaviorAuditor(db)
    reports = []

    for account in accounts:
        try:
            report = await auditor.analyze_account(account.id, days)
            report["account_name"] = account.name or f"账号-{account.id}"
            reports.append(report)
        except Exception as e:
            await log_warn(f"审计账号 [{account.name}] 失败: {e}")

    # 按风险评分降序排列
    reports.sort(key=lambda r: r.get("risk_score", 0), reverse=True)

    # 输出高风险账号警告
    for report in reports:
        if report.get("risk_score", 0) >= 5.0:
            await log_warn(
                f"⚠️ 高风险账号: {report['account_name']} "
                f"(风险评分: {report['risk_score']}/10, "
                f"告警数: {len(report.get('alerts', []))})"
            )

    return reports


async def audit_and_govern(db: Database, days: int = 7) -> list[dict[str, Any]]:
    """
    行为审计 + 自动治理（daemon 周期任务入口，闭环"审计 → 调度权重"）。

    对风险评分达到阈值的矩阵账号自动下调 post_weight（每次 -3，下限 1），
    使其在本发帖调度中的选中概率降低；下调写入 weight_history（source=
    behavior_audit），可追溯、可通过权重重算恢复。相关设置：
    - audit_auto_adjust_weight: 是否启用自动下调（默认 true）
    - audit_risk_threshold: 高风险阈值（默认 5.0，0-10）
    """
    reports = await audit_all_accounts(db, days=days)
    if not reports:
        return reports

    try:
        auto_adjust = (await db.get_setting("audit_auto_adjust_weight", "true")).lower() != "false"
    except Exception:
        auto_adjust = True
    try:
        threshold = float(await db.get_setting("audit_risk_threshold", "5.0"))
    except Exception:
        threshold = 5.0

    if not auto_adjust:
        return reports

    for report in reports:
        if report.get("risk_score", 0) < threshold:
            continue
        account_id = report.get("account_id")
        account_name = report.get("account_name", f"账号-{account_id}")
        try:
            account = await db.get_account_by_id(account_id)
            if not account:
                continue
            old_weight = account.post_weight or 5
            new_weight = max(1, old_weight - 3)
            if new_weight >= old_weight:
                continue  # 已在下限，无需调整
            await db.update_account_weight(account_id, new_weight, source="behavior_audit")
            await log_warn(
                f"📉 风险治理：账号 [{account_name}] 评分 {report['risk_score']}/10，"
                f"发帖权重 {old_weight} → {new_weight}（来源: behavior_audit）"
            )
            try:
                await db.add_notification(
                    type="warning",
                    title="行为风险自动治理",
                    message=(
                        f"账号 [{account_name}] 风险评分 {report['risk_score']}/10，"
                        f"已自动下调发帖权重 {old_weight} → {new_weight}。"
                        f"主要告警：{'；'.join(report.get('alerts', [])[:3])}"
                    ),
                )
            except Exception:
                pass  # 通知失败不影响治理本身
        except Exception as e:
            await log_warn(f"风险治理账号 [{report.get('account_name')}] 失败: {e}")

    return reports
