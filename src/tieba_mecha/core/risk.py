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
ERR_SILENT_INTERCEPT = 220012     # 发帖静默拦截（web 发帖接口无文本拒绝码）

# ── 验证码 / 风控拦截 ──
# 关键词取历史上两套实现的并集：batch_post 的 ["验证码","captcha","安全验证",
# "操作太频繁","频繁登录","账号异常"] 与 post.py 的 "captcha"/"验证码"
CAPTCHA_KEYWORDS = ("验证码", "captcha", "安全验证", "操作太频繁", "频繁登录", "账号异常")
CAPTCHA_ERR_CODES = (6, 7, 16, 18, 40, 100006, 100007)

# ── 账号级封禁 ──
ACCOUNT_BAN_KEYWORDS = ("封禁", "屏蔽")

# ── 关注/取关场景 ──
ALREADY_FOLLOWED_KEYWORDS = ("已关注",)
NOT_FOLLOWED_KEYWORDS = ("未关注", "没有关注", "尚未关注", "未收藏", "没有收藏", "尚未收藏")
BLACKLIST_KEYWORDS = ("被拉黑",)

# ── 物料占位符/模板假链标记族（单一事实源，原 web 层 preflight 内联定义上移）──
# 物料模板预留位未替换就投放 = 内容残缺的典型垃圾帖特征（2026-10-01 电影吧
# 220012 事故的疑似诱因之一；池内实测共 5 个变体家族）。
# 消费方：web 导入预检 scan_import_pairs、AI 改写输出门禁（optimize_post）。
PLACEHOLDER_MARKS = (
    "这里插入链接",
    "此处插入链接",
    "在这里插入链接",
    "你的链接地址",
    "[链接地址]",
    "example.com",
    "公众号",
    # AI 改写编造的 markdown 假链变体（10-03 池内复扫 179 条中招）：
    # 种子无链接时模型产出 "[标题](#)" 型装饰链接，贴吧不渲染 markdown，
    # 发出去就是赤裸的占位假链
    "](#)",
    "](＃)",
    "]()",
)

_CODE_RE = re.compile(r"(\d{4,})")
# vcode.need_vcode 真值提取：兼容 python dict 字符串（'need_vcode': 0）与
# JSON（"need_vcode":0）两种形态
_NEED_VCODE_RE = re.compile(r"need_vcode['\"]?\s*[:=]\s*([01])")


def extract_err_code(err_msg) -> int:
    """从错误文本中提取 4 位以上数字错误码，找不到返回 0。

    替代历史散落两处的 `re.search(r'(\\d{4,})', err_msg)` 复制粘贴。
    """
    if not err_msg:
        return 0
    m = _CODE_RE.search(str(err_msg))
    return int(m.group(1)) if m else 0


def is_captcha_error(err_msg="", err_code: int = 0) -> bool:
    """是否触发验证码/风控人机拦截。

    响应中 vcode.need_vcode 是唯一真值来源：报文里带 "captcha" 的字段名
    （如 captcha_vcode_str）不构成判定依据——对字符串化响应做子串匹配曾把
    need_vcode=0 的普通拒绝误判成验证码（2026-10-01 220012 熔断误报）。
    """
    msg = str(err_msg or "")
    m = _NEED_VCODE_RE.search(msg)
    if m:
        return m.group(1) == "1"
    return any(kw in msg for kw in CAPTCHA_KEYWORDS) or err_code in CAPTCHA_ERR_CODES


def is_silent_intercept_error(err_msg="", err_code: int = 0) -> bool:
    """是否为发帖静默拦截（220012：error=None、tid=0、帖子未发出）。

    实证语义：账号在封禁状态下发帖（2026-10-01 电影吧吧务单吧封禁、
    2026-09-21 hwdemtv3 全吧封禁均返回此码）。若报文同时给出
    need_vcode=1，属验证码挑战，调用方应先按验证码分支处理。
    """
    if err_code == ERR_SILENT_INTERCEPT:
        return True
    return str(ERR_SILENT_INTERCEPT) in str(err_msg or "")


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


def is_not_followed_error(err_msg="") -> bool:
    """是否为“未关注”类幂等错误（取关场景可安全视为已达成并清理记录）。"""
    return any(kw in str(err_msg or "") for kw in NOT_FOLLOWED_KEYWORDS)
