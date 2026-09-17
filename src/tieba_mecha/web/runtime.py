"""进程级自动化运行时。

历史问题：daemon 与心跳/代理巡检/通知同步/更新检查均通过 `page.run_task`
启动，生命周期被绑定到浏览器会话——会话断开（关标签页/刷新/网络闪断）时
任务被取消，自动化随之停止；而新会话又因 daemon 单例的 _started 标志不会
重新拉起，导致升级/重启后自动化"静默失效"。

本模块用 `asyncio.create_task`（持有强引用）在服务器进程的事件循环上启动
这些任务：生命周期与进程一致，不随任何浏览器会话终止。全进程仅启动一次
（幂等），后续会话接入不再叠加执行（顺带修复重复扫描的堆叠 bug）。

注意：这些协程运行在 Web 服务器的事件循环上，与 UI 会话共享同一个
Database 引擎（SQLite + WAL 下多连接安全）。
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from ..db.crud import Database

# 强引用，防止任务被垃圾回收（asyncio 官方建议）
_tasks: list[asyncio.Task] = []
_started = False

# UI 侧钩子：由最近一次接入的会话注册（如通知铃刷新）；会话断开后调用
# 会被钩子内部的 try/except 吞掉，不影响自动化。
_ui_hook: Optional[Callable[[], Coroutine]] = None


def set_ui_hook(hook: Optional[Callable[[], Coroutine]]) -> None:
    """注册/更新 UI 回调（每个新会话在 _full_initialize 时调用）。"""
    global _ui_hook
    _ui_hook = hook


async def _notify_ui_refresh() -> None:
    if _ui_hook:
        try:
            await _ui_hook()
        except Exception:
            # 会话已断开等场景：UI 刷新失败不影响后台任务
            pass


async def _account_heartbeat_loop(db: Database):
    """账号心跳检测循环（自 app.py 迁移，逻辑保持一致）"""
    from ..core.logger import log_info, log_warn, log_error
    from ..core.account import verify_account, decrypt_value

    async def _is_quiet_hour() -> bool:
        from datetime import datetime
        try:
            start_str = await db.get_setting("quiet_start", "01:00")
            end_str = await db.get_setting("quiet_end", "06:00")
            now = datetime.now().time()
            start = datetime.strptime(start_str, "%H:%M").time()
            end = datetime.strptime(end_str, "%H:%M").time()
            if start <= end:
                return start <= now <= end
            return now >= start or now <= end
        except Exception:
            return False

    while True:
        try:
            if await _is_quiet_hour():
                await log_info("当前处于系统静默时间窗，后台自动化任务已挂起")
                await asyncio.sleep(1800)
                continue

            interval_str = await db.get_setting("heartbeat_interval", "2")
            interval = max(1, int(interval_str))

            await log_info(f"开启账号状态全域扫描 (计划周期: {interval}h)")

            accounts = await db.get_accounts()
            for acc in accounts:
                try:
                    bduss = decrypt_value(acc.bduss)
                    stoken = decrypt_value(acc.stoken) if acc.stoken else ""

                    is_valid, uid, uname, msg = await verify_account(bduss, stoken)
                    status = "active" if is_valid else "expired"
                    if not is_valid:
                        from ..core.risk import is_account_ban_error
                        if "timeout" in msg.lower() or "connection" in msg.lower() or "网络" in msg:
                            status = "error"  # 网络问题不代表过期
                        elif is_account_ban_error(msg):
                            status = "banned"

                    await db.update_account_status(acc.id, status)

                    if not is_valid:
                        await log_warn(f"账号 [{acc.name}] 验证失败: {msg}")
                except Exception as e:
                    await log_error(f"扫描账号 [{acc.name}] 时发生异常: {str(e)}")

            await log_info("账号巡回检查完毕")
            await asyncio.sleep(interval * 3600)

        except asyncio.CancelledError:
            break
        except Exception as e:
            await log_error(f"心跳任务异常: {str(e)}")
            await asyncio.sleep(300)


async def _proxy_monitor_loop(db: Database):
    """代理池智能监控与自动维护引擎（自 app.py 迁移）"""
    from ..core.logger import log_info, log_warn, log_error
    from ..core.proxy import test_proxy

    while True:
        try:
            proxies = await db.get_active_proxies()
            if proxies:
                await log_info(f"开启周期性网络探测: 正在巡检 {len(proxies)} 个代理节点")
                for p in proxies:
                    proxy_url = f"{p.protocol}://{p.host}:{p.port}"
                    success, result = await test_proxy(proxy_url, p.username, p.password)

                    if not success:
                        await log_warn(f"节点连通性异常: {p.host}:{p.port} -> {result}")
                        await db.mark_proxy_fail(p.id)

                        proxy_obj = await db.get_proxy(p.id)
                        if proxy_obj and not proxy_obj.is_active:
                            suspended = await db.suspend_accounts_for_proxy(
                                p.id, reason=f"代理 {p.host}:{p.port} 连续失效，自动隔离"
                            )
                            if suspended:
                                names = [a.name for a in suspended]
                                await log_warn(
                                    f"代理失效联动：已挂起 {len(suspended)} 个关联账号 → {names}"
                                )
                    else:
                        if p.fail_count > 0:
                            async with db.async_session() as session:
                                from sqlalchemy import update as sa_update
                                from ..db.models import Proxy
                                await session.execute(
                                    sa_update(Proxy).where(Proxy.id == p.id).values(fail_count=0)
                                )
                                await session.commit()

                            restored = await db.restore_accounts_for_proxy(p.id)
                            if restored:
                                names = [a.name for a in restored]
                                await log_info(
                                    f"代理 {p.host}:{p.port} 已恢复，解挂 {len(restored)} 个账号 → {names}"
                                )

            await asyncio.sleep(1800)
        except asyncio.CancelledError:
            break
        except Exception as e:
            await log_error(f"代理监控引擎异常: {str(e)}")
            await asyncio.sleep(600)


async def _perform_notification_sync(db: Database, nm):
    """执行通知同步逻辑（自 app.py 迁移；UI 铃刷新走受保护的钩子）"""
    from ..core.logger import log_info

    license_key = await db.get_setting("license_key", "")
    device_id = await db.get_setting("device_id", "")
    server_url = await db.get_setting("license_server_url", "")

    nm.set_license_config(license_key, device_id, server_url)
    added = await nm.sync_remote_notifications()
    if added > 0:
        await log_info(f"同步远程通知: 新增 {added} 条")
        await _notify_ui_refresh()


async def _notification_sync_loop(db: Database):
    """通知同步循环（自 app.py 迁移）"""
    from ..core.logger import log_error
    from ..core.notification import get_notification_manager

    nm = get_notification_manager()
    if not nm:
        return

    try:
        await _perform_notification_sync(db, nm)
    except Exception as e:
        await log_error(f"程序启动初始通知同步异常: {str(e)}")

    while True:
        try:
            await asyncio.sleep(3600)
            await _perform_notification_sync(db, nm)
        except asyncio.CancelledError:
            break
        except Exception as e:
            await log_error(f"周期性通知同步异常: {str(e)}")
            await asyncio.sleep(1800)


async def _update_checker_loop(db: Database):
    """更新检测循环（自 app.py 迁移）"""
    from ..core.logger import log_info, log_error
    from ..core.notification import get_notification_manager
    from ..core.updater import get_update_manager

    updater = get_update_manager()

    while True:
        try:
            if await updater.should_check_update(interval_hours=24):
                release = await updater.check_update()
                if release:
                    nm = get_notification_manager()
                    if nm:
                        await nm.push(
                            type="update_available",
                            title=f"发现新版本 {release.tag_name}",
                            message="点击查看更新内容",
                            action_url=release.html_url,
                            extra={
                                "version": release.version,
                                "published_at": release.published_at.isoformat(),
                            },
                            show_snackbar=True,
                        )
                        await log_info(f"检测到新版本: {release.tag_name}")

            await asyncio.sleep(86400)
        except asyncio.CancelledError:
            break
        except Exception as e:
            await log_error(f"更新检测异常: {str(e)}")
            await asyncio.sleep(3600)


async def ensure_started(db: Database) -> bool:
    """启动进程级自动化（daemon + 四个后台循环），幂等。

    Returns:
        本次调用是否实际执行了启动（False 表示已在运行）。
    """
    global _started
    if _started:
        return False
    _started = True

    from ..core.daemon import daemon_instance

    _tasks.append(asyncio.create_task(daemon_instance.start()))
    _tasks.append(asyncio.create_task(_account_heartbeat_loop(db)))
    _tasks.append(asyncio.create_task(_proxy_monitor_loop(db)))
    _tasks.append(asyncio.create_task(_notification_sync_loop(db)))
    _tasks.append(asyncio.create_task(_update_checker_loop(db)))
    return True


def is_running() -> bool:
    return _started


class AutomationManager:
    """进程级自动化的门面（app.py 调用入口）。"""

    @staticmethod
    def set_ui_hook(hook: Optional[Callable[[], Coroutine]]) -> None:
        set_ui_hook(hook)

    @staticmethod
    async def ensure_started(db: Database) -> bool:
        return await ensure_started(db)

    @staticmethod
    def is_running() -> bool:
        return is_running()
