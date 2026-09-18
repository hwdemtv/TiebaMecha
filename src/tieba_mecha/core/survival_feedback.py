"""存活分析 → 发帖策略反馈闭环。

数据流：
    帖子存活检测（页面手动触发，写入 survival_status/death_reason）
        ├─ 吧务删除等贴吧侧风险 → auto_sync_post_target 关停火力目标（已有能力）
        └─ 系统风控删除（内容侧风险）→ 本模块聚集告警 + AI 改写策略联动
                ├─ 存活样本注入：material_repo.get_survival_examples → AIOptimizer few-shot
                └─ 聚集告警：短时间多次系统删除 → 通知建议深度改写/降低混淆密度

daemon 每 12 小时调用 run_survival_governance 执行一次策略同步。
"""

from __future__ import annotations

from ..db.crud import Database
from .logger import log_info, log_warn

# 近 N 天系统删除达到该数量时触发内容策略告警
SYSTEM_DELETE_ALERT_THRESHOLD = 3


async def run_survival_governance(db: Database, days: int = 14) -> dict:
    """
    存活治理主入口：
    1. 跑一遍贴吧侧风险关停（auto_sync_post_target，已分流系统删除）
    2. 统计近 N 天死亡原因，系统删除聚集时发内容策略告警

    Returns:
        {"closed_forums": int, "death_stats": {reason: count}, "alerted": bool}
    """
    closed = 0
    try:
        closed = await db.auto_sync_post_target()
        if closed > 0:
            await log_info(f"存活治理：{closed} 个贴吧因吧务侧风险自动关闭火力目标")
    except Exception as e:
        await log_warn(f"存活治理：同步火力目标失败: {e}")

    death_stats: dict[str, int] = {}
    try:
        death_stats = await db.get_death_reason_stats(days=days)
    except Exception as e:
        await log_warn(f"存活治理：统计死亡原因失败: {e}")

    system_deletes = death_stats.get("deleted_by_system", 0)
    alerted = False
    if system_deletes >= SYSTEM_DELETE_ALERT_THRESHOLD:
        alerted = True
        msg = (
            f"近 {days} 天有 {system_deletes} 帖被系统风控删除（内容侧风险）。"
            f"建议：开启 AI 深度改写并切换人格、提高文案多样性、适当降低发帖频率。"
            f"存活良好的标题已自动作为 AI 改写的风格参考。"
        )
        await log_warn(f"⚠️ {msg}")
        try:
            await db.add_notification(type="warning", title="系统删除聚集告警", message=msg)
        except Exception:
            pass  # 通知失败不影响治理

    return {"closed_forums": closed, "death_stats": death_stats, "alerted": alerted}
