"""网盘链接有效性检测（采集时死链过滤，2026-10-06）

采集流程在 extract_floor_link 提取到网盘链接后调用 check_link_alive 拉一次
分享公开页做死链判定。设计铁律 **fail-open（宁可漏判不可错杀）**：

- 只有"明确失效"证据才判死：HTTP 404，或响应体命中该网盘的失效标记文案；
- 网络异常/超时/反爬验证页/未支持类型 → 一律"未知"，照常入库（维持旧行为）；
- 判死 → 不入库 + 记已见账本（mark_harvest_seen），防止同一死链候选每轮
  养号重复检测白耗请求。

真实网络实测结论（2026-10-06 初版 / 2026-11-03 扩测，勿凭想象回改）：
- **百度盘响应头说谎**：Content-Encoding 声称 gzip 但实际是裸 HTML——必须
  关闭客户端自动解压、按 magic bytes 手工解压，否则 aiohttp/httpx 直接
  DecodingError/ClientPayloadError；无效分享链实测返回 HTTP 404。
- **夸克/UC 是 SPA 壳页，纯 GET 判不了死活**，但分享 token 接口可判
  （2026-11-03 真网实测：存活=HTTP 200 + JSON code=0 + stoken；失效=
  HTTP 404 + code=41006 + "分享不存在"；UC 同族端点同特征）——这两类
  改走接口判定；带提取码的存活分享会返回提取码相关错误，同样算存活。
- **阿里云盘仍不检测**（SPA 且无对等公开接口验证），返回未知照常入库。
  新网盘类型接入前必须先真网验证渲染方式。

请求为公开分享页 GET / 分享 token 接口 POST，不带任何账号凭证，不占用
贴吧客户端（零贴吧请求，不触碰账号风控面）。lanzou 等站点可能 gbk 编码，
utf-8/gbk 双解码后匹配。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# 服务端渲染、可纯 GET 判死活的网盘 → "确认失效"页面特征文案
# （只收明确措辞，fail-open：漏判=照旧入库，错杀=丢物料；不进表且不进
# _SHARE_API 的类型=不检测）
_DEAD_MARKERS: dict[str, tuple[str, ...]] = {
    "baidu": ("已经被取消", "已经被删除", "分享已过期", "分享不存在", "页面不存在", "涉及侵权"),
    "lanzou": ("文件不存在", "已取消分享"),
}

# 夸克/UC 分享 token 接口（SPA 壳页判不了死活，走接口；2026-11-03 真网实测特征）
_SHARE_API: dict[str, str] = {
    "quark": "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc",
    "uc": "https://pc-api.uc.cn/1/clouddrive/share/sharepage/token?pr=UCBrowser&fr=pc",
}
_SHARE_DEAD_MESSAGES = ("分享不存在", "分享已被取消", "分享已过期")
_SHARE_ALIVE_MESSAGES = ("提取码", "访问码", "passcode")
_SHARE_ID_RE = re.compile(r"/s/([A-Za-z0-9]+)")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_BODY_MAX_BYTES = 262144


@dataclass
class LinkCheckVerdict:
    """alive: True=有效 False=确认失效 None=未知（检测失败/不支持类型）"""
    alive: bool | None
    detail: str = ""


def judge_link_body(link_type: str, status: int, body: str) -> LinkCheckVerdict:
    """纯判定：HTTP 状态 + 响应体文本 → 死活结论（不碰网络，单测主战场）"""
    if status == 404:
        return LinkCheckVerdict(False, "HTTP 404")
    markers = _DEAD_MARKERS.get(link_type)
    if not markers:
        return LinkCheckVerdict(None, f"未支持类型 {link_type}（SPA 壳页无法纯 GET 判定），跳过检测")
    if body:
        for marker in markers:
            if marker in body:
                return LinkCheckVerdict(False, f"命中失效标记: {marker}")
    return LinkCheckVerdict(True, "页面无失效标记")


def extract_share_id(url: str) -> str:
    """从分享链接提取 pwd_id（/s/<id> 段），取不到返回空串（单测主战场）"""
    m = _SHARE_ID_RE.search(url or "")
    return m.group(1) if m else ""


def judge_share_api_response(link_type: str, status: int, body: str) -> LinkCheckVerdict:
    """纯判定：分享 token API 响应 → 死活结论（不碰网络，单测主战场）。

    fail-open：解析不出确定证据一律"未知"照常入库，绝不向上抛。
    """
    if not body:
        return LinkCheckVerdict(None, f"{link_type} token API 响应体为空")
    try:
        payload = json.loads(body)
    except Exception:
        return LinkCheckVerdict(None, f"{link_type} token API 响应非 JSON")
    if not isinstance(payload, dict):
        return LinkCheckVerdict(None, f"{link_type} token API 响应非对象")
    message = str(payload.get("message", ""))
    api_code = payload.get("code", payload.get("error_code"))
    if any(m in message for m in _SHARE_DEAD_MESSAGES):
        return LinkCheckVerdict(False, f"{link_type} 分享已失效: {message}")
    if status == 200 and api_code == 0:
        return LinkCheckVerdict(True, f"{link_type} 分享存活")
    if any(m in message for m in _SHARE_ALIVE_MESSAGES):
        return LinkCheckVerdict(True, f"{link_type} 分享存活（需提取码）")
    return LinkCheckVerdict(None, f"{link_type} token API 未知响应: HTTP {status} {message[:60]}")


async def _check_via_share_api(target: str, link_type: str, timeout: float) -> LinkCheckVerdict:
    """POST 分享 token 接口判死活（不带凭证、不占贴吧客户端）。异常 → 未知。"""
    pwd_id = extract_share_id(target)
    if not pwd_id:
        return LinkCheckVerdict(None, f"{link_type} 链接解析不出分享 id")
    try:
        import aiohttp
        async with aiohttp.ClientSession(
            headers={"User-Agent": _UA},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as session:
            async with session.post(
                _SHARE_API[link_type], json={"pwd_id": pwd_id, "passcode": ""},
            ) as resp:
                body = (await resp.text())[:_BODY_MAX_BYTES]
                return judge_share_api_response(link_type, resp.status, body)
    except Exception as e:
        return LinkCheckVerdict(None, f"检测异常: {type(e).__name__}")


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _decode_body(raw: bytes) -> str:
    """百度实测 Content-Encoding 头不可信（声称 gzip 实为裸 HTML）：
    按 magic bytes 手工解压，其余当明文；utf-8 主体 + gbk 兜底双解码拼正文。"""
    import gzip as _gzip
    import zlib as _zlib
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = _gzip.decompress(raw)
        except Exception:
            pass
    elif raw[:1] == b"\x78":
        try:
            raw = _zlib.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore") + raw.decode("gbk", errors="ignore")


async def check_link_alive(url: str, link_type: str, timeout: float = 6.0) -> LinkCheckVerdict:
    """拉取分享公开页判死活。任何异常 → 未知（fail-open，绝不向上抛）。"""
    target = _normalize_url(url)
    if not target:
        return LinkCheckVerdict(None, "空链接")
    if link_type in _SHARE_API:
        return await _check_via_share_api(target, link_type, timeout)
    if link_type not in _DEAD_MARKERS:
        return LinkCheckVerdict(None, f"未支持类型 {link_type}（SPA 壳页无法纯 GET 判定），跳过检测")
    try:
        import aiohttp
        async with aiohttp.ClientSession(
            headers={"User-Agent": _UA},
            timeout=aiohttp.ClientTimeout(total=timeout),
            auto_decompress=False,  # 百度 Content-Encoding 头说谎，必须按 magic bytes 自行处理
        ) as session:
            async with session.get(target, allow_redirects=True) as resp:
                raw = await resp.content.read(_BODY_MAX_BYTES)
                return judge_link_body(link_type, resp.status, _decode_body(raw))
    except Exception as e:
        return LinkCheckVerdict(None, f"检测异常: {type(e).__name__}")
