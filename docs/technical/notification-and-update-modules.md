# 系统通知模块 & 应用更新模块 设计方案

> 版本: v1.0
> 日期: 2026-04-01
> 状态: 设计讨论中

---

## 一、需求背景

TiebaMecha 作为一个本地化部署的桌面应用，目前缺乏以下能力：

1. **系统通知**：用户无法及时获知后台任务的执行状态（如发帖成功/失败、签到完成、账号异常等）
2. **应用更新**：用户需要手动检查 GitHub 发布新版本，缺乏自动检测和更新机制

---

## 二、系统通知模块设计

### 2.1 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      通知中心 (NotificationCenter)            │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │ 事件监听器   │  │ 通知队列    │  │ 通知渲染器  │          │
│  │ (EventBus)  │→ │ (Queue)     │→ │ (Renderer)  │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│         ↑                                    ↓               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              通知存储 (SQLite)                        │    │
│  │  - id, type, title, message, is_read, created_at    │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 通知类型定义

```python
# src/tieba_mecha/core/notification.py

from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

class NotificationType(Enum):
    """通知类型枚举"""
    # 任务相关
    POST_SUCCESS = "post_success"           # 发帖成功
    POST_FAILED = "post_failed"             # 发帖失败
    BATCH_COMPLETE = "batch_complete"       # 批量任务完成
    SIGN_COMPLETE = "sign_complete"         # 签到完成

    # 账号相关
    ACCOUNT_EXPIRED = "account_expired"     # 账号失效
    ACCOUNT_WARNING = "account_warning"     # 账号异常预警

    # 系统相关
    UPDATE_AVAILABLE = "update_available"   # 新版本可用
    SYSTEM_ALERT = "system_alert"           # 系统告警
    PROXY_FAILED = "proxy_failed"           # 代理失效

    # 养号相关
    MAINT_COMPLETE = "maint_complete"       # 养号周期完成

@dataclass
class Notification:
    """通知数据结构"""
    id: Optional[int] = None
    type: NotificationType = NotificationType.SYSTEM_ALERT
    title: str = ""
    message: str = ""
    is_read: bool = False
    created_at: datetime = None
    extra: dict = None  # 扩展数据，如 tid, fname 等

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.extra is None:
            self.extra = {}
```

### 2.3 数据库模型扩展

```python
# 在 src/tieba_mecha/db/models.py 中添加

class Notification(Base):
    """系统通知"""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False, comment="通知类型")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="标题")
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="内容")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已读")
    extra_json: Mapped[str] = mapped_column(Text, default="{}", comment="扩展数据 JSON")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_notifications_is_read", "is_read"),
        Index("ix_notifications_created_at", "created_at"),
    )
```

### 2.4 通知中心实现

```python
# src/tieba_mecha/core/notification.py

import asyncio
from typing import Callable, List, Optional
from dataclasses import dataclass
from datetime import datetime

import flet as ft

class NotificationCenter:
    """系统通知中心 - 单例模式"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, page: ft.Page = None, db=None):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self.page = page
            self.db = db
            self._listeners: List[Callable] = []
            self._snackbar_queue: asyncio.Queue = asyncio.Queue()

    async def push(
        self,
        type: NotificationType,
        title: str,
        message: str,
        extra: dict = None,
        show_snackbar: bool = True
    ):
        """
        推送通知到系统

        Args:
            type: 通知类型
            title: 标题
            message: 详细消息
            extra: 扩展数据
            show_snackbar: 是否显示即时 SnackBar
        """
        # 1. 持久化到数据库
        notification = await self.db.add_notification(
            type=type.value,
            title=title,
            message=message,
            extra_json=json.dumps(extra or {})
        )

        # 2. 触发监听器
        for listener in self._listeners:
            try:
                await listener(notification)
            except Exception as e:
                print(f"[NotificationCenter] Listener error: {e}")

        # 3. 显示即时通知
        if show_snackbar and self.page:
            await self._show_snackbar(type, title, message)

    async def _show_snackbar(self, type: NotificationType, title: str, message: str):
        """显示 Flet SnackBar"""
        color_map = {
            NotificationType.POST_SUCCESS: "green",
            NotificationType.POST_FAILED: "red",
            NotificationType.ACCOUNT_EXPIRED: "red",
            NotificationType.UPDATE_AVAILABLE: "blue",
            NotificationType.SYSTEM_ALERT: "orange",
        }

        snackbar = ft.SnackBar(
            content=ft.Row([
                ft.Icon(ft.icons.INFO, color="white"),
                ft.Text(f"{title}: {message}", color="white"),
            ]),
            bgcolor=ft.colors.with_opacity(0.9, color_map.get(type, "grey")),
            duration=4000,
        )

        self.page.open(snackbar)
        await self.page.update_async()

    async def get_unread(self, limit: int = 50) -> List[Notification]:
        """获取未读通知列表"""
        return await self.db.get_unread_notifications(limit)

    async def mark_read(self, notification_id: int):
        """标记为已读"""
        await self.db.mark_notification_read(notification_id)

    async def mark_all_read(self):
        """全部标记已读"""
        await self.db.mark_all_notifications_read()

    def add_listener(self, callback: Callable):
        """添加事件监听器"""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable):
        """移除事件监听器"""
        self._listeners.remove(callback)


# 全局实例
notification_center: Optional[NotificationCenter] = None

def init_notification_center(page: ft.Page, db) -> NotificationCenter:
    """初始化通知中心"""
    global notification_center
    notification_center = NotificationCenter(page, db)
    return notification_center

def get_notification_center() -> Optional[NotificationCenter]:
    """获取通知中心实例"""
    return notification_center
```

