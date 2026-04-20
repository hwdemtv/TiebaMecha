
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

async def _pick_optimal_account_for_target(task, target_fname, step, native_map):
    # [核平级增强] 如果是严格轮询模式，直接强跳原生探测，实现绝对平均分配
    if task.strategy == "strict_round_robin":
        return await _pick_account(task, step, [])

    native_accounts = native_map.get(target_fname, [])
    # Filter only accounts that are in the task
    native_accounts = [acc for acc in native_accounts if acc in task.accounts]
    native_accounts.sort() # Ensure stable order

    if native_accounts:
        if task.strategy == "round_robin":
            # [优化] 优先寻找全局索引命中的账号，实现真正的全局均匀分布
            desired_acc_id = task.accounts[step % len(task.accounts)]
            if desired_acc_id in native_accounts:
                return desired_acc_id
            # fallback: 在原生号中轮询，但使用全局索引以保持某种程度的散列
            return native_accounts[step % len(native_accounts)]
        return native_accounts[0]
    
    # 最终回退：大盘调度策略 (空降兵打法)
    return await _pick_account(task, step, [])

async def test_rotation(strategy="round_robin"):
    # Mock accounts: 1, 2, 3, 4, 5. IDs: [02, 18701, 3, 1, 5]
    accounts = [2, 18701, 3, 1, 5]
    task = BatchPostTask(accounts=accounts, strategy=strategy)
    
    # Mock native forums: 
    # Account 5 follows multiple forums (the imbalance issue)
    # Account 2 follows nothing (the unused issue)
    native_map = {
        "Forum A": [3],
        "Forum B": [5],
        "Forum C": [5],
        "Forum D": [1],
        "Forum E": [2, 18701, 3, 1, 5],
        "Forum F": [3]
    }
    
    forums = ["Forum A", "Forum B", "Forum C", "Forum D", "Forum E", "Forum F"]
    
    print(f"\nSimulating rotation with '{strategy}' strategy:")
    stats = {}
    for i in range(15): # Run 15 times for 5 accounts
        fname = forums[i % len(forums)]
        acc = await _pick_optimal_account_for_target(task, fname, i, native_map)
        stats[acc] = stats.get(acc, 0) + 1
        print(f"Step {i:2}: Forum {fname:10} -> Account {acc:<5} {'(MATCH!)' if acc == accounts[i % len(accounts)] else ''}")
    
    print("\nAccount Stats:")
    for acc in accounts:
        print(f"Account {acc:<5}: {stats.get(acc, 0)} times")

async def main():
    await test_rotation("round_robin")
    await test_rotation("strict_round_robin")

asyncio.run(main())
