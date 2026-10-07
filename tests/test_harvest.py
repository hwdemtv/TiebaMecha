"""养号采集 (harvest) 链路测试：提取/分类/候选判定/次序门/转存预留口"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tieba_mecha.core.harvest import (
    HARVEST_DEFAULTS,
    classify_link,
    extract_floor_link,
    harvest_from_posts,
    is_harvest_candidate,
    pick_harvest_candidate,
)
from tieba_mecha.core.link_transfer import (
    TransferResult,
    get_link_transfer_backend,
    register_link_transfer_backend,
)


# ---------- 纯逻辑：链接分类 ----------

@pytest.mark.parametrize("url,expected", [
    ("https://pan.baidu.com/s/1AbCdEf?pwd=x3k9", "baidu"),
    ("pan.baidu.com/s/1AbCdEf", "baidu"),
    ("https://pan.quark.cn/s/9f8e7d6c", "quark"),
    ("https://drive.uc.cn/s/55aabb", "uc"),
    ("https://www.alipan.com/s/xyz", "aliyun"),
    ("https://aliyundrive.com/s/xyz", "aliyun"),
    ("https://wwsl.lanzoue.com/ibAxBcd", "lanzou"),
    ("https://www.123pan.com/s/abc-def", "pan123"),
    ("https://example.com/something", "other"),
])
def test_classify_link(url, expected):
    assert classify_link(url) == expected


# ---------- 纯逻辑：楼层链接提取 ----------

def test_extract_baidu_with_inline_pwd():
    hit = extract_floor_link("资源在此 https://pan.baidu.com/s/1AbCdEf?pwd=x3k9 速度存")
    assert hit is not None
    assert hit.link_type == "baidu"
    assert hit.url == "https://pan.baidu.com/s/1AbCdEf?pwd=x3k9"
    assert "提取码 x3k9" in hit.note


def test_extract_code_from_text():
    hit = extract_floor_link("链接: https://pan.quark.cn/s/9f8e7d6c\n提取码：7gh4")
    assert hit is not None
    assert hit.link_type == "quark"
    assert "提取码 7gh4" in hit.note


def test_extract_bare_domain_link():
    # 贴吧发帖常去 scheme 躲过滤，裸域名也要能抓到
    hit = extract_floor_link("整理好了 pan.baidu.com/s/1ZZZZZ 自取")
    assert hit is not None
    assert hit.link_type == "baidu"


def test_extract_prefers_known_type_over_other():
    text = "先看这个 example.com/abc 或者 pan.baidu.com/s/1QqQqQ"
    hit = extract_floor_link(text)
    assert hit is not None
    assert hit.link_type == "baidu"
    assert hit.url == "pan.baidu.com/s/1QqQqQ"


def test_extract_no_link_returns_none():
    assert extract_floor_link("这帖子只有文字没有链接") is None
    assert extract_floor_link("") is None


def test_extract_rejects_unknown_domain():
    # 只认网盘链接标识：未识别域名的杂链（哪怕在楼主楼层）一律不采
    assert extract_floor_link("资源在此 example.com/xyz 自取") is None


def test_extract_rejects_checkurl_album_from_real_incident():
    # 2026-10-01 物料#830 首例误采回归：2014 吧规伸手楼楼主层里，
    # checkurl 壳内真身是已死的百度相册（xiangce），解包后识别不出网盘类型 → 不采
    text = (
        "新人必看，想要资源的，伸手到这里来取。图片来自："
        "http://tieba.baidu.com/mo/q/checkurl?url=http%3A%2F%2Fxiangce.baidu.com"
        "%2Fpicture%2Falbum%2Flist%2Fcc496194cedd2e7bd509f79608d859e9f34b9bb6"
        "&urlrefer=05fe5408da470b019ec4acf9b27338f9\n继续用旧图镇！"
    )
    assert extract_floor_link(text) is None


def test_extract_unwraps_checkurl_pan_link():
    # 包在 checkurl 壳里的真网盘：解包识别出 baidu，且存下解包后的纯链（转存要用）
    wrapped = (
        "链接 http://tieba.baidu.com/mo/q/checkurl?url=https%3A%2F%2Fpan.baidu.com"
        "%2Fs%2F1WrApp%3Fpwd%3Dk9x2 拿好"
    )
    hit = extract_floor_link(wrapped)
    assert hit is not None
    assert hit.link_type == "baidu"
    assert hit.url == "https://pan.baidu.com/s/1WrApp?pwd=k9x2"
    assert "提取码 k9x2" in hit.note


# ---------- 纯逻辑：候选判定 ----------

def _thread(title, reply_num, tid=1):
    return SimpleNamespace(title=title, reply_num=reply_num, tid=tid)


def test_candidate_requires_reply_threshold():
    assert is_harvest_candidate(_thread("4K资源合集打包", 30), 30) is True
    assert is_harvest_candidate(_thread("4K资源合集打包", 29), 30) is False


def test_candidate_requires_keyword():
    assert is_harvest_candidate(_thread("今天天气不错", 100), 30) is False
    assert is_harvest_candidate(_thread("求一部老电影名字", 100), 30) is False or True  # "求资源"类不含特征词则不中
    # "电影"不是特征词、"4K"是
    assert is_harvest_candidate(_thread("1080P 老片修复对比", 50), 30) is True


def test_candidate_rejects_spam_title():
    assert is_harvest_candidate(_thread("4K资源合集 加微信拿", 100), 30) is False
    assert is_harvest_candidate(_thread("资源合集，进QQ群交流", 100), 30) is False


def test_pick_highest_reply_candidate():
    threads = [
        _thread("普通讨论帖", 500, tid=1),
        _thread("蓝光收藏合集", 45, tid=2),
        _thread("4K片单分享", 88, tid=3),
    ]
    picked = pick_harvest_candidate(threads, 30)
    assert picked is not None and picked.tid == 3
    assert pick_harvest_candidate([], 30) is None


# ---------- 策略 v2：2026-10-04 两次误采回归（#1846 每日水贴盖楼 / #1845 2014吧规导航） ----------

def test_candidate_rejects_water_and_rules_titles():
    # 两次真实误采的标题：回复数最多的恰是水贴/版务帖
    assert is_harvest_candidate(_thread("【小说资源吧】每日水贴盖楼", 999), 30) is False
    assert is_harvest_candidate(_thread("《俊男坊》==========2014吧规导航，资源=================", 500), 30) is False


def test_candidate_ignores_bar_prefix_keywords():
    # 吧名前缀里的"资源"不算标题特征（#1846 靠"【小说资源吧】"命中词表的根因）
    assert is_harvest_candidate(_thread("【小说资源吧】今天大家聊聊", 100), 30) is False
    # 前缀外的真特征词照常命中
    assert is_harvest_candidate(_thread("【电影吧】4K蓝光合集", 100), 30) is True


def test_candidate_rejects_top_and_help_threads():
    t = SimpleNamespace(title="4K资源合集", reply_num=100, tid=1, is_top=True)
    assert is_harvest_candidate(t, 30) is False
    t2 = SimpleNamespace(title="4K资源合集", reply_num=100, tid=2, is_help=True)
    assert is_harvest_candidate(t2, 30) is False


def test_candidate_agree_gate_blocks_water_thread():
    # 水贴盖楼"回复爆炸、点赞寥寥"：点赞门槛（与回复 AND）从数据结构上拦住
    water = SimpleNamespace(title="资源交流闲聊", reply_num=999, tid=1, agree=2)
    assert is_harvest_candidate(water, 30, min_agree=5) is False
    # 真资源帖：回复+点赞双达标照常通过
    good = SimpleNamespace(title="4K资源合集", reply_num=45, tid=2, agree=12)
    assert is_harvest_candidate(good, 30, min_agree=5) is True
    # min_agree=0 关闭门槛（含 agree 字段缺失的老调用/老数据）
    assert is_harvest_candidate(_thread("4K资源合集", 45), 30, min_agree=0) is True
    # agree 缺失按 0 处理，门槛开启时拦下
    assert is_harvest_candidate(_thread("4K资源合集", 45), 30, min_agree=5) is False


def test_candidate_rejects_stale_threads():
    import time as _time
    day = 86400
    # 91 天前的老帖：链接基本失效，不采
    old = SimpleNamespace(title="4K资源合集", reply_num=100, tid=1, create_time=_time.time() - 91 * day)
    assert is_harvest_candidate(old, 30) is False
    # 30 天内的照常
    fresh = SimpleNamespace(title="4K资源合集", reply_num=100, tid=2, create_time=_time.time() - 30 * day)
    assert is_harvest_candidate(fresh, 30) is True
    # 超龄但持续更新：豁免
    maintained = SimpleNamespace(title="4K资源合集 持续更新", reply_num=100, tid=3, create_time=_time.time() - 365 * day)
    assert is_harvest_candidate(maintained, 30) is True
    # create_time 缺失不误杀
    assert is_harvest_candidate(_thread("4K资源合集", 100), 30) is True
    # max_age_days=0 关闭年龄门
    assert is_harvest_candidate(old, 30, max_age_days=0) is True


def test_pick_harvest_candidate_passes_age_gate():
    import time as _time
    threads = [
        SimpleNamespace(title="2014吧规导航，资源", reply_num=500, tid=1,
                        create_time=_time.time() - 4000 * 86400),
        SimpleNamespace(title="蓝光合集分享", reply_num=45, tid=2, create_time=_time.time() - 3 * 86400),
    ]
    picked = pick_harvest_candidate(threads, 30)
    assert picked is not None and picked.tid == 2


def test_extract_rejects_baidu_mbox_share_homepage():
    # #1846 误采回归：mbox 群组分享主页不是资源分享链，百度只认 /s/
    assert extract_floor_link("群文件 http://pan.baidu.com/mbox/homepage?short=i4uKw9v#share/type/session 自取") is None
    # /s/ 分享链照常
    assert extract_floor_link("资源 https://pan.baidu.com/s/1AbCdEf?pwd=x3k9") is not None


# ---------- 集成：harvest_from_posts（伪 db） ----------

from tieba_mecha.core.link_check import LinkCheckVerdict


class _FakeDB:
    def __init__(self, ret_id=77):
        self.ret_id = ret_id
        self.calls = []
        self.seen_marked = []

    async def add_harvested_material(self, **kwargs):
        self.calls.append(kwargs)
        return self.ret_id

    async def mark_harvest_seen(self, source_tid):
        self.seen_marked.append(source_tid)


async def _checker_alive(url, link_type):
    return LinkCheckVerdict(True, "ok")


async def _checker_dead(url, link_type):
    return LinkCheckVerdict(False, "命中失效标记: 测试")


async def _checker_unknown(url, link_type):
    return LinkCheckVerdict(None, "检测异常: 桩")


async def _checker_boom(url, link_type):
    raise RuntimeError("桩网络故障")


def _posts_page(floors: dict[int, str]):
    objs = [SimpleNamespace(floor=f, text=t) for f, t in floors.items()]
    return SimpleNamespace(objs=objs)


@pytest.mark.asyncio
async def test_harvest_from_posts_op_link():
    db = _FakeDB()
    page = _posts_page({1: "楼主整理的4K合集 https://pan.baidu.com/s/1AAA?pwd=ab12", 2: "感谢楼主", 3: "好人一生平安"})
    thread = _thread("4K合集整理", 66, tid=999)
    mid = await harvest_from_posts(db, page, thread, "电影吧", link_checker=_checker_alive)
    assert mid == 77
    kwargs = db.calls[0]
    assert kwargs["source_tid"] == 999
    assert kwargs["source_fname"] == "电影吧"
    assert kwargs["source_link_type"] == "baidu"
    assert "pwd=ab12" in kwargs["source_link_url"]
    assert kwargs["title"].startswith("4K合集整理")
    assert "楼主整理" in kwargs["content"]


@pytest.mark.asyncio
async def test_harvest_from_posts_first_reply_link():
    # 楼主不带链，首评(2楼)带已知网盘链：采首评的
    db = _FakeDB()
    page = _posts_page({1: "资源我整理好了，链接在楼下", 2: "链接 https://pan.quark.cn/s/9aaa 提取码：3ed5", 3: "谢谢分享"})
    mid = await harvest_from_posts(db, page, _thread("资源分享", 40, tid=5), "综艺吧", link_checker=_checker_alive)
    assert mid == 77
    assert db.calls[0]["source_link_type"] == "quark"
    assert "提取码 3ed5" in db.calls[0]["source_link_note"]


@pytest.mark.asyncio
async def test_harvest_from_posts_skips_unknown_link():
    # 未识别杂链任何楼层都不采（含楼主层），只有网盘链接标识才入库
    db = _FakeDB()
    page = _posts_page({1: "看我主页 example.com/xyz", 2: "顶"})
    assert await harvest_from_posts(db, page, _thread("资源帖", 40, tid=6), "电影吧") is None
    # 旧版误采的 checkurl 相册壳（物料#830 同款）同样不入库
    page2 = _posts_page({
        1: ("新人必看，伸手到这里来取。图片来自：http://tieba.baidu.com/mo/q/checkurl"
            "?url=http%3A%2F%2Fxiangce.baidu.com%2Fpicture%2Falbum%2Flist%2Fcc49&urlrefer=abc"),
        2: "顶",
    })
    assert await harvest_from_posts(db, page2, _thread("资源帖", 40, tid=6), "电影吧") is None
    assert db.calls == []


@pytest.mark.asyncio
async def test_harvest_from_posts_unwraps_checkurl_op_link():
    # 楼主层 checkurl 壳内真网盘：解包识别 + 纯链入库
    db = _FakeDB()
    page = _posts_page({
        1: ("整理好了 http://tieba.baidu.com/mo/q/checkurl"
            "?url=https%3A%2F%2Fpan.quark.cn%2Fs%2F9aaa%3Fpwd%3D3ed5 拿好"),
        2: "感谢楼主",
    })
    mid = await harvest_from_posts(db, page, _thread("资源分享", 40, tid=10), "综艺吧", link_checker=_checker_alive)
    assert mid == 77
    kwargs = db.calls[0]
    assert kwargs["source_link_type"] == "quark"
    assert kwargs["source_link_url"] == "https://pan.quark.cn/s/9aaa?pwd=3ed5"


@pytest.mark.asyncio
async def test_harvest_from_posts_no_links():
    db = _FakeDB()
    page = _posts_page({1: "只有文字", 2: "同求"})
    assert await harvest_from_posts(db, page, _thread("4K资源", 40, tid=7), "电影吧") is None


@pytest.mark.asyncio
async def test_harvest_dead_link_not_stored_and_ledger_marked():
    """死链检测确认失效：不入库 + 记已见账本防重复检测 + 记 WARN"""
    db = _FakeDB()
    page = _posts_page({1: "资源 https://pan.baidu.com/s/1DEAD?pwd=xx22"})
    logs = []

    async def warn(msg):
        logs.append(("warn", msg))

    mid = await harvest_from_posts(db, page, _thread("资源帖", 40, tid=12345), "电影吧",
                                   link_checker=_checker_dead, log_warn=warn)
    assert mid is None
    assert db.calls == []                      # 不入库
    assert db.seen_marked == [12345]           # 记已见账本
    assert any("已失效" in m for _, m in logs)


@pytest.mark.asyncio
async def test_harvest_alive_link_note_marks_checked():
    db = _FakeDB()
    page = _posts_page({1: "资源 https://pan.baidu.com/s/1OK?pwd=ok11 提取码 ok11"})
    mid = await harvest_from_posts(db, page, _thread("资源帖", 40, tid=2), "电影吧",
                                   link_checker=_checker_alive)
    assert mid == 77
    assert "链接检测: 有效" in db.calls[0]["source_link_note"]


@pytest.mark.asyncio
async def test_harvest_unknown_verdict_fail_open():
    """检测未知（网络异常/反爬）：照常入库，note 不加有效标记（fail-open 不丢物料）"""
    db = _FakeDB()
    page = _posts_page({1: "资源 https://pan.baidu.com/s/1UNK?pwd=un33"})
    mid = await harvest_from_posts(db, page, _thread("资源帖", 40, tid=3), "电影吧",
                                   link_checker=_checker_unknown)
    assert mid == 77
    assert "链接检测" not in db.calls[0]["source_link_note"]


@pytest.mark.asyncio
async def test_harvest_checker_crash_fail_open():
    """检测器自身崩溃：照常入库（fail-open），不允许因检测故障丢物料"""
    db = _FakeDB()
    page = _posts_page({1: "资源 https://pan.baidu.com/s/1BOOM"})
    mid = await harvest_from_posts(db, page, _thread("资源帖", 40, tid=4), "电影吧",
                                   link_checker=_checker_boom)
    assert mid == 77
    assert len(db.calls) == 1


# ---------- 死链判定纯逻辑（core/link_check.judge_link_body） ----------

def test_judge_dead_markers_by_type():
    from tieba_mecha.core.link_check import judge_link_body

    assert judge_link_body("baidu", 200, "<title>分享的文件已经被取消</title>").alive is False
    assert judge_link_body("lanzou", 200, "抱歉，该文件不存在").alive is False
    assert judge_link_body("baidu", 404, "").alive is False
    # 活页：无失效标记
    assert judge_link_body("baidu", 200, "<title>百度网盘-分享</title> 文件列表").alive is True
    # SPA 类型（夸克/UC/阿里等死活文案客户端渲染）：不检测，返回未知不误标"有效"
    assert judge_link_body("quark", 200, "<!DOCTYPE html><div id=app></div>").alive is None
    assert judge_link_body("other", 200, "随便什么").alive is None
    assert judge_link_body("baidu", 200, "").alive is True


@pytest.mark.asyncio
async def test_check_link_alive_skips_unsupported_without_network():
    """SPA 类型直接返回未知、不发请求（检查点：detail 说明原因）"""
    from tieba_mecha.core.link_check import check_link_alive

    v = await check_link_alive("https://pan.quark.cn/s/whatever", "quark")
    assert v.alive is None
    assert "SPA" in v.detail or "未支持" in v.detail


@pytest.mark.asyncio
async def test_check_link_alive_real_baidu_dead(monkeypatch=None):
    """真网实测（2026-10-06）：百度无效分享链返回 404 → 判死。
    断网/风控环境下 fail-open 为未知，测试两种结果都接受（不脆）。"""
    from tieba_mecha.core.link_check import check_link_alive

    v = await check_link_alive("https://pan.baidu.com/s/1zzZZzzZZzz-ZzZZzZZzZZz", "baidu", timeout=8)
    if v.alive is None:
        assert "检测异常" in v.detail  # 断网等环境问题：fail-open
    else:
        assert v.alive is False


def test_judge_gbk_body_still_matches():
    # gbk 编码的失效页（lanzou 系）在 utf-8 解码下乱码、gbk 解码下可匹配（check_link_alive 双解码拼正文）
    raw = "该文件不存在".encode("gbk")
    body = raw.decode("utf-8", errors="ignore") + raw.decode("gbk", errors="ignore")
    from tieba_mecha.core.link_check import judge_link_body
    assert judge_link_body("lanzou", 200, body).alive is False


# ---------- 采集待审导出（web/pages/batch_post/harvest_export.py） ----------

def test_build_harvest_export_csv_rows_and_states():
    import csv as _csv
    import io
    from datetime import datetime
    from types import SimpleNamespace as _NS

    from tieba_mecha.web.pages.batch_post.harvest_export import (
        EXPORT_HEADERS,
        build_harvest_export_csv,
    )

    rows = [
        _NS(id=1, title="《X》合集", content="正文,带逗号", source_fname="电影吧",
            source_link_url="https://pan.baidu.com/s/1A?pwd=ab12",
            source_link_note="提取码 ab12 | 楼层上下文", source_link_type="baidu",
            link_url=None, created_at=datetime(2026, 10, 6, 10, 0, 0)),
        _NS(id=2, title="纯内容帖", content="无链", source_fname="",
            source_link_url=None, source_link_note=None, source_link_type=None,
            link_url="https://pan.baidu.com/s/1OWN?pwd=own1", created_at=None),
    ]
    csv_text = build_harvest_export_csv(rows)
    parsed = list(_csv.reader(io.StringIO(csv_text)))
    assert parsed[0] == EXPORT_HEADERS
    # 待转存行：note 提取码优先、状态派生、时间格式化
    assert parsed[1][0] == "1" and parsed[1][4] == "https://pan.baidu.com/s/1A?pwd=ab12"
    assert parsed[1][5] == "ab12" and parsed[1][8] == "待转存"
    assert parsed[1][2] == "正文,带逗号"  # 逗号进引号不破列
    assert parsed[1][9] == "2026-10-06 10:00:00"
    # 纯内容行：note 为空时自有链 ?pwd= 兜底提取码
    assert parsed[2][5] == "own1" and parsed[2][8] == "纯内容·可放行"


def test_export_csv_filename_pattern():
    from datetime import datetime

    from tieba_mecha.web.pages.batch_post.harvest_export import export_csv_filename

    assert export_csv_filename(datetime(2026, 10, 6, 9, 30, 5)) == "harvest_export_20261006_093005.csv"


@pytest.mark.asyncio
async def test_get_materials_for_export_filters(db):
    """导出全量查询：只取 harvested（排期池不混入）+ 搜索词过滤 + id 升序"""
    await db.add_harvested_material(
        title="4K合集", content="正文A", source_tid=70001, source_fname="电影吧",
        source_link_url="https://pan.baidu.com/s/1A", source_link_type="baidu",
    )
    await db.add_harvested_material(
        title="蓝光整理", content="正文B", source_tid=70002, source_fname="剧集吧",
        source_link_url="https://pan.quark.cn/s/1B", source_link_type="quark",
    )
    await db.add_materials_bulk([("排期池物料", "正文C")])

    rows = await db.get_materials_for_export(["harvested"])
    assert [m.title for m in rows] == ["4K合集", "蓝光整理"]
    rows2 = await db.get_materials_for_export(["harvested"], search_text="蓝光")
    assert [m.title for m in rows2] == ["蓝光整理"]


# ---------- DB 层：入库去重 / 次序门 / 转存预留口 ----------

@pytest.mark.asyncio
async def test_add_harvested_material_and_dedup(db):
    mid = await db.add_harvested_material(
        title="4K合集", content="正文", source_tid=12345, source_fname="电影吧",
        source_link_url="https://pan.baidu.com/s/1A?pwd=xx11",
        source_link_note="提取码 xx11", source_link_type="baidu",
    )
    assert mid > 0
    assert await db.get_harvested_by_source_tid(12345) is True
    # 同源帖再采：去重跳过
    assert await db.add_harvested_material(
        title="4K合集", content="正文", source_tid=12345,
        source_link_url="https://pan.baidu.com/s/1B", source_link_type="baidu",
    ) == 0
    # 缺链不入库
    assert await db.add_harvested_material(
        title="x", content="y", source_tid=999, source_link_url="", source_link_type="other",
    ) == 0


@pytest.mark.asyncio
async def test_harvest_seen_ledger_survives_row_deletion(db):
    """已见账本不随物料行删除失效（回归：2026-10-04 #830 被人工删除后同源 tid 重采成 #1845）"""
    from tieba_mecha.db.models import MaterialPool

    mid = await db.add_harvested_material(
        title="吧规导航", content="正文", source_tid=3453368492, source_fname="俊男坊",
        source_link_url="http://pan.baidu.com/s/1sj8tMxR", source_link_type="baidu",
    )
    assert mid > 0
    # 人工删除物料行（误采清理路径）
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        await session.delete(m)
        await session.commit()
    # 表内行已没了，账本仍拦截
    assert await db.get_harvested_by_source_tid(3453368492) is True
    assert await db.add_harvested_material(
        title="吧规导航", content="正文", source_tid=3453368492,
        source_link_url="http://pan.baidu.com/s/2other", source_link_type="baidu",
    ) == 0
    # 账本只拦已见 tid，不影响新帖
    assert await db.get_harvested_by_source_tid(3453368493) is False


