"""Global Daemon for Scheduled Tasks"""
import asyncio
import json
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .sign import sign_all_forums
from .auto_rule import apply_rules_to_threads
from .client_factory import create_client
from .account import get_account_credentials
from .batch_post import BatchPostManager, BatchPostTask as CoreBatchPostTask
from ..db.crud import get_db

async def do_sign_task():
    """执行定时签到任务的内部包裹"""
    print(f"[{datetime.now()}] [DAEMON] 开始执行定时全域签到...")
    db = await get_db()
    
    success_count = 0
    fail_count = 0

    async for result in sign_all_forums(db, delay=2.0):
        if result.success:
            success_count += 1
        else:
            fail_count += 1

    print(f"[{datetime.now()}] [DAEMON] 签到完成: 成功 {success_count}, 失败 {fail_count}")

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
        
    bduss, stoken, proxy_id, cuid, ua = creds
    async with await create_client(db, bduss, stoken, proxy_id, cuid, ua) as client:
        for fname in target_fnames:
            try:
                # 获取第一页帖子 (30条左右)
                threads = await client.get_threads(fname, rn=15)
                if threads:
                    await apply_rules_to_threads(db, fname, threads)
            except Exception as e:
                print(f"[DAEMON] 监控 {fname} 失败: {e}")

async def do_batch_post_tasks():
    """执行到期的批量发帖任务"""
    db = await get_db()
    pending_tasks = await db.get_pending_batch_tasks()
    if not pending_tasks:
        return

    manager = BatchPostManager(db)
    for task in pending_tasks:
        print(f"[{datetime.now()}] [DAEMON] 触发定时任务: ID={task.id} 贴吧={task.fname}")
        
        # 转换数据库模型为 Core 任务对象
        core_task = CoreBatchPostTask(
            id=str(task.id),
            fname=task.fname,
            fnames=json.loads(task.fnames_json),
            titles=[], # 新版引擎从 DB 读，这里传空即可
            contents=[],
            accounts=json.loads(task.accounts_json),
            strategy=task.strategy.split(":")[0] if ":" in task.strategy else task.strategy,
            delay_min=task.delay_min,
            delay_max=task.delay_max,
            use_ai=task.use_ai,
            total=task.total
        )
        
        # 如果是循环任务，执行前自动重置物料状态为 pending
        if task.interval_hours > 0:
            print(f"[DAEMON] 循环任务 ID={task.id} 触发，自动重置物料池状态...")
            await db.reset_materials_status()

        # 更新任务状态为 running
        await db.update_batch_task(task.id, status="running")
        
        try:
            # 执行任务（内部会更新物料状态）
            async for _ in manager.execute_task(core_task):
                pass
            
            # 执行完毕后处理：如果是循环任务，更新下一次执行时间；否则标记完成
            if task.interval_hours > 0:
                next_time = datetime.now() + __import__("datetime").timedelta(hours=task.interval_hours)
                await db.update_batch_task(task.id, status="pending", schedule_time=next_time, progress=0)
                print(f"[DAEMON] 循环任务 ID={task.id} 已完成本轮，下次执行: {next_time}")
            else:
                await db.update_batch_task(task.id, status="completed")
        except Exception as e:
            print(f"[DAEMON] 任务 ID={task.id} 执行异常: {e}")
            await db.update_batch_task(task.id, status="failed")

async def do_auto_bump_task():
    """执行自动回帖(自顶)任务的内部包裹"""
    db = await get_db()
    from .batch_post import AutoBumpManager
    manager = AutoBumpManager(db)
    await manager.process_all_candidates()

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
            self.scheduler.add_job(do_batch_post_tasks, 'interval', minutes=1, id="batch_post_polling", replace_existing=True)
            self.scheduler.add_job(do_auto_bump_task, 'interval', minutes=20, id="auto_bump_job", replace_existing=True)
            
            # 尝试从库热加载签到
            db = await get_db()
            await self.reload(db)
            
            self.scheduler.start()
        except Exception as e:
            # 捕获已启动错误等竞态异常
            if "already running" not in str(e).lower():
                print(f"[DAEMON] 启动异常警告: {e}")

    async def reload(self, db):
        """动态重载定时参数"""
        raw_data = await db.get_setting("schedule", "{}")
        schedule = json.loads(raw_data) if raw_data else {}
        
        # 先清除现有任务
        if self.scheduler.get_job(self.sign_job_id):
            self.scheduler.remove_job(self.sign_job_id)

        # 检查是否开启
        if schedule.get("enabled", False):
            time_str = schedule.get("sign_time", "08:30")
            try:
                hour_s, minute_s = time_str.split(":")
                hour = int(hour_s)
                minute = int(minute_s)
                
                self.scheduler.add_job(
                    do_sign_task, 
                    'cron', 
                    hour=hour, 
                    minute=minute, 
                    id=self.sign_job_id,
                    replace_existing=True
                )
                print(f"[DAEMON] 已重载热更新: 每天 {hour:02d}:{minute:02d} 执行...")
            except Exception as e:
                print(f"[DAEMON] 解析配置签到时间出错: {e}")
        else:
            print("[DAEMON] 已重载热更新: 守护签到已禁用")

    def stop(self):
        if self._started:
            self.scheduler.shutdown()
            self._started = False
            
# 全局唯一实例
daemon_instance = TiebaMechaDaemon()
