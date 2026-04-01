"""Async logger for TiebaMecha UI dashboard"""

import asyncio
from collections import deque
from datetime import datetime
from typing import List, Optional

# 全局日志广播队列 (给流式监听者)
_LOG_QUEUE = asyncio.Queue(maxsize=100)
# 全局日志持久化环形池 (给切页返回者)
_LOG_HISTORY = deque(maxlen=200)

async def log_info(msg: str):
    """记录信息日志"""
    await _add_log("INFO", msg)

async def log_warn(msg: str):
    """记录警告日志"""
    await _add_log("WARN", msg)

async def log_error(msg: str):
    """记录错误日志"""
    await _add_log("ERROR", msg)

async def _add_log(level: str, msg: str):
    """内部推送到队列"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {
        "time": timestamp,
        "level": level,
        "message": msg
    }
    
    # 存入静默池，用于切边页面后快照读取
    _LOG_HISTORY.append(log_entry)
    
    # 如果广播队列满了，先弹出一个
    if _LOG_QUEUE.full():
        try:
            _LOG_QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            pass
            
    await _LOG_QUEUE.put(log_entry)

def get_log_queue() -> asyncio.Queue:
    """获取日志队列引用"""
    return _LOG_QUEUE

async def get_recent_logs(limit: int = 20) -> List[dict]:
    """获取最近的日志快照 (持久化跨页可用)"""
    history_list = list(_LOG_HISTORY)
    return history_list[-limit:] if history_list else []