### 2.5 集成点

#### 在后台任务中触发通知

```python
# src/tieba_mecha/core/batch_post.py 中添加通知

from .notification import get_notification_center, NotificationType

class BatchPostManager:
    async def execute_task(self, task):
        # ... 发帖逻辑 ...

        # 发帖成功后推送通知
        nc = get_notification_center()
        if nc:
            await nc.push(
                type=NotificationType.POST_SUCCESS,
                title="发帖成功",
                message=f"已在 {fname} 发布: {title}",
                extra={"tid": tid, "fname": fname},
                show_snackbar=False  # 批量任务不逐条弹窗
            )

        # 任务完成后推送汇总
        if success_count > 0:
            await nc.push(
                type=NotificationType.BATCH_COMPLETE,
                title="批量任务完成",
                message=f"成功 {success_count} 条，失败 {fail_count} 条",
                show_snackbar=True
            )
```

#### 在 Daemon 中触发通知

```python
# src/tieba_mecha/core/daemon.py

from .notification import get_notification_center, NotificationType

async def do_auto_bump_task():
    # ... 自顶逻辑 ...

    if results:
        nc = get_notification_center()
        await nc.push(
            type=NotificationType.SYSTEM_ALERT,
            title="自动回帖完成",
            message=f"已处理 {len(results)} 条帖子",
            show_snackbar=False
        )
```

### 2.6 UI 组件 - 通知铃铛

```python
# src/tieba_mecha/web/components/notification_bell.py

import flet as ft
from typing import List

class NotificationBell(ft.Container):
    """通知铃铛组件 - 显示在导航栏"""

    def __init__(self, page: ft.Page, on_click=None):
        super().__init__()
        self.page = page
        self.on_click = on_click
        self._unread_count = 0

        self.badge = ft.Badge(
            content=ft.IconButton(
                icon=ft.icons.NOTIFICATIONS_OUTLINED,
                selected_icon=ft.icons.NOTIFICATIONS,
                on_click=self._on_bell_click,
            ),
            text="0",
            visible=False,
        )

        self.content = self.badge

    async def update_count(self, count: int):
        """更新未读数量"""
        self._unread_count = count
        self.badge.text = str(count) if count < 100 else "99+"
        self.badge.visible = count > 0
        await self.page.update_async()

    async def _on_bell_click(self, e):
        """点击铃铛显示通知面板"""
        if self.on_click:
            await self.on_click(e)
```

---

## 三、应用更新模块设计

### 3.1 更新检测策略

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **GitHub Releases API** | 官方渠道，自动获取 | 需联网，有 API 限速 | ⭐⭐⭐⭐⭐ |
| 自建更新服务器 | 可控性强 | 需额外维护服务器 | ⭐⭐⭐ |
| 配置文件版本号 | 简单 | 不够实时 | ⭐⭐ |

**推荐方案**: GitHub Releases API + 本地缓存

