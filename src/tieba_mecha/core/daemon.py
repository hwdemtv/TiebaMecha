"""Global Daemon for Scheduled Tasks"""
import asyncio
import json
import random
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .sign import sign_all_forums, sign_all_accounts, sign_flow_lock
from .auto_rule import apply_rules_to_threads
from .client_factory import create_client
from .batch_post import BatchPostManager, BatchPostTask as CoreBatchPostTask
from .auth import get_auth_manager
from .account import get_account_credentials
from ..db.crud import get_db

async def do_sign_task():
    """执行定时签到任务的内部包裹（自适应模式）"""
    db = await get_db()

    # 跨天状态重置：守护进程路径无 UI 参与，若不在此处重置昨日的 is_sign_today，
    # update_forum_sign 的按天去重门槛会永久冻结连续天数与成功/失败统计
    if hasattr(db, "check_and_reset_daily_sign"):
        await db.check_and_reset_daily_sign()

    # 1. 获取执行模式
    raw_sched = await db.get_setting("schedule", "{}")
    try:
        schedule = json.loads(raw_sched) if raw_sched else {}
    except (ValueError, TypeError):
        print(f"[DAEMON] schedule 配置损坏，按默认单账号模式执行")
        schedule = {}
    mode = schedule.get("mode", "single")
    
    # 2. 获取行为频率参数
    try:
        d_min = float(await db.get_setting("sign_delay_min", "5"))
        d_max = float(await db.get_setting("sign_delay_max", "15"))
        ad_min = float(await db.get_setting("sign_acc_delay_min", "30"))
        ad_max = float(await db.get_setting("sign_acc_delay_max", "120"))
    except Exception:
        d_min, d_max, ad_min, ad_max = 5.0, 15.0, 30.0, 120.0

    print(f"[{datetime.now()}] [DAEMON] 触发定时签到流 | 模式: {mode.upper()} | 吧间延迟: {d_min}-{d_max}s")

    # 与手动签到互斥：定时触发不排队等待（一轮全扫可能长达数小时），
    # 已有签到流在执行时直接放弃本次触发
    if sign_flow_lock.locked():
        print(f"[{datetime.now()}] [DAEMON] 检测到已有签到流在执行 (手动?)，跳过本次定时触发")
        return

    success_count = 0
    fail_count = 0

    async with sign_flow_lock:
        if mode == "matrix":
            print(f"[{datetime.now()}] [DAEMON] 正在执行全矩阵跨账号扫号...")
            async for result in sign_all_accounts(db, d_min, d_max, ad_min, ad_max):
                if result.get("success"):
                    success_count += 1
                else:
                    fail_count += 1
        else:
            # 单账号模式
            async for result in sign_all_forums(db, delay_min=d_min, delay_max=d_max):
                if result.success:
                    success_count += 1
                else:
                    fail_count += 1

    print(f"[{datetime.now()}] [DAEMON] 任务闭环 | 成功: {success_count} | 失败: {fail_count}")

async def do_auto_monitor_task():
    """执行自动化规则监控的内部包裹"""
    db = await get_db()
    
    # 获取有活跃规则的所有贴吧名
    rules = await db.get_auto_rules()
    target_fnames = list(set([r.fname for r in rules if r.is_active]))
    
    if not target_fnames:
        return

    creds = await get_account_credentials(db)
    if not creds:
        return
        
    acc_id, bduss, stoken, proxy_id, cuid, ua = creds # 解构 6 元组
    async with await create_client(db, bduss, stoken, proxy_id, cuid, ua) as client:
        for fname in target_fnames:
            try:
                # 获取第一页帖子 (30条左右)
                threads = await client.get_threads(fname, rn=15)
                if threads:
                    await apply_rules_to_threads(db, fname, threads)
            except Exception as e:
                print(f"[DAEMON] 监控 {fname} 失败: {e}")

async def _load_batch_task(task_id: str):
    """按 ID 从数据库加载批量任务行。"""
    from ..db.models import BatchPostTask as BatchPostTaskModel
    from sqlalchemy import select
    db = await get_db()
    async with db.async_session() as session:
        result = await session.execute(
            select(BatchPostTaskModel).where(BatchPostTaskModel.id == task_id)
        )
        return result.scalar_one_or_none()


