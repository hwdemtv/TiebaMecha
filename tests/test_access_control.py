"""Web 访问控制与导入导出安全回归（2026-11-24 全项目体检整改）

此前的三重失效（均无测试覆盖）：
- 登录页「暂不设置，直接进入」按钮无条件显示，已设密码时点一下即认证成功；
- 认证门只是 nav_rail.visible=False，导航项（含通知铃）的 on_click 仍挂在控件树上，
  _navigate 也没有鉴权检查——未认证客户端凭控件 ID 发点击事件即可加载任意业务页面；
- 上传完成回调直接采信客户端给的 file_name（os.path.join 后 os.remove）、选框结果直接采信
  客户端给的 path 读盘导入；下载路由文件名可枚举且不验令牌。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 全套跑批时后收集的模块可能把 sys.modules["flet"] 换成桩：本文件按字母序最先收集，
# 在模块顶层导入被测对象，保证它们绑定到真实 flet
import tieba_mecha.core.web_auth as web_auth  # noqa: E402
import tieba_mecha.web.downloads as dl  # noqa: E402
from tieba_mecha.web.app import TiebaMechaApp, _authenticated_sessions  # noqa: E402
from tieba_mecha.web.import_safety import is_importable_local_file, is_safe_upload_name  # noqa: E402
from tieba_mecha.web.pages import login as login_mod  # noqa: E402
from tieba_mecha.web.pages.login import LoginPage  # noqa: E402
from tieba_mecha.web.pages.batch_post_page import BatchPostPage  # noqa: E402
from tieba_mecha.web.pages.posts.batch_ops import BatchOpsTabMixin  # noqa: E402


class _FakePage:
    def __init__(self, session_id="sess-test"):
        self.session_id = session_id
        self.controls = []
        self.overlay = []
        self.window = SimpleNamespace(width=0, height=0, min_width=0, min_height=0)
        self.update = MagicMock()
        self.open = MagicMock()
        self.close = MagicMock()
        self.run_task = MagicMock()
        self.launch_url = MagicMock()
        self.show_snack_bar = MagicMock()
        self.web = None
        self.add = MagicMock(side_effect=lambda *ctrls: self.controls.extend(ctrls))


def iter_controls(root):
    """遍历控件树（只按属性名走，兼容桩控件）"""
    yield root
    for attr in ("controls", "actions"):
        for child in getattr(root, attr, None) or []:
            yield from iter_controls(child)
    content = getattr(root, "content", None)
    if content is not None and not isinstance(content, (str, list)):
        yield from iter_controls(content)


def _stub_db():
    """登录页 load_data 会读 web_password_hash：给个返回空串的库桩"""
    db = MagicMock()
    db.get_setting = AsyncMock(return_value="")
    return db


def buttons_in(tree):
    return [c for c in iter_controls(tree) if getattr(c, "on_click", None) is not None and hasattr(c, "text")]


# ---------- 认证门 ----------

@pytest.fixture
def make_app():
    created = []

    def _make(authenticated=False):
        sid = f"sess-test-{uuid4().hex}"
        if authenticated:
            _authenticated_sessions.add(sid)
        created.append(sid)
        return TiebaMechaApp(_FakePage(sid))

    yield _make
    for sid in created:
        _authenticated_sessions.discard(sid)


def test_unauthenticated_session_builds_no_nav_controls(make_app):
    app = make_app()
    assert app.nav_rail.content.controls == []   # 没有任何带 on_click 的导航项可被伪造点击
    assert app.nav_rail.visible is False
    assert app._divider.visible is False


async def test_unauthenticated_cannot_navigate_to_business_pages(make_app):
    app = make_app()
    fake_page = SimpleNamespace(build=MagicMock(return_value=object()), load_data=AsyncMock())
    app._pages_cache["accounts"] = fake_page

    await app._navigate("accounts")
    fake_page.build.assert_not_called()
    assert app.content_area.content is None
    assert app.current_page == "dashboard"

    # 登录页本身放行
    await app._navigate("login")
    assert app.current_page == "login"
    assert app.content_area.content is not None
    assert app.nav_rail.content.controls == []   # 停留在登录页期间侧栏依旧为空


async def test_authenticated_session_gets_nav_and_can_navigate(make_app):
    app = make_app(authenticated=True)
    app._show_main_ui()
    nav = app.nav_rail.content.controls
    group_titles = sum(1 for title, _ in app._nav_groups if title)
    assert len(nav) == 1 + group_titles + len(app._nav_items)   # 通知铃 + 组标题 + 各页面项
    assert app.nav_rail.visible is True and app._divider.visible is True

    fake_page = SimpleNamespace(build=MagicMock(return_value=object()), load_data=AsyncMock())
    app._pages_cache["accounts"] = fake_page
    await app._navigate("accounts")
    fake_page.build.assert_called_once()
    assert app.current_page == "accounts"


async def test_initialize_with_password_stays_on_login(monkeypatch, make_app):
    monkeypatch.setattr(web_auth, "is_password_set", AsyncMock(return_value=True))
    app = make_app()
    await app.initialize(db=_stub_db())
    assert app.current_page == "login"
    assert app.page.session_id not in _authenticated_sessions
    assert app.nav_rail.content.controls == []


async def test_initialize_without_password_authenticates_then_builds_nav(monkeypatch, make_app):
    monkeypatch.setattr(web_auth, "is_password_set", AsyncMock(return_value=False))
    full_init = AsyncMock()
    monkeypatch.setattr(TiebaMechaApp, "_full_initialize", full_init)
    app = make_app()
    await app.initialize(db=object())
    assert app.page.session_id in _authenticated_sessions
    full_init.assert_awaited_once()
    assert app.nav_rail.visible is True and len(app.nav_rail.content.controls) > 0


async def test_reconnect_of_authenticated_session_shows_nav(make_app):
    """浏览器刷新复用已认证会话：initialize 必须把侧栏重新显示并构建出来"""
    app = make_app(authenticated=True)
    app._full_initialize = AsyncMock()
    assert app.nav_rail.visible is False   # _setup_page 默认隐藏
    await app.initialize(db=object())
    app._full_initialize.assert_awaited_once()
    assert app.nav_rail.visible is True and len(app.nav_rail.content.controls) > 0


# ---------- 登录页 ----------

def _login_page():
    page = _FakePage("sess-login")
    lp = LoginPage(page, db=object(), on_navigate=None)
    lp._show_snackbar = MagicMock()
    return lp


def test_login_page_has_no_skip_button():
    lp = _login_page()
    tree = lp.build()
    texts = [getattr(b, "text", None) for b in buttons_in(tree)]
    assert "暂不设置，直接进入" not in texts
    assert not hasattr(lp, "_on_skip")
    assert texts == ["验证身份"]   # 唯一的按钮就是提交密码


async def test_login_page_wrong_password_does_not_authenticate(monkeypatch):
    monkeypatch.setattr(login_mod, "check_password", AsyncMock(return_value=False))
    lp = _login_page()
    success = AsyncMock()
    lp.set_success_callback(success)
    lp.build()
    lp._is_setup_mode = False
    lp.password_field.value = "wrong-password"
    await lp._on_submit(None)
    success.assert_not_called()


async def test_login_page_correct_password_authenticates(monkeypatch):
    monkeypatch.setattr(login_mod, "check_password", AsyncMock(return_value=True))
    lp = _login_page()
    success = AsyncMock()
    lp.set_success_callback(success)
    lp.build()
    lp._is_setup_mode = False
    lp.password_field.value = "right-password"
    await lp._on_submit(None)
    success.assert_awaited_once()


# ---------- 导入：文件名 / 路径校验 ----------

@pytest.mark.parametrize("name", ["a.txt", "物料.CSV", "2026清单-final_v2.csv"])
def test_safe_upload_name_accepts_plain_import_files(name):
    assert is_safe_upload_name(name) is True


@pytest.mark.parametrize("name", [
    "../../data/tieba_mecha.db", "..\\..\\.env", "/etc/passwd.txt", "C:\\Windows\\win.ini",
    "dir/a.txt", "a.txt/", ".", "..", "", "a.exe", "a.log", "a\x00.txt", "x" * 201, None, 123,
])
def test_safe_upload_name_rejects_path_components_and_other_types(name):
    assert is_safe_upload_name(name) is False


def test_importable_local_file_requires_real_txt_or_csv(tmp_path):
    good = tmp_path / "物料.txt"
    good.write_text("hello", encoding="utf-8")
    assert is_importable_local_file(str(good)) is True
    assert is_importable_local_file(str(tmp_path / "missing.txt")) is False   # 不存在
    other = tmp_path / "notes.md"
    other.write_text("x", encoding="utf-8")
    assert is_importable_local_file(str(other)) is False                      # 非导入格式
    assert is_importable_local_file(".env") is False                          # 相对路径
    assert is_importable_local_file(None) is False


@pytest.fixture
def bp(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setenv("FLET_UPLOAD_DIR", str(upload_dir))
    mock_db = MagicMock()
    mock_db.get_materials_status_counts = AsyncMock(return_value={"pending": 0, "success": 0, "failed": 0, "harvested": 0})
    mock_db.get_materials_by_status_paginated = AsyncMock(return_value=([], 0))
    page = _FakePage("sess-batch")
    page.get_upload_url = MagicMock(return_value="http://127.0.0.1:9006/upload?sig=x")
    page.upload_dir = None
    page_obj = BatchPostPage(page=page, db=mock_db)
    page_obj._show_snackbar = MagicMock()
    page_obj._process_file_import = AsyncMock()
    page_obj._file_picker.upload = MagicMock()
    page_obj._file_picker.pick_files = MagicMock()
    page_obj._upload_dir = upload_dir
    return page_obj


def _result_event(*files):
    return SimpleNamespace(files=[SimpleNamespace(name=n, path=p) for n, p in files], path=files[0][1])


def _upload_event(file_name, progress=1.0, error=None):
    return SimpleNamespace(file_name=file_name, progress=progress, error=error)


async def test_unsolicited_picker_result_is_dropped(bp):
    """没有服务端发起过选择时到达的结果事件（可伪造）一律丢弃"""
    await bp._on_file_result(_result_event(("a.txt", None)))
    bp._file_picker.upload.assert_not_called()
    assert bp._pending_uploads == set()


async def test_web_session_ignores_client_supplied_local_path(bp):
    bp.page.web = True
    bp._pick_import_file()
    await bp._on_file_result(_result_event(("a.txt", "C:/Windows/win.ini")))
    bp._process_file_import.assert_not_called()   # 不读客户端声称的本地路径
    assert bp._pending_uploads == {"a.txt"}        # 走上传流程，仅登记文件名


async def test_desktop_session_rejects_non_importable_path(bp, tmp_path):
    bp.page.web = None
    bp._pick_import_file()
    await bp._on_file_result(_result_event(("x.txt", str(tmp_path / ".env"))))
    bp._process_file_import.assert_not_called()
    assert bp.page.open is not None


async def test_desktop_session_imports_real_local_txt(bp, tmp_path):
    bp.page.web = None
    local = tmp_path / "本机物料.txt"
    local.write_text("第一行\n第二行\n", encoding="utf-8")
    bp._pick_import_file()
    await bp._on_file_result(_result_event((local.name, str(local))))
    bp._process_file_import.assert_awaited_once_with(str(local))


async def test_forged_upload_completion_cannot_touch_files_outside_upload_dir(bp, tmp_path):
    """核心回归：file_name 指向库文件时既不导入也不删除"""
    protected = tmp_path / "data" / "tieba_mecha.db"
    protected.parent.mkdir()
    protected.write_bytes(b"sqlite-format-3\x00fake")

    # 攻击者自行伪造一条「选框结果」来登记上传名
    bp.page.web = True
    bp._pick_import_file()
    await bp._on_file_result(_result_event(("ok.txt", None), ("../data/tieba_mecha.db", None)))
    # 目录穿越的名字在登记阶段就被拒绝
    assert bp._pending_uploads == {"ok.txt"}

    await bp._on_upload_progress(_upload_event("../data/tieba_mecha.db"))
    await bp._on_upload_progress(_upload_event("..\\data\\tieba_mecha.db"))
    await bp._on_upload_progress(_upload_event("tieba_mecha.db"))
    bp._process_file_import.assert_not_called()
    assert protected.read_bytes() == b"sqlite-format-3\x00fake"


async def test_upload_completion_processes_only_pending_name_and_cleans_up(bp):
    bp.page.web = True
    bp._pick_import_file()
    await bp._on_file_result(_result_event(("ok.txt", None)))
    # 未登记的另一个合法名字同样不被处理
    await bp._on_upload_progress(_upload_event("other.txt"))
    bp._process_file_import.assert_not_called()

    target = bp._upload_dir / "ok.txt"
    target.write_text("hello", encoding="utf-8")
    await bp._on_upload_progress(_upload_event("ok.txt", progress=0.5))
    bp._process_file_import.assert_not_called()
    await bp._on_upload_progress(_upload_event("ok.txt", progress=1.0))
    bp._process_file_import.assert_awaited_once_with(str(target))
    assert not target.exists()                    # 处理完的临时文件被清理
    assert bp._pending_uploads == set()           # 用完即弃，不能重放


async def test_upload_error_resets_busy_state(bp):
    bp.page.web = True
    bp._pick_import_file()
    await bp._on_file_result(_result_event(("ok.txt", None)))
    await bp._on_upload_progress(_upload_event("ok.txt", progress=0.3, error="failed"))
    assert bp._pending_uploads == set()
    bp._process_file_import.assert_not_called()


# ---------- 导出 / 下载通道 ----------

@pytest.fixture
def tmp_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "EXPORTS_DIR", tmp_path)
    return tmp_path


def test_download_url_carries_signed_token():
    url = dl.download_url("harvest_export_20261006_093005.csv")
    assert url.startswith("/downloads/harvest_export_20261006_093005.csv?t=")
    assert len(dl.download_token("harvest_export_20261006_093005.csv")) == 32
    # 令牌与文件名绑定，换一个文件名令牌即不同
    assert dl.download_token("harvest_export_20261006_093006.csv") != dl.download_token("harvest_export_20261006_093005.csv")


def test_download_requires_token(tmp_exports):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    dl.register_download_routes(app)
    fname = dl.save_export_file("a,b\r\n", "harvest_export_20261006_093005.csv")
    client = TestClient(app)

    assert client.get(f"/downloads/{fname}").status_code == 403                      # 无令牌
    assert client.get(f"/downloads/{fname}?t={'0' * 32}").status_code == 403          # 错令牌
    assert client.get(f"/downloads/{fname}?t=中文").status_code == 403                # 非法字符
    # 令牌不对时连「文件是否存在」都探不到（不是 404 而是 403）
    assert client.get("/downloads/harvest_export_20990101_000000.csv").status_code == 403

    r = client.get(dl.download_url(fname))
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")


def test_download_token_survives_process_key_not_found_env(tmp_exports, monkeypatch):
    """无进程密钥时退化为进程级随机密钥：令牌仍稳定（同进程内两次一致）"""
    monkeypatch.delenv("FLET_SECRET_KEY", raising=False)
    monkeypatch.delenv("TIEBA_MECHA_SECRET_KEY", raising=False)
    assert dl.download_token("posts_export_x.csv") == dl.download_token("posts_export_x.csv")


def test_cleanup_covers_posts_exports_too(tmp_exports):
    old = tmp_exports / "posts_export_20200101_000000.csv"
    old.write_text("old", encoding="utf-8")
    import os
    os.utime(old, (0, 0))
    dl.save_export_file("a,b\r\n", "posts_export_20261006_093005.csv")
    assert not old.exists()


class _ExportHarness(BatchOpsTabMixin):
    def __init__(self, page, rows):
        self.page = page
        self._rows = rows
        self._selected = []
        self.snackbars = []

    def _selected_rows(self):
        return list(self._selected)

    def _show_snackbar(self, message, type="info"):
        self.snackbars.append((message, type))

    def _account_display(self, account_id):
        return f"acc{account_id}"


def _row(tid=1):
    return SimpleNamespace(tid=tid, title=f"标题{tid}", account_id=7, fname="测试吧", reply_num=3,
                           post_time=None, survival_status="alive", death_reason=None,
                           last_checked_at=None, is_good=False, content="正文内容")


async def test_web_export_ignores_client_path_and_offers_download(tmp_exports, monkeypatch):
    victim = tmp_exports.parent / "victim.py"
    victim.write_text("original", encoding="utf-8")

    page = _FakePage("sess-posts")
    page.web = True
    h = _ExportHarness(page, [_row(1), _row(2)])
    await h._on_export(None)

    written = list(tmp_exports.glob("posts_export_*.csv"))
    assert len(written) == 1
    assert "标题1" in written[0].read_text(encoding="utf-8-sig")
    page.open.assert_called_once()                 # 弹窗给出下载入口
    assert not getattr(h, "_export_picker", None)  # Web 端不挂保存框

    # 即便客户端伪造保存结果（指向任意路径），没有刚发起的保存框就一律丢弃
    await h._on_export_result(SimpleNamespace(path=str(victim)))
    assert victim.read_text(encoding="utf-8") == "original"


async def test_export_result_without_initiated_save_is_dropped(tmp_exports, tmp_path):
    target = tmp_path / "evil.csv"
    h = _ExportHarness(_FakePage("sess-posts"), [_row()])
    h._export_rows = [_row()]
    await h._on_export_result(SimpleNamespace(path=str(target)))
    assert not target.exists()


async def test_desktop_export_accepts_result_only_right_after_save_dialog(tmp_exports, tmp_path):
    target = tmp_path / "out" / "导出.csv"
    h = _ExportHarness(_FakePage("sess-posts"), [_row(5)])
    h._export_rows = [_row(5)]
    h._export_save_pending = True
    await h._on_export_result(SimpleNamespace(path=str(target)))
    assert "标题5" in target.read_text(encoding="utf-8-sig")
    assert h._export_save_pending is False
    # 同一条结果重放不再生效
    target.unlink()
    await h._on_export_result(SimpleNamespace(path=str(target)))
    assert not target.exists()
