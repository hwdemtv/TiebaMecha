"""物料导出下载通道：/downloads/{fname} 静态附件路由（2026-10-06）

flet 的 export_asgi_app 返回 FastAPI 实例，start_web.py 注册本模块的
GET /downloads/{fname} 直接以附件形式送达浏览器（Content-Disposition
attachment，Excel/系统下载对话框接管）。文件只能来自本模块写出的
exports 目录，文件名白名单校验防目录穿越；每次导出顺手清理超 24h 的
历史导出文件。

桌面模式（无 HTTP 服务）没有该路由：导出处理器会检测 page.web，
桌面侧只落盘并提示本地路径。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

# 与 DEFAULT_DB_PATH 同源的相对路径约定（服务端 CWD=部署根目录）
EXPORTS_DIR = Path("data/exports")
EXPORT_TTL_SECONDS = 24 * 3600

# 文件名白名单：本模块生成的 harvest_export_YYYYMMDD_HHMMSS.csv 及同类安全名
_SAFE_FNAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,128}$")

_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".json": "application/json",
}


def is_safe_export_fname(fname: str) -> bool:
    """仅接受本模块命名风格的安全文件名（拒路径穿越/隐藏文件/任意后缀）"""
    if not fname or not _SAFE_FNAME_RE.match(fname):
        return False
    if ".." in fname or fname.endswith((".py", ".sh", ".db")):
        return False
    return EXPORTS_DIR.joinpath(fname).resolve().parent == EXPORTS_DIR.resolve()


def save_export_file(content: str, fname: str) -> str:
    """导出内容落盘（utf-8-sig 带 BOM，Excel 直开不乱码），返回文件名并清理过期文件。

    文件名由调用方生成（采集导出用 harvest_export.export_csv_filename，单一事实源）。
    """
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if not is_safe_export_fname(fname):
        raise ValueError(f"非法导出文件名: {fname}")
    (EXPORTS_DIR / fname).write_text(content, encoding="utf-8-sig", newline="")
    _cleanup_expired()
    return fname


def _cleanup_expired() -> None:
    now = time.time()
    for p in EXPORTS_DIR.glob("harvest_export_*.csv"):
        try:
            if now - p.stat().st_mtime > EXPORT_TTL_SECONDS:
                p.unlink()
        except OSError:
            continue


def register_download_routes(app) -> None:
    """注册 /downloads/{fname} 附件路由。

    ⚠️ 必须插到路由表队首：flet 的 app 工厂在创建时已把 FletStaticFiles
    mount 在 "/"（Starlette 按注册顺序匹配，mount 在后会吞掉一切路径，
    后注册的 API 路由永远轮不到）。插队首则只精确匹配 /downloads/ 前缀，
    不影响 /ws、/upload、/oauth_callback 与静态资源。
    """
    from starlette.routing import Route
    from fastapi.responses import FileResponse, PlainTextResponse

    async def _download(request):
        fname = request.path_params.get("fname", "")
        if not is_safe_export_fname(fname):
            return PlainTextResponse("非法下载文件名", status_code=400)
        path = EXPORTS_DIR / fname
        if not path.is_file():
            return PlainTextResponse(
                "导出文件不存在（超过 24 小时会被自动清理，请重新导出）", status_code=404
            )
        suffix = path.suffix.lower()
        return FileResponse(
            path,
            media_type=_CONTENT_TYPES.get(suffix, "application/octet-stream"),
            filename=fname,
        )

    app.router.routes.insert(0, Route("/downloads/{fname}", _download, methods=["GET"]))