def _build_core_task(task) -> CoreBatchPostTask:
    """数据库任务行 → 引擎核心任务对象（精确触发与轮询派发共用）。

    优先使用独立 pairing_mode 字段，向后兼容旧复合字符串格式 "strategy:pairing"。
    """
    task_pairing = getattr(task, 'pairing_mode', None)
    if not task_pairing and ":" in task.strategy:
        task_pairing = task.strategy.split(":")[1]
    else:
        task_pairing = task_pairing or "random"

    return CoreBatchPostTask(
        id=str(task.id),
        fname=task.fname,
        fnames=json.loads(task.fnames_json),
        accounts=json.loads(task.accounts_json),
        strategy=task.strategy.split(":")[0] if ":" in task.strategy else task.strategy,
        pairing_mode=task_pairing,
        delay_min=task.delay_min,
        delay_max=task.delay_max,
        use_ai=task.use_ai,
        ai_persona=getattr(task, 'ai_persona', 'normal') or 'normal',
        forum_offset=getattr(task, 'cycle_count', 0) or 0,
        total=task.total
    )


async def _reset_recurring_materials(db, task):
    """循环轮次开始前的物料处理：reuse 模式重置物料（AI 开启时同时恢复原文）。"""
    reset_strategy = getattr(task, 'reset_strategy', 'new_only') or 'new_only'
    if reset_strategy != 'reuse':
        return
    use_ai = getattr(task, 'use_ai', False)
    try:
        reset_count = await db.reset_materials_for_task(
            strategy="reuse",
            restore_original=use_ai,
            task_id=str(task.id),
        )
        ai_note = " (含原文恢复)" if use_ai else ""
        print(f"[{datetime.now()}] [DAEMON] 循环任务 ID={task.id} 物料重置: 策略=reuse{ai_note}, 重置数={reset_count}")
    except Exception as e:
        print(f"[{datetime.now()}] [DAEMON] 循环任务 ID={task.id} 物料重置失败: {e}")


async def _run_claimed_task(task_id: str):
    """执行一个已被认领（status=running）的任务并处理完成后的调度。

    once 任务执行完标记 completed；循环任务计算下次执行时间、归位 pending
    并注册下一个精确触发器。精确触发与轮询派发两条路径共用本实现。
    """
    db = await get_db()
    task = await _load_batch_task(task_id)
    if not task:
        print(f"[DAEMON] 任务 {task_id} 不存在，跳过执行")
        return

    schedule_type = getattr(task, 'schedule_type', 'once') or 'once'
    if schedule_type != 'once':
        await _reset_recurring_materials(db, task)

    print(f"[{datetime.now()}] [DAEMON] 开始执行任务: ID={task.id} 贴吧={task.fname} 类型={schedule_type}")
    manager = BatchPostManager(db)
    core_task = _build_core_task(task)

    try:
        async for update in manager.execute_task(core_task):
            update_status = update.get("status", "running")
            # 记录错误/跳过信息到日志，但不中断任务流
            if update_status in ("error", "failed"):
                print(f"[{datetime.now()}] [DAEMON] 任务 ID={task.id} 单条失败: {update.get('msg', update)}")
            await db.update_batch_task(
                task.id,
                progress=update.get("progress", 0),
                status="running"
            )

        if schedule_type != 'once':
            next_time = _calc_next_schedule_time(task)
            new_cycle = (getattr(task, 'cycle_count', 0) or 0) + 1
            await db.update_batch_task(
                task.id,
                status="pending",
                schedule_time=next_time,
                progress=0,
                cycle_count=new_cycle,
            )
            daemon_instance.schedule_batch_task(task.id, next_time)
            print(f"[{datetime.now()}] [DAEMON] 循环任务 ID={task.id} 第{new_cycle}轮完成，下次执行: {next_time}")
        else:
            await db.update_batch_task(task.id, status="completed")
            print(f"[{datetime.now()}] [DAEMON] once 任务 ID={task.id} 执行完成")
    except Exception as e:
        print(f"[{datetime.now()}] [DAEMON] 任务 ID={task.id} 执行异常: {e}")
        # 循环任务异常归位 pending 并重注册触发器，下次继续；once 任务标记失败
        if schedule_type != 'once':
            next_time = _calc_next_schedule_time(task)
            await db.update_batch_task(task.id, status="pending", schedule_time=next_time)
            daemon_instance.schedule_batch_task(task.id, next_time)
        else:
            await db.update_batch_task(task.id, status="failed")


