"""aiotieba 返回值语义回归（2026-11-24 全项目体检整改）

aiotieba 的 API 方法全部被 @handle_exception 包装：失败不抛异常，而是返回带 .err 的空对象
（BoolResponse.__bool__ 即「无 .err」；Posts 的真值取决于楼层列表是否非空）。项目里两处
没按这个真实契约判定：

- BatchPostManager.reply_to_thread 不看 add_post 的返回值：验证码、吧内禁言、帖子已删
  都被记成「已发出」，3 分钟后校验不到楼层被误判为被吞并给账号记击；吧务封禁熔断不可达。
- check_post_survival 把 get_posts 的失败（网络抖动、超时、验证码、限流）当成「正常响应
  无 thread」判阵亡，再经 auto_sync_post_target 关掉整个吧的发帖目标标记。

旧的同类测试用「抛异常」的 mock 建模 API 失败，与真实契约不符，所以一直没发现问题。
本文件一律用 aiotieba 真实的 BoolResponse / Posts / TiebaServerError 按真实契约构造响应。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiotieba.api.get_posts import Posts
from aiotieba.api.get_posts._classdef import Thread_p
from aiotieba.exception import BoolResponse, TiebaServerError

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from tieba_mecha.core import account as account_mod  # noqa: E402
from tieba_mecha.core import client_factory  # noqa: E402
from tieba_mecha.core import obfuscator as obf_mod  # noqa: E402
from tieba_mecha.core.batch_post import BatchPostManager  # noqa: E402
from tieba_mecha.core.post import check_post_survival  # noqa: E402


def _failed_bool(code: int, msg: str) -> BoolResponse:
    resp = BoolResponse()
    resp.err = TiebaServerError(code, msg)
    return resp


def _failed_posts(err: BaseException) -> Posts:
    res = Posts()   # 与 aiotieba handle_exception 的空工厂产物同构：无楼层、thread 全零
    res.err = err
    return res


_UNSET = object()


@contextlib.contextmanager
def _aiotieba_client(get_posts=_UNSET, add_post=_UNSET):
    """替身 aiotieba.Client：get_posts / add_post 按真实契约「返回」结果（不抛异常）"""
    with patch("aiotieba.Client") as client_cls:
        client = client_cls.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        if get_posts is not _UNSET:
            client.get_posts = AsyncMock(return_value=get_posts)
        if add_post is not _UNSET:
            client.add_post = AsyncMock(return_value=add_post)
        yield client


async def _seed_posted(db, *, fid, fname, tid, name):
    """一个发帖号 + 一个标记为发帖目标的吧 + 一条已发布物料"""
    acc = await db.add_account(name=name, bduss="X" * 80, user_name=f"u_{name}")
    forum = await db.add_forum(fid=fid, fname=fname, account_id=acc.id)
    mid = await db.register_posted_material("标题", "正文", posted_fname=fname,
                                            posted_tid=tid, posted_account_id=acc.id)
    async with db.async_session() as session:
        from tieba_mecha.db.models import Forum

        row = await session.get(Forum, forum.id)
        row.is_post_target = True
        await session.commit()
    return acc, mid


# ---------- 存活检测 ----------

@pytest.mark.parametrize("err, expected", [
    (TiebaServerError(350008, "该贴已被删除，请浏览其他贴子"), ("dead", "deleted_unknown")),  # 真网实测的删帖响应
    (TiebaServerError(273, "内容违规已被系统删除"), ("dead", "deleted_by_system")),
    (TiebaServerError(269, "帖子不存在"), ("dead", "auto_removed")),
    (TiebaServerError(999, "帖子不存在"), ("dead", "auto_removed")),
    (TiebaServerError(40, "请输入验证码"), ("unknown", "captcha_required")),   # 检测者被拦，不是帖子死亡
    (TiebaServerError(40, ""), ("unknown", "error")),
    (TiebaServerError(999, "服务繁忙"), ("unknown", "error")),
    (asyncio.TimeoutError(), ("unknown", "error")),                              # 网络抖动/超时
    (ConnectionError("connection reset"), ("unknown", "error")),
    (RuntimeError("Thread has been deleted"), ("unknown", "error")),             # 非 API 错误不从消息猜原因
])
async def test_survival_translates_embedded_api_error(err, expected):
    with _aiotieba_client(get_posts=_failed_posts(err)):
        assert await check_post_survival(12345) == expected


async def test_survival_alive_when_thread_present():
    thread = Thread_p()
    thread.tid = 12345
    with _aiotieba_client(get_posts=Posts(objs=[object()], thread=thread)):
        assert await check_post_survival(12345) == ("alive", "")


async def test_survival_alive_for_zero_reply_thread():
    """活帖但楼层列表为空：Posts 为假值，仍须凭 thread.tid 判存活"""
    thread = Thread_p()
    thread.tid = 12345
    with _aiotieba_client(get_posts=Posts(thread=thread)):
        assert bool(Posts(thread=thread)) is False    # 确认前提：容器真值为假
        assert await check_post_survival(12345) == ("alive", "")


@pytest.mark.parametrize("res", [None, Posts()], ids=["None", "空响应无错误"])
async def test_survival_keeps_legacy_dead_verdict_for_empty_response(res):
    """无 thread 证据且无错误的空响应：沿用「已不可公开访问」的既有判定"""
    with _aiotieba_client(get_posts=res):
        status, reason = await check_post_survival(12345)
        assert status == "dead" and reason == "deleted_unknown"


async def test_survival_mock_style_response_still_alive():
    """既有测试用的 MagicMock 响应（.err 是 MagicMock 而非异常）不受影响"""
    with _aiotieba_client(get_posts=MagicMock()):
        assert await check_post_survival(12345) == ("alive", "")


async def test_survival_unknown_does_not_reach_forum_target_sync(db):
    """端到端：检测请求失败不得把物料记成阵亡，也就不会连带关掉吧的发帖目标"""
    from tieba_mecha.core.survival_feedback import run_survival_check

    acc, mid = await _seed_posted(db, fid=31, fname="存活测试吧", tid=5555, name="poster_surv")

    with _aiotieba_client(get_posts=_failed_posts(asyncio.TimeoutError())):
        result = await run_survival_check(db, materials=[await _material_row(db, mid)], limit=None)

    assert (result["checked"], result["unknown"], result["dead"]) == (1, 1, 0)
    row = await _material_row(db, mid)
    assert row.survival_status != "dead"
    assert (await db.get_forums(acc.id))[0].is_post_target is True


async def test_survival_real_deletion_still_marks_dead(db):
    from tieba_mecha.core.survival_feedback import run_survival_check

    acc, mid = await _seed_posted(db, fid=32, fname="存活测试吧2", tid=5556, name="poster_surv2")

    with _aiotieba_client(get_posts=_failed_posts(TiebaServerError(350008, "该贴已被删除，请浏览其他贴子"))):
        result = await run_survival_check(db, materials=[await _material_row(db, mid)], limit=None)

    assert (result["dead"], result["unknown"]) == (1, 0)
    assert (await _material_row(db, mid)).survival_status == "dead"


async def _material_row(db, mid):
    async with db.async_session() as session:
        from tieba_mecha.db.models import MaterialPool

        return await session.get(MaterialPool, mid)


# ---------- 回帖 ----------

@pytest.fixture
def reply_env(monkeypatch):
    """reply_to_thread 的外部依赖替身：客户端 / 凭据 / 混淆器"""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.account = SimpleNamespace(tbs="tbs-ok")
    client.get_self_info = AsyncMock()
    client.add_post = AsyncMock(return_value=BoolResponse())
    client.unfollow_forum = AsyncMock(return_value=BoolResponse())
    monkeypatch.setattr(client_factory, "create_client", AsyncMock(return_value=client))
    monkeypatch.setattr(account_mod, "get_account_credentials", AsyncMock(
        return_value=(None, "B" * 192, "S" * 64, None, "", "")))
    monkeypatch.setattr(obf_mod.Obfuscator, "from_db", AsyncMock(
        return_value=SimpleNamespace(obfuscate_all=lambda content: content)))
    return client


@pytest.mark.parametrize("code, msg", [
    (40, "请输入验证码"),
    (3250006, "该吧已被封禁"),          # 吧内禁言
    (350008, "该贴已被删除，请浏览其他贴子"),
    (1990006, "请点击取消按钮"),
])
async def test_reply_api_failure_is_reported_as_failure(db, reply_env, code, msg):
    """add_post 返回带 .err 的 BoolResponse：必须判失败，而不是记成「已发出」"""
    reply_env.add_post = AsyncMock(return_value=_failed_bool(code, msg))

    ok, reason = await BatchPostManager(db).reply_to_thread(1, "测试吧", 777, "顶一下")

    assert ok is False
    assert str(code) in reason and msg in reason
    reply_env.unfollow_forum.assert_not_awaited()      # 普通失败不触发封禁熔断


async def test_reply_success_when_no_error(db, reply_env):
    ok, reason = await BatchPostManager(db).reply_to_thread(1, "测试吧", 777, "顶一下")
    assert (ok, reason) == (True, "")
    reply_env.add_post.assert_awaited_once_with("测试吧", 777, "顶一下")


async def test_reply_network_exception_is_still_a_failure(db, reply_env):
    reply_env.add_post = AsyncMock(side_effect=ConnectionError("connection reset"))
    ok, reason = await BatchPostManager(db).reply_to_thread(1, "测试吧", 777, "顶一下")
    assert ok is False and "connection reset" in reason


async def test_reply_forum_ban_triggers_circuit_breaker(db, reply_env):
    """吧务封禁（3250004）：此前熔断分支不可达；现在标记载吧、停发、取关并关闭该帖自顶"""
    reply_env.add_post = AsyncMock(return_value=_failed_bool(3250004, "该吧已被吧务封禁"))
    acc, mid = await _seed_posted(db, fid=41, fname="回帖测试吧", tid=777, name="poster_reply")
    async with db.async_session() as session:
        from tieba_mecha.db.models import MaterialPool

        m = await session.get(MaterialPool, mid)
        m.is_auto_bump = True
        await session.commit()

    ok, reason = await BatchPostManager(db).reply_to_thread(acc.id, "回帖测试吧", 777, "顶一下")

    assert ok is False and reason.startswith("吧务封禁")
    forum = (await db.get_forums(acc.id))[0]
    assert forum.is_banned is True
    assert (await _material_row(db, mid)).is_auto_bump is False
    reply_env.unfollow_forum.assert_awaited_once_with("回帖测试吧")


# ---------- 签到：风控退避分支须对「不抛异常的失败返回值」生效 ----------

async def _seed_sign_account(db, name, forums):
    acc = await account_mod.add_account(db=db, name=name, bduss="a" * 192, stoken="b" * 64)
    for fid, fname in enumerate(forums, start=600):
        await db.add_forum(fid=fid, fname=fname, account_id=acc.id)
    return acc


def _sign_client(sign_forum):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_threads = AsyncMock(return_value=None)
    client.sign_forum = sign_forum
    return client


async def _sign_all(db, client):
    from tieba_mecha.core.sign import sign_all_accounts

    results = []
    with patch("tieba_mecha.core.sign.create_client", AsyncMock(return_value=client)), \
         patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        async for r in sign_all_accounts(db, 0, 0, 0, 0):
            results.append(r)
    return results, sleep


async def _run_sign_flow(db, flow, acc_id, client):
    with patch("tieba_mecha.core.sign.create_client", AsyncMock(return_value=client)), \
         patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        if flow == "matrix":
            from tieba_mecha.core.sign import sign_all_accounts

            results = [r async for r in sign_all_accounts(db, 0, 0, 0, 0)]
        elif flow == "single":
            from tieba_mecha.core.sign import sign_account_forums

            results = [r async for r in sign_account_forums(db, acc_id, 0, 0)]
        else:
            from tieba_mecha.core.sign import sign_all_forums

            # 旧版单账号全扫产出的是 SignResult 数据类，归一化成字典便于统一断言
            results = [{"success": r.success, "message": r.message, "fname": r.fname}
                       for r in [x async for x in sign_all_forums(db, 0, 0)]]
    return results, sleep


def _sixty_second_sleeps(sleep) -> int:
    return sum(1 for call in sleep.await_args_list if call.args and call.args[0] == 60)


SIX_FORUMS = [f"风控吧{i}" for i in range(6)]


@pytest.mark.parametrize("flow", ["matrix", "single", "legacy"])
@pytest.mark.parametrize("code, msg", [
    (40, "请输入验证码"),
    (6, "操作太频繁, 请稍后重试"),
    (100006, "需要安全验证"),
])
async def test_sign_persistent_risk_control_backs_off_then_aborts_account(db, flow, code, msg):
    """每个吧都命中风控：每次退避 60 秒，连续 3 次判定账号被限流，中止本账号本轮。

    此前 sign_forum 不抛异常，风控被当成普通失败——隔几秒继续签下一个吧，一路撞到底。
    （吧的执行顺序按天洗牌，断言只看数量不看先后）
    """
    acc = await _seed_sign_account(db, "sign_risky", SIX_FORUMS)
    client = _sign_client(AsyncMock(return_value=_failed_bool(code, msg)))

    results, sleep = await _run_sign_flow(db, flow, acc.id, client)

    assert client.sign_forum.await_count == 3           # 第 3 次命中后中止，后三个吧没有再请求
    assert len(results) == 3 and all(r["success"] is False for r in results)
    assert all("风控" in r["message"] for r in results)
    assert _sixty_second_sleeps(sleep) == 3            # 每次命中退避 60 秒
    forums = await db.get_forums(acc.id)
    statuses = sorted(f.last_sign_status for f in forums)
    assert statuses == ["failure"] * 3 + ["pending"] * 3   # 未触达的吧不被记失败
    risk_logs = [log for f in forums for log in await db.get_sign_logs(forum_id=f.id) if log.message]
    assert sum("风控" in log.message for log in risk_logs) == 3


@pytest.mark.parametrize("flow", ["matrix", "single", "legacy"])
async def test_sign_single_forum_captcha_wall_does_not_abort_account(db, flow):
    """单吧验证码墙（err=40 多为吧级）：退避一次后继续，后面的吧照常签。

    为一个吧中止整个账号，会让其余几十个吧当天都签不上（连续天数中断）。
    """
    acc = await _seed_sign_account(db, "sign_wall", SIX_FORUMS)
    # 首个请求命中验证码墙，其余全部成功（顺序按天洗牌，与具体是哪个吧无关）
    responses = [_failed_bool(40, "请输入验证码")] + [BoolResponse()] * 10
    client = _sign_client(AsyncMock(side_effect=responses))

    results, sleep = await _run_sign_flow(db, flow, acc.id, client)

    assert client.sign_forum.await_count == 6           # 没有中止，全部尝试
    assert _sixty_second_sleeps(sleep) == 1            # 只为那一次命中退避
    assert sum(r["success"] for r in results) == 5
    forums = await db.get_forums(acc.id)
    assert sum(f.is_sign_today for f in forums) == 5


@pytest.mark.parametrize("flow", ["matrix", "single", "legacy"])
async def test_sign_risk_streak_resets_after_a_normal_result(db, flow):
    """风控命中不连续则永不中止：墙、正常、墙、正常…… 交替时 6 个吧全部尝试"""
    acc = await _seed_sign_account(db, "sign_alt", SIX_FORUMS)
    responses = []
    for i in range(12):
        responses.append(_failed_bool(40, "请输入验证码") if i % 2 == 0 else BoolResponse())
    client = _sign_client(AsyncMock(side_effect=responses))

    results, sleep = await _run_sign_flow(db, flow, acc.id, client)

    assert client.sign_forum.await_count == 6
    assert _sixty_second_sleeps(sleep) == 3


async def test_sign_ordinary_failure_continues_to_next_forum(db):
    """普通失败（非风控）：照常记失败并继续签下一个吧，不退避"""
    await _seed_sign_account(db, "sign_plain", ["普通吧甲", "普通吧乙"])
    client = _sign_client(AsyncMock(return_value=_failed_bool(1990006, "请点击取消按钮")))

    results, sleep = await _sign_all(db, client)

    assert client.sign_forum.await_count == 2
    assert len(results) == 2 and all(r["success"] is False for r in results)
    assert not any(call.args and call.args[0] == 60 for call in sleep.await_args_list)


async def test_sign_forum_ban_is_not_mistaken_for_risk_control(db):
    """3250004 的文案即使含风控关键词，也走「吧务封禁熔断」而不是中止整个账号"""
    acc = await _seed_sign_account(db, "sign_ban", ["封禁吧", "正常吧"])
    responses = [_failed_bool(3250004, "您的账号异常，已被本吧封禁"), BoolResponse()]
    client = _sign_client(AsyncMock(side_effect=responses))

    results, sleep = await _sign_all(db, client)

    assert client.sign_forum.await_count == 2                # 第二个吧照签
    forums = {f.fname: f for f in await db.get_forums(acc.id)}
    assert forums["封禁吧"].is_banned is True
    assert forums["正常吧"].is_sign_today is True
    assert not any(call.args and call.args[0] == 60 for call in sleep.await_args_list)
