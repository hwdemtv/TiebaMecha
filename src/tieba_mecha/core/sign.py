"""Sign-in functionality"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator

import aiotieba

from ..db.crud import Database
from .account import get_account_credentials
from .client_factory import create_client
from .proxy import get_best_proxy_config
from .logger import log_info, log_warn, log_error


@dataclass
class SignResult:
    """签到结果"""

    fname: str
    success: bool
    message: str
    sign_count: int = 0


@dataclass
class ForumInfo:
    """贴吧信息"""

    fid: int
    fname: str
    is_sign_today: bool
    sign_count: int
    level: int = 0


async def get_follow_forums(db: Database, account_id: int | None = None) -> list[ForumInfo]:
    creds = await get_account_credentials(db, account_id)
    if not creds:
        return []

    bduss, stoken, proxy_id, cuid, ua = creds
    forums = []

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        user_info = await client.get_self_info()
        if not user_info:
            return []

        pn = 1
        while True:
            result = await client.get_follow_forums(user_info.user_id, pn=pn, rn=50)
            if result.err:
                await log_warn(f"获取关注列表失败 (账户ID: {account_id}, 第 {pn} 页): {result.err}")
                break

            for forum in result.objs:
                forums.append(
                    ForumInfo(
                        fid=forum.fid,
                        fname=forum.fname,
                        is_sign_today=False,
                        sign_count=0,
                        level=getattr(forum, "level", 0),
                    )
                )

            if not result.has_more:
                break
            pn += 1

    return forums


async def sync_forums_to_db(db: Database) -> int:
    """
    同步全域贴吧：遍历所有矩阵账号，将所有关注贴吧推入数据库
    """
    accounts = await db.get_matrix_accounts()
    if not accounts:
        return 0

    total_added = 0
    await log_info(f"全域同步指令已发出：正在同步 {len(accounts)} 个运行终端的贴吧...")

    for account in accounts:
        try:
            forums = await get_follow_forums(db, account.id)
            existing = await db.get_forums(account.id)
            existing_fids = {f.fid for f in existing}

            added = 0
            for forum in forums:
                if forum.fid not in existing_fids:
                    await db.add_forum(
                        fid=forum.fid,
                        fname=forum.fname,
                        account_id=account.id,
                        level=forum.level,
                    )
                    added += 1
                else:
                    # 更新已有贴吧的等级变动
                    await db.add_forum(
                        fid=forum.fid,
                        fname=forum.fname,
                        account_id=account.id,
                        level=forum.level,
                    )
            
            total_added += added
            await log_info(f"账号 [{account.name}] 同步完毕 | 新增 {added} 个")
        except Exception as e:
            await log_error(f"同步账号 [{account.name}] 时发生背刺: {str(e)}")

    return total_added


async def sign_forum(db: Database, fname: str) -> SignResult:
    """
    签到单个贴吧

    Args:
        db: 数据库实例
        fname: 贴吧名称

    Returns:
        SignResult: 签到结果
    """
    creds = await get_account_credentials(db)
    if not creds:
        return SignResult(fname=fname, success=False, message="未找到账号凭证")

    bduss, stoken, proxy_id, cuid, ua = creds

    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid) as client:
        result = await client.sign_forum(fname)

        if result:
            await log_info(f"手动签到成功: {fname}")
            return SignResult(
                fname=fname,
                success=True,
                message="签到成功",
            )
        else:
            await log_warn(f"手动签到失败: {fname}")
            return SignResult(
                fname=fname,
                success=False,
                message="签到失败",
            )


async def sign_all_forums(
    db: Database,
    delay_min: float = 5.0,
    delay_max: float = 15.0,
) -> AsyncGenerator[SignResult, None]:
    """
    签到所有关注贴吧 (已解决 N+1 痛点)

    Args:
        db: 数据库实例
        delay: 每次签到间隔(秒)

    Yields:
        SignResult: 每个贴吧的签到结果
    """
    import random
    import asyncio
    from aiotieba.exception import TiebaServerError

    account = await db.get_active_account()
    if not account:
        yield SignResult(fname="", success=False, message="未找到活跃账号")
        return

    creds = await get_account_credentials(db, account.id)
    if not creds:
        yield SignResult(fname="", success=False, message="未找到账号凭证")
        return

    bduss, stoken, proxy_id, cuid, ua = creds
    forums = await db.get_forums(account.id)

    # N+1 优化: 在外层建立单一持久化连接池
    async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid, ua=ua) as client:
        for forum in forums:
            try:
                sign_res = await client.sign_forum(forum.fname)
                if sign_res:
                    result = SignResult(fname=forum.fname, success=True, message="签到成功", sign_count=forum.sign_count + 1)
                else:
                    result = SignResult(fname=forum.fname, success=False, message="签到请求被拒或未命中判定")
            except TiebaServerError as e:
                await log_warn(f"[{forum.fname}] 触发风控或 API 阻隔 ({e.code})，系统静默退避休眠 60 秒...")
                await asyncio.sleep(60)
                result = SignResult(fname=forum.fname, success=False, message=f"被风控限流: {e.msg}")
            except Exception as e:
                result = SignResult(fname=forum.fname, success=False, message=str(e))

            # 记录日志
            await db.add_sign_log(
                forum_id=forum.id,
                fname=forum.fname,
                success=result.success,
                message=result.message,
            )

            # 更新签到状态
            await db.update_forum_sign(forum.id, result.success)
            
            if result.success:
                await log_info(f"自动签到任务: {forum.fname} | 已连续 {result.sign_count} 天")
            else:
                await log_warn(f"自动签到跳过/失败: {forum.fname} | {result.message}")

            yield result

            # 行为人性化：随机波动延迟
            sleep_time = random.uniform(delay_min, delay_max)
            await asyncio.sleep(sleep_time)



async def get_sign_stats(db: Database) -> dict:
    """
    获取签到统计

    Returns:
        {total: 总数, success: 成功数, failure: 失败数}
    """
    account = await db.get_active_account()
    if not account:
        return {"total": 0, "success": 0, "failure": 0}

    forums = await db.get_forums(account.id)
    total = len(forums)
    success = sum(1 for f in forums if f.last_sign_status == "success")
    failure = sum(1 for f in forums if f.last_sign_status == "failure")

    return {
        "total": total,
        "success": success,
        "failure": failure,
    }


async def sign_all_accounts(
    db: Database,
    delay_min: float = 5.0,
    delay_max: float = 15.0,
    acc_delay_min: float = 30.0,
    acc_delay_max: float = 120.0,
):
    """
    矩阵全扫签到：遍历所有可用账号，依次完成每个账号下所有贴吧的签到。

    Args:
        db: 数据库实例
        delay_min: 同账号内，贴吧间最小延迟（秒）
        delay_max: 同账号内，贴吧间最大延迟（秒）
        acc_delay_min: 账号切换间最小延迟（秒），防止关联风险
        acc_delay_max: 账号切换间最大延迟（秒）

    Yields:
        dict: {
            "account_id": int,
            "account_name": str,
            "fname": str,
            "success": bool,
            "message": str,
            "proxy_status": "ok" | "missing" | "suspended"
        }
    """
    import random

    # 获取所有矩阵可用账号（跳过 suspended_proxy 状态）
    accounts = await db.get_matrix_accounts()
    if not accounts:
        yield {
            "account_id": -1,
            "account_name": "无可用账号",
            "fname": "",
            "success": False,
            "message": "矩阵中无可用账号，请检查账号状态",
            "proxy_status": "missing",
        }
        return

    await log_info(f"矩阵全扫启动：共 {len(accounts)} 个有效账号进入签到队列")

    for acc_idx, account in enumerate(accounts):
        # 检查代理健康度
        proxy_status = "ok"
        if account.proxy_id:
            proxy = await db.get_proxy(account.proxy_id)
            if not proxy or not proxy.is_active:
                proxy_status = "suspended"
                await log_warn(
                    f"账号 [{account.name}] 绑定代理已失效，已跳过该账号签到"
                )
                yield {
                    "account_id": account.id,
                    "account_name": account.name,
                    "fname": "",
                    "success": False,
                    "message": "绑定代理已失效，账号已隔离",
                    "proxy_status": proxy_status,
                }
                continue
        else:
            # 无代理绑定：裸连警告但允许继续
            proxy_status = "missing"
            await log_warn(f"账号 [{account.name}] 未绑定代理，以裸连模式运行（存在关联风险）")

        await log_info(
            f"[{acc_idx + 1}/{len(accounts)}] 开始处理账号: {account.name}"
        )

        # 获取该账号的贴吧列表
        forums = await db.get_forums(account.id)
        if not forums:
            await log_warn(f"账号 [{account.name}] 无关注贴吧，跳过")
            continue

        for forum in forums:
            try:
                # 使用指定账号签到
                creds = await get_account_credentials(db, account.id)
                if not creds:
                    continue

                bduss, stoken, proxy_id, cuid, ua = creds
                async with await create_client(db, bduss, stoken, proxy_id=proxy_id, cuid=cuid) as client:
                    result_raw = await client.sign_forum(forum.fname)

                success = bool(result_raw)
                message = "签到成功" if success else "签到失败或已签过"

                # 写入日志与数据库
                await db.add_sign_log(
                    forum_id=forum.id,
                    fname=forum.fname,
                    success=success,
                    message=message,
                )
                await db.update_forum_sign(forum.id, success)

                if success:
                    await log_info(f"矩阵签到 [{account.name}] → {forum.fname}: 成功")
                else:
                    await log_warn(f"矩阵签到 [{account.name}] → {forum.fname}: {message}")

                yield {
                    "account_id": account.id,
                    "account_name": account.name,
                    "fname": forum.fname,
                    "success": success,
                    "message": message,
                    "proxy_status": proxy_status,
                }

                # 吧间延迟（人性化行为模拟）
                await asyncio.sleep(random.uniform(delay_min, delay_max))

            except Exception as e:
                await log_error(f"矩阵签到异常 [{account.name}] → {forum.fname}: {str(e)}")
                yield {
                    "account_id": account.id,
                    "account_name": account.name,
                    "fname": forum.fname,
                    "success": False,
                    "message": str(e),
                    "proxy_status": proxy_status,
                }

        # 账号切换延迟（防关联核心防线）
        if acc_idx < len(accounts) - 1:
            wait = random.uniform(acc_delay_min, acc_delay_max)
            await log_info(
                f"账号 [{account.name}] 签到完毕，等待 {wait:.1f}s 后切换下一个账号..."
            )
            await asyncio.sleep(wait)

    await log_info("矩阵全扫签到任务全部完成")

