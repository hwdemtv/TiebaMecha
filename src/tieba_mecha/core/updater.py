"""应用更新检测模块 - 基于 GitHub Releases API"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import aiohttp

from .. import __version__

@dataclass
class ReleaseInfo:
    """发布版本信息"""
    version: str           # 版本号 "1.3.0"
    tag_name: str          # Git Tag "v1.3.0"
    published_at: datetime # 发布时间
    body: str              # 更新说明
    assets: List[dict]     # 下载资源列表
    html_url: str          # GitHub 页面链接
    is_prerelease: bool    # 是否预发布


class UpdateManager:
    """应用更新管理器"""

    GITHUB_API = "https://api.github.com/repos/hwdemtv/TiebaMecha"
    GITHUB_API_BACKUP = "https://git.hubinwei.top/repos/hwdemtv/TiebaMecha"

    def __init__(self, db=None):
        self.db = db
        self._current_version = __version__

    @property
    def current_version(self) -> str:
        """获取当前版本号"""
        return self._current_version

    async def _fetch_from_api(self, session, url, headers):
        """内部辅助函数：从指定 API URL 获取发布信息"""
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            print(f"[UpdateManager] Failed to fetch from {url}: {e}")
            return None

    async def check_update(self, include_prerelease: bool = False) -> Optional[ReleaseInfo]:
        """
        检查是否有新版本（支持备用节点自动切换）
        """
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": f"TiebaMecha/{self._current_version}",
                }

                latest = None
                # 尝试主节点和备用节点
                endpoints = [self.GITHUB_API, self.GITHUB_API_BACKUP]
                
                for api_root in endpoints:
                    url = f"{api_root}/releases" if include_prerelease else f"{api_root}/releases/latest"
                    data = await self._fetch_from_api(session, url, headers)
                    
                    if data:
                        if include_prerelease and isinstance(data, list) and len(data) > 0:
                            latest = data[0]
                        elif not include_prerelease and isinstance(data, dict):
                            latest = data
                        
                        if latest:
                            print(f"[UpdateManager] Successfully fetched update info from: {api_root}")
                            break

                if not latest:
                    return None

            release = ReleaseInfo(
                version=latest["tag_name"].lstrip("v"),
                tag_name=latest["tag_name"],
                published_at=datetime.fromisoformat(
                    latest["published_at"].replace("Z", "+00:00")
                ),
                body=latest.get("body") or "",
                assets=latest.get("assets", []),
                html_url=latest["html_url"],
                is_prerelease=latest.get("prerelease", False),
            )

            # 比较版本号
            if self._compare_versions(release.version, self._current_version) > 0:
                # 保存到数据库
                if self.db:
                    await self.db.set_setting("latest_version", release.version)
                    await self.db.set_setting("latest_version_url", release.html_url)
                    await self.db.set_setting(
                        "last_update_check", datetime.now().isoformat()
                    )
                return release

            # 没有新版本，也记录检查时间
            if self.db:
                await self.db.set_setting("last_update_check", datetime.now().isoformat())

            return None

        except aiohttp.ClientError as e:
            print(f"[UpdateManager] Network error: {e}")
            return None
        except Exception as e:
            print(f"[UpdateManager] Check update failed: {e}")
            return None

    def _compare_versions(self, v1: str, v2: str) -> int:
        """
        比较两个版本号

        Returns:
            1 if v1 > v2, -1 if v1 < v2, 0 if equal
        """
        def parse(v):
            # 移除非数字字符（保留点和数字）
            clean = re.sub(r'[^0-9.]', '', v)
            return [int(x) if x else 0 for x in clean.split('.')]

        parts1, parts2 = parse(v1), parse(v2)

        # 补齐长度
        max_len = max(len(parts1), len(parts2))
        parts1.extend([0] * (max_len - len(parts1)))
        parts2.extend([0] * (max_len - len(parts2)))

        for p1, p2 in zip(parts1, parts2):
            if p1 > p2:
                return 1
            elif p1 < p2:
                return -1
        return 0

    async def get_portable_download_url(self, release: ReleaseInfo) -> Optional[str]:
        """
        获取便携版 ZIP 下载链接

        Args:
            release: 版本信息

        Returns:
            下载链接，如果没有找到返回 None
        """
        for asset in release.assets:
            name = asset.get("name", "").lower()
            if "portable" in name and name.endswith(".zip"):
                return asset.get("browser_download_url")
        return None

    async def get_changelog(self, release: ReleaseInfo, max_length: int = 500) -> str:
        """
        获取格式化的更新日志

        Args:
            release: 版本信息
            max_length: 最大长度（字符数）

        Returns:
            格式化的更新日志
        """
        body = release.body.strip()
        if len(body) > max_length:
            body = body[:max_length] + "..."

        return f"""## {release.tag_name}

**发布时间**: {release.published_at.strftime('%Y-%m-%d %H:%M')}

{body}

---
[查看完整更新日志]({release.html_url})
"""

    async def should_check_update(self, interval_hours: int = 24) -> bool:
        """
        判断是否应该检查更新（避免频繁请求）

        Args:
            interval_hours: 检查间隔（小时）

        Returns:
            是否应该检查
        """
        if not self.db:
            return True

        last_check = await self.db.get_setting("last_update_check", "")
        if not last_check:
            return True

        try:
            last = datetime.fromisoformat(last_check)
            elapsed = (datetime.now() - last).total_seconds()
            return elapsed >= interval_hours * 3600
        except Exception:
            return True

    async def get_stored_latest_version(self) -> Optional[str]:
        """获取数据库中存储的最新版本号"""
        if not self.db:
            return None
        return await self.db.get_setting("latest_version", "")


# 全局实例
_update_manager: Optional[UpdateManager] = None


def get_update_manager(db=None) -> UpdateManager:
    """获取更新管理器实例"""
    global _update_manager
    if _update_manager is None:
        _update_manager = UpdateManager(db=db)
    elif db:
        _update_manager.db = db
    return _update_manager