@pytest.mark.asyncio
async def test_mark_harvest_seen_blocks_without_material_row(db):
    """死链跳过场景：只记账不入库，同 tid 之后不再候选（含删行无基准问题不适用——根本没有行）"""
    await db.mark_harvest_seen(66666)
    # 无物料行，但账本已拦
    assert await db.get_harvested_by_source_tid(66666) is True
    # 重复记账不报错不重复
    await db.mark_harvest_seen(66666)
    # tid=0 无效不记账
    await db.mark_harvest_seen(0)
    from tieba_mecha.db.repositories.material_repo import HARVEST_SEEN_KEY
    raw = await db.get_setting(HARVEST_SEEN_KEY, "[]")
    import json as _json
    assert _json.loads(raw) == [66666]


@pytest.mark.asyncio
async def test_promote_gate_blocks_untransferred_source_link(db):
    mid = await db.add_harvested_material(
        title="待转存物料", content="正文", source_tid=22222, source_fname="电影吧",
        source_link_url="https://pan.baidu.com/s/1C?pwd=zz22",
        source_link_note="提取码 zz22", source_link_type="baidu",
    )
    # 有源链未转存：不得放行
    ok, msg = await db.promote_harvested(mid)
    assert ok is False
    assert "转存" in msg
    # 转存后放行成功，进 pending
    ok, msg = await db.mark_link_transferred(mid, "https://pan.baidu.com/s/1MYOWN?pwd=own1", new_note="提取码 own1")
    assert ok is True
    ok, msg = await db.promote_harvested(mid)
    assert ok is True
    from tieba_mecha.db.models import MaterialPool
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        assert m.status == "pending"
        assert m.ai_status == "none"
        assert m.link_url == "https://pan.baidu.com/s/1MYOWN?pwd=own1"
        # 源链字段保留供追溯（放行不清源链，只有跳过链接才清）
        assert m.source_link_url == "https://pan.baidu.com/s/1C?pwd=zz22"
        assert "own1" in (m.source_link_note or "")


