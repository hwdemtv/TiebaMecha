"""Harvest - 养号顺手采集热门资源物料（零新增请求）

挂在养号浏览循环里：候选判定用帖子列表响应自带的 reply_num/title 元数据，
链接提取只用已拉回的 get_posts 首页楼层（楼主正文 + 1楼/2楼 等首评）。
全流程不发起任何额外网络请求。

词表口径（与出站文案黑名单严格分离）：
- 入站只挡明显引流信号（加微信/QQ群/公众号等），营销禁词在人工审核+AI改写环节生效；
- "合集"是资源帖特征词，不在入站黑名单里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote

# 采集配置默认值（与 web/pages/settings.py 的 _maint_config 保持一致）
HARVEST_DEFAULTS = {
    "maint_harvest_enabled": "true",
    "maint_harvest_min_reply": "30",
    "maint_harvest_max_per_cycle": "1",
}

# 标题特征词：命中其一即视为资源帖候选（"求资源"类请求帖不挡，由楼层扫描有无链接自然裁决）
HARVEST_TITLE_KEYWORDS = (
    "资源", "合集", "打包", "自取", "分享", "整理", "蓝光", "杜比",
    "4K", "1080", "全集", "完整版", "持续更新", "高清", "片单",
)

# 入站引流信号：标题命中即放弃采集（出站营销禁词不在此列）
HARVEST_TITLE_SPAM_WORDS = (
    "加微信", "加V", "加v", "vx", "VX", "V信", "QQ群", "qq群", "QQ裙",
    "公众号", "引流", "招代理", "兼职", "下单", "优惠", "折扣",
)

# 链接提取：兼容带 scheme 与裸域名（贴吧发帖常去 scheme 躲过滤，与 preflight 口径一致）
_HARVEST_URL_RE = re.compile(
    r"https?://[^\s<>\"'）)，。、！？；]+"
    r"|[-A-Za-z0-9.]{4,}\.(?:com|cn|net|top|xyz|me|cc|io|link)(?:/[^\s<>\"'）)，。、！？；]*)?",
    re.IGNORECASE,
)
# 提取码：提取码/密码/pwd 后跟 3-8 位字母数字（百度 ?pwd= 形式由 classify 前的预处理拆出）
_HARVEST_CODE_RE = re.compile(r"(?:提取码|访问码|密码|pwd)[=：:\s]*([A-Za-z0-9]{3,8})", re.IGNORECASE)

# 网盘类型识别（采集时打标，将来转存后端按类型分发）
_NETDISK_TYPE_RES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"pan\.baidu\.com|bdy\.sina", re.IGNORECASE), "baidu"),
    (re.compile(r"quark\.cn", re.IGNORECASE), "quark"),
    (re.compile(r"(?:drive|pan)\.uc\.cn", re.IGNORECASE), "uc"),
    (re.compile(r"aliyundrive|alipan\.com", re.IGNORECASE), "aliyun"),
    (re.compile(r"lanzou[a-z]{0,3}\.com|wpan\.cc", re.IGNORECASE), "lanzou"),
    (re.compile(r"123pan\.com|123684\.com", re.IGNORECASE), "pan123"),
)
KNOWN_NETDISK_TYPES = {"baidu", "quark", "uc", "aliyun", "lanzou", "pan123"}

# 楼层上下文截断长度（source_link_note 存提取码+短上下文）
_NOTE_MAX_LEN = 200

# harvested 物料派生态（不落库，由 source_link_url/link_url 现场推导，避免存储态与真实态脱节）
HARVEST_STATE_PENDING_TRANSFER = "pending_transfer"  # 待转存：有源链无自有链，次序门拦截中
HARVEST_STATE_TRANSFERRED = "transferred"            # 已转存·待审核
HARVEST_STATE_CONTENT_ONLY = "content_only"          # 纯内容：无源链（或已跳过链接），可直接放行


def harvest_state(source_url: str | None, own_url: str | None) -> str:
    if (source_url or "").strip() and (own_url or "").strip():
        return HARVEST_STATE_TRANSFERRED
    if (source_url or "").strip():
        return HARVEST_STATE_PENDING_TRANSFER
    return HARVEST_STATE_CONTENT_ONLY


@dataclass
class HarvestHit:
    """单楼层提取结果"""
    url: str
    link_type: str
    note: str


def classify_link(url: str) -> str:
    """按域名归类网盘类型；未识别返回 other"""
    for pattern, type_name in _NETDISK_TYPE_RES:
        if pattern.search(url):
            return type_name
    return "other"


def _extract_code(text: str) -> str:
    m = _HARVEST_CODE_RE.search(text)
    return m.group(1) if m else ""


# 贴吧客户端给外链套的跳转壳：真身在 url= 参数里（percent-encoded）
_CHECKURL_HOST = "tieba.baidu.com/mo/q/checkurl"
_CHECKURL_URL_PARAM_RE = re.compile(r"[?&]url=([^&\s]+)", re.IGNORECASE)


def _unwrap_checkurl(url: str) -> str:
    """解贴吧 checkurl 跳转壳，返回壳内真实目标；非壳或解不出参数时原样返回。

    不解包的后果：真网盘链接被包进壳后域名是 tieba.baidu.com，识别不出网盘类型；
    反过来壳内的相册图/杂链也必须解包后才能被正确拒收（2026-10-01 物料#830 首例误采）。
    """
    if _CHECKURL_HOST not in url.lower():
        return url
    m = _CHECKURL_URL_PARAM_RE.search(url)
    if not m:
        return url
    return unquote(m.group(1))


def extract_floor_link(text: str) -> Optional[HarvestHit]:
    """从单楼层文本提取网盘链接——只认已知网盘标识，未识别域名一律不采。

    checkurl 壳先解包再识别：壳内真网盘能被打标并存下解包后的纯链
    （转存需程序化打开纯 URL），壳内相册图/杂链不冒充资源。
    note = 提取码(若有) + 楼层文本前 200 字符上下文。
    无网盘链接返回 None。
    """
    if not text:
        return None
    urls = _HARVEST_URL_RE.findall(text)
    if not urls:
        return None

    for raw_url in urls:
        url = _unwrap_checkurl(raw_url).rstrip(".,;:!?)]}") or raw_url
        link_type = classify_link(url)
        if link_type == "other":
            continue  # 未识别域名（贴吧壳内杂链/相册图/无关外链）不采
        # 百度盘 ?pwd= 内联码拆进 note，URL 保持原样（format_link_for_share 发出时也会同规则拆）
        code = _extract_code(text)
        inline_pwd = ""
        pwd_m = re.search(r"[?&]pwd=([A-Za-z0-9]{3,8})", url)
        if pwd_m:
            inline_pwd = pwd_m.group(1)
        parts = []
        if inline_pwd:
            parts.append(f"提取码 {inline_pwd}")
        if code and code != inline_pwd:
            parts.append(f"提取码 {code}")
        context = text.strip()[:_NOTE_MAX_LEN]
        note = " | ".join(parts + ([context] if context else []))
        return HarvestHit(url=url, link_type=link_type, note=note)
    return None


def is_harvest_candidate(thread, min_reply: int) -> bool:
    """帖子列表元数据判定：回复数达标 + 标题命中资源特征词 + 未命中引流信号"""
    reply_num = getattr(thread, "reply_num", 0) or 0
    title = (getattr(thread, "title", "") or "").strip()
    if reply_num < min_reply or not title:
        return False
    if any(w in title for w in HARVEST_TITLE_SPAM_WORDS):
        return False
    return any(k in title.upper() for k in HARVEST_TITLE_KEYWORDS)


def pick_harvest_candidate(threads_objs: list, min_reply: int):
    """从帖子列表（仅元数据，零请求）选出采集候选：命中的里面取回复数最高的"""
    candidates = [t for t in (threads_objs or []) if is_harvest_candidate(t, min_reply)]
    if not candidates:
        return None
    return max(candidates, key=lambda t: getattr(t, "reply_num", 0) or 0)


async def harvest_from_posts(db, posts_page, thread, source_fname: str) -> Optional[int]:
    """从已拉回的 get_posts 首页响应提取并入库一条采集物料。

    楼层优先级：楼主(1楼)最先，其后按楼层顺序取最早命中；只扫本页，不翻楼中楼。
    extract_floor_link 只认网盘链接，整帖无网盘链接则不入库。
    入库走 add_harvested_material（内部按 source_tid 去重）。
    Returns: 新物料 ID；未命中链接/已采过/异常返回 None。
    """
    objs = getattr(posts_page, "objs", None) or []
    floors = sorted(objs, key=lambda p: getattr(p, "floor", 0) or 0)

    hit: Optional[HarvestHit] = None
    for post in floors:
        text = getattr(post, "text", "") or ""
        candidate = extract_floor_link(text)
        if candidate is not None:
            hit = candidate
            break

    if hit is None:
        return None

    op_text = ""
    for post in floors:
        if (getattr(post, "floor", 0) or 0) == 1:
            op_text = (getattr(post, "text", "") or "").strip()
            break

    try:
        return await db.add_harvested_material(
            title=(getattr(thread, "title", "") or "")[:500],
            content=op_text,
            source_tid=getattr(thread, "tid", 0) or 0,
            source_fname=(source_fname or "")[:100],
            source_link_url=hit.url[:500],
            source_link_note=hit.note,
            source_link_type=hit.link_type,
        )
    except Exception:
        return None
