"""CLI entry point using Typer"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from tieba_mecha import __version__
from tieba_mecha.core import account, post, sign
from tieba_mecha.db.crud import Database, get_db

app = typer.Typer(
    name="tieba",
    help="TiebaMecha - Cyber-Mecha style Baidu Tieba management tool",
    add_completion=False,
)
console = Console()

# 子命令组
account_app = typer.Typer(name="account", help="账号管理")
sign_app = typer.Typer(name="sign", help="签到管理")
post_app = typer.Typer(name="post", help="帖子管理")

app.add_typer(account_app)
app.add_typer(sign_app)
app.add_typer(post_app)


def run_async(coro):
    """运行异步函数的辅助函数"""

    async def wrapper():
        db = await get_db()
        try:
            return await coro(db)
        finally:
            await db.close()

    return asyncio.run(wrapper())


# ========== 主命令 ==========


@app.command()
def web(
    port: int = typer.Option(9006, "--port", "-p", help="Web 服务端口（与 Web 端默认一致）"),
    host: str = typer.Option("localhost", "--host", "-h", help="绑定地址"),
):
    """启动 Web 界面"""
    import os

    # 设置环境变量供 Flet 使用
    os.environ["FLET_SERVER_PORT"] = str(port)

    console.print(f"[cyan]启动 TiebaMecha Web 界面...[/cyan]")
    console.print(f"[green]访问地址: http://{host}:{port}[/green]")

    # 导入并启动 Web 应用
    from tieba_mecha.web.app import run_app

    run_app(port=port, host=host)


@app.command()
def version():
    """显示版本信息"""
    console.print(f"[cyan]TiebaMecha[/cyan] v{__version__}")


# ========== 账号管理 ==========


@account_app.command("add")
def account_add(
    bduss: str = typer.Option(..., "--bduss", "-b", help="BDUSS"),
    stoken: str = typer.Option("", "--stoken", "-s", help="STOKEN"),
    name: str = typer.Option("", "--name", "-n", help="账号备注名称"),
    no_verify: bool = typer.Option(False, "--no-verify", help="跳过账号验证"),
):
    """添加账号"""

    async def _add(db: Database):
        # 使用用户名作为默认名称（稍后可能被验证结果覆盖）
        account_name = name

        # 添加账号（自动验证并获取用户信息）
        console.print("[yellow]添加账号中...[/yellow]")
        acc = await account.add_account(
            db, account_name, bduss, stoken, verify=not no_verify
        )

        # 如果用户未指定名称，使用真实用户名
        if not name and acc.user_name:
            await db.update_account(acc.id, name=acc.user_name)
            acc.name = acc.user_name

        if acc.status.startswith("invalid"):
            console.print(f"[yellow]警告: 账号验证失败 - {acc.status[9:]}[/yellow]")
            console.print(f"[green]账号已添加: {acc.name} (ID: {acc.id})[/green]")
        else:
            console.print(f"[green]账号添加成功: {acc.name} (ID: {acc.id})[/green]")
            if acc.user_name:
                console.print(f"  用户名: {acc.user_name}")
                console.print(f"  用户ID: {acc.user_id}")

    run_async(_add)


@account_app.command("list")
def account_list():
    """列出所有账号"""

    async def _list(db: Database):
        accounts = await account.list_accounts(db)

        table = Table(title="账号列表")
        table.add_column("ID", style="cyan")
        table.add_column("名称", style="green")
        table.add_column("用户名", style="yellow")
        table.add_column("状态", style="magenta")

        for acc in accounts:
            status = "[green]活跃[/green]" if acc.is_active else "[dim]未激活[/dim]"
            table.add_row(str(acc.id), acc.name, acc.user_name or "-", status)

        console.print(table)

    run_async(_list)


@account_app.command("switch")
def account_switch(
    account_id: int = typer.Argument(..., help="账号ID"),
):
    """切换活跃账号"""

    async def _switch(db: Database):
        success = await account.switch_account(db, account_id)
        if success:
            console.print(f"[green]已切换到账号 ID: {account_id}[/green]")
        else:
            console.print("[red]切换失败[/red]")

    run_async(_switch)


@account_app.command("delete")
def account_delete(
    account_id: int = typer.Argument(..., help="账号ID"),
):
    """删除账号"""

    async def _delete(db: Database):
        success = await account.remove_account(db, account_id)
        if success:
            console.print(f"[green]已删除账号 ID: {account_id}[/green]")
        else:
            console.print("[red]删除失败[/red]")

    run_async(_delete)


@account_app.command("verify")
def account_verify():
    """验证当前账号状态"""

    async def _verify(db: Database):
        creds = await account.get_account_credentials(db)
        if not creds:
            console.print("[red]未找到活跃账号[/red]")
            return

        # get_account_credentials 返回 (id, bduss, stoken, proxy_id, cuid, user_agent) 6 元组
        _, bduss, stoken, proxy_id, cuid, user_agent = creds
        valid, user_id, user_name, error = await account.verify_account(bduss, stoken)

        if valid:
            console.print(f"[green]账号有效[/green]")
            console.print(f"  用户ID: {user_id}")
            console.print(f"  用户名: {user_name}")
        else:
            console.print("[red]账号已失效，请重新登录[/red]")
            if error:
                console.print(f"  错误: {error}")

    run_async(_verify)


@account_app.command("refresh")
def account_refresh(
    account_id: Optional[int] = typer.Argument(None, help="账号ID，留空则刷新所有账号"),
):
    """刷新账号信息 (验证登录状态并更新 user_id/user_name)"""

    async def _refresh(db: Database):
        if account_id:
            # 刷新单个账号
            console.print(f"[yellow]刷新账号 {account_id}...[/yellow]")
            acc = await account.refresh_account(db, account_id)
            if acc:
                if acc.status.startswith("invalid"):
                    console.print(f"[red]账号已失效: {acc.name}[/red]")
                    console.print(f"  状态: {acc.status}")
                else:
                    console.print(f"[green]账号有效: {acc.name}[/green]")
                    console.print(f"  用户名: {acc.user_name}")
                    console.print(f"  用户ID: {acc.user_id}")
            else:
                console.print(f"[red]账号 {account_id} 不存在[/red]")
        else:
            # 刷新所有账号
            accounts = await account.list_accounts(db)
            if not accounts:
                console.print("[yellow]没有账号[/yellow]")
                return

            console.print(f"[cyan]开始刷新 {len(accounts)} 个账号...[/cyan]")

            valid_count = 0
            invalid_count = 0

            for acc in accounts:
                console.print(f"[yellow]刷新: {acc.name}...[/yellow]")
                updated = await account.refresh_account(db, acc.id)
                if updated:
                    if updated.status.startswith("invalid"):
                        console.print(f"  [red]已失效[/red]")
                        invalid_count += 1
                    else:
                        console.print(f"  [green]有效 - {updated.user_name}[/green]")
                        valid_count += 1
                else:
                    console.print(f"  [red]刷新失败[/red]")
                    invalid_count += 1

            console.print(f"\n[cyan]刷新完成: 有效 {valid_count}, 失效 {invalid_count}[/cyan]")

    run_async(_refresh)


# ========== 签到管理 ==========


@sign_app.command("run")
def sign_run(
    forum: Optional[str] = typer.Argument(None, help="贴吧名称，留空则签到所有"),
    delay: float = typer.Option(1.0, "--delay", "-d", help="签到间隔(秒)"),
):
    """执行签到"""

    async def _sign(db: Database):
        if forum:
            # 签到单个贴吧
            result = await sign.sign_forum(db, forum)
            if result.success:
                console.print(f"[green]✓ {forum}: {result.message}[/green]")
            else:
                console.print(f"[red]✗ {forum}: {result.message}[/red]")
        else:
            # 签到所有贴吧
            console.print("[cyan]开始批量签到...[/cyan]")
            success_count = 0
            fail_count = 0

            async for result in sign.sign_all_forums(db, delay_min=delay, delay_max=delay + 2.0):
                if result.success:
                    console.print(f"[green]✓ {result.fname}[/green]")
                    success_count += 1
                else:
                    console.print(f"[red]✗ {result.fname}: {result.message}[/red]")
                    fail_count += 1

            console.print(f"\n[cyan]签到完成: 成功 {success_count}, 失败 {fail_count}[/cyan]")

    run_async(_sign)


@sign_app.command("sync")
def sign_sync():
    """同步关注贴吧列表"""

    async def _sync(db: Database):
        console.print("[cyan]同步关注贴吧中...[/cyan]")
        added = await sign.sync_forums_to_db(db)
        console.print(f"[green]新增 {added} 个贴吧[/green]")

    run_async(_sync)


@sign_app.command("status")
def sign_status():
    """查看签到状态"""

    async def _status(db: Database):
        stats = await sign.get_sign_stats(db)

        table = Table(title="签到统计")
        table.add_column("项目", style="cyan")
        table.add_column("数量", style="green")

        table.add_row("总贴吧数", str(stats["total"]))
        table.add_row("已签到", str(stats["success"]))
        table.add_row("未签到/失败", str(stats["total"] - stats["success"]))

        console.print(table)

    run_async(_status)


# ========== 帖子管理 ==========


@post_app.command("list")
def post_list(
    forum: str = typer.Argument(..., help="贴吧名称"),
    page: int = typer.Option(1, "--page", "-p", help="页码"),
    num: int = typer.Option(20, "--num", "-n", help="每页数量"),
):
    """列出帖子"""

    async def _list(db: Database):
        threads = await post.get_threads(db, forum, pn=page, rn=num)

        table = Table(title=f"{forum} 帖子列表 (第{page}页)")
        table.add_column("TID", style="cyan")
        table.add_column("标题", style="green", max_width=40)
        table.add_column("作者", style="yellow")
        table.add_column("回复", style="magenta")
        table.add_column("状态", style="blue")

        for t in threads:
            status = ""
            if t.is_top:
                status = "[red]置顶[/red]"
            elif t.is_good:
                status = "[yellow]精品[/yellow]"

            title = t.title[:40] + "..." if len(t.title) > 40 else t.title
            table.add_row(str(t.tid), title, t.author_name, str(t.reply_num), status)

        console.print(table)

    run_async(_list)


@post_app.command("delete")
def post_delete(
    tid: int = typer.Argument(..., help="帖子TID"),
    forum: str = typer.Option(..., "--forum", "-f", help="贴吧名称"),
):
    """删除帖子"""

    async def _delete(db: Database):
        success, msg = await post.delete_thread(db, forum, tid)
        if success:
            console.print(f"[green]{msg}[/green]")
        else:
            console.print(f"[red]{msg}[/red]")

    run_async(_delete)


@post_app.command("search")
def post_search(
    keyword: str = typer.Argument(..., help="搜索关键词"),
    forum: str = typer.Option(..., "--forum", "-f", help="贴吧名称"),
):
    """搜索帖子"""

    async def _search(db: Database):
        threads = await post.search_threads(db, forum, keyword)

        if not threads:
            console.print("[yellow]未找到相关帖子[/yellow]")
            return

        table = Table(title=f"搜索结果: {keyword}")
        table.add_column("TID", style="cyan")
        table.add_column("标题", style="green", max_width=40)
        table.add_column("作者", style="yellow")

        for t in threads:
            title = t.title[:40] + "..." if len(t.title) > 40 else t.title
            table.add_row(str(t.tid), title, t.author_name)

        console.print(table)

    run_async(_search)


@post_app.command("good")
def post_good(
    tid: int = typer.Argument(..., help="帖子TID"),
    forum: str = typer.Option(..., "--forum", "-f", help="贴吧名称"),
    undo: bool = typer.Option(False, "--undo", "-u", help="取消加精"),
):
    """设置/取消精品"""

    async def _good(db: Database):
        success, msg = await post.set_good(db, forum, tid, not undo)
        if success:
            console.print(f"[green]{msg}[/green]")
        else:
            console.print(f"[red]{msg}[/red]")

    run_async(_good)


if __name__ == "__main__":
    app()
