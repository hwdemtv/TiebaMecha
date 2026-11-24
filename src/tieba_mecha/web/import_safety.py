"""物料导入的文件定位安全校验（2026-11-24）

flet 的 FilePicker 事件（选框结果、上传进度）里的 path / file_name 全部取自
客户端消息，服务端不可信任。早期实现有两处直接采信：

- 上传完成回调 ``os.path.join(upload_dir, e.file_name)`` 处理完后 ``os.remove``：
  伪造 ``file_name="../data/tieba_mecha.db"`` 即可让服务删除上传目录之外的任意文件；
- 选框结果 ``e.files[0].path`` 直接读盘导入：伪造 path 可让服务端把任意本地文本
  文件读进物料池（随后可能被批量任务公开发到贴吧）。

本模块只做无状态的纯校验，调用方另需自持「本会话发起过的上传名」集合。
"""

from __future__ import annotations

import os
from pathlib import Path

# 与 BatchPostPage._process_file_import 实际支持的解析格式保持一致
IMPORT_EXTENSIONS = (".txt", ".csv")
# 导入文件按行读入内存，设个远高于正常物料文件的体积上限兜底
MAX_IMPORT_BYTES = 50 * 1024 * 1024


def is_safe_upload_name(name) -> bool:
    """上传文件名白名单：纯文件名的 .txt/.csv，且文件名本身安全（不含任何路径成分）

    拒绝路径分隔符、盘符、上级引用、控制字符与超长名。上传目录内由本名字直接拼路径，
    因此这里不允许出现任何能改变目录层级的字符。
    """
    if not isinstance(name, str) or not name or len(name) > 200:
        return False
    if name in (".", ".."):
        return False
    if any(ch in name for ch in ("/", "\\", ":", "\x00")):
        return False
    # basename/dirname 双重确认（Windows 与 POSIX 对分隔符的判定不同，两边都要求为纯文件名）
    if os.path.basename(name) != name or os.path.dirname(name):
        return False
    return name.lower().endswith(IMPORT_EXTENSIONS)


def is_importable_local_file(path) -> bool:
    """桌面原生选框给出的本地路径须是真实存在的常规 .txt/.csv 文件（事件可伪造，不采信任意路径）"""
    try:
        p = Path(path)
        if not p.is_absolute() or p.suffix.lower() not in IMPORT_EXTENSIONS:
            return False
        if not p.is_file() or p.stat().st_size > MAX_IMPORT_BYTES:
            return False
        return True
    except (OSError, ValueError, TypeError):
        return False
