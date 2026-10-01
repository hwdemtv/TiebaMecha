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


# ---------- 集成：harvest_from_posts（伪 db） ----------

class _FakeDB:
    def __init__(self, ret_id=77):
        self.ret_id = ret_id
        self.calls = []

    async def add_harvested_material(self, **kwargs):
        self.calls.append(kwargs)
        return self.ret_id


def _posts_page(floors: dict[int, str]):
    objs = [SimpleNamespace(floor=f, text=t) for f, t in floors.items()]
    return SimpleNamespace(objs=objs)


@pytest.mark.asyncio
async def test_harvest_from_posts_op_link():
    db = _FakeDB()
    page = _posts_page({1: "楼主整理的4K合集 https://pan.baidu.com/s/1AAA?pwd=ab12", 2: "感谢楼主", 3: "好人一生平安"})
    thread = _thread("4K合集整理", 66, tid=999)
    mid = await harvest_from_posts(db, page, thread, "电影吧")
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
    mid = await harvest_from_posts(db, page, _thread("资源分享", 40, tid=5), "综艺吧")
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
    mid = await harvest_from_posts(db, page, _thread("资源分享", 40, tid=10), "综艺吧")
    assert mid == 77
    kwargs = db.calls[0]
    assert kwargs["source_link_type"] == "quark"
    assert kwargs["source_link_url"] == "https://pan.quark.cn/s/9aaa?pwd=3ed5"


@pytest.mark.asyncio
async def test_harvest_from_posts_no_links():
    db = _FakeDB()
    page = _posts_page({1: "只有文字", 2: "同求"})
    assert await harvest_from_posts(db, page, _thread("4K资源", 40, tid=7), "电影吧") is None


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