### 3.2 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      更新管理器 (UpdateManager)               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ 版本检测器   │    │ 更新下载器   │    │ 安装执行器   │  │
│  │ (Checker)    │───→│ (Downloader) │───→│ (Installer)  │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                   │                    │          │
│         ↓                   ↓                    ↓          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              GitHub Releases API                     │   │
│  │  https://api.github.com/repos/hwdemtv/TiebaMecha/   │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 核心实现

```python
# src/tieba_mecha/core/updater.py

import aiohttp
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import zipfile
import shutil

@dataclass
class ReleaseInfo:
    """发布版本信息"""
    version: str           # 版本号 "v1.2.0"
    tag_name: str          # Git Tag
    published_at: datetime # 发布时间
    body: str              # 更新说明
    assets: List[dict]     # 下载资源列表
    html_url: str          # GitHub 页面链接
    is_prerelease: bool    # 是否预发布

class UpdateManager:
    """应用更新管理器"""

    GITHUB_API = "https://api.github.com/repos/hwdemtv/TiebaMecha"
    CURRENT_VERSION = "0.1.0"  # 应从 __init__.py 动态获取

    def __init__(self, db=None):
        self.db = db
        self._last_check: Optional[datetime] = None

    async def check_update(self, include_prerelease: bool = False) -> Optional[ReleaseInfo]:
        """
        检查是否有新版本

        Args:
            include_prerelease: 是否包含预发布版本

        Returns:
            如果有新版本返回 ReleaseInfo，否则返回 None
        """
        async with aiohttp.ClientSession() as session:
            # 获取最新发布版本
            async with session.get(f"{self.GITHUB_API}/releases/latest") as resp:
                if resp.status != 200:
                    return None
                latest = await resp.json()

        release = ReleaseInfo(
            version=latest["tag_name"].lstrip("v"),
            tag_name=latest["tag_name"],
            published_at=datetime.fromisoformat(latest["published_at"].replace("Z", "+00:00")),
            body=latest["body"] or "",
            assets=latest.get("assets", []),
            html_url=latest["html_url"],
            is_prerelease=latest.get("prerelease", False)
        )

        # 比较版本号
        if self._compare_versions(release.version, self.CURRENT_VERSION) > 0:
            self._last_check = datetime.now()

            # 保存到数据库供 UI 使用
            if self.db:
                await self.db.set_setting("latest_version", release.version)
                await self.db.set_setting("last_update_check", datetime.now().isoformat())

            return release

        return None

    def _compare_versions(self, v1: str, v2: str) -> int:
        """
        比较两个版本号
        Returns: 1 if v1 > v2, -1 if v1 < v2, 0 if equal
        """
        def parse(v):
            return [int(x) for x in re.sub(r'[^0-9.]', '', v).split('.')]

        parts1, parts2 = parse(v1), parse(v2)
        for p1, p2 in zip(parts1, parts2):
            if p1 > p2:
                return 1
            elif p1 < p2:
                return -1
        return 0

    async def download_update(
        self,
        release: ReleaseInfo,
        progress_callback=None
    ) -> Optional[Path]:
        """
        下载更新包

        Args:
            release: 版本信息
            progress_callback: 进度回调函数 (current, total)

        Returns:
            下载文件路径，失败返回 None
        """
        # 查找便携版 ZIP 资源
        asset = None
        for a in release.assets:
            if "portable" in a["name"].lower() and a["name"].endswith(".zip"):
                asset = a
                break

        if not asset:
            return None

        download_dir = Path.home() / ".tieba_mecha" / "updates"
        download_dir.mkdir(parents=True, exist_ok=True)
        download_path = download_dir / asset["name"]

        async with aiohttp.ClientSession() as session:
            async with session.get(asset["browser_download_url"]) as resp:
                if resp.status != 200:
                    return None

                total = int(resp.headers.get("content-length", 0))
                current = 0

                with open(download_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                        current += len(chunk)
                        if progress_callback:
                            await progress_callback(current, total)

        return download_path

    async def install_update(self, update_path: Path, backup: bool = True) -> bool:
        """
        安装更新（便携版方式）

        Args:
            update_path: 更新包路径
            backup: 是否备份当前版本

        Returns:
            安装是否成功
        """
        try:
            current_dir = Path(__file__).parent.parent.parent.parent

            if backup:
                backup_dir = current_dir.parent / f"TiebaMecha_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                shutil.copytree(current_dir, backup_dir)

            # 解压更新包
            with zipfile.ZipFile(update_path, 'r') as zip_ref:
                # 解压到临时目录
                temp_dir = current_dir.parent / "TiebaMecha_temp"
                zip_ref.extractall(temp_dir)

            # 替换文件（保留 data 目录和 .env）
            extracted_dir = temp_dir / "TiebaMecha"
            for item in extracted_dir.iterdir():
                if item.name in ("data", ".env", "tieba_mecha.db"):
                    continue
                dest = current_dir / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))

            # 清理
            shutil.rmtree(temp_dir)
            update_path.unlink()

            return True

        except Exception as e:
            print(f"[UpdateManager] 安装失败: {e}")
            return False

    async def get_changelog(self, release: ReleaseInfo) -> str:
        """获取格式化的更新日志"""
        return f"""## {release.tag_name}

**发布时间**: {release.published_at.strftime('%Y-%m-%d %H:%M')}

{release.body}

---
[查看完整更新日志]({release.html_url})
"""
```

