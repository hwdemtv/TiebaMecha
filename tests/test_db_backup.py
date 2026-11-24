"""数据库每日自动备份回归（2026-11-24 全项目体检整改）

体检确认生产环境没有任何自动备份（systemd 无定时器、crontab 无备份项，仅有的几份 .bak 都是
历次手术前手动留的）。这里验证 db/backup.py 的在线备份与清理，以及守护任务的调度：

- 备份是在线一致性快照：源库处于 WAL 模式且有未落盘写入时也能拿到完整数据；
- 完整性校验 + 原子改名：中途失败不会留下「看起来像备份」的半个文件；
- 清理只动本模块命名的文件，不碰目录里其它东西。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tieba_mecha.core import daemon as daemon_mod
from tieba_mecha.db import backup as bk
from tieba_mecha.db.crud import Database


def _read_table(path: Path, sql: str):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---------- 在线备份 ----------

async def test_backup_captures_uncheckpointed_wal_writes_without_stopping_service(db, tmp_path):
    """源库保持打开（服务不停）、处于 WAL 模式且有写入时，备份拿到完整最新数据"""
    await db.set_setting("backup_probe", "written-before-backup")
    assert "wal" in _read_table(db.engine.url.database, "PRAGMA journal_mode")[0][0].lower()

    target = await asyncio.to_thread(bk.backup_database, db.engine.url.database, tmp_path / "backups")

    assert target.name.startswith("tieba_mecha-") and target.name.endswith(".db")
    rows = _read_table(target, "SELECT value FROM settings WHERE key = 'backup_probe'")
    assert rows == [("written-before-backup",)]
    assert _read_table(target, "PRAGMA integrity_check")[0][0] == "ok"


async def test_backup_is_restorable_as_a_working_database(db, tmp_path):
    """恢复演练：用备份文件起一个 Database，能正常读写"""
    await db.add_account(name="restorable", bduss="X" * 80)
    target = await asyncio.to_thread(bk.backup_database, db.engine.url.database, tmp_path)

    restored = Database(target)
    try:
        await restored.init_db()
        accounts = await restored.get_accounts()
        assert [a.name for a in accounts] == ["restorable"]
        await restored.set_setting("after_restore", "ok")
        assert await restored.get_setting("after_restore") == "ok"
    finally:
        await restored.close()


async def test_backup_failure_leaves_no_half_file(db, tmp_path):
    """完整性校验不过时：报错、不留任何成品或临时文件"""
    backup_dir = tmp_path / "backups"
    with patch.object(bk, "_quick_check", lambda _conn: "*** In database main ***; On page 2: row 1 missing"):
        with pytest.raises(RuntimeError, match="完整性校验未通过"):
            await asyncio.to_thread(bk.backup_database, db.engine.url.database, backup_dir)

    assert list(backup_dir.iterdir()) == []               # 没有「看起来像备份」的半个文件


async def test_backup_interrupted_before_rename_leaves_no_half_file(db, tmp_path):
    """数据已拷完、改名前出错：临时文件被清掉，不留下成品"""
    backup_dir = tmp_path / "backups"
    with patch.object(bk.os, "replace", side_effect=OSError("disk I/O error")):
        with pytest.raises(OSError, match="disk I/O error"):
            await asyncio.to_thread(bk.backup_database, db.engine.url.database, backup_dir)

    assert list(backup_dir.iterdir()) == []


def test_backup_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bk.backup_database(tmp_path / "nope.db", tmp_path / "backups")
    assert not (tmp_path / "backups" / "nope.db").exists()


def test_same_second_backups_do_not_overwrite_each_other(tmp_path):
    src = tmp_path / "src.db"
    sqlite3.connect(str(src)).close()
    moment = datetime(2026, 11, 24, 4, 30, 0)
    first = bk.backup_database(src, tmp_path / "b", moment=moment)
    second = bk.backup_database(src, tmp_path / "b", moment=moment)
    assert first != second and first.exists() and second.exists()
    # 文件名只到秒，同一时刻的两份按名称排序没有先后的意义，只比较集合
    assert sorted(p.name for p, _ in bk.list_backups(tmp_path / "b")) == sorted([first.name, second.name])


# ---------- 清理 ----------

def _touch(path: Path, age_days: float):
    path.write_bytes(b"old")
    stamp = (datetime.now() - timedelta(days=age_days)).timestamp()
    import os
    os.utime(path, (stamp, stamp))


def test_prune_only_removes_own_expired_backups(tmp_path):
    old = bk.backup_path_for(tmp_path, datetime.now() - timedelta(days=20))
    recent = bk.backup_path_for(tmp_path, datetime.now() - timedelta(days=2))
    _touch(old, 20)
    _touch(recent, 2)
    foreign = tmp_path / "tieba_mecha.db.bak_1015"           # 用户手动留的手术备份
    foreign.write_bytes(b"manual")
    other = tmp_path / "notes.txt"
    other.write_bytes(b"keep")
    subdir = tmp_path / "tieba_mecha-20200101-000000.db"     # 同名但是目录
    subdir.mkdir()

    removed = bk.prune_backups(tmp_path, keep_days=14)

    assert removed == [old]
    assert not old.exists() and recent.exists()
    assert foreign.read_bytes() == b"manual" and other.read_bytes() == b"keep"
    assert subdir.is_dir()


@pytest.mark.parametrize("keep_days", [0, -1])
def test_prune_keeps_everything_when_retention_disabled(tmp_path, keep_days):
    ancient = bk.backup_path_for(tmp_path, datetime.now() - timedelta(days=400))
    _touch(ancient, 400)
    assert bk.prune_backups(tmp_path, keep_days=keep_days) == []
    assert ancient.exists()


def test_prune_on_missing_directory_is_a_no_op(tmp_path):
    assert bk.prune_backups(tmp_path / "absent", keep_days=1) == []
    assert bk.list_backups(tmp_path / "absent") == []
    assert bk.latest_backup_age(tmp_path / "absent") is None


def test_latest_backup_age_reflects_newest_backup(tmp_path):
    now = datetime.now()
    _touch(bk.backup_path_for(tmp_path, now - timedelta(days=3)), 3)
    _touch(bk.backup_path_for(tmp_path, now - timedelta(hours=2)), 2 / 24)
    assert bk.latest_backup_age(tmp_path, now=now) == pytest.approx(2 * 3600, abs=1)   # 文件名只到秒


def test_run_scheduled_backup_reports_size_and_prunes(tmp_path):
    src = tmp_path / "src.db"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('" + "y" * 5000 + "')")
    conn.commit()
    conn.close()
    backup_dir = tmp_path / "b"
    backup_dir.mkdir()
    _touch(bk.backup_path_for(backup_dir, datetime.now() - timedelta(days=30)), 30)

    target, size_kb, pruned = bk.run_scheduled_backup(src, backup_dir, keep_days=14)

    assert target.exists() and size_kb >= 1 and pruned == 1
    assert len(bk.list_backups(backup_dir)) == 1


# ---------- 守护任务 ----------

async def test_daemon_registers_daily_backup_as_cron_and_can_stop_cleanly(db):
    """每日备份按固定本地时刻注册（不是 interval），守护停止时后台补偿任务一并取消"""
    daemon = daemon_mod.TiebaMechaDaemon()
    # 替身 _spawn：只记录调用并关掉协程（不真的等 5 分钟去备份）
    spawn = MagicMock(side_effect=lambda coro: coro.close())
    with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
         patch("tieba_mecha.core.daemon.do_auth_check_task", new_callable=AsyncMock), \
         patch("tieba_mecha.core.daemon._spawn", spawn):
        await daemon.start()
        job = daemon.scheduler.get_job(daemon_mod.BACKUP_JOB_ID)
        try:
            assert job is not None
            trigger_fields = {f.name: str(f) for f in job.trigger.fields}
            assert trigger_fields["hour"] == str(daemon_mod.BACKUP_HOUR)
            assert trigger_fields["minute"] == str(daemon_mod.BACKUP_MINUTE)
        finally:
            daemon.stop()
    assert daemon._started is False
    spawn.assert_called_once()                               # 启动补偿任务已派发（_spawn 是同步调用）


async def test_stop_cancels_background_tasks_spawned_by_daemon():
    async def _forever():
        await asyncio.sleep(3600)

    task = daemon_mod._spawn(_forever())
    assert task in daemon_mod._BG_TASKS
    daemon = daemon_mod.TiebaMechaDaemon()
    daemon._started = True
    daemon.scheduler = MagicMock()

    daemon.stop()
    await asyncio.sleep(0)                                    # 取消在下一轮事件循环才生效

    assert task.cancelled()
    assert daemon_mod._BG_TASKS == set()


async def test_backup_task_writes_backup_and_respects_settings(db, monkeypatch, tmp_path):
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(daemon_mod, "DEFAULT_DB_PATH", Path(db.engine.url.database))
    monkeypatch.setattr(daemon_mod, "default_backup_dir", lambda _p: backup_dir)
    monkeypatch.setattr(daemon_mod, "run_scheduled_backup",
                        lambda db_path, out_dir, keep_days: bk.run_scheduled_backup(db_path, out_dir, keep_days))
    await db.set_setting("backup_probe", "daemon-task")

    with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
         patch("tieba_mecha.core.daemon.log_info", AsyncMock()) as info, \
         patch("tieba_mecha.core.daemon.log_error", AsyncMock()) as err:
        await daemon_mod.do_database_backup_task()

    err.assert_not_awaited()
    backups = bk.list_backups(backup_dir)
    assert len(backups) == 1
    assert "数据库自动备份完成" in str(info.await_args.args[0])
    assert _read_table(backups[0][0], "SELECT value FROM settings WHERE key = 'backup_probe'") == [("daemon-task",)]


async def test_backup_task_failure_is_logged_and_does_not_raise(db, monkeypatch):
    """备份失败只记日志：不能把异常抛进调度器、连累同一调度器里的其它任务"""
    def _boom(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(daemon_mod, "run_scheduled_backup", _boom)
    with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
         patch("tieba_mecha.core.daemon.log_error", AsyncMock()) as err:
        await daemon_mod.do_database_backup_task()          # 不向外抛

    assert "No space left on device" in str(err.await_args.args[0])


async def test_backup_task_can_be_disabled_or_made_permanent(db, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon_mod, "run_scheduled_backup", lambda *a: calls.append(a) or (Path("x"), 1, 0))
    with patch("tieba_mecha.core.daemon.get_db", return_value=db), \
         patch("tieba_mecha.core.daemon.log_info", AsyncMock()), patch("tieba_mecha.core.daemon.log_error", AsyncMock()):
        await db.set_setting("db_backup_enabled", "false")
        await daemon_mod.do_database_backup_task()
        assert calls == []                                 # 关闭时不备份

        await db.set_setting("db_backup_enabled", "true")
        await db.set_setting("db_backup_keep_days", "0")
        await daemon_mod.do_database_backup_task()
        assert calls and calls[-1][-1] == 0                # 保留天数 0 = 永久保留，原样传下去


@pytest.mark.parametrize("stale, expect_backup", [(None, True), (30 * 3600, True), (2 * 3600, False)])
async def test_startup_compensation_runs_only_when_no_recent_backup(db, monkeypatch, stale, expect_backup):
    ran = AsyncMock()
    monkeypatch.setattr(daemon_mod, "BACKUP_STARTUP_DELAY_SEC", 0)
    monkeypatch.setattr(daemon_mod, "latest_backup_age", lambda _dir: stale)
    monkeypatch.setattr(daemon_mod, "do_database_backup_task", ran)
    with patch("tieba_mecha.core.daemon.log_error", AsyncMock()) as err:
        await daemon_mod._backup_shortly_after_start()

    assert ran.await_count == (1 if expect_backup else 0)
    err.assert_not_awaited()
