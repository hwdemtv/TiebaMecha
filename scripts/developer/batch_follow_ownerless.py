"""无吧主吧分批关注执行器（灰度节奏：每号每天 2-3 个，自动入靶场池+记账）。

用法（服务器上，仓库根目录执行）：
    PYTHONPATH=src .venv/bin/python scripts/developer/batch_follow_ownerless.py

设计（2026-09-25 用户圈定 30 吧，A 类 24 + A- 人气 6）：
- 仅用账号 6/9/10（hwdemtv1/hwdemtv5/hwdemtv1341）轮转，187 排除。
- 执行时逐个复核 get_forum：不存在/已有吧主/已关注 分流，状态可能已变。
- 成功才写入 maint_autofollow_state 账本（与 BioWarming 自动关注共享账本与上限），
  并 upsert 靶场池【无吧主】分组、落库 forum 表（关注即参与签到，无需手动全域同步）。
- 拦截处理：单号任何一次 follow 失败即中止该号当日动作；两个号失败则整轮熔断。
- 每日额度存 data/autofollow_batch_state.json（跨天自动重置）；flock 防并发。
"""
import asyncio
import fcntl
import json
import random
import sys
from datetime import date, datetime
from pathlib import Path

from tieba_mecha.core.account import get_account_credentials
from tieba_mecha.core.client_factory import create_client
from tieba_mecha.db.crud import Database

# 用户圈定的 30 个吧（2026-09-25 验真：A 类 24 + A- 人气 6）
FOLLOW_QUEUE = [
    # A 类：真·无吧主 ≥500 人
    "卡片召唤师", "鼬卡", "卡夫卡", "硬科幻", "南柱赫", "风流逐声工作室",
    "俊男坊", "丁墨", "简谱", "广播剧综合", "daragon美文", "刘有生",
    "护师资格", "癫痫病", "甘肃中医学院", "laychen", "心理疏导", "峰怡",
    "简暗", "憨批", "泰山现代中学", "御龙在天新区", "快穿推文", "甄爱家族",
    # A- 类：无吧主但 <500 人，挑人气较高的 6 个
    "鹿鼎网游", "小灿粉丝", "迷糊家族", "k11", "栎迷家族", "南唐故郡",
]

# 参与账号：均裸连无代理依赖；187（仍绑已烧 proxy 13）明确排除
ACCOUNT_IDS = [6, 9, 10]
DAILY_MIN, DAILY_MAX = 2, 3          # 每号每天关注个数（含随机）
FAIL_GIVE_UP = 3                      # 同一吧累计失败 N 次后放弃
LEDGER_KEY = "maint_autofollow_state"
GROUP = "无吧主"
STATE_PATH = Path("data/autofollow_batch_state.json")
LOCK_PATH = Path("data/autofollow_batch.lock")


def log(msg: str):
    print(f"<{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}> {msg}", flush=True)


def load_state() -> dict:
    today = date.today().isoformat()
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    if state.get("date") != today:
        state = {"date": today, "today": {}, "fail": state.get("fail", {}), "dead": state.get("dead", {})}
    state.setdefault("today", {})
    state.setdefault("fail", {})
    state.setdefault("dead", {})
    return state


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


async def load_ledger(db: Database) -> dict[str, list[str]]:
    try:
        raw = await db.get_setting(LEDGER_KEY, "{}")
        return json.loads(raw or "{}")
    except Exception as e:
        log(f"[warn] 账本读取失败，按空账本继续: {type(e).__name__}: {e}")
        return {}


async def save_ledger(db: Database, ledger: dict[str, list[str]]):
    await db.set_setting(LEDGER_KEY, json.dumps(ledger, ensure_ascii=False))


def followed_all(ledger: dict) -> set[str]:
    return {f for lst in ledger.values() for f in lst}


