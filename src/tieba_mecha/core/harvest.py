"""Harvest - 养号顺手采集热门资源物料（零新增请求）

挂在养号浏览循环里：候选判定用帖子列表响应自带的 reply_num/title 元数据，
链接提取只用已拉回的 get_posts 首页楼层（楼主正文 + 1楼/2楼 等首评）。
全流程不发起任何额外网络请求。

词表口径（与出站文案黑名单严格分离）：
- 入站只挡明显引流信号（加微信/QQ群/公众号等），营销禁词在人工审核+AI改写环节生效；
- "合集"是资源帖特征词，不在入站黑名单里；
- 求助帖（"求个/来一个"类）由标题词防线直接拒绝：2026-10-18 实弹"网站或资源来一个"
  入库证明"楼层无链接自然裁决"会被首评带链的求助帖击穿。
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote

# 采集配置默认值（与 web/pages/settings.py 的 _maint_config 保持一致；
# maint_harvest_forums / maint_harvest_directed_prob 仅 settings 表，无 UI 控件）
HARVEST_DEFAULTS = {
    "maint_harvest_enabled": "true",
    "maint_harvest_min_reply": "30",
    "maint_harvest_min_agree": "5",
    "maint_harvest_max_per_cycle": "1",
    "maint_harvest_max_age_days": "90",
    "maint_harvest_forums": "",
    "maint_harvest_directed_prob": "30",
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

# 水贴/版务特征词：命中即放弃采集——吧规、每日水贴盖楼、导航恰是回复数最高的非资源帖
# （2026-10-04 两次误采首恶：#1846 每日水贴盖楼、#1845 2014吧规导航）
HARVEST_TITLE_WATER_WORDS = (
    "水贴", "盖楼", "吧规", "版规", "导航", "签到", "打卡", "闲聊", "吹水", "公告",
)

# 求助/伸手特征词：命中即放弃采集——楼主正文是"求"不是"分享"，物料复用价值为负
# （2026-10-18 实弹：求助帖首评有人给链照样入库，"无链自然裁决"防线被击穿）
HARVEST_TITLE_BEG_WORDS = (
    "求个", "求部", "求资源", "求分享", "求推荐", "求链接", "求发", "求大佬",
    "跪求", "伸手", "来一个", "来个", "来一份", "有没有", "谁有", "哪位有",
)

# 漏斗拒绝原因码 → 日志标签（顺序即判定顺序，funnel_summary_line 按此序展示）
HARVEST_REJECT_LABELS = {
    "reply": "回复不足", "agree": "点赞不足", "top": "置顶/求助",
    "spam": "引流词", "water": "水贴版务", "beg": "求助词",
    "age": "超龄", "keyword": "词表未中",
}

# 老帖年龄门：资源链有效性与帖子年龄强相关（#1845 采进 2014 年吧规，链接基本必死）。
# 超龄帖不采，"持续更新"类合集豁免；create_time 缺失不误杀；0=不限制。可经 settings 覆盖。
HARVEST_MAX_AGE_DAYS_DEFAULT = 90
HARVEST_AGE_EXEMPT_WORDS = ("持续更新",)

# 标题开头的【…】/[…] 段通常是吧名标签（如"【小说资源吧】每日水贴盖楼"——
# 吧名里的"资源"不该算标题特征词），词表匹配前剥掉
_TITLE_PREFIX_RE = re.compile(r"^[【\[][^】\]]*[】\]]")

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
        if link_type == "baidu" and re.search(r"pan\.baidu\.com", url, re.IGNORECASE) \
                and not re.search(r"pan\.baidu\.com/s/", url, re.IGNORECASE):
            continue  # 百度非 /s/ 分享链（mbox 群分享主页等）不是资源链（2026-10-04 #1846 误采）
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


def _matchable_title(title: str) -> str:
    """剥掉标题开头的【…】/[…] 吧名标签段（可连续多段），返回参与词表匹配的净标题"""
    t = (title or "").strip()
    m = _TITLE_PREFIX_RE.match(t)
    while m:
        t = t[m.end():].strip()
        m = _TITLE_PREFIX_RE.match(t)
    return t


def classify_harvest_reject(thread, min_reply: int, max_age_days: int = HARVEST_MAX_AGE_DAYS_DEFAULT,
                            min_agree: int = 0) -> Optional[str]:
    """is_harvest_candidate 的归因版：七道门逐检，返回首个拒绝原因码（HARVEST_REJECT_LABELS 键），
    None=通过为候选。门序与旧实现一致：回复→点赞→置顶/求助→引流词→水贴→求助词→超龄→词表。
    """
    reply_num = getattr(thread, "reply_num", 0) or 0
    title = (getattr(thread, "title", "") or "").strip()
    if reply_num < min_reply:
        return "reply"
    if not title:
        return "keyword"
    if min_agree > 0 and (getattr(thread, "agree", 0) or 0) < min_agree:
        return "agree"
    # 置顶/求助帖：吧规、水贴盖楼、导航、问答的聚集地，几乎必然不是资源分享
    if getattr(thread, "is_top", False) or getattr(thread, "is_help", False):
        return "top"
    core_title = _matchable_title(title)
    if any(w in core_title for w in HARVEST_TITLE_SPAM_WORDS):
        return "spam"
    if any(w in core_title for w in HARVEST_TITLE_WATER_WORDS):
        return "water"
    if any(w in core_title for w in HARVEST_TITLE_BEG_WORDS):
        return "beg"
    create_time = getattr(thread, "create_time", 0) or 0
    if create_time > 0 and max_age_days > 0:
        age_days = (time.time() - create_time) / 86400
        if age_days > max_age_days and not any(w in core_title for w in HARVEST_AGE_EXEMPT_WORDS):
            return "age"
    if not any(k in core_title.upper() for k in HARVEST_TITLE_KEYWORDS):
        return "keyword"
    return None


def is_harvest_candidate(thread, min_reply: int, max_age_days: int = HARVEST_MAX_AGE_DAYS_DEFAULT,
                         min_agree: int = 0) -> bool:
    """帖子列表元数据判定：回复数达标 + 点赞达标 + 标题(去吧名前缀)命中资源词
    + 非置顶/求助 + 非水贴版务 + 非求助伸手 + 未超龄。

    点赞(agree)是比回复更贴近资源价值的信号——水贴盖楼"回复爆炸、点赞寥寥"，
    AND 门槛从数据结构上拦住这类帖；min_agree<=0 关闭（纯回复数旧行为）。
    """
    return classify_harvest_reject(thread, min_reply, max_age_days, min_agree) is None


def rank_harvest_candidates(threads_objs: list, min_reply: int, max_age_days: int = HARVEST_MAX_AGE_DAYS_DEFAULT,
                            min_agree: int = 0) -> list:
    """通过全部门判定的候选，按回复数降序——供调用方对已采过的逐个查重回退取用。"""
    candidates = [t for t in (threads_objs or []) if is_harvest_candidate(t, min_reply, max_age_days, min_agree)]
    candidates.sort(key=lambda t: getattr(t, "reply_num", 0) or 0, reverse=True)
    return candidates


def pick_harvest_candidate(threads_objs: list, min_reply: int, max_age_days: int = HARVEST_MAX_AGE_DAYS_DEFAULT,
                           min_agree: int = 0):
    """从帖子列表（仅元数据，零请求）选出采集候选：命中的里面取回复数最高的"""
    ranked = rank_harvest_candidates(threads_objs, min_reply, max_age_days, min_agree)
    return ranked[0] if ranked else None


def harvest_funnel_stats(threads_objs: list, min_reply: int,
                         max_age_days: int = HARVEST_MAX_AGE_DAYS_DEFAULT,
                         min_agree: int = 0) -> tuple[dict, list, list]:
    """漏斗归因统计（2026-10-22 可观测整改：过滤原本全静默，2条/周无法定位卡点）。

    Returns: (各拒绝原因计数 dict, 候选列表[回复降序], 词表未中样本标题[最多2条])。
    样本仅取"其余门全过、只死在词表"的非空标题帖（回复降序前2）——调词表的直接
    依据，由调用方在零候选时打出，控噪。
    """
    counts: dict[str, int] = {}
    candidates = []
    keyword_missed = []
    for t in (threads_objs or []):
        reason = classify_harvest_reject(t, min_reply, max_age_days, min_agree)
        if reason is None:
            candidates.append(t)
        else:
            counts[reason] = counts.get(reason, 0) + 1
            if reason == "keyword":
                keyword_missed.append(t)
    candidates.sort(key=lambda t: getattr(t, "reply_num", 0) or 0, reverse=True)
    keyword_missed.sort(key=lambda t: getattr(t, "reply_num", 0) or 0, reverse=True)
    samples = [(getattr(t, "title", "") or "").strip()[:20]
               for t in keyword_missed if (getattr(t, "title", "") or "").strip()][:2]
    return counts, candidates, samples


def funnel_summary_line(counts: dict, total: int, n_candidates: int,
                        min_reply: int, min_agree: int = 0, max_age_days: int = 0) -> str:
    """漏斗聚合行文案：固定门序、只列非零项、门槛值内嵌。"""
    labels = {
        "reply": f"回复<{min_reply}",
        "agree": f"点赞<{min_agree}",
        "top": "置顶/求助",
        "spam": "引流词",
        "water": "水贴版务",
        "beg": "求助词",
        "age": f"超龄>{max_age_days}天" if max_age_days > 0 else "超龄",
        "keyword": "词表未中",
    }
    parts = [f"共{total}帖"]
    parts += [f"{labels[k]}:{counts[k]}" for k in HARVEST_REJECT_LABELS if counts.get(k)]
    parts.append(f"→ 候选{n_candidates}")
    return "｜".join(parts)


def pick_directed_forum(raw: dict) -> Optional[str]:
    """采集定向轮换：白名单概率命中时返回本轮应逛的吧名，否则 None。

    raw 为 get_settings_bulk(HARVEST_DEFAULTS 键) 结果。maint_harvest_forums
    为中英文逗号分隔吧名白名单，maint_harvest_directed_prob 为百分比整数（0-100，
    越界收敛）。enabled=false / 白名单空 / 概率未命中 / 脏值一律 None，
    降级为常规随机逛吧。维护循环用它替换本轮目标吧（替换而非追加，零新增请求）。
    """
    try:
        if str(raw.get("maint_harvest_enabled", "true")).strip().lower() == "false":
            return None
        forums = [s.strip() for s in re.split(r"[，,]", str(raw.get("maint_harvest_forums", "") or "")) if s.strip()]
        if not forums:
            return None
        prob = min(max(int(float(raw.get("maint_harvest_directed_prob", "30") or 30)), 0), 100)
        if prob <= 0 or random.random() >= prob / 100:
            return None
        return random.choice(forums)
    except Exception:
        return None


async def _default_link_checker(url: str, link_type: str):
    """默认死链检测器（懒加载，隔离 aiohttp 导入成本）"""
    from .link_check import check_link_alive
    return await check_link_alive(url, link_type)


async def harvest_from_posts(db, posts_page, thread, source_fname: str, *,
                             link_checker=None, log_info=None, log_warn=None) -> Optional[int]:
    """从已拉回的 get_posts 首页响应提取并入库一条采集物料。

    楼层优先级：楼主(1楼)最先，其后按楼层顺序取最早命中；只扫本页，不翻楼中楼。
    extract_floor_link 只认网盘链接，整帖无网盘链接则不入库。
    入库走 add_harvested_material（内部按 source_tid 去重）。
    死链检测（fail-open，2026-10-06）：提取到链接先验有效性，确认失效不入库
    并记已见账本防重复检测；未知/有效照常入库，有效时在 note 追加检测结论。
    link_checker/log_info/log_warn 可注入（maintenance 传真实日志，测试传桩）。
    Returns: 新物料 ID；未命中链接/已采过/链接确认失效/异常返回 None。
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

    # 死链检测：确认失效→不入库+记已见账本；未知→照常入库（fail-open）
    checker = link_checker or _default_link_checker
    try:
        verdict = await checker(hit.url, hit.link_type)
    except Exception:
        verdict = None
    if verdict is not None and verdict.alive is False:
        try:
            await db.mark_harvest_seen(getattr(thread, "tid", 0) or 0)
        except Exception:
            pass
        if log_warn:
            await log_warn(
                f"[BioWarming] 🌾候选帖源链已失效({verdict.detail})，不入库: "
                f"{(getattr(thread, 'title', '') or '')[:24]}... [{hit.url[:60]}]"
            )
        return None
    if verdict is not None and verdict.alive is True:
        hit.note = f"{hit.note} | 链接检测: 有效" if hit.note else "链接检测: 有效"
    elif verdict is not None and log_info:
        await log_info(f"[BioWarming] 🌾候选帖链接检测未知({verdict.detail})，照常入库待人工核")

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
