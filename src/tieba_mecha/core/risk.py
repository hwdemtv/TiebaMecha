"""百度贴吧风控错误统一分类器。

验证码、吧务封禁、账号封禁、拉黑等判定历史上在 batch_post / post / sign /
account / app 心跳多处各写一套（关键词集合还不一致），本模块是唯一判定来源，
所有调用方应从这里导入判定函数，不再自行维护关键词列表。
"""

from __future__ import annotations

import re

# ── 错误码常量（全局唯一定义，原 sign.py 中的同名常量从这里重导出）──
ERR_ALREADY_SIGNED = 160002
ERR_FORUM_INVALID = (340006, 340001)
ERR_FORUM_BANNED = 3250004        # 吧务封禁（在该吧被封）
ERR_ACCOUNT_BLACKLISTED = 400013  # 账号被该吧拉黑

# ── 验证码 / 风控拦截 ──
# 关键词取历史上两套实现的并集：batch_post 的 ["验证码","captcha","安全验证",
# "操作太频繁","频繁登录","账号异常"] 与 post.py 的 "captcha"/"验证码"
CAPTCHA_KEYWORDS = ("验证码", "captcha", "安全验证", "操作太频繁", "频繁登录", "账号异常")
CAPTCHA_ERR_CODES = (6, 7, 16, 18, 40, 100006, 100007)

# ── 账号级封禁 ──
ACCOUNT_BAN_KEYWORDS = ("封禁", "屏蔽")

# ── 关注/取关场景 ──
ALREADY_FOLLOWED_KEYWORDS = ("已关注",)
BLACKLIST_KEYWORDS = ("被拉黑",)

_CODE_RE = re.compile(r"(\d{4,})")


def extract_err_code(err_msg) -> int:
    """从错误文本中提取 4 位以上数字错误码，找不到返回 0。

    替代历史散落两处的 `re.search(r'(\\d{4,})', err_msg)` 复制粘贴。
    """
    if not err_msg:
        return 0
    m = _CODE_RE.search(str(err_msg))
    return int(m.group(1)) if m else 0


def is_captcha_error(err_msg="", err_code: int = 0) -> bool:
    """是否触发验证码/风控人机拦截。"""
    msg = str(err_msg or "")
    return any(kw in msg for kw in CAPTCHA_KEYWORDS) or err_code in CAPTCHA_ERR_CODES


def is_forum_ban_error(err_msg="", err_code: int = 0) -> bool:
    """是否为吧务封禁（错误码 3250004 或错误文本包含该码）。"""
    if err_code == ERR_FORUM_BANNED:
        return True
    return str(ERR_FORUM_BANNED) in str(err_msg or "")


def is_account_ban_error(err_msg="") -> bool:
    """是否为账号级封禁（账号被平台封禁/屏蔽）。"""
    msg = str(err_msg or "")
    return any(kw in msg for kw in ACCOUNT_BAN_KEYWORDS)


def is_blacklisted_error(err_msg="") -> bool:
    """是否为账号被该吧拉黑（400013 / “被拉黑”）。"""
    msg = str(err_msg or "")
    return any(kw in msg for kw in BLACKLIST_KEYWORDS) or str(ERR_ACCOUNT_BLACKLISTED) in msg


def is_already_followed_error(err_msg="") -> bool:
    """是否为“已关注”类幂等错误（可安全跳过）。"""
    return any(kw in str(err_msg or "") for kw in ALREADY_FOLLOWED_KEYWORDS)
