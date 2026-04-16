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
) -> tuple[bool, str]:
    """
    删除帖子

    Args:
        db: 数据库实例
        fname: 贴吧名称
        tid: 主题帖ID

    Returns:
        (是否成功, 消息)
    """
    creds = await get_account_credentials(db)
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
) -> tuple[bool, str, int]:
    from .obfuscator import Obfuscator
    import httpx
    import asyncio
    """
    发帖

    Args:
        db: 数据库实例
        fname: 贴吧名称
        title: 帖子标题
        content: 帖子内容

    Returns:
        (是否成功, 消息, tid)
    """
    creds = await get_account_credentials(db)
    if not creds:
        return False, "未找到账号凭证", 0

    _, bduss, stoken, proxy_id, cuid, ua = creds

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        try:
            import httpx
            # 通过 aiotieba 获取包含 fid 和 tbs 在内的上下文环境
            await client.get_self_info()
            if not getattr(client.account, 'tbs', None):
                return False, "获取账号发帖凭证(TBS)失败", 0

            forum = await client.get_forum(fname)
            
            # 增强环境伪装头
            # [核心加固] 对贴吧名进行转义，防止 Windows 环境下请求头触发 ASCII 编码异常
            quoted_fname = urllib.parse.quote(fname)
            
            headers = {
                "Cookie": f"BDUSS={bduss}; STOKEN={stoken}",
                "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                "Referer": f"https://tieba.baidu.com/f?kw={quoted_fname}",
                "Origin": "https://tieba.baidu.com",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            
            # 使用现有代理及 URL 构建
            proxy_model = await db.get_proxy(proxy_id) if proxy_id else None
            proxy_url = None
            if proxy_model:
                from .account import decrypt_value
                scheme = proxy_model.protocol
                h = proxy_model.host
                p = proxy_model.port
                user = decrypt_value(proxy_model.username) if proxy_model.username else ""
                pwd = decrypt_value(proxy_model.password) if proxy_model.password else ""
                if user and pwd:
                    proxy_url = f"{scheme}://{user}:{pwd}@{h}:{p}"
                else:
                    proxy_url = f"{scheme}://{h}:{p}"
            
            # 【核心层】反风控干扰触发 (仅混淆中文字符防抽，保留原意)
            safe_title = Obfuscator.inject_zero_width_chars(title, density=0.2)
            safe_content = Obfuscator.humanize_spacing(Obfuscator.inject_zero_width_chars(content, density=0.3))

            data = {
                "ie": "utf-8",
                "kw": fname,
                "fid": forum.fid,
                "tbs": client.account.tbs,
                "title": safe_title,
                "content": safe_content,
                "anonymous": 0
            }
            
            async with httpx.AsyncClient(proxy=proxy_url) as http_client:
                # 【诊断日志】确认新固件已加载
                from .logger import log_info as diagnostic_log
                await diagnostic_log(f"[加固协议开启] 正在对 {fname} 执行预读转义...")
                
                # 【第一步防封】预热会话，模拟真人正在阅读本吧首页 (同步转义 URL)
                try:
                    await http_client.get(f"https://tieba.baidu.com/f?kw={quoted_fname}", headers=headers, timeout=10.0)
                    await asyncio.sleep(1.2)  # 停留模拟人类打字停顿
                except Exception as e:
                    from .logger import log_warn
                    await log_warn(f"[{fname}] 预读失败: {str(e)}")
                    
                # 【第二步防封】实际提交流程
                # [终极加固] 手动将数据编码为 UTF-8 字节流，避免 httpx 在 ASCII 环境下自动转换失效
                body_content = urllib.parse.urlencode(data).encode('utf-8')
                
                res = await http_client.post(
                    "https://tieba.baidu.com/f/commit/thread/add",
                    headers=headers,
                    content=body_content,
                    timeout=15.0
                )
                
                res_json = res.json()
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


async def check_post_survival(tid: int) -> tuple[str, str]:
    """
    检测帖子是否存活

    Args:
        tid: 主题帖ID

    Returns:
        (存活状态: "alive"/"dead", 被删原因)
    """
    try:
        async with aiotieba.Client() as client:
            res = await client.get_posts(tid)
            
            # 检测验证码拦截
            if res and hasattr(res, 'text') and '验证码' in str(res.text or ''):
                return "dead", "captcha_required"
            
            if res and res.forum and res.forum.fid > 0:
                # 增强判断：检查帖子基本信息完整性
                if res.thread:
                    # 有回复数说明帖子健康（被删帖通常没有回复数据）
                    if res.thread.reply_num is not None:
                        return "alive", ""
                    # 有标题但无回复数，也视为存活
                    if res.thread.title:
                        return "alive", ""
                # 兜底：只要有有效的 forum fid 就视为存活
                return "alive", ""
            else:
                return "dead", "unknown_error"
    except Exception as ex:
        error_msg = str(ex).lower()
        if "captcha" in error_msg or "验证码" in str(ex):
            return "dead", "captcha_required"
        elif "deleted" in error_msg or "removed" in error_msg:
            return "dead", "deleted_by_user"
        elif "banned" in error_msg or "blocked" in error_msg:
            return "dead", "banned_by_mod"
        elif "not found" in error_msg or "404" in error_msg:
            return "dead", "auto_removed"
        else:
            return "dead", "error"
