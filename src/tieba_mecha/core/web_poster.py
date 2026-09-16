"""贴吧 Web 表单 API 发帖公共层。

add_thread（post.py）与 execute_task（batch_post.py）历史上各维护一套
headers 构造 / [br] 换行转换 / 预热浏览 / commit 提交（连默认 UA 都不一致：
Chrome/119 vs Chrome/120）。本模块是两处共用的单源实现。
"""

from __future__ import annotations

import asyncio
import random
import urllib.parse

import httpx

# 统一默认 UA（此前 post.py 用 Chrome/119、batch_post.py 用 Chrome/120）
DEFAULT_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

COMMIT_THREAD_ADD_URL = "https://tieba.baidu.com/f/commit/thread/add"


def build_web_headers(bduss: str, stoken: str, quoted_fname: str, ua: str | None = None) -> dict:
    """构建贴吧 Web 表单 API 的仿浏览器请求头（吧名需先 quote）。"""
    return {
        "Cookie": f"BDUSS={bduss}; STOKEN={stoken}",
        "User-Agent": ua or DEFAULT_WEB_UA,
        "Referer": f"https://tieba.baidu.com/f?kw={quoted_fname}",
        "Origin": "https://tieba.baidu.com",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }


def content_to_web_bbcode(content: str) -> str:
    """规范化换行并转换为 [br] BBCode（贴吧 Web 表单 API 的换行格式）。"""
    return content.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '[br]')


def build_thread_payload(fname: str, fid: int, tbs: str, title: str, web_content: str) -> bytes:
    """构建发帖表单并手动 URL 编码为原始字节（避免 httpx 对 <> 的过度编码）。"""
    data = {
        "ie": "utf-8",
        "kw": fname,
        "fid": fid,
        "tbs": tbs,
        "title": title,
        "content": web_content,
        "anonymous": 0,
        "rich_text": "1",
    }
    return urllib.parse.urlencode(data, encoding='utf-8').encode('utf-8')


async def prewarm_and_commit_thread(
    http_client: httpx.AsyncClient,
    headers: dict,
    quoted_fname: str,
    post_body: bytes,
    *,
    prewarm_sleep: tuple[float, float] = (1.2, 3.5),
    commit_timeout: float = 25.0,
    logger=None,
) -> dict:
    """预热浏览本吧首页后提交发帖，返回解析后的 JSON 响应。

    Args:
        prewarm_sleep: 预热后停顿时长范围（秒），模拟真人打字停顿。
        commit_timeout: 提交请求超时（批量路径用 25s，手动单发可用更短）。
        logger: 可选的日志函数（接收 str），预热失败记为非关键警告。
    """
    # 第一步防封：预热会话，模拟真人正在阅读本吧首页
    try:
        await http_client.get(f"https://tieba.baidu.com/f?kw={quoted_fname}", headers=headers, timeout=10.0)
        await asyncio.sleep(random.uniform(*prewarm_sleep))
    except Exception as e:
        if logger:
            try:
                await logger(f"预热浏览非关键失败: {e}")
            except Exception:
                pass

    # 第二步防封：实际提交流程
    res = await http_client.post(
        COMMIT_THREAD_ADD_URL,
        headers=headers,
        content=post_body,
        timeout=commit_timeout,
    )
    return res.json()
