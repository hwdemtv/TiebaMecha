"""Post management functionality"""

from __future__ import annotations

import asyncio
import urllib.parse
from dataclasses import dataclass
from typing import AsyncGenerator

import aiotieba

from ..db.crud import Database
from .account import get_account_credentials
from .client_factory import create_client


@dataclass
class ThreadInfo:
    """主题帖信息"""

    tid: int
    pid: int
    title: str
    text: str
    author_id: int
    author_name: str
    reply_num: int
    create_time: int
    is_good: bool
    is_top: bool


@dataclass
class PostInfo:
    """回复信息"""

    pid: int
    tid: int
    floor: int
    text: str
    author_id: int
    author_name: str
    create_time: int


async def get_threads(
    db: Database,
    fname: str,
    pn: int = 1,
    rn: int = 50,
) -> list[ThreadInfo]:
    """
    获取贴吧帖子列表

    Args:
        db: 数据库实例
        fname: 贴吧名称
        pn: 页码
        rn: 每页数量

    Returns:
        帖子列表
    """
    creds = await get_account_credentials(db)
    if not creds:
        return []

    _, bduss, stoken, proxy_id, cuid, ua = creds
    threads = []

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        result = await client.get_threads(fname, pn=pn, rn=rn)

        for thread in result:
            threads.append(
                ThreadInfo(
                    tid=thread.tid,
                    pid=thread.pid,
                    title=thread.title,
                    text=thread.text[:200] if thread.text else "",
                    author_id=thread.author_id,
                    author_name=thread.user.user_name if thread.user else "",
                    reply_num=thread.reply_num,
                    create_time=thread.create_time,
                    is_good=thread.is_good,
                    is_top=thread.is_top,
                )
            )

    return threads


async def get_posts(
    db: Database,
    tid: int,
    pn: int = 1,
) -> list[PostInfo]:
    """
    获取帖子回复列表

    Args:
        db: 数据库实例
        tid: 主题帖ID
        pn: 页码

    Returns:
        回复列表
    """
    creds = await get_account_credentials(db)
    if not creds:
        return []

    _, bduss, stoken, proxy_id, cuid, ua = creds
    posts = []

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        result = await client.get_posts(tid, pn=pn)

        for post in result:
            posts.append(
                PostInfo(
                    pid=post.pid,
                    tid=tid,
                    floor=post.floor,
                    text=post.text[:200] if post.text else "",
                    author_id=post.author_id,
                    author_name=post.user.user_name if post.user else "",
                    create_time=post.create_time,
                )
            )

    return posts


async def delete_thread(
    db: Database,
    fname: str,
    tid: int,
    account_id: int | None = None,
) -> tuple[bool, str]:
    """
    删除帖子

    Args:
        db: 数据库实例
        fname: 贴吧名称
        tid: 主题帖ID
        account_id: 执行删除的账号ID（贴吧仅允许作者删帖，应传帖子作者；
            None 则回退当前活跃账号）

    Returns:
        (是否成功, 消息)
    """
    creds = await get_account_credentials(db, account_id)
    if not creds:
        return False, "未找到账号凭证"

    _, bduss, stoken, proxy_id, cuid, ua = creds

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        result = await client.del_thread(fname, tid)
        if result:
            return True, "删除成功"
        else:
            return False, "删除失败"


async def delete_threads(
    db: Database,
    fname: str,
    tids: list[int],
    delay: float = 0.5,
) -> AsyncGenerator[tuple[int, bool, str], None]:
    """
    批量删除帖子

    Args:
        db: 数据库实例
        fname: 贴吧名称
        tids: 帖子ID列表
        delay: 每次操作间隔

    Yields:
        (tid, 是否成功, 消息)
    """
    for tid in tids:
        success, msg = await delete_thread(db, fname, tid)
        yield tid, success, msg
        await asyncio.sleep(delay)


async def set_good(
    db: Database,
    fname: str,
    tid: int,
    is_good: bool = True,
) -> tuple[bool, str]:
    """
    设置/取消精品

    Args:
        db: 数据库实例
        fname: 贴吧名称
        tid: 主题帖ID
        is_good: True 加精, False 取消

    Returns:
        (是否成功, 消息)
    """
    creds = await get_account_credentials(db)
    if not creds:
        return False, "未找到账号凭证"

    _, bduss, stoken, proxy_id, cuid, ua = creds

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        if is_good:
            result = await client.good(fname, tid)
        else:
            result = await client.ungood(fname, tid)

        if result:
            return True, "操作成功"
        else:
            return False, "操作失败"