# 派发式执行的协程强引用池：防止 asyncio.create_task 的任务被垃圾回收
_SPAWNED_TASK_REFS: set = set()


def calc_batch_task_resume_time(task) -> datetime:
    """恢复暂停任务后的下次执行时间。

    once 任务保留原计划时刻（已被暂停耗过的时刻则恢复后立即执行）；
    循环任务按调度类型从当前时刻重算下一档（daily/weekly 取原时刻的
    HH:MM，interval 从现在起算一个间隔），暂停期间不补跑错过的轮次。
    """
    schedule_type = getattr(task, 'schedule_type', 'once') or 'once'
    if schedule_type == 'once':
        orig = task.schedule_time
        now = datetime.now()
        return orig if (orig and orig > now) else now
    return _calc_next_schedule_time(task)


def _spawn_batch_task(coro) -> None:
    t = asyncio.create_task(coro)
    _SPAWNED_TASK_REFS.add(t)
    t.add_done_callback(_SPAWNED_TASK_REFS.discard)


async def wait_spawned_batch_tasks():
    """等待所有已派发的批量任务协程结束（测试同步与优雅关闭用）。"""
    if _SPAWNED_TASK_REFS:
        await asyncio.gather(*list(_SPAWNED_TASK_REFS), return_exceptions=True)


def _plan_signature(accounts_json: str, fnames_json: str):
    """发帖计划签名：账号池 + 目标池的集合。

    同一计划的多个时段副本（如 daily 任务的四个分身）签名相同；
    JSON 解析失败时退化为原文比对。"""
    try:
        return (
            frozenset(json.loads(accounts_json or "[]")),
            frozenset(json.loads(fnames_json or "[]")),
        )
    except (ValueError, TypeError):
        return (accounts_json or "", fnames_json or "")


def _find_running_same_plan(running_tasks, task):
    """在运行中任务里找与 task 属同一发帖计划的任务，无则返回 None。

    用于派发前互斥：同计划的多个时段副本并发执行会导致同账号同吧
    短窗内重复发帖（2026-09-21 事故根因之一）。"""
    task_sig = _plan_signature(task.accounts_json, task.fnames_json)
    for running in running_tasks:
        if running.id == task.id:
            continue
        if _plan_signature(running.accounts_json, running.fnames_json) == task_sig:
            return running
    return None


async def _execute_scheduled_task(task_id: str):
    """精确触发入口（APScheduler date 触发器），once 与循环任务通用。"""
    task = await _load_batch_task(task_id)
    if not task:
        print(f"[DAEMON] 定时任务 {task_id} 不存在，跳过触发")
        return
    if task.status != "pending":
        print(f"[{datetime.now()}] [DAEMON] 定时任务 {task_id} 状态为 {task.status}，跳过触发")
        return
    # 陈旧触发器守卫：注册后任务的计划时间被编辑推迟，按新时间重注册
    if task.schedule_time and task.schedule_time > datetime.now() + timedelta(seconds=5):
        print(f"[{datetime.now()}] [DAEMON] 任务 {task_id} 计划时间已变更为 {task.schedule_time}，重注册触发器")
        daemon_instance.schedule_batch_task(task.id, task.schedule_time)
        return

    db = await get_db()
    # 同计划互斥：已有同配置任务在跑时让位，任务保持 pending 交由轮询兜底接管
    blocked_by = _find_running_same_plan(await db.get_running_batch_tasks(), task)
    if blocked_by is not None:
        print(f"[{datetime.now()}] [DAEMON] 任务 {task_id} 与运行中任务 ID={blocked_by.id} 属同一发帖计划，本次触发让位")
        return
    if not await db.claim_batch_task(task.id):
        print(f"[{datetime.now()}] [DAEMON] 任务 {task_id} 已被其他路径认领，跳过触发")
        return

    await _run_claimed_task(str(task.id))


