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

        # 维度 1：签到率分析
        sign_rate = stats.get("sign_rate", 0)
        if sign_rate > 0.95:
            alerts.append(f"签到率过高 ({sign_rate:.0%})，行为过于规律")
            recommendations.append("建议随机跳过 5-10% 的贴吧签到，模拟真人偶尔忘记签到的行为")
        elif sign_rate < 0.3 and stats.get("total_signs", 0) > 10:
            alerts.append(f"签到率过低 ({sign_rate:.0%})，账号活跃度不足")
            recommendations.append("适当增加签到频率，提升账号权重")

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

            # 检查是否只在工作时间发帖
            work_hours_posts = sum(v for k, v in hour_dist.items() if 9 <= int(k) <= 18)
            if total_posts > 0 and work_hours_posts / total_posts > 0.9:
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

                # 签到统计
                from sqlalchemy import case
                sign_stmt = select(
                    func.count(SignLog.id).label("total"),
                    func.sum(case((SignLog.success == True, 1), else_=0)).label("success")
                ).join(
                    Forum, SignLog.forum_id == Forum.id
                ).where(
                    Forum.account_id == account_id,
                    SignLog.signed_at >= cutoff
                )
                sign_result = await session.execute(sign_stmt)
                row = sign_result.one_or_none()
                if row:
                    total = row.total or 0
                    success = row.success or 0
                    stats["total_signs"] = total
                    stats["successful_signs"] = success
                    stats["sign_rate"] = success / max(total, 1)

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

                # 代理失败统计
                account = await self.db.get_account_by_id(account_id)
                if account and account.proxy_id:
                    proxy = await self.db.get_proxy(account.proxy_id)
                    if proxy:
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
    ) -> float:
        """
        综合风险评分 (0-10)，各维度加权：
        - 签到异常：20%
        - 时间集中度：25%
        - 内容重复度：25%
        - 操作间隔：15%
        - 代理健康：15%
        """
        score = 0.0

        # 签到异常评分
        if sign_rate > 0.95:
            score += 2.0 * 0.20
        elif sign_rate < 0.3:
            score += 1.5 * 0.20

        # 时间集中度评分
        if hour_distribution:
            total = sum(hour_distribution.values())
            if total > 0:
                peak_ratio = max(hour_distribution.values()) / total
                if peak_ratio > 0.6:
                    score += 3.0 * 0.25
                elif peak_ratio > 0.4:
                    score += 1.5 * 0.25

        # 内容重复度评分
        if content_variety < 0.5:
            score += 3.0 * 0.25
        elif content_variety < 0.7:
            score += 2.0 * 0.25
        elif content_variety < 0.85:
            score += 1.0 * 0.25

        # 操作间隔评分
        if 0 < avg_interval < 3:
            score += 3.0 * 0.15
        elif 0 < avg_interval < 5:
            score += 1.5 * 0.15

        # 代理健康评分
        if proxy_fails > 10:
            score += 3.0 * 0.15
        elif proxy_fails > 5:
            score += 2.0 * 0.15
        elif proxy_fails > 2:
            score += 1.0 * 0.15

        return min(10.0, score)


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