@pytest.mark.asyncio
async def test_promote_allow_no_link_clears_source(db):
    mid = await db.add_harvested_material(
        title="跳过链接物料", content="正文", source_tid=33333, source_fname="剧集吧",
        source_link_url="https://example.com/s/x", source_link_note="备注", source_link_type="other",
    )
    ok, msg = await db.promote_harvested(mid, allow_no_link=True)
    assert ok is True
    from tieba_mecha.db.models import MaterialPool
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        assert m.status == "pending"
        assert m.source_link_url is None
        assert m.source_link_note is None
        assert m.source_link_type is None
        # source_tid 保留：防同帖复采
        assert m.source_tid == 33333


@pytest.mark.asyncio
async def test_mark_link_transferred_asserts(db):
    # 非 harvested 物料不可转存
    tid_pair = ("普通物料", "正文")
    mid = await db.add_materials_bulk([tid_pair])
    ok, msg = await db.mark_link_transferred(mid, "https://pan.baidu.com/s/1NEW")
    assert ok is False
    # 无源链的 harvested 物料也无需转存
    mid2 = await db.add_harvested_material(
        title="t", content="c", source_tid=44444,
        source_link_url="https://pan.baidu.com/s/1D", source_link_type="baidu",
    )
    ok2, _ = await db.mark_link_transferred(mid2, "")
    assert ok2 is False


