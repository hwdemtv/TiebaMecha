"""自动化规则 Repository mixin（自 crud.py 按领域拆分，方法实现保持原样）。"""

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
    CrawlTask,
    Forum,
    MaterialPool,
    Notification,
    PostCache,
    Proxy,
    Setting,
    SignLog,
    TargetPool,
    ThreadRecord,
    WeightHistory,
)

logger = logging.getLogger(__name__)


class RuleRepository:
    """自动化规则（依赖宿主类提供 async_session/engine）。"""

    async def add_auto_rule(
        self,
        fname: str,
        rule_type: str,
        pattern: str,
        action: str = "delete",
    ) -> AutoRule:
        """添加自动化规则"""
        async with self.async_session() as session:
            rule = AutoRule(fname=fname, rule_type=rule_type, pattern=pattern, action=action)
            session.add(rule)
            await session.commit()
            await session.refresh(rule)
            return rule
    async def get_auto_rules(self, fname: str | None = None) -> list[AutoRule]:
        """获取规则列表"""
        async with self.async_session() as session:
            if fname:
                result = await session.execute(
                    select(AutoRule).where(AutoRule.fname == fname).order_by(AutoRule.id)
                )
            else:
                result = await session.execute(select(AutoRule).order_by(AutoRule.id))
            return list(result.scalars().all())
    async def toggle_rule(self, rule_id: int, is_active: bool) -> None:
        """开启/关闭规则"""
        async with self.async_session() as session:
            rule = await session.get(AutoRule, rule_id)
            if rule:
                rule.is_active = is_active
                await session.commit()
    async def delete_auto_rule(self, rule_id: int) -> bool:
        """删除规则"""
        async with self.async_session() as session:
            rule = await session.get(AutoRule, rule_id)
            if rule:
                await session.delete(rule)
                await session.commit()
                return True
            return False
