"""网盘链接有效性检测（采集时死链过滤，2026-10-06）

采集流程在 extract_floor_link 提取到网盘链接后调用 check_link_alive 拉一次
分享公开页做死链判定。设计铁律 **fail-open（宁可漏判不可错杀）**：

- 只有"明确失效"证据才判死：HTTP 404，或响应体命中该网盘的失效标记文案；
- 网络异常/超时/反爬验证页/未支持类型 → 一律"未知"，照常入库（维持旧行为）；
- 判死 → 不入库 + 记已见账本（mark_harvest_seen），防止同一死链候选每轮
  养号重复检测白耗请求。

真实网络实测结论（2026-10-06，勿凭想象回改）：
- **百度盘响应头说谎**：Content-Encoding 声称 gzip 但实际是裸 HTML——必须
  关闭客户端自动解压、按 magic bytes 手工解压，否则 aiohttp/httpx 直接
  DecodingError/ClientPayloadError；无效分享链实测返回 HTTP 404。
- **夸克/UC/阿里云盘是 SPA 壳页**：死链也返回 200+空壳（死活文案客户端渲染），
  纯 GET 判不了死活——这些类型不做检测（返回未知照常入库），只留
  服务端渲染的 baidu/lanzou。新网盘类型接入前必须先真网验证渲染方式。

请求为公开分享页 GET，不带任何账号凭证，不占用贴吧客户端（零贴吧请求，
不触碰账号风控面）。lanzou 等站点可能 gbk 编码，utf-8/gbk 双解码后匹配。
"""

from __future__ import annotations

from dataclasses import dataclass

# 服务端渲染、可纯 GET 判死活的网盘 → "确认失效"页面特征文案
# （只收明确措辞，fail-open：漏判=照旧入库，错杀=丢物料；SPA 类型不进表=不检测）
_DEAD_MARKERS: dict[str, tuple[str, ...]] = {
    "baidu": ("已经被取消", "已经被删除", "分享已过期", "分享不存在", "页面不存在", "涉及侵权"),
    "lanzou": ("文件不存在", "已取消分享"),
}

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
