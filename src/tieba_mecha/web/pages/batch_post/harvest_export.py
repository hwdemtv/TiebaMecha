"""采集待审导出：harvested 物料 → Excel 友好 CSV（纯函数，单测主战场）

格式对齐仓库根《网盘精准配对导入模板.csv》的口径：UTF-8 带 BOM + CRLF，
Excel 双击打开中文不乱码。列设计覆盖人工审核/转存配对的下游用法——
"源链+提取码"相邻两列，方便按模板粘贴。
"""

from __future__ import annotations

import csv
import io
import re

from tieba_mecha.core.harvest import (
    HARVEST_STATE_CONTENT_ONLY,
    HARVEST_STATE_PENDING_TRANSFER,
    HARVEST_STATE_TRANSFERRED,
    _extract_code,
    harvest_state,
)

EXPORT_HEADERS = ["ID", "标题", "正文", "来源吧", "源链", "提取码", "链接类型", "自有链", "状态", "采集时间"]

_STATE_LABELS = {
    HARVEST_STATE_PENDING_TRANSFER: "待转存",
    HARVEST_STATE_TRANSFERRED: "已转存·待审核",
    HARVEST_STATE_CONTENT_ONLY: "纯内容·可放行",
}

_URL_PWD_RE = re.compile(r"[?&]pwd=([A-Za-z0-9]{3,8})", re.IGNORECASE)


def _code_of(m) -> str:
    """提取码：源链 note 里的 '提取码 xx' 优先，其后源链/自有链 URL 的 ?pwd= 兜底"""
    code = _extract_code(m.source_link_note or "")
    if code:
        return code
    for url in (getattr(m, "source_link_url", None), getattr(m, "link_url", None)):
        m2 = _URL_PWD_RE.search(url or "")
        if m2:
            return m2.group(1)
    return ""


def build_harvest_export_csv(rows: list) -> str:
    """物料 ORM/等价对象列表 → CSV 文本（CRLF，不含 BOM；BOM 在写文件时加 utf-8-sig）"""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(EXPORT_HEADERS)
    for m in rows:
        state = _STATE_LABELS.get(harvest_state(m.source_link_url, m.link_url), "未知")
        created = getattr(m, "created_at", None)
        writer.writerow([
            m.id,
            m.title or "",
            m.content or "",
            m.source_fname or "",
            m.source_link_url or "",
            _code_of(m),
            m.source_link_type or "",
            m.link_url or "",
            state,
            created.strftime("%Y-%m-%d %H:%M:%S") if created else "",
        ])
    return buf.getvalue()


def export_csv_filename(now=None) -> str:
    """导出文件名：harvest_export_YYYYMMDD_HHMMSS.csv（秒级时间戳，可按名排序）"""
    from datetime import datetime

    now = now or datetime.now()
    return f"harvest_export_{now.strftime('%Y%m%d_%H%M%S')}.csv"
