"""Automation rules engine logic"""
import re
from typing import List
from ..db.crud import Database
from ..db.models import AutoRule, PostCache
from .client_factory import create_client
from .logger import log_info, log_warn, log_error

async def apply_rules_to_threads(db: Database, fname: str, threads: list):
    """将自动化规则应用到一组主题帖"""
    rules = await db.get_auto_rules(fname)
    active_rules = [r for r in rules if r.is_active]
    
    if not active_rules:
        return

    creds = await get_account_credentials(db)
    if not creds:
        return

    _, bduss, stoken, proxy_id, cuid, ua = creds
    
    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        for thread in threads:
            for rule in active_rules:
                match = False
                if rule.rule_type == "keyword":
                    if rule.pattern in thread.title:
                        match = True
                elif rule.rule_type == "regex":
                    if re.search(rule.pattern, thread.title):
                        match = True
                
                if match:
                    if rule.action == "delete":
                        await log_info(f"[AutoRule] 正在删除匹配帖子: {thread.title} (Reason: {rule.pattern})")
                        success, msg = await client.del_thread(fname, thread.tid)
                        if success:
                            # 如果删除了，就不再应用后续规则
                            break
                    elif rule.action == "notify":
                        # 这里可以扩展通知逻辑
                        await log_info(f"[AutoRule] 发现匹配帖子(监控): {thread.title}")