async def set_top(
    db: Database,
    fname: str,
    tid: int,
    is_top: bool = True,
) -> tuple[bool, str]:
    """
    设置/取消置顶

    Args:
        db: 数据库实例
        fname: 贴吧名称
        tid: 主题帖ID
        is_top: True 置顶, False 取消

    Returns:
        (是否成功, 消息)
    """
    creds = await get_account_credentials(db)
    if not creds:
        return False, "未找到账号凭证"

    _, bduss, stoken, proxy_id, cuid, ua = creds

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        result = await client.top(fname, tid, is_top)
        if result:
            return True, "操作成功"
        else:
            return False, "操作失败"


async def search_threads(
    db: Database,
    fname: str,
    keyword: str,
) -> list[ThreadInfo]:
    """
    搜索帖子

    Args:
        db: 数据库实例
        fname: 贴吧名称
        keyword: 搜索关键词

    Returns:
        帖子列表
    """
    creds = await get_account_credentials(db)
    if not creds:
        return []

    _, bduss, stoken, proxy_id, cuid, ua = creds
    threads = []

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        result = await client.search_exact(fname, keyword)

        for thread in result:
            threads.append(
                ThreadInfo(
                    tid=thread.tid,
                    pid=thread.pid,
                    title=thread.title,
                    text=thread.text[:200] if thread.text else "",
                    author_id=thread.author_id,
                    author_name=thread.user.user_name if thread.user else "",
                    reply_num=thread.reply_num,
                    create_time=thread.create_time,
                    is_good=thread.is_good,
                    is_top=thread.is_top,
                )
            )

    return threads


async def add_thread(
    db: Database,
    fname: str,
    title: str,
    content: str,
    account_id: int | None = None,
) -> tuple[bool, str, int]:
    """
    发帖

    Args:
        db: 数据库实例
        fname: 贴吧名称
        title: 帖子标题
        content: 帖子内容
        account_id: 发帖账号ID（None 则使用当前活跃账号）

    Returns:
        (是否成功, 消息, tid)
    """
    from .obfuscator import Obfuscator
    from .web_poster import build_web_headers, normalize_web_content, build_thread_payload, prewarm_and_commit_thread
    import httpx
    creds = await get_account_credentials(db, account_id)
    if not creds:
        return False, "未找到账号凭证", 0

    _, bduss, stoken, proxy_id, cuid, ua = creds

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        try:
            # 通过 aiotieba 获取包含 fid 和 tbs 在内的上下文环境
            await client.get_self_info()
            if not getattr(client.account, 'tbs', None):
                return False, "获取账号发帖凭证(TBS)失败", 0

            forum = await client.get_forum(fname)

            # [核心加固] 对贴吧名进行转义，防止 Windows 环境下请求头触发 ASCII 编码异常
            quoted_fname = urllib.parse.quote(fname)
            headers = build_web_headers(bduss, stoken, quoted_fname, ua)

            # 使用统一代理 URL 构建（单源：core/proxy.build_proxy_url_from_model）
            from .proxy import build_proxy_url_from_model
            proxy_url = await build_proxy_url_from_model(db, proxy_id)

            # 【核心层】反风控干扰触发 (仅混淆中文字符防抽，保留原意)
            obf = await Obfuscator.from_db(db)
            safe_title = obf.inject_zero_width_chars(title, density=0.2)
            safe_content = obf.obfuscate_all(content)

            post_body = build_thread_payload(fname, forum.fid, client.account.tbs, safe_title, normalize_web_content(safe_content))

            async with httpx.AsyncClient(proxy=proxy_url) as http_client:
                res_json = await prewarm_and_commit_thread(
                    http_client, headers, quoted_fname, post_body,
                    prewarm_sleep=(1.2, 1.2),  # 手动单发保持原固定停顿
                    commit_timeout=15.0,
                )
                if res_json.get("err_code") == 0:
                    tid = res_json.get("data", {}).get("tid", 0)
                    return True, "发帖成功", tid
                else:
                    return False, f"发帖失败: {res_json.get('error') or res_json}", 0
        except Exception as e:
            return False, f"发帖发生异常: {str(e)}", 0


async def add_post(
    db: Database,
    fname: str,
    tid: int,
    content: str,
) -> tuple[bool, str]:
    """
    回复帖子

    Args:
        db: 数据库实例
        fname: 贴吧名称
        tid: 主题帖ID
        content: 回复内容

    Returns:
        (是否成功, 消息)
    """
    creds = await get_account_credentials(db)
    if not creds:
        return False, "未找到账号凭证"

    _, bduss, stoken, proxy_id, cuid, ua = creds

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        try:
            result = await client.add_post(fname, tid, content)
            if result:
                return True, "回复成功"
            else:
                return False, "回复失败"
        except Exception as e:
            return False, f"回复失败: {str(e)}"