async def do_batch_post_tasks():
    """轮询兜底：捞起到期的批量任务并派发执行。

    精确调度（date 触发器）是主路径，本轮询只兜底 daemon 宕机期间
    错过触发的任务。每个班次最多派发 1 个任务：daemon 停摆后的积压
    任务按轮询间隔错峰串行执行，配合同计划互斥避免同配置任务并发
    （2026-09-21 事故：3 个积压副本同时派发，同账号同吧 1 秒双帖）。
    """
    db = await get_db()
    pending_tasks = await db.get_pending_batch_tasks()
    if not pending_tasks:
        return

    running_tasks = await db.get_running_batch_tasks()
    pending_tasks.sort(key=lambda t: (t.schedule_time or datetime.max, t.id))
    for task in pending_tasks:
        blocked_by = _find_running_same_plan(running_tasks, task)
        if blocked_by is not None:
            print(f"[{datetime.now()}] [DAEMON] 任务 ID={task.id} 与运行中任务 ID={blocked_by.id} 属同一发帖计划，本轮询跳过")
            continue
        if not await db.claim_batch_task(task.id):
            continue
        print(f"[{datetime.now()}] [DAEMON] 轮询兜底派发任务: ID={task.id} 贴吧={task.fname}")
        _spawn_batch_task(_run_claimed_task(str(task.id)))
        return  # 单班次只派发一个，其余任务等下一班次错峰执行


def _calc_next_schedule_time(task) -> datetime:
    """
    根据任务的调度类型计算下次执行时间。
    
    - daily: 明天 schedule_time 的 HH:MM
    - weekly: 下一个 schedule_day_of_week 的 schedule_time HH:MM  
    - interval: now + interval_hours
    """
    schedule_type = getattr(task, 'schedule_type', 'once') or 'once'
    schedule_time = task.schedule_time or datetime.now()
    
    if schedule_type == 'daily':
        # 每天：明天的同一时刻
        now = datetime.now()
        next_dt = now.replace(
            hour=schedule_time.hour,
            minute=schedule_time.minute,
            second=0, microsecond=0
        )
        if next_dt <= now:
            next_dt += timedelta(days=1)
        return next_dt
        
    elif schedule_type == 'weekly':
        # 每周：下一个指定星期几
        day_of_week = getattr(task, 'schedule_day_of_week', 0) or 0  # 0=周一...6=周日
        now = datetime.now()
        # 计算目标时间的时分
        target_time = now.replace(
            hour=schedule_time.hour,
            minute=schedule_time.minute,
            second=0, microsecond=0
        )
        # Python weekday(): 0=Monday...6=Sunday，与我们的定义一致
        current_weekday = now.weekday()
        days_ahead = day_of_week - current_weekday
        if days_ahead < 0 or (days_ahead == 0 and target_time <= now):
            days_ahead += 7
        return target_time + timedelta(days=days_ahead)
        
    elif schedule_type == 'interval':
        # 自定义间隔
        interval_hours = getattr(task, 'interval_hours', 6) or 6
        # 强制最小6小时间隔，防止频繁触发
        actual_interval = max(interval_hours, 6)
        if interval_hours < 6:
            print(f"[DAEMON] ⚠️ 循环间隔过短 ({interval_hours}h)，已自动调整为 {actual_interval}h")
        return datetime.now() + timedelta(hours=actual_interval)
    
    else:
        # fallback
        return datetime.now() + timedelta(hours=6)

async def do_auto_bump_task():
    """执行自动回帖(自顶)任务的内部包裹"""
    db = await get_db()
    from .batch_post import AutoBumpManager
    manager = AutoBumpManager(db)
    # 带链首评优先于常规自顶：链接走楼中楼、主帖净文化（2026-09-22 内容池整改架构）
    await manager.process_link_first_replies()
    await manager.process_all_candidates()

async def do_behavior_audit_task():
    """行为审计 + 风险自动治理（审计 → 自动下调发帖权重的控制回路）"""
    db = await get_db()
    try:
        if (await db.get_setting("behavior_audit_enabled", "true")).lower() == "false":
            return
    except Exception:
        pass
    from .behavior_audit import audit_and_govern
    await audit_and_govern(db)

async def do_survival_governance_task():
    """存活治理：死亡原因分流 + 系统删除聚集告警（存活→策略反馈闭环）"""
    db = await get_db()
    try:
        if (await db.get_setting("survival_governance_enabled", "true")).lower() == "false":
            return
    except Exception:
        pass
    from .survival_feedback import run_survival_governance
    await run_survival_governance(db)

