"""数据库在线备份（2026-11-24 全项目体检整改）

生产库是单文件 SQLite，承载全部账号凭据（加密）、代理、物料、帖子与运行记录。体检时确认
生产环境没有任何自动备份：systemd 无定时器、crontab 无备份项，data/ 下仅有的几份
.bak 都是历次手术前手动留的。

本模块是纯同步实现（标准库 sqlite3），供守护任务在线程池里调用：

- backup_database()：用 SQLite 在线备份 API 做一致性快照（WAL 模式下无需停服务），
  先写临时文件、quick_check 校验后再原子改名，中途失败不会留下半个备份；
- prune_backups()：按保留天数清理旧备份——只认本模块的命名，不碰目录里的其它文件；
- run_scheduled_backup()：上面两步的组合，供定时任务调用。

备份文件含加密后的凭据，与源库同等敏感：新建文件权限收紧为仅属主可读写（POSIX）。
恢复：停服务 → 把备份文件复制为 data/tieba_mecha.db（并删除同名 -wal / -shm）→ 启动。
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BACKUP_PREFIX = "tieba_mecha-"
BACKUP_SUFFIX = ".db"
_BACKUP_NAME_RE = re.compile(r"^tieba_mecha-(\d{8})-(\d{6})(?:-\d+)?\.db$")   # 末尾 -N 为同秒重复备份的序号
DEFAULT_KEEP_DAYS = 14
_STAMP_FORMAT = "%Y%m%d-%H%M%S"


def default_backup_dir(db_path: Path | str) -> Path:
    """备份目录：数据库所在目录下的 backups/"""
    return Path(db_path).parent / "backups"


def backup_path_for(backup_dir: Path | str, moment: datetime) -> Path:
    return Path(backup_dir) / f"{BACKUP_PREFIX}{moment.strftime(_STAMP_FORMAT)}{BACKUP_SUFFIX}"


def _restrict_permissions(path: Path) -> None:
    """备份含加密凭据：POSIX 上收紧为 0600（Windows 无此语义，忽略）"""
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError, AttributeError):
        pass


def _quick_check(connection: sqlite3.Connection) -> str:
    """备份文件的完整性校验结果（PRAGMA quick_check，正常返回 'ok'）"""
    row = connection.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row else ""


def backup_database(db_path: Path | str, backup_dir: Path | str, *, moment: datetime | None = None) -> Path:
    """对 db_path 做一致性快照，落到 backup_dir，返回备份文件路径。

    源库不需要停服务：sqlite3 的在线备份 API 在页面级拷贝期间持有读锁，遇到并发写入会
    自动重来，WAL 模式下得到的是快照时刻的完整状态。
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 同一秒内重复备份时避免覆盖已有文件
    moment = moment or datetime.now()
    target = backup_path_for(backup_dir, moment)
    seq = 1
    while target.exists():
        seq += 1
        target = backup_dir / (
            f"{BACKUP_PREFIX}{moment.strftime(_STAMP_FORMAT)}-{seq}{BACKUP_SUFFIX}"
        )

    fd, tmp_name = tempfile.mkstemp(prefix=f"{BACKUP_PREFIX}incomplete-", suffix=".part", dir=backup_dir)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        src = sqlite3.connect(str(db_path), timeout=30)
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
                check = _quick_check(dst)
            finally:
                dst.close()
        finally:
            src.close()

        if check != "ok":
            raise RuntimeError(f"备份完整性校验未通过: {check or '无结果'}")
        _restrict_permissions(tmp)
        os.replace(tmp, target)   # 同目录内改名：要么没有，要么是完整文件
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def list_backups(backup_dir: Path | str) -> list[tuple[Path, datetime]]:
    """本模块命名规则的备份文件及其时间戳（按时间升序）；同秒序号后缀 -N 也认"""
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return []
    found: list[tuple[Path, datetime]] = []
    for path in backup_dir.iterdir():
        match = _BACKUP_NAME_RE.match(path.name)
        if match and path.is_file():
            try:
                stamp = datetime.strptime(f"{match.group(1)}-{match.group(2)}", _STAMP_FORMAT)
            except ValueError:
                continue
            found.append((path, stamp))
    return sorted(found, key=lambda item: (item[1], item[0].name))


def latest_backup_age(backup_dir: Path | str, *, now: datetime | None = None) -> float | None:
    """最近一份备份距今的秒数；没有任何备份返回 None"""
    backups = list_backups(backup_dir)
    if not backups:
        return None
    return max(0.0, ((now or datetime.now()) - backups[-1][1]).total_seconds())


def prune_backups(backup_dir: Path | str, keep_days: int = DEFAULT_KEEP_DAYS, *, now: datetime | None = None) -> list[Path]:
    """删除超过保留天数的备份，返回被删除的文件。keep_days <= 0 表示全部保留。

    只按文件名里的时间戳判断，只动本模块命名的文件：目录里其它文件（用户手动拷进来的
    .bak、无关文件）一律不碰。
    """
    if keep_days <= 0:
        return []
    cutoff = (now or datetime.now()) - timedelta(days=keep_days)
    removed: list[Path] = []
    for path, stamp in list_backups(backup_dir):
        if stamp < cutoff:
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def run_scheduled_backup(
    db_path: Path | str,
    backup_dir: Path | str | None = None,
    keep_days: int = DEFAULT_KEEP_DAYS,
) -> tuple[Path, int, int]:
    """备份 + 清理，返回 (备份路径, 大小 KB, 清理掉的份数)。失败向上抛，由调用方记日志。"""
    backup_dir = Path(backup_dir) if backup_dir else default_backup_dir(Path(db_path))
    target = backup_database(db_path, backup_dir)
    size_kb = max(1, target.stat().st_size // 1024)
    pruned = prune_backups(backup_dir, keep_days)
    return target, size_kb, len(pruned)
