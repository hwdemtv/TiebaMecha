"""无吧主吧候选清单验真扫描（只读：只查询，不关注、不写库）。

用法（服务器上，仓库根目录执行）：
    PYTHONPATH=src .venv/bin/python /tmp/scan_ownerless_forums.py

输出四类：
    A  真·无吧主（可关注；成员<系统阈值500的标注 A-）
    B  有吧主
    C  不存在/查询异常（含重试一次后仍失败）
并标注是否已在靶场池。
"""
import asyncio
import random
import sqlite3
import sys

from tieba_mecha.core.account import get_account_credentials
from tieba_mecha.core.client_factory import create_client
from tieba_mecha.db.crud import Database

# 用户提供的候选清单（59 个，按原顺序）
CANDIDATE_FORUMS = [
    "女人", "卡片召唤师", "上位卡组", "血色曼陀罗", "火影图文班",
    "卡卡西", "鼬卡", "卡夫卡", "硬科幻", "迷糊家族",
    "南柱赫", "栎迷家族", "k11", "a86", "南唐故郡",
    "风流逐声工作室", "俊男坊", "丁墨", "这该死的求生欲", "简谱",
    "广播剧综合", "李暮夕", "兰陵", "苍山", "80后",
    "daragon美文", "xhr", "刘有生", "护师资格", "癫痫病",
    "聂蓉", "甘肃中医学院", "laychen", "夏空的英仙座", "巴塞罗那",
    "小灿粉丝", "楼主就是那个男的", "扁皮", "吊图搞怪", "心理疏导",
    "搞笑君", "峰怡", "a326", "狂沙奇缘", "简暗",
    "歌曲", "憨批", "请糟蹋我吧公瑾", "哈尔滨", "les",
    "泰山现代中学", "暗黑3", "美女请别影响我学习", "天龙网游", "炉石传说",
    "御龙在天新区", "鹿鼎网游", "快穿推文", "甄爱家族",
]

# 扫描用账号优先级：均须 active 且不绑失效代理
CREDENTIAL_ACCOUNT_IDS = [6, 9, 10]
MEMBER_MIN = 500  # 与系统 maint_autofollow_member_min 一致，仅作标注阈值


async def pick_credentials(db: Database):
    for acc_id in CREDENTIAL_ACCOUNT_IDS:
        try:
            creds = await get_account_credentials(db, acc_id)
        except Exception as e:
            print(f"[warn] 账号 {acc_id} 凭证获取异常: {type(e).__name__}: {e}")
            continue
        if creds:
            print(f"[info] 使用账号 id={creds[0]} 的凭据做查询（只读）")
            return creds
    return None


def load_target_pool_index() -> dict[str, str]:
    """fname -> post_group，来自靶场池（只读）。"""
    idx: dict[str, str] = {}
    try:
        con = sqlite3.connect("file:data/tieba_mecha.db?mode=ro", uri=True)
        for fname, grp in con.execute(
            "SELECT fname, post_group FROM target_pool WHERE fname IS NOT NULL"
        ):
            idx[fname] = grp or ""
        con.close()
    except Exception as e:
        print(f"[warn] 靶场池读取失败（不影响扫描）: {type(e).__name__}: {e}")
    return idx


async def probe(client, fname: str):
    """查询单个吧，失败自动重试一次。返回 (class, member_num, thread_num, err_desc)。"""
    last_err = ""
    for attempt in (1, 2):
        try:
            forum = await client.get_forum(fname)
            err = getattr(forum, "err", None)
            if err is not None or not getattr(forum, "fname", None):
                last_err = str(err) if err is not None else "返回空吧名"
            else:
                members = getattr(forum, "member_num", 0) or 0
                threads = getattr(forum, "thread_num", 0) or 0
                has_bawu = bool(getattr(forum, "has_bawu", False))
                cls = "B" if has_bawu else ("A" if members >= MEMBER_MIN else "A-")
                return cls, members, threads, ""
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
        if attempt == 1:
            await asyncio.sleep(3)
    return "C", 0, 0, last_err


async def main() -> int:
    db = Database("data/tieba_mecha.db")
    creds = await pick_credentials(db)
    if not creds:
        print("[fatal] 无可用账号凭据，终止")
        return 1
    _acc_id, bduss, stoken, _proxy_id, cuid, ua = creds
    pool = load_target_pool_index()

    results = []  # (cls, fname, members, threads, in_pool, err)
    async with await create_client(db, bduss, stoken, proxy_id=None, cuid=cuid, ua=ua) as client:
        for i, fname in enumerate(CANDIDATE_FORUMS, 1):
            cls, members, threads, err = await probe(client, fname)
            results.append((cls, fname, members, threads, pool.get(fname), err))
            print(f"[{i:>2}/{len(CANDIDATE_FORUMS)}] {cls:<2} {fname}"
                  f" 成员={members} 主题={threads}"
                  f"{' 靶场池:'+p if (p := pool.get(fname)) is not None else ''}"
                  f"{' err=' + err if err else ''}")
            await asyncio.sleep(random.uniform(1.5, 3.0))

    counts: dict[str, int] = {}
    for cls, *_ in results:
        counts[cls] = counts.get(cls, 0) + 1

    print("\n===== 分类汇总 =====")
    for label, key in (("A  真·无吧主(≥500人)", "A"), ("A- 无吧主但<500人", "A-"),
                       ("B  有吧主", "B"), ("C  不存在/异常", "C")):
        names = [r[1] for r in results if r[0] == key]
        print(f"{label}: {counts.get(key, 0)} 个")
        if names:
            print(f"    {'、'.join(names)}")

    already = [r[1] for r in results if r[4] is not None]
    if already:
        print(f"\n已在靶场池(勿重复入池): {'、'.join(already)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