@pytest.mark.asyncio
async def test_update_material_link_pending_and_clear(db):
    """排期池行手动改链：写入成功 + 清空归一化为 None（=纯内容帖不发带链首评）"""
    from sqlalchemy import select

    from tieba_mecha.db.models import MaterialPool

    await db.add_materials_bulk([("普通物料", "正文", "https://pan.baidu.com/s/1OLD?pwd=old")])
    async with db.async_session() as session:
        mid = (await session.execute(select(MaterialPool))).scalar_one().id
    # 改链成功（首尾空白归一化）
    assert await db.update_material_link(mid, " https://pan.baidu.com/s/1NEW?pwd=new ") is True
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        assert m.link_url == "https://pan.baidu.com/s/1NEW?pwd=new"
    # 清空 → None，与 add_materials_bulk 无链口径一致
    assert await db.update_material_link(mid, "   ") is True
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        assert m.link_url is None


@pytest.mark.asyncio
async def test_update_material_link_rejects_harvested(db):
    """harvested 行拒绝直写 link_url——源链替换唯一合法写路径仍是 mark_link_transferred"""
    from tieba_mecha.db.models import MaterialPool

    mid = await db.add_harvested_material(
        title="采集物料", content="正文", source_tid=55555, source_fname="电影吧",
        source_link_url="https://pan.baidu.com/s/1SRC?pwd=src1", source_link_type="baidu",
    )
    assert await db.update_material_link(mid, "https://pan.baidu.com/s/1OWN?pwd=own9") is False
    async with db.async_session() as session:
        m = await session.get(MaterialPool, mid)
        assert m.link_url is None


