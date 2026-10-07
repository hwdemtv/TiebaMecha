"""存活分析 → 发帖策略反馈闭环。

数据流：
    帖子存活检测（run_survival_check：daemon 定时轮转 + 页面手动触发，
                  写入 survival_status/death_reason）
        ├─ 吧务删除等贴吧侧风险 → auto_sync_post_target 关停火力目标（已有能力）
        └─ 系统风控删除（内容侧风险）→ 本模块聚集告警 + AI 改写策略联动
                ├─ 存活样本注入：material_repo.get_survival_examples → AIOptimizer few-shot
                └─ 聚集告警：短时间多次系统删除 → 通知建议深度改写/降低混淆密度

daemon 每 12 小时调用 run_survival_check 做一轮增量检测（供治理消费新鲜数据），
每 12 小时调用 run_survival_governance 执行一次策略同步。
"""

from __future__ import annotations

import asyncio

from ..db.crud import Database
from .logger import log_info, log_warn

# 近 N 天系统删除达到该数量时触发内容策略告警
SYSTEM_DELETE_ALERT_THRESHOLD = 3

# 手动按钮与 daemon 定时轮转共用互斥锁（daemon 与 web 同进程）：
# 已有一轮检测在跑时另一方直接跳过，不并发打 get_posts
_survival_check_lock = asyncio.Lock()


async def run_survival_check(
    db: Database,
    limit: int | None = None,
    materials: list | None = None,
    progress_cb=None,
    item_cb=None,
) -> dict:
    """批量存活检测主入口（daemon 定时轮转 / 存活页手动全量 / 发帖中心勾选批量共用）。

    - 选材：materials 显式指定（发帖中心勾选集，自动滤除未发帖项），
      否则按轮转策略取（近 7 天新帖优先 > 最久未测优先）；limit=None 全量
    - 结论落库：仅 alive/dead 写 survival_status/death_reason；unknown（检测被
      验证码/网络拦截）不写存活档案，仅推进 last_checked_at 防轮转卡死在队头
    - 互斥：已有一轮在跑时直接返回 skipped=True
    - progress_cb(counts) 每条完成后回调总进度；item_cb(material, status, reason)
      回调单条结果（异常时 status="error"）。回调异常互不影响检测本身。

    Returns:
        {"skipped": bool, "total": int, "checked": int,
         "alive": int, "dead": int, "unknown": int, "failed": int}
    """
    counts = {
        "skipped": False, "total": 0, "checked": 0,
        "alive": 0, "dead": 0, "unknown": 0, "failed": 0,
    }
    if _survival_check_lock.locked():
        counts["skipped"] = True
        return counts
    async with _survival_check_lock:
        from .post import check_post_survival

        if materials is None:
            materials = await db.get_materials_for_survival_check(limit=limit)
        else:
            materials = [m for m in materials if m.status == "success" and m.posted_tid]
        counts["total"] = len(materials)
        if not counts["total"]:
            return counts

        semaphore = asyncio.Semaphore(3)

        async def _check_one(m):
            async with semaphore:
                try:
                    status, reason = await check_post_survival(m.posted_tid)
                    if status in ("alive", "dead"):
                        await db.update_material_survival_status(m.id, status, reason)
                    else:
                        await db.mark_material_checked(m.id)
                    if status == "alive":
                        counts["alive"] += 1
                    elif status == "dead":
                        counts["dead"] += 1
                    else:
                        counts["unknown"] += 1
                    if item_cb:
                        try:
                            await item_cb(m, status, reason)
                        except Exception:
                            pass  # UI 回调失败不影响检测
                except Exception as ex:
                    counts["failed"] += 1
                    await log_warn(f"存活检测异常 tid={m.posted_tid}: {ex}")
                    if item_cb:
                        try:
                            await item_cb(m, "error", str(ex))
                        except Exception:
                            pass
                counts["checked"] += 1
                if progress_cb:
                    try:
                        await progress_cb(dict(counts))
                    except Exception:
                        pass  # UI 回调失败不影响检测
                await asyncio.sleep(0.5)  # 单路限速

        await asyncio.gather(*[_check_one(m) for m in materials])

        await log_info(
            f"存活检测完成: 检测{counts['checked']}/{counts['total']} "
            f"存活{counts['alive']} 阵亡{counts['dead']} "
            f"未确认{counts['unknown']} 失败{counts['failed']}"
        )
        return counts


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
