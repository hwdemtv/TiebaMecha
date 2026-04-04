"""Main Flet application - TiebaMecha Cyber-Mecha v2.0"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import flet as ft

from .utils import with_opacity

from .components import get_dark_theme, get_light_theme
from .pages.dashboard import DashboardPage
from .pages.accounts import AccountsPage
from .pages.welcome import WelcomePage
from .pages.sign import SignPage
from .pages.posts import PostsPage
from .pages.crawl import CrawlPage
from .pages.proxy import ProxyPage
from .pages.rules import RulesPage
from .pages.batch_post_page import BatchPostPage
from .pages.plugins_page import PluginsPage
from .pages.settings import SettingsPage
from ..db.crud import get_db
from ..core.account import verify_account, decrypt_value
from ..core.logger import log_info, log_warn, log_error
from ..core.notification import init_notification_manager, get_notification_manager
from ..core.updater import get_update_manager
from .components.notification_bell import NotificationBell, show_notification_dialog

if TYPE_CHECKING:
    from tieba_mecha.db.crud import Database


class TiebaMechaApp:
    """TiebaMecha 主应用 - 路由管理与全局状态控制"""

    def __init__(self, page: ft.Page):
        self.page = page
        self.db: Database | None = None
        self.current_page: str = "dashboard"
        
        self._setup_page()

    def _setup_page(self):
        """初始化页面设置"""
        self.page.title = "TiebaMecha | Command Center"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.theme = get_dark_theme()
        self.page.dark_theme = get_dark_theme()

        # 窗口设置
        self.page.window.width = 1100
        self.page.window.height = 750
        self.page.window.min_width = 1000
        self.page.window.min_height = 650

        # 侧边导航栏 (Compact Aesthetic)
        self.nav_rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=80,
            min_extended_width=160,
            bgcolor=with_opacity(0.1, "surface"),
            destinations=[
                # --- 📊 监控中心 ---
                ft.NavigationRailDestination(
                    icon=ft.icons.RADAR,
                    selected_icon=ft.icons.RADAR,
                    label="指挥中心",
                ),
                # --- 👤 账号管理 ---
                ft.NavigationRailDestination(
                    icon=ft.icons.ACCOUNT_CIRCLE_OUTLINED,
                    selected_icon=ft.icons.ACCOUNT_CIRCLE,
                    label="账号列表",
                ),
                ft.NavigationRailDestination(
                    icon=ft.icons.VPN_LOCK_OUTLINED,
                    selected_icon=ft.icons.VPN_LOCK,
                    label="代理池",
                ),

                # --- 📝 内容管理 ---
                ft.NavigationRailDestination(
                    icon=ft.icons.BOLT_OUTLINED,
                    selected_icon=ft.icons.BOLT,
                    label="全域签到",
                ),
                ft.NavigationRailDestination(
                    icon=ft.icons.FORUM_OUTLINED,
                    selected_icon=ft.icons.FORUM,
                    label="帖子管理",
                ),
                ft.NavigationRailDestination(
                    icon=ft.icons.TRAVEL_EXPLORE_OUTLINED,
                    selected_icon=ft.icons.TRAVEL_EXPLORE,
                    label="数据爬取",
                ),
                # --- 🤖 自动化 ---
                ft.NavigationRailDestination(
                    icon=ft.icons.SHIELD_OUTLINED,
                    selected_icon=ft.icons.SHIELD,
                    label="自动化规则",
                ),
                ft.NavigationRailDestination(
                    icon=ft.icons.SEND_ROUNDED,
                    selected_icon=ft.icons.SEND_ROUNDED,
                    label="批量发帖",
                ),
                # --- ⚙️ 系统 ---
                ft.NavigationRailDestination(
                    icon=ft.icons.EXTENSION_OUTLINED,
                    selected_icon=ft.icons.EXTENSION,
                    label="插件中心",
                ),
                ft.NavigationRailDestination(
                    icon=ft.icons.SETTINGS_OUTLINED,
                    selected_icon=ft.icons.SETTINGS,
                    label="全局设置",
                ),
            ],
            on_change=self._on_nav_change,
        )

        # 通知铃铛
        self.notification_bell = NotificationBell(
            page=self.page,
            on_click=lambda _: self.page.run_task(
                show_notification_dialog, self.page, get_notification_manager()
            )
        )

        # 将铃铛设置为侧边栏头部
        self.nav_rail.leading = ft.Container(
            content=self.notification_bell,
            padding=ft.padding.only(top=20, bottom=10),
            alignment=ft.alignment.center,
        )

        # 内容预览区
        self.content_area = ft.Container(
            padding=0,
            expand=True,
            bgcolor="background",
            animate=ft.Animation(400, ft.AnimationCurve.DECELERATE),
        )

        # 组合布局
        self.page.add(
            ft.Row(
                controls=[
                    self.nav_rail,
                    ft.VerticalDivider(width=1, color=with_opacity(0.1, "onSurface")),
                    self.content_area,
                ],
                expand=True,
                spacing=0,
            )
        )

    async def initialize(self, db: Database):
        """初始化数据库和其他异步资源"""
        self.db = db

        # 初始化通知管理器
        nm = init_notification_manager(db=db, page=self.page)
        self.notification_bell.set_notification_manager(nm)
        await self.notification_bell.refresh()

        # 初始化更新管理器
        get_update_manager(db=db)

        # 检查是否是首次运行（无账号）
        accounts = await self.db.get_accounts()
        if not accounts:
            await self._navigate("welcome")
        else:
            await self._navigate("dashboard")

        # 启动后台任务
        self.page.run_task(self._account_heartbeat)
        self.page.run_task(self._batch_scheduler)
        self.page.run_task(self._proxy_monitor) # 挂载代理智能监控
        self.page.run_task(self._notification_sync)  # 通知同步
        self.page.run_task(self._update_checker)  # 更新检测

        # 拉起全局调度引擎
        from ..core.daemon import daemon_instance
        self.page.run_task(daemon_instance.start)

        await log_info("TiebaMecha 系统内聚核动力引擎已启动")

    async def _is_quiet_hour(self) -> bool:
        """检查当前是否处于静默时间窗"""
        from datetime import datetime
        try:
            start_str = await self.db.get_setting("quiet_start", "01:00")
            end_str = await self.db.get_setting("quiet_end", "06:00")
            
            now = datetime.now().time()
            start = datetime.strptime(start_str, "%H:%M").time()
            end = datetime.strptime(end_str, "%H:%M").time()
            
            if start <= end:
                return start <= now <= end
            else:
                # 跨天情况 (如 23:00 - 05:00)
                return now >= start or now <= end
        except Exception:
            return False

    async def _account_heartbeat(self):
        """账号心跳检测后台循环"""
        while True:
            try:
                # 检查静默期
                if await self._is_quiet_hour():
                    await log_info("当前处于系统静默时间窗，后台自动化任务已挂起")
                    await asyncio.sleep(1800) # 静默期内每 30 分钟检查一次
                    continue

                # 获取检测间隔 (默认 2 小时)
                interval_str = await self.db.get_setting("heartbeat_interval", "2")
                interval = max(1, int(interval_str)) # 最少 1 小时
                
                await log_info(f"开启账号状态全域扫描 (计划周期: {interval}h)")
                
                accounts = await self.db.get_accounts()
                for acc in accounts:
                    try:
                        bduss = decrypt_value(acc.bduss)
                        stoken = decrypt_value(acc.stoken) if acc.stoken else ""
                        
                        is_valid, uid, uname, msg = await verify_account(bduss, stoken)
                        status = "active" if is_valid else "expired"
                        if not is_valid and "network" in msg.lower():
                            status = "error" # 网络问题不代表过期
                            
                        await self.db.update_account_status(acc.id, status)
                        
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
                await asyncio.sleep(300) # 出错后 5 分钟重试

    async def _batch_scheduler(self):
        """批量任务后台扫描调度引擎"""
        from ..core.batch_post import BatchPostManager, BatchPostTask
        manager = BatchPostManager(self.db)
        import json
        
        while True:
            try:
                pending_tasks = await self.db.get_pending_batch_tasks()
                if pending_tasks:
                    await log_info(f"发现 {len(pending_tasks)} 个可执行定时任务")
                    
                    for t in pending_tasks:
                        # 解析组合的 strategy 参数 (e.g. "round_robin:strict")
                        strategy_parts = t.strategy.split(":")
                        real_strategy = strategy_parts[0]
                        pairing_mode = strategy_parts[1] if len(strategy_parts) > 1 else "random"

                        # 构造核心逻辑所需的任务对象
                        fnames_list = json.loads(t.fnames_json) if getattr(t, 'fnames_json', None) and t.fnames_json != "[]" else [t.fname]
                        core_task = BatchPostTask(
                            id=str(t.id),
                            fname=t.fname,
                            fnames=fnames_list,
                            titles=json.loads(t.titles_json),
                            contents=json.loads(t.contents_json),
                            accounts=json.loads(t.accounts_json),
                            strategy=real_strategy,
                            pairing_mode=pairing_mode,
                            delay_min=t.delay_min,
                            delay_max=t.delay_max,
                            use_ai=t.use_ai,
                            total=t.total
                        )
                        
                        # 设置为运行中，防止重复拾取
                        await self.db.update_batch_task(t.id, status="running")
                        
                        # 开启异步发帖协程
                        async def run_and_update(origin_t, c_task):
                            has_error = False
                            async for update in manager.execute_task(c_task):
                                # 同步进度到数据库
                                task_status = "running"
                                if update["status"] != "success":
                                    task_status = "failed"
                                    has_error = True
                                    
                                await self.db.update_batch_task(
                                    origin_t.id, 
                                    progress=update.get("progress", 0),
                                    status=task_status
                                )
                            
                            # 最终完成状态界定
                            final_status = "failed" if has_error else "completed"
                            await self.db.update_batch_task(origin_t.id, status=final_status)
                            
                            # 🚨 定时重复派生机制
                            if getattr(origin_t, 'interval_hours', 0) > 0:
                                from datetime import timedelta, datetime
                                next_time = datetime.now() + timedelta(hours=origin_t.interval_hours)
                                
                                await log_info(f"任务周期重复触发: {origin_t.interval_hours} 小时后执行下一班次 ({next_time.strftime('%m-%d %H:%M')})")
                                
                                await self.db.add_batch_task(
                                    fname=origin_t.fname,
                                    fnames_json=origin_t.fnames_json,
                                    titles_json=origin_t.titles_json,
                                    contents_json=origin_t.contents_json,
                                    accounts_json=origin_t.accounts_json,
                                    strategy=origin_t.strategy,
                                    total=origin_t.total,
                                    delay_min=origin_t.delay_min,
                                    delay_max=origin_t.delay_max,
                                    use_ai=origin_t.use_ai,
                                    interval_hours=origin_t.interval_hours,
                                    schedule_time=next_time,
                                    status="pending"
                                )
                        
                        self.page.run_task(run_and_update, t, core_task)
                
                await asyncio.sleep(60) # 每分钟扫描一次
            except asyncio.CancelledError:
                break
            except Exception as e:
                await log_error(f"调度器运行时异常: {str(e)}")
                await asyncio.sleep(300)

    async def _proxy_monitor(self):
        """代理池智能监控与自动维护引擎（含账号联动挂起/恢复）"""
        from ..core.proxy import test_proxy

        while True:
            try:
                proxies = await self.db.get_active_proxies()
                if proxies:
                    await log_info(f"开启周期性网络探测: 正在巡检 {len(proxies)} 个代理节点")
                    for p in proxies:
                        proxy_url = f"{p.protocol}://{p.host}:{p.port}"
                        success, result = await test_proxy(proxy_url, p.username, p.password)

                        if not success:
                            await log_warn(f"节点连通性异常: {p.host}:{p.port} -> {result}")
                            await self.db.mark_proxy_fail(p.id)

                            # 重新检查是否达到失效阈值（mark_proxy_fail 内部处理）
                            # 若代理已被标记为 inactive，联动挂起关联账号
                            from ..db.crud import Database
                            proxy_obj = await self.db.get_proxy(p.id)
                            if proxy_obj and not proxy_obj.is_active:
                                suspended = await self.db.suspend_accounts_for_proxy(
                                    p.id, reason=f"代理 {p.host}:{p.port} 连续失效，自动隔离"
                                )
                                if suspended:
                                    names = [a.name for a in suspended]
                                    await log_warn(
                                        f"代理失效联动：已挂起 {len(suspended)} 个关联账号 → {names}"
                                    )
                        else:
                            # 代理连通性正常：若之前曾被标记失效并恢复，解挂关联账号
                            if p.fail_count > 0:
                                # 重置失败计数
                                async with self.db.async_session() as session:
                                    from sqlalchemy import update as sa_update
                                    from ..db.models import Proxy
                                    await session.execute(
                                        sa_update(Proxy).where(Proxy.id == p.id).values(fail_count=0)
                                    )
                                    await session.commit()

                                restored = await self.db.restore_accounts_for_proxy(p.id)
                                if restored:
                                    names = [a.name for a in restored]
                                    await log_info(
                                        f"代理 {p.host}:{p.port} 已恢复，解挂 {len(restored)} 个账号 → {names}"
                                    )

                await asyncio.sleep(1800)  # 每 30 分钟巡检一次
            except asyncio.CancelledError:
                break
            except Exception as e:
                await log_error(f"代理监控引擎异常: {str(e)}")
                await asyncio.sleep(600)

    async def _notification_sync(self):
        """通知同步后台任务 - 定期从 hw-license-center 拉取远程通知"""
        nm = get_notification_manager()
        if not nm:
            return

        # 启动时立即尝试执行一次同步
        try:
            await self._perform_notification_sync(nm)
        except Exception as e:
            await log_error(f"程序启动初始通知同步异常: {str(e)}")

        while True:
            try:
                # 每小时定期同步一次远程通知
                await asyncio.sleep(3600)
                await self._perform_notification_sync(nm)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await log_error(f"周期性通知同步异常: {str(e)}")
                await asyncio.sleep(1800)

    async def _perform_notification_sync(self, nm):
        """执行具体的通知同步逻辑"""
        # 加载许可证配置
        license_key = await self.db.get_setting("license_key", "")
        device_id = await self.db.get_setting("device_id", "")
        server_url = await self.db.get_setting("license_server_url", "")

        # 配置并触发同步
        nm.set_license_config(license_key, device_id, server_url)
        added = await nm.sync_remote_notifications()
        if added > 0:
            await log_info(f"同步远程通知: 新增 {added} 条")
            await self.notification_bell.refresh()

    async def _update_checker(self):
        """更新检测后台任务 - 定期检查 GitHub Releases"""
        updater = get_update_manager()

        while True:
            try:
                # 检查是否应该检测更新（默认 24 小时一次）
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

                # 每 24 小时检查一次
                await asyncio.sleep(86400)

            except asyncio.CancelledError:
                break
            except Exception as e:
                await log_error(f"更新检测异常: {str(e)}")
                await asyncio.sleep(3600)


    def _on_nav_change(self, e):
        """处理导航切换"""
        dest_map = {
            0: "dashboard",
            1: "accounts",
            2: "proxy",
            3: "sign",
            4: "posts",
            5: "crawl",
            6: "rules",
            7: "batch_post",
            8: "plugins",
            9: "settings",
        }
        page_name = dest_map.get(e.control.selected_index, "dashboard")
        self.page.run_task(self._navigate, page_name)

    async def _navigate(self, page_name: str):
        """页面路由跳转核心逻辑"""
        if self.current_page == page_name and self.content_area.content:
            return

        self.current_page = page_name
        
        # 路由映射表
        pages = {
            "dashboard": DashboardPage,
            "accounts": AccountsPage,
            "welcome": WelcomePage,
            "sign": SignPage,
            "posts": PostsPage,
            "crawl": CrawlPage,
            "proxy": ProxyPage,
            "rules": RulesPage,
            "batch_post": BatchPostPage,
            "plugins": PluginsPage,
            "settings": SettingsPage,
        }

        page_class = pages.get(page_name, DashboardPage)
        
        page_obj = page_class(self.page, self.db, self._navigate_sync)
        self.content_area.content = page_obj.build()
        # 先刷新框架，再异步加载数据（_navigate 本身是 async，直接 await 即可）
        self.page.update()
        if hasattr(page_obj, "load_data"):
            await page_obj.load_data()

    def _navigate_sync(self, page_name: str):
        """供子页面使用的同步导航回调"""
        self.page.run_task(self._navigate, page_name)

# 确保 create_gradient_button 在 fallback 中能用
def run_app(port: int = 9006):
    """启动 Flet 应用"""
    # 修复：aiotieba 导入后会将 logging level 30 的名称从标准的 'WARNING'
    # 覆盖为 'WARN'，导致 flet_runtime 传给 uvicorn 的日志级别字符串为 'warn'，
    # 而 uvicorn 的 LOG_LEVELS 字典只接受 'warning'，因此抛出 KeyError。
    # 在此恢复标准名称以确保兼容性。
    logging.addLevelName(logging.WARNING, "WARNING")

    async def main(page: ft.Page):
        app = TiebaMechaApp(page)
        db = await get_db()
        await app.initialize(db)

    ft.app(target=main, port=port, view=ft.AppView.WEB_BROWSER)


if __name__ == "__main__":
    run_app()