# 存活检测错误码语义（百度侧）
_DELETION_SIGNAL_CODES = {273}  # 帖子已删除
_NOT_FOUND_CODES = {269}        # 帖子不存在


def _classify_death_reason(code: int | None, msg: str) -> str:
    """根据百度错误码 + 消息细分删帖原因

    百度接口不会告知删帖者，deleted_by_* 细分只能靠消息关键词启发式；
    证据不足时返回 deleted_unknown。返回值不含 alive —— 本函数只在
    "已确认不可访问"的语境下调用了才有效。
    """
    m = (msg or "").lower()

    # 验证码拦截（检测者被拦，不是帖子死亡）
    if "captcha" in m or "验证码" in (msg or ""):
        return "captcha_required"

    # 帖子已删除类（错误码 273 或含删除关键词）
    if code in _DELETION_SIGNAL_CODES or "删除" in (msg or "") or "deleted" in m or "removed" in m:
        if "系统" in msg or "system" in m or "违规" in msg or "spam" in m:
            return "deleted_by_system"
        if "吧务" in msg or "吧主" in msg or "mod" in m or "banned" in m or "blocked" in m:
            return "deleted_by_mod"
        if "用户" in msg or "楼主" in msg or "自删" in msg:
            return "deleted_by_user"
        return "deleted_unknown"

    # 封禁类（账号/吧被封导致的不可见，按吧务侧处理）
    if "banned" in m or "blocked" in m or "封禁" in (msg or ""):
        return "deleted_by_mod"

    # 帖子不存在
    if "not found" in m or "404" in m or code in _NOT_FOUND_CODES:
        return "auto_removed"

    return "error"


async def check_post_survival(tid: int) -> tuple[str, str]:
    """
    检测帖子是否存活

    判定以 thread 为主证据：thread 存在 → alive；
    thread 缺失（正常响应但无 thread / 响应为空）→ dead（deleted_unknown）。
    旧版"forum 有效即存活"的兜底会把"有 forum 无 thread"的中间态误判存活，已废除。

    检测被拦 ≠ 帖子阵亡：验证码挑战、网络异常、未映射错误码一律返回
    ("unknown", reason)，调用方不应将其落库（保留原状态，等下一轮检测）。

    Returns:
        (存活状态: "alive"/"dead"/"unknown", 原因)
        原因分类:
        - "": 存活
        - "deleted_by_system": 系统风控删除（百度AI/反作弊自动删帖，仅错误消息可辨时）
        - "deleted_by_mod": 吧务手动删除（含封禁类错误）
        - "deleted_by_user": 用户自删/楼主删除（仅错误消息可辨时）
        - "deleted_unknown": 帖子已删除但无法确定删除者（生产实测删帖基本落此）
        - "auto_removed": 帖子不存在/404
        - "captcha_required": 检测者被验证码拦截（unknown）
        - "error": 检测异常/未映射错误（unknown）
    """
    from aiotieba.exception import TiebaServerError

    try:
        async with aiotieba.Client() as client:
            res = await client.get_posts(tid)
    except TiebaServerError as ex:
        reason = _classify_death_reason(ex.code, ex.msg or "")
        if reason in ("captcha_required", "error"):
            # 验证码挑战/未映射错误码：检测本身失败，不能断言帖子死亡
            return "unknown", reason
        from .logger import log_info
        await log_info(
            f"存活检测判定阵亡 tid={tid} code={ex.code} msg={ex.msg!r} → {reason}"
        )
        return "dead", reason
    except Exception as ex:
        # 网络抖动等基础设施异常：不判死，保留原状态等下一轮
        from .logger import log_warn
        await log_warn(f"存活检测异常 tid={tid}: {ex}")
        return "unknown", "error"

    # 正常响应：thread 是帖子可公开访问的唯一可靠证据
    # (Posts.text 属性并不存在，旧版基于它的验证码检查是死代码，已删除)
    if res and getattr(res, "thread", None):
        return "alive", ""

    # 正常响应但 thread 缺失 → 帖子已不可公开访问。
    # 生产实测删帖响应常连 forum 一并无，forum 有无不影响结论，仅记录线索
    hint = ""
    forum = getattr(res, "forum", None) if res else None
    if forum is not None:
        hint = f" forum={getattr(forum, 'name', None) or '?'}"
    from .logger import log_info
    await log_info(f"存活检测判定阵亡 tid={tid}（正常响应无 thread{hint}）→ deleted_unknown")
    return "dead", "deleted_unknown"