async def do_maintenance_task():
    """执行拟人化养号维护任务的内部包裹"""
    db = await get_db()
    from .maintenance import MaintManager
    manager = MaintManager(db)

    # 获取所有开启了养号功能的账号
    maint_accounts = await db.get_maint_accounts()
    if not maint_accounts:
        return

    # [Fix 8] 从数据库读取可配置的账号间延迟范围
    try:
        acc_delay_min = float(await db.get_setting("maint_acc_delay_min", "300"))
        acc_delay_max = float(await db.get_setting("maint_acc_delay_max", "900"))
    except Exception:
        acc_delay_min, acc_delay_max = 300.0, 900.0

    from .logger import log_info, log_error
    await log_info(f"[BioWarming] 启动全域养号周期，覆盖 {len(maint_accounts)} 个终端...")
    for acc in maint_accounts:
        try:
            await manager.run_maint_cycle(acc.id)
            # 账号间增加长随机延迟，防止 IP 行为重合
            await asyncio.sleep(random.uniform(acc_delay_min, acc_delay_max))
        except Exception as e:
            await log_error(f"[BioWarming] 账号 {acc.name} 维护异常: {e}")

async def do_auth_check_task():
    """执行在线授权静默探测的内部包裹"""
    try:
        am = await get_auth_manager()
        if not hasattr(am, "verify_online"):
            print(f"[DAEMON] FATAL: get_auth_manager() 返回了 {type(am)} 而非 LicenseManager")
            return
        print(f"[{datetime.now()}] [DAEMON] 启动后台授权校准与多节点探活...")
        success = await am.verify_online()
        if success:
            print(f"[{datetime.now()}] [DAEMON] 授权状态校准完毕: PRO 已激活")
        else:
            print(f"[{datetime.now()}] [DAEMON] 授权状态校准完毕: FREE/ERROR 系统将维持当前状态")
    except Exception as e:
        print(f"[DAEMON] 授权校验异常: {e}")
        import traceback
        traceback.print_exc()

