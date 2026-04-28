
import asyncio
from dataclasses import dataclass, field

@dataclass
class BatchPostTask:
    accounts: list[int] = field(default_factory=list)
    strategy: str = "round_robin"

async def _pick_account(task, step, weights):
    if task.strategy in ("round_robin", "strict_round_robin"):
        return task.accounts[step % len(task.accounts)]
    return task.accounts[0]

async def _pick_optimal_account_for_target(task, target_fname, step, native_map, followed_map):
    """修复后的版本：strict_round_robin 带搜索的均匀分配"""

    # 严格轮询：从轮询位置向后搜索，优先找已关注该吧的账号
    if task.strategy == "strict_round_robin":
        n = len(task.accounts)
        native_accounts = native_map.get(target_fname, [])
        available_accounts = followed_map.get(target_fname, [])
        # 优先在原生号中搜索
        for offset in range(n):
            candidate = task.accounts[(step + offset) % n]
            if candidate in native_accounts:
                return candidate
        # 次优：在关注号中搜索
        for offset in range(n):
            candidate = task.accounts[(step + offset) % n]
            if candidate in available_accounts:
                return candidate
        # 最后：纯轮询（空降）
        return task.accounts[step % len(task.accounts)]

    native_accounts = native_map.get(target_fname, [])
    # Filter only accounts that are in the task
    native_accounts = [acc for acc in native_accounts if acc in task.accounts]
    native_accounts.sort()

    if native_accounts:
        if task.strategy == "round_robin":
            # 修复：从轮询位置向后搜索，找到第一个在原生列表中的账号
            n = len(task.accounts)
            for offset in range(n):
                candidate = task.accounts[(step + offset) % n]
                if candidate in native_accounts:
                    return candidate
        return native_accounts[0]

    # 次优：普通关注号
    available_accounts = followed_map.get(target_fname, [])
    available_accounts = [acc for acc in available_accounts if acc in task.accounts]
    available_accounts.sort()
    if available_accounts:
        if task.strategy == "round_robin":
            n = len(task.accounts)
            for offset in range(n):
                candidate = task.accounts[(step + offset) % n]
                if candidate in available_accounts:
                    return candidate
        return available_accounts[0]

    # 最终回退
    return await _pick_account(task, step, [])

async def test_rotation(strategy="round_robin"):
    accounts = [2, 18701, 3, 1, 5]
    task = BatchPostTask(accounts=accounts, strategy=strategy)

    # 原生账号映射（关注+发布目标）
    native_map = {
        "Forum A": [3],
        "Forum B": [5],
        "Forum C": [5],
        "Forum D": [1],
        "Forum E": [2, 18701, 3, 1, 5],
        "Forum F": [3]
    }

    # 普通关注号映射
    followed_map = {
        "Forum A": [3, 5],
        "Forum B": [5, 1],
        "Forum C": [5, 2],
        "Forum D": [1, 3],
        "Forum E": [2, 18701, 3, 1, 5],
        "Forum F": [3, 18701]
    }

    forums = ["Forum A", "Forum B", "Forum C", "Forum D", "Forum E", "Forum F"]

    print(f"\n{'='*60}")
    print(f"Strategy: '{strategy}'")
    print(f"{'='*60}")
    stats = {}
    forum_acc_stats = {}
    for i in range(15):
        fname = forums[i % len(forums)]
        acc = await _pick_optimal_account_for_target(task, fname, i, native_map, followed_map)
        stats[acc] = stats.get(acc, 0) + 1
        forum_acc_stats.setdefault(fname, {})
        forum_acc_stats[fname][acc] = forum_acc_stats[fname].get(acc, 0) + 1

        is_native = acc in native_map.get(fname, [])
        marker = "(原生)" if is_native else "(空降)"
        print(f"  Step {i:2}: {fname:10} -> Account {acc:<5} {marker}")

    print(f"\n  Account distribution:")
    for acc in accounts:
        count = stats.get(acc, 0)
        bar = "#" * count
        print(f"    Account {acc:<5}: {count:2} times {bar}")

    # 检查均匀度
    counts = list(stats.values())
    max_c, min_c = max(counts), min(counts)
    print(f"  Balance: min={min_c}, max={max_c}, spread={max_c - min_c}")

async def main():
    await test_rotation("round_robin")
    await test_rotation("strict_round_robin")

asyncio.run(main())
