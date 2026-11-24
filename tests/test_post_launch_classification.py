"""发帖主流程的「发射/未发射」判定与提交结果判定回归（2026-11-24 全项目体检整改）

execute_task 此前只要本轮没成功就一律落到「物料失败 + 目标吧记失败」，不管有没有真的
向贴吧提交过。预检阶段的账号侧跳过（熔断冷却、凭据缺失、取不到 TBS、取不到吧信息）
一条请求都没发，却会被记成物料失败——取号信息被风控时，几十秒内就能把整个待发池标成
failed，并给每个目标吧记失败（靶场连续 3 次即被停用）。生产 11-09 的
「末次拦截: 未知」就是这条路径的指纹。

同一处的提交结果判定也有两处与「失败不记成功」相悖：
- err_code 缺失按 0 处理（手动发帖路径是缺失算失败），畸形响应会记成 success 且 posted_tid=0；
- 成功后的收尾落库若抛异常，会落进「执行链异常」——给刚成功的账号记一次失败，并换号把
  同一物料再发一遍。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from tieba_mecha.core.account import add_account  # noqa: E402
from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask  # noqa: E402

FNAME, FID = "launch_test_forum", 510


# ---------- 脚手架 ----------

async def _seed(db, *, n_accounts=1, materials):
    ids = []
    for i in range(n_accounts):
        acc = await add_account(db, f"launch_acc{i}", "b" * 192, "s" * 64, verify=False)
        await db.add_forum(fid=FID, fname=FNAME, account_id=acc.id)
        ids.append(acc.id)
    await db.add_materials_bulk(materials)
    return ids


def _task(account_ids, total, strategy="strict_round_robin") -> BatchPostTask:
    return BatchPostTask(id="t-launch", fname=FNAME, fnames=[FNAME], accounts=list(account_ids),
                         strategy=strategy, total=total)


def _client(*, tbs="tbs-ok", fid=FID, forum_err=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.account = SimpleNamespace(tbs=tbs)
    client.get_self_info = AsyncMock()
    if forum_err is not None:
        client.get_forum = AsyncMock(return_value=SimpleNamespace(err=forum_err, fid=0))
    else:
        client.get_forum = AsyncMock(return_value=MagicMock(fid=fid))
    return client


def _breaker():
    brk = MagicMock()
    brk.load = AsyncMock()
    brk.is_in_cooldown.return_value = False
    brk.record_failure = AsyncMock()
    brk.record_success = AsyncMock()
    brk.check_and_trigger = AsyncMock()
    return brk


@contextlib.contextmanager
def _engine(client, commit_result):
    """execute_task 的全部外部依赖替身：授权 / aiotieba 客户端 / 提交请求 / 熔断器 / 休眠"""
    with contextlib.ExitStack() as stack:
        def _p(target, **kwargs):
            return stack.enter_context(patch(target, **kwargs))

        auth = AsyncMock()
        auth.check_local_status = AsyncMock()
        auth.status = MagicMock()
        auth.status.__eq__ = lambda s, o: True   # PRO
        _p("tieba_mecha.core.batch_post.get_auth_manager", new_callable=AsyncMock, return_value=auth)
        create = AsyncMock(return_value=client)
        _p("tieba_mecha.core.batch_post.create_client", new=create)
        _p("tieba_mecha.core.batch_post.BionicDelay.sleep", new_callable=AsyncMock)
        af = MagicMock()
        af.can_post.return_value = True
        af.get_available_forum = AsyncMock(side_effect=lambda a, f: f[0])
        af.record_post = AsyncMock()
        _p("tieba_mecha.core.batch_post.AccountForumCooldown", return_value=af)
        failure, captcha = _breaker(), _breaker()
        _p("tieba_mecha.core.batch_post.FailureCircuitBreaker", return_value=failure)
        _p("tieba_mecha.core.batch_post.CaptchaCircuitBreaker", return_value=captcha)
        commit = AsyncMock(return_value=commit_result)
        _p("tieba_mecha.core.web_poster.prewarm_and_commit_thread", new=commit)
        yield SimpleNamespace(commit=commit, create=create, failure=failure, captcha=captcha)


async def _run(db, task, client, commit_result):
    with _engine(client, commit_result) as env:
        updates = [u async for u in BatchPostManager(db).execute_task(task)]
    return updates, env


async def _statuses(db) -> dict:
    return {m.title: m for m in await db.get_materials(limit=50)}


async def _logs(db) -> list:
    return await db.get_batch_post_logs(limit=50)


async def _target_pool(db):
    async with db.async_session() as session:
        from sqlalchemy import select

        from tieba_mecha.db.models import TargetPool

        return (await session.execute(select(TargetPool).where(TargetPool.fname == FNAME))).scalar_one_or_none()


FOUR = [(f"标题{i}", f"内容{i}") for i in range(4)]


# ---------- 未发射：不记物料失败、不记靶场失败 ----------

async def test_tbs_failure_keeps_materials_pending_and_forum_untouched(db):
    """取不到 TBS（取号信息被风控/网络失败）：4 条物料一条请求都没发，全部保持待发"""
    ids = await _seed(db, materials=FOUR)

    updates, env = await _run(db, _task(ids, 4), _client(tbs=None), {"err_code": 0, "data": {"tid": 1}})

    env.commit.assert_not_awaited()                       # 根本没进入提交
    statuses = await _statuses(db)
    assert {m.status for m in statuses.values()} == {"pending"}
    skipped = [u for u in updates if u["status"] == "skipped"]
    assert len(skipped) == 3 and all("TBS" in u["msg"] for u in skipped)
    # 连续 3 条都未能进入发射 → 提前收束本轮，而不是把整个待发池逐条扫完
    assert updates[-1]["status"] == "error" and "未能进入发射" in updates[-1]["msg"]
    logs = await _logs(db)
    assert {log.status for log in logs} == {"skip"}       # 没有任何 error 流水
    pool = await _target_pool(db)
    assert pool is None or (pool.fail_count or 0) == 0     # 靶场没有被记失败
    env.failure.record_failure.assert_not_awaited()       # 账号也没被记失败


async def test_forum_info_failure_prevents_launch(db):
    """get_forum 失败（aiotieba 不抛异常、内嵌 .err）：不能用 fid=0 去撞一次注定失败的发射"""
    ids = await _seed(db, materials=FOUR)

    client = _client(forum_err=RuntimeError("connection reset"))
    updates, env = await _run(db, _task(ids, 4), client, {"err_code": 0, "data": {"tid": 1}})

    env.commit.assert_not_awaited()
    assert {m.status for m in (await _statuses(db)).values()} == {"pending"}
    skipped = [u for u in updates if u["status"] == "skipped"]
    assert len(skipped) == 3 and all("获取吧信息失败" in u["msg"] for u in skipped)


async def test_unlaunched_round_ends_early_instead_of_scanning_whole_pool(db):
    """连续 3 条未能进入发射即结束本轮：后面的物料不被逐条记录，仍保持待发"""
    materials = [(f"多物料{i}", f"多内容{i}") for i in range(12)]
    ids = await _seed(db, materials=materials)

    updates, env = await _run(db, _task(ids, 12), _client(tbs=None), {"err_code": 0, "data": {"tid": 1}})

    assert [u["status"] for u in updates] == ["skipped", "skipped", "skipped", "error"]
    assert len(await _logs(db)) == 3
    assert all(m.status == "pending" for m in (await _statuses(db)).values())


async def test_account_side_skip_recovers_next_round(db):
    """账号恢复后同一批物料照常发出：顺延不是终态"""
    ids = await _seed(db, materials=FOUR)
    task = _task(ids, 4)

    await _run(db, task, _client(tbs=None), {"err_code": 0, "data": {"tid": 1}})
    assert all(m.status == "pending" for m in (await _statuses(db)).values())

    updates, env = await _run(db, task, _client(), {"err_code": 0, "data": {"tid": 4242}})
    assert [u["status"] for u in updates] == ["success"] * 4
    assert env.commit.await_count == 4
    assert {m.status for m in (await _statuses(db)).values()} == {"success"}


# ---------- 已发射：原有判定不变 ----------

async def test_real_rejection_after_launch_is_still_a_material_failure(db):
    """对照组：真的提交并被拒，仍按失败落库并给目标吧记一次失败"""
    ids = await _seed(db, materials=FOUR)

    updates, env = await _run(db, _task(ids, 4), _client(),
                              {"err_code": 1990006, "error": "系统繁忙，请稍后再试"})

    assert env.commit.await_count == 4
    assert {m.status for m in (await _statuses(db)).values()} == {"failed"}
    assert (await _target_pool(db)).fail_count == 4
    assert sum(u["status"] == "error" for u in updates) == 4
    env.failure.record_failure.assert_awaited()


async def test_mixed_accounts_launch_counts_as_real_attempt(db):
    """一个账号预检跳过、另一个账号真的发射被拒：以「发射过」为准，记失败"""
    ids = await _seed(db, n_accounts=2, materials=[("混合标题", "混合内容")])
    client = _client()
    task = _task(ids, 1)
    calls = {"n": 0}

    async def _self_info():
        calls["n"] += 1
        client.account.tbs = None if calls["n"] == 1 else "tbs-ok"

    client.account = SimpleNamespace(tbs=None)
    client.get_self_info = AsyncMock(side_effect=_self_info)
    updates, env = await _run(db, task, client, {"err_code": 1990006, "error": "系统繁忙"})

    assert env.commit.await_count == 1
    assert (await _statuses(db))["混合标题"].status == "failed"
    assert (await _target_pool(db)).fail_count == 1


# ---------- 提交结果判定 ----------

@pytest.mark.parametrize("resp", [
    {"data": {"tid": 777}},                       # 缺 err_code：手动发帖路径视为失败
    {"err_code": 0, "data": None},               # 声称成功却没有 tid
    {"err_code": 0, "data": {}},
    {"err_code": 0, "data": {"tid": 0}},
    {"err_code": 0, "data": {"tid": "not-a-number"}},
], ids=["缺err_code", "data为None", "无tid", "tid为0", "tid非整数"])
async def test_malformed_success_response_is_never_recorded_as_success(db, resp):
    ids = await _seed(db, n_accounts=2, materials=[("畸形响应标题", "畸形响应内容")])

    updates, env = await _run(db, _task(ids, 1), _client(), resp)

    mat = (await _statuses(db))["畸形响应标题"]
    assert mat.status != "success" and mat.posted_tid in (None, 0)
    assert env.commit.await_count == 1             # 不换号硬撞：服务端可能已经建帖


async def test_missing_tid_is_reported_for_manual_review(db):
    ids = await _seed(db, n_accounts=2, materials=[("无tid标题", "无tid内容")])

    await _run(db, _task(ids, 1), _client(), {"err_code": 0, "data": None})

    mat = (await _statuses(db))["无tid标题"]
    assert mat.status == "failed" and "无 tid" in (mat.last_error or "")
    log = (await _logs(db))[0]
    assert log.status == "error" and "无 tid" in log.message


async def test_success_with_bookkeeping_error_does_not_repost_or_penalize(db):
    """发帖成功后收尾落库异常：不换号重发、不给成功的账号记失败"""
    ids = await _seed(db, n_accounts=2, materials=[("收尾异常标题", "收尾异常内容")])

    with patch.object(db, "update_target_pool_status", AsyncMock(side_effect=RuntimeError("database is locked"))):
        updates, env = await _run(db, _task(ids, 1), _client(), {"err_code": 0, "data": {"tid": 9001}})

    env.commit.assert_awaited_once()                # 修复前：第二个账号会把同一物料再发一遍
    env.failure.record_failure.assert_not_awaited()
    env.failure.record_success.assert_awaited_once_with(ids[0])
    assert [u["status"] for u in updates] == ["success"]
    assert (await _statuses(db))["收尾异常标题"].status == "success"   # 物料状态这一笔关键写入照常落库


async def test_material_status_write_is_retried_once(db):
    ids = await _seed(db, materials=[("重试写入标题", "重试写入内容")])
    real = db.update_material_status
    attempts = {"n": 0}

    async def _flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("database is locked")
        return await real(*args, **kwargs)

    with patch.object(db, "update_material_status", _flaky):
        await _run(db, _task(ids, 1), _client(), {"err_code": 0, "data": {"tid": 9002}})

    assert attempts["n"] == 2
    assert (await _statuses(db))["重试写入标题"].status == "success"


async def test_similar_content_fails_with_truthful_reason(db):
    """内容重复度拦截仍按失败落库（物料自身的问题不能卡住队列），但流水如实写明原因"""
    body = "这是一段用于检测重复度的正文内容，请勿重复发送"
    ids = await _seed(db, materials=[("首发标题", body), ("疑似重复标题", body + "。")])

    updates, env = await _run(db, _task(ids, 2), _client(), {"err_code": 0, "data": {"tid": 9003}})

    assert [u["status"] for u in updates] == ["success", "error"]
    assert env.commit.await_count == 1
    logs = sorted(await _logs(db), key=lambda log: log.id)
    assert logs[0].status == "success"
    assert logs[1].status == "error" and "历史重复度过高" in logs[1].message
    failed = [m for m in (await _statuses(db)).values() if m.status == "failed"]
    assert len(failed) == 1 and "历史重复度过高" in failed[0].last_error


# ---------- 提交阶段的异常：结果未知时不得换号重发 ----------

@pytest.mark.parametrize("exc", [
    asyncio.TimeoutError("timed out"),
    httpx.ReadTimeout("read timeout"),
    ValueError("Expecting value: line 1 column 1 (char 0)"),     # res.json() 解析失败
], ids=["超时", "读超时", "非JSON响应"])
async def test_exception_after_commit_does_not_retry_with_another_account(db, exc):
    """提交请求已发出但没拿到可解析的响应：服务端可能已经建帖，换号重发 = 同一物料重复发帖"""
    ids = await _seed(db, n_accounts=3, materials=[("提交异常标题", "提交异常内容")])

    with _engine(_client(), None) as env:
        env.commit.side_effect = exc
        updates = [u async for u in BatchPostManager(db).execute_task(_task(ids, 1))]

    assert env.commit.await_count == 1                        # 没有换第二个账号再发一遍
    assert updates[-1]["status"] == "error"
    mat = (await _statuses(db))["提交异常标题"]
    assert mat.status == "failed" and "核对是否已发出" in mat.last_error
    env.failure.record_failure.assert_awaited()               # 账号侧仍记一次失败（与既有口径一致）


async def test_exception_before_commit_still_fails_over_to_next_account(db):
    """对照：提交之前出错（如建客户端失败）属于确定没发出去，保留换号重试"""
    ids = await _seed(db, n_accounts=3, materials=[("提交前异常标题", "提交前异常内容")])
    client = _client()

    with _engine(client, {"err_code": 0, "data": {"tid": 9100}}) as env:
        env.create.side_effect = [RuntimeError("proxy connect failed"), client, client]
        updates = [u async for u in BatchPostManager(db).execute_task(_task(ids, 1))]

    assert env.commit.await_count == 1
    assert updates[0]["status"] == "success"
    assert (await _statuses(db))["提交前异常标题"].status == "success"