async def run() -> int:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("已有实例在运行（锁被占），本轮退出")
            return 0

        state = load_state()
        db = Database("data/tieba_mecha.db")
        ledger = await load_ledger(db)
        done = followed_all(ledger)
        dead: dict = state["dead"]
        pending = [f for f in FOLLOW_QUEUE if f not in done and f not in dead]
        if not pending:
            log("队列已全部完成或放弃，无待关注吧")
            return 0
        log(f"待关注 {len(pending)} 个：{'、'.join(pending)}")
        random.shuffle(pending)

        accounts = ACCOUNT_IDS[:]
        random.shuffle(accounts)
        aborted_accounts = 0
        followed_this_run: list[str] = []

        for acc_id in accounts:
            already = int(state["today"].get(str(acc_id), 0))
            quota = random.randint(DAILY_MIN, DAILY_MAX) - already
            if quota <= 0:
                log(f"账号 {acc_id} 今日已关注 {already} 个，达到额度，跳过")
                continue
            creds = await get_account_credentials(db, acc_id)
            if not creds:
                log(f"[warn] 账号 {acc_id} 凭证获取失败，跳过")
                continue
            _aid, bduss, stoken, proxy_id, cuid, ua = creds
            if proxy_id:
                proxy = await db.get_proxy(proxy_id)
                if not proxy or not proxy.is_active:
                    log(f"[warn] 账号 {acc_id} 绑定代理失效，跳过")
                    continue

            picks = pending[:quota]
            async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
                for pick_idx, fname in enumerate(picks):
                    # 执行时复核：圈定到执行之间状态可能已变
                    forum = await client.get_forum(fname)
                    err = getattr(forum, "err", None)
                    if err is not None or not getattr(forum, "fname", None):
                        dead[fname] = f"查询异常:{err}"
                        pending.remove(fname)
                        log(f"[dead] {fname} 查询异常({err})，移出队列")
                        continue
                    if getattr(forum, "has_bawu", False):
                        dead[fname] = "已有吧主"
                        pending.remove(fname)
                        log(f"[dead] {fname} 已出现吧主，移出队列")
                        continue
                    if getattr(forum, "is_followed", False):
                        ledger.setdefault(str(acc_id), []).append(fname)
                        await save_ledger(db, ledger)
                        # 账本已记但 forum 表缺失的历史关注在此自愈落库（add_forum 带查重）
                        fid = getattr(forum, "fid", 0) or 0
                        if fid:
                            await db.add_forum(fid=fid, fname=fname, account_id=acc_id)
                        pending.remove(fname)
                        log(f"[skip] {fname} 已关注过（直接记账）")
                        continue

                    res = await client.follow_forum(fname)
                    res_err = getattr(res, "err", None)
                    if res_err is not None:
                        state["fail"][fname] = state["fail"].get(fname, 0) + 1
                        log(f"[fail] 账号 {acc_id} 关注 [{fname}] 失败: {res_err}")
                        if state["fail"][fname] >= FAIL_GIVE_UP:
                            dead[fname] = f"连续失败{FAIL_GIVE_UP}次"
                            pending.remove(fname)
                            log(f"[dead] {fname} 移出队列")
                        aborted_accounts += 1
                        log(f"[breaker] 账号 {acc_id} 当日关注中止（疑似拦截）")
                        break

                    ledger.setdefault(str(acc_id), []).append(fname)
                    await save_ledger(db, ledger)
                    await db.upsert_target_pools([fname], group=GROUP)
                    # 即时落库 forum 表：否则该吧不参与签到，详情页关注数也与实际不符
                    fid = getattr(forum, "fid", 0) or 0
                    if fid:
                        await db.add_forum(fid=fid, fname=fname, account_id=acc_id)
                    state["today"][str(acc_id)] = already = already + 1
                    followed_this_run.append(fname)
                    pending.remove(fname)
                    save_state(state)
                    log(f"[ok] 账号 {acc_id} 关注 [{fname}] 成功 (成员 {forum.member_num})，已入靶场池")
                    if pick_idx < len(picks) - 1:
                        await asyncio.sleep(random.uniform(60, 180))
            save_state(state)
            if pending and aborted_accounts < 2:
                await asyncio.sleep(random.uniform(180, 360))
            if aborted_accounts >= 2:
                log("[breaker] 两个账号出现失败，整轮熔断，剩余留待下轮")
                break

        save_state(state)
        log(f"本轮结束：成功 {len(followed_this_run)} 个"
            f"{'：' + '、'.join(followed_this_run) if followed_this_run else ''}；"
            f"剩余 {len(pending)} 个；累计失败放弃 {len(dead)} 个")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