### 3.4 启动时自动检测

```python
# src/tieba_mecha/web/app.py 中添加

from ..core.updater import UpdateManager
from ..core.notification import get_notification_center, NotificationType

class TiebaMechaApp:
    async def initialize(self, db: Database):
        # ... 现有初始化逻辑 ...

        # 启动时检查更新（静默，不阻塞）
        self.page.run_task(self._check_for_updates)

    async def _check_for_updates(self):
        """后台检查更新"""
        try:
            # 检查上次检测时间，避免频繁请求
            last_check = await self.db.get_setting("last_update_check", "")
            if last_check:
                last = datetime.fromisoformat(last_check)
                if (datetime.now() - last).total_seconds() < 3600 * 24:  # 24小时内不重复检测
                    return

            updater = UpdateManager(self.db)
            release = await updater.check_update()

            if release:
                nc = get_notification_center()
                if nc:
                    await nc.push(
                        type=NotificationType.UPDATE_AVAILABLE,
                        title=f"发现新版本 {release.tag_name}",
                        message="点击查看更新内容",
                        extra={"version": release.version, "url": release.html_url},
                        show_snackbar=True
                    )

                    # 显示更新对话框
                    await self._show_update_dialog(release)

        except Exception as e:
            print(f"[App] 检查更新失败: {e}")

    async def _show_update_dialog(self, release):
        """显示更新对话框"""
        updater = UpdateManager(self.db)

        async def on_download(e):
            # 下载更新
            self._update_dialog.content = ft.Column([
                ft.Text("正在下载更新..."),
                ft.ProgressBar(width=300),
            ])
            await self.page.update_async()

            path = await updater.download_update(release)
            if path:
                self._update_dialog.content = ft.Column([
                    ft.Text("下载完成，是否立即安装？"),
                    ft.Text("安装前会自动备份当前版本。", size=12, color="grey"),
                ])
                self._update_dialog.actions = [
                    ft.TextButton("稍后安装", on_click=lambda _: self.page.close(self._update_dialog)),
                    ft.ElevatedButton("立即安装", on_click=on_install),
                ]
            else:
                self._update_dialog.content = ft.Text("下载失败，请手动下载更新。")
            await self.page.update_async()

        async def on_install(e):
            # 安装更新
            success = await updater.install_update(path)
            if success:
                self.page.close(self._update_dialog)
                await self.page.dialog_async(
                    ft.AlertDialog(
                        title=ft.Text("更新完成"),
                        content=ft.Text("请重启应用以完成更新。"),
                        on_dismiss=lambda _: self.page.window.close(),
                    )
                )
            else:
                self._update_dialog.content = ft.Text("安装失败，请手动更新。")
                await self.page.update_async()

        self._update_dialog = ft.AlertDialog(
            title=ft.Text(f"发现新版本 {release.tag_name}"),
            content=ft.Column([
                ft.Text(f"当前版本: v{updater.CURRENT_VERSION}"),
                ft.Text(f"发布时间: {release.published_at.strftime('%Y-%m-%d')}"),
                ft.Divider(),
                ft.Markdown(release.body[:500] + "..." if len(release.body) > 500 else release.body),
            ], scroll=ft.ScrollMode.AUTO, height=300),
            actions=[
                ft.TextButton("稍后提醒", on_click=lambda _: self.page.close(self._update_dialog)),
                ft.TextButton("查看详情", on_click=lambda _: self.page.launch_url(release.html_url)),
                ft.ElevatedButton("立即更新", on_click=on_download),
            ],
        )
        self.page.open(self._update_dialog)
```

