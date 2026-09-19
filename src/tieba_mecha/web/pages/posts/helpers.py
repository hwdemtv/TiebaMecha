"""帖子管理页公共常量与纯函数（无 UI 依赖，方便单测）。"""

from __future__ import annotations

import re
from datetime import datetime

# 存活展示四态：存活 / 疑似删除 / 已删除 / 未知
# dead + 可确认删除原因 → 已删除；dead + 检测异常/验证码等 → 疑似删除
CONFIRMED_DEATH_REASONS = frozenset({
    "deleted_by_system", "deleted_by_mod", "deleted_by_user",
    "deleted_unknown", "auto_removed",
})

DEATH_REASON_DISPLAY = {
    "deleted_by_system": "系统风控删除",
    "deleted_by_mod": "吧务手动删除",
    "deleted_by_user": "用户自删",
    "deleted_unknown": "已删除(原因未明)",
    "auto_removed": "帖子不存在/已过期",
    "captcha_required": "验证码拦截(疑似)",
    "error": "检测异常(疑似)",
    "unknown_error": "未知错误(疑似)",
}

# 存活筛选值 → (图标, 标签, 颜色)
SURVIVAL_DISPLAY = {
    "alive": ("check_circle", "存活", "green"),
    "suspected": ("warning_amber", "疑似删除", "#FF9800"),
    "dead": ("error", "已删除", "error"),
    "unknown": ("remove_circle_outlined", "未知", "onSurfaceVariant"),
}

TIEBA_THREAD_URL = "https://tieba.baidu.com/p/{tid}"

# 标题/正文限制（与 core.add_thread 校验保持一致）
TITLE_MIN, TITLE_MAX = 5, 31
CONTENT_MAX = 2000
# 正文链接数提醒阈值（超出易触发贴吧风控）
LINK_WARN_THRESHOLD = 2

_LINK_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%\[\]]+", re.IGNORECASE)


def extract_links(text: str) -> list[str]:
    """提取正文中的 http(s) 链接（白名单字符集，中文文本不会误吞；去重保序）"""
    seen: set[str] = set()
    links: list[str] = []
    for m in _LINK_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;，。；）)」】")
        if url and url not in seen:
            seen.add(url)
            links.append(url)
    return links


def classify_survival(survival_status: str, death_reason: str = "") -> str:
    """(survival_status, death_reason) → 展示四态 alive/suspected/dead/unknown"""
    if survival_status == "alive":
        return "alive"
    if survival_status == "dead":
        if (death_reason or "") in CONFIRMED_DEATH_REASONS:
            return "dead"
        return "suspected"
    return "unknown"


def death_reason_display(reason: str) -> str:
    return DEATH_REASON_DISPLAY.get(reason, reason or "未知")


def thread_url(tid: int) -> str:
    return TIEBA_THREAD_URL.format(tid=tid)


def fmt_time(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return dt.strftime(fmt) if dt else "-"


def estimate_next_bump(
    is_auto_bump: bool,
    bump_mode: str = "once",
    bump_count: int = 0,
    last_bumped_at: datetime | None = None,
    bump_hour: int = 10,
    bump_duration_days: int = 0,
    bump_start_date=None,
    max_bump_count: int = 20,
    cooldown_minutes: int = 45,
) -> str:
    """估算下一次自顶执行时间（与 core.batch_post._should_bump_this_cycle 语义对齐）"""
    if not is_auto_bump:
        return "已停止"
    from datetime import date as date_cls, timedelta

    now = datetime.now()
    mode = bump_mode or "once"

    if mode in ("scheduled", "matrix_loop"):
        # 有效期检查：bump_start_date 为 date 对象（兼容 str），0 = 永久
        start = bump_start_date
        if isinstance(start, str):
            try:
                start = date_cls.fromisoformat(start)
            except ValueError:
                start = None
        if isinstance(start, date_cls) and bump_duration_days > 0:
            if now.date() > start + timedelta(days=bump_duration_days):
                return f"已超过持续期({bump_duration_days}天)"
        if last_bumped_at and last_bumped_at.date() == now.date():
            return f"明日 {bump_hour}:00 后"
        if now.hour < bump_hour:
            return f"今日 {bump_hour}:00 后"
        return "下个巡检周期(约45分钟内)"

    # once 模式：受全局次数上限 + 冷却时间约束
    if bump_count >= max_bump_count:
        return f"已达次数上限({max_bump_count}次)"
    if last_bumped_at:
        ready_at = last_bumped_at + timedelta(minutes=cooldown_minutes)
        if ready_at > now:
            mins = int((ready_at - now).total_seconds() // 60) + 1
            return f"约 {mins} 分钟后(冷却)"
    return "下个巡检周期(约45分钟内)"
