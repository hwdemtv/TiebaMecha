"""全局设置 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select, text, update

from ..models import (
    Account,
    AutoRule,
    Base,
    BatchPostLog,
    BatchPostTask,
    CaptchaEvent,
    Forum,
    MaterialPool,
    Notification,
    Proxy,
    Setting,
    SignLog,
    TargetPool,
    ThreadRecord,
    WeightHistory,
)

logger = logging.getLogger(__name__)


class SettingRepository:
    """全局设置（依赖宿主类提供 async_session/engine）。"""

    async def get_setting(self, key: str, default: str = "") -> str:
        """获取设置"""
        async with self.async_session() as session:
            setting = await session.get(Setting, key)
            return setting.value if setting else default

    async def get_settings_bulk(self, keys: list[str], defaults: dict[str, str] | None = None) -> dict[str, str]:
        """批量获取设置（单次查询）。缺失的键回退到 defaults（再退到空串）。"""
        defaults = defaults or {}
        async with self.async_session() as session:
            result = await session.execute(select(Setting).where(Setting.key.in_(keys)))
            found = {s.key: s.value for s in result.scalars().all()}
        return {k: found.get(k, defaults.get(k, "")) for k in keys}
    async def set_setting(self, key: str, value: str) -> None:
        """保存设置"""
        async with self.async_session() as session:
            setting = await session.get(Setting, key)
            if setting:
                setting.value = value
            else:
                setting = Setting(key=key, value=value)
                session.add(setting)
            await session.commit()
    async def set_settings_bulk(self, settings: dict[str, str]) -> None:
        """批量保存设置（单次事务）"""
        async with self.async_session() as session:
            for key, value in settings.items():
                setting = await session.get(Setting, key)
                if setting:
                    setting.value = value
                else:
                    setting = Setting(key=key, value=value)
                    session.add(setting)
            await session.commit()