### 3.5 设置页面集成

在「系统设置」页面添加更新检查选项：

| 设置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `auto_check_update` | bool | True | 启动时自动检查更新 |
| `update_channel` | enum | stable | 更新渠道 (stable/prerelease) |
| `last_update_check` | datetime | - | 上次检查时间 |

---

## 四、实现优先级

### Phase 1: MVP（最小可用版本）

1. **通知模块**
   - [x] 数据库模型 `Notification`
   - [ ] `NotificationCenter` 核心类
   - [ ] CRUD 操作 (`add_notification`, `get_unread_notifications`)
   - [ ] 导航栏通知铃铛组件

2. **更新模块**
   - [ ] `UpdateManager.check_update()` 基础检测
   - [ ] 启动时静默检测
   - [ ] 发现新版本时推送通知

### Phase 2: 完善功能

3. **通知模块增强**
   - [ ] 通知面板页面（查看历史）
   - [ ] 通知分类筛选
   - [ ] 批量已读 / 清除

4. **更新模块增强**
   - [ ] 自动下载更新包
   - [ ] 便携版一键安装
   - [ ] 更新日志展示

### Phase 3: 高级特性

5. **通知模块**
   - [ ] 邮件通知（可选）
   - [ ] Webhook 推送（可选）
   - [ ] 通知模板自定义

6. **更新模块**
   - [ ] 增量更新（Delta）
   - [ ] 回滚支持

---

## 五、API 参考

### NotificationCenter

```python
# 推送通知
await notification_center.push(
    type=NotificationType.POST_SUCCESS,
    title="发帖成功",
    message="帖子已发布到 贴吧名",
    extra={"tid": 123456, "fname": "贴吧名"},
    show_snackbar=True
)

# 获取未读通知
notifications = await notification_center.get_unread(limit=20)

# 标记已读
await notification_center.mark_read(notification_id)

# 全部已读
await notification_center.mark_all_read()

# 添加监听器
notification_center.add_listener(my_callback)
```

### UpdateManager

```python
updater = UpdateManager(db)

# 检查更新
release = await updater.check_update()
if release:
    print(f"发现新版本: {release.version}")

# 下载更新
path = await updater.download_update(release, progress_callback=my_progress)

# 安装更新
success = await updater.install_update(path, backup=True)
```

---

## 六、测试用例

### 通知模块测试

```python
# tests/test_notification.py

import pytest
from tieba_mecha.core.notification import NotificationCenter, NotificationType

@pytest.mark.asyncio
async def test_push_notification(db):
    nc = NotificationCenter(db=db)

    await nc.push(
        type=NotificationType.POST_SUCCESS,
        title="测试通知",
        message="这是一条测试通知"
    )

    unread = await nc.get_unread()
    assert len(unread) == 1
    assert unread[0].title == "测试通知"
```

### 更新模块测试

```python
# tests/test_updater.py

import pytest
from tieba_mecha.core.updater import UpdateManager

@pytest.mark.asyncio
async def test_check_update():
    updater = UpdateManager()
    release = await updater.check_update()

    # 如果有新版本，检查字段完整性
    if release:
        assert release.version
        assert release.tag_name
        assert release.html_url

def test_version_compare():
    updater = UpdateManager()

    assert updater._compare_versions("1.0.0", "0.9.0") == 1
    assert updater._compare_versions("0.9.0", "1.0.0") == -1
    assert updater._compare_versions("1.0.0", "1.0.0") == 0
```

---

## 七、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| GitHub API 限速 | 无法检测更新 | 本地缓存 24 小时，降级为手动检查 |
| 下载中断 | 更新失败 | 支持断点续传，保留备份 |
| 安装异常 | 应用损坏 | 自动备份，支持回滚 |
| 通知过多 | 用户体验差 | 可配置通知过滤规则 |

---

## 八、总结

本设计方案为 TiebaMecha 提供了完整的系统通知和应用更新能力：

1. **通知模块**：基于事件驱动，支持多种通知类型，持久化存储，UI 组件完善
2. **更新模块**：基于 GitHub Releases，支持自动检测、下载、安装便携版

建议优先实现 Phase 1，快速交付核心价值，后续迭代逐步完善。