class TiebaMechaDaemon:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TiebaMechaDaemon, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            # 创建调度器并不立即启动
            self.scheduler = AsyncIOScheduler()
            self._started = False
            self.sign_job_id = "global_sign_job"
            self.monitor_job_id = "global_monitor_job"

    async def start(self):
        """挂载后台启动"""
        if self._started:
            return
            
        self._started = True  # 尽早设置标识避免 Flet 并发多页面竞争
        print("[DAEMON] 初始化全域定时任务守护进程...")
        
        try:
            # 始终加载监控任务和批量发帖轮询
            self.scheduler.add_job(do_auto_monitor_task, 'interval', minutes=10, id=self.monitor_job_id, replace_existing=True)
            # 轮询兜底：精确调度（date 触发器）是主路径，5 分钟轮询只捞 daemon
            # 宕机期间错过的任务；认领后异步派发，长任务不阻塞后续班次
            self.scheduler.add_job(do_batch_post_tasks, 'interval', minutes=5, id="batch_post_job", replace_existing=True)
            
            # 6. 每 12 小时执行一次应用更新检查 (已在 updater 实现逻辑，此处挂载)
            from .updater import get_update_manager
            # 注意：updater.py 中的方法名是 check_update，不是 check_for_updates
            self.scheduler.add_job(get_update_manager().check_update, 'interval', hours=12, id="update_check_job")
            
            # 7. 每 6 小时执行一次授权心跳
            self.scheduler.add_job(do_auth_check_task, 'interval', hours=6, id="auth_check_job")
            
            # --- 立即执行一次初始化探测 ---
            asyncio.create_task(do_auth_check_task())

            self.scheduler.add_job(do_auto_bump_task, 'interval', minutes=20, id="auto_bump_job", replace_existing=True)

            # 行为审计治理 + 存活反馈治理（均为幂等策略任务，12h 周期）
            self.scheduler.add_job(do_behavior_audit_task, 'interval', hours=12, id="behavior_audit_job", replace_existing=True)
            self.scheduler.add_job(do_survival_governance_task, 'interval', hours=12, id="survival_governance_job", replace_existing=True)

            # 尝试从库热加载签到 + 养号间隔
            db = await get_db()

            # [Fix 8] 养号间隔可配置，默认 4 小时
            try:
                maint_hours = float(await db.get_setting("maint_interval_hours", "4"))
            except Exception:
                maint_hours = 4.0
            self.scheduler.add_job(do_maintenance_task, 'interval', hours=maint_hours, id="biowarming_job", replace_existing=True)

            await self.reload(db)

            # 批量任务精确调度同步：先做崩溃恢复（遗留 running 复位），再为所有
            # 未到期的 pending 任务注册 date 触发器；已到期的交给轮询兜底立即捞走
            try:
                recovered = await db.reset_running_batch_tasks()
                if recovered:
                    print(f"[DAEMON] 崩溃恢复: {recovered} 个遗留 running 任务已复位为 pending")
                scheduled = await db.get_scheduled_batch_tasks()
                now = datetime.now()
                reg_count = 0
                for t in scheduled:
                    st = getattr(t, 'schedule_time', None)
                    if st and st > now:
                        self.schedule_batch_task(t.id, st)
                        reg_count += 1
                print(f"[DAEMON] 批量任务精确调度: {reg_count} 个未来任务已注册触发器")
            except Exception as sync_err:
                print(f"[DAEMON] 批量任务调度同步失败（轮询兜底仍有效）: {sync_err}")

            self.scheduler.start()
        except asyncio.CancelledError:
            print("[DAEMON] 启动过程被取消")
            return
        except Exception as e:
            # 捕获已启动错误等竞态异常
            if "already running" not in str(e).lower():
                print(f"[DAEMON] 启动异常警告: {e}")

    async def reload(self, db):
        """动态重载定时参数"""
        raw_data = await db.get_setting("schedule", "{}")
        try:
            schedule = json.loads(raw_data) if raw_data else {}
        except (ValueError, TypeError):
            print("[DAEMON] schedule 配置损坏，跳过本次重载（保留现有任务）")
            return
        if not isinstance(schedule, dict):
            print("[DAEMON] schedule 配置格式异常，跳过本次重载（保留现有任务）")
            return

        # 未启用时才移除现有任务；启用时依赖 add_job 的 replace_existing 原子替换，
        # 避免先删后建在解析失败时把已注册的任务静默丢失
        if not schedule.get("enabled", False):
            if self.scheduler.get_job(self.sign_job_id):
                self.scheduler.remove_job(self.sign_job_id)
            print("[DAEMON] 已重载热更新: 守护签到已禁用")
            return

        time_str = schedule.get("sign_time", "08:00")
        try:
            hour_s, minute_s = str(time_str).split(":")
            hour = int(hour_s)
            minute = int(minute_s)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("时间超出范围")

            self.scheduler.add_job(
                do_sign_task,
                'cron',
                hour=hour,
                minute=minute,
                id=self.sign_job_id,
                replace_existing=True,
                misfire_grace_time=300,  # 事件循环被长任务阻塞时保留 5 分钟补触发窗口
            )
            print(f"[DAEMON] 已重载热更新: 每天 {hour:02d}:{minute:02d} 执行...")
        except Exception as e:
            # 解析失败时不动现有任务，避免定时签到静默失效
            print(f"[DAEMON] 解析配置签到时间出错: {e} (已保留原有任务配置)")

    def schedule_batch_task(self, task_id, run_date: datetime):
        """
        为批量任务注册精确调度（APScheduler date 触发器，once 与循环任务通用）。
        任务会在 run_date 精确触发，不再依赖轮询相位。

        Args:
            task_id: 任务 ID（数据库主键）
            run_date: 计划执行时间
        """
        if self.scheduler is None:
            print(f"[DAEMON] 调度器未初始化，任务 {task_id} 将由轮询兜底")
            return
        job_id = f"batch_task_{task_id}"
        self.scheduler.add_job(
            _execute_scheduled_task,
            'date',
            run_date=run_date,
            args=[str(task_id)],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,  # 5分钟容错窗口
        )
        print(f"[DAEMON] 已注册精确调度: 任务 {task_id} 将在 {run_date} 执行")

    def schedule_once_task(self, task_id: str, schedule_time: datetime):
        """
        为 once 类型任务注册精确调度（保留旧接口，内部委托 schedule_batch_task）。
        """
        self.schedule_batch_task(task_id, schedule_time)

    def cancel_once_task(self, task_id: str):
        """取消已注册的任务精确调度（兼容新旧两种 job id）"""
        for job_id in (f"once_batch_{task_id}", f"batch_task_{task_id}"):
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                print(f"[DAEMON] 已取消任务调度: {job_id}")

    def stop(self):
        if self._started:
            self.scheduler.shutdown()
            self._started = False
            
# 全局唯一实例
daemon_instance = TiebaMechaDaemon()