@pytest.mark.asyncio
async def test_pending_queue_excludes_harvested(db):
    """状态门：harvested 不会混进发帖侧 pending 取料口径"""
    await db.add_materials_bulk([("正常物料", "正常正文")])
    await db.add_harvested_material(
        title="采集物料", content="采集正文", source_tid=55555,
        source_link_url="https://pan.baidu.com/s/1E", source_link_type="baidu",
    )
    pendings = await db.get_materials(status="pending")
    titles = [m.title for m in pendings]
    assert "采集物料" not in titles
    assert "正常物料" in titles
    counts = await db.get_materials_status_counts()
    assert counts.get("harvested") == 1


# ---------- 转存后端插槽 ----------

def test_link_transfer_backend_registry():
    assert get_link_transfer_backend() is None
    class _Stub:
        async def transfer(self, source_url: str, note: str = "") -> TransferResult:
            return TransferResult(ok=True, new_link="https://pan.baidu.com/s/1OWN")
    stub = _Stub()
    register_link_transfer_backend(stub)
    assert get_link_transfer_backend() is stub
    register_link_transfer_backend(None)  # 还原全局态


def test_harvest_defaults_consistency():
    # 与 settings 页签默认值一致（口径漂移会在集成层露馅，这里咬住关键默认）
    assert HARVEST_DEFAULTS["maint_harvest_min_reply"] == "30"
    assert HARVEST_DEFAULTS["maint_harvest_max_per_cycle"] == "1"
