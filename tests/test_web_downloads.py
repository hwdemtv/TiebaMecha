"""物料导出下载通道测试：文件名白名单/落盘 BOM/过期清理/路由附件响应"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tieba_mecha.web.downloads as dl


@pytest.fixture
def tmp_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "EXPORTS_DIR", tmp_path)
    return tmp_path


def test_safe_fname_whitelist():
    assert dl.is_safe_export_fname("harvest_export_20261006_093005.csv")
    assert not dl.is_safe_export_fname("../tieba_mecha.db")   # 目录穿越
    assert not dl.is_safe_export_fname("sub/dir.csv")         # 含路径分隔
    assert not dl.is_safe_export_fname("")                    # 空
    assert not dl.is_safe_export_fname("x.py")                # 可执行后缀
    assert not dl.is_safe_export_fname(".hidden")             # 隐藏文件


def test_save_export_file_bom_and_cleanup(tmp_exports):
    old = tmp_exports / "harvest_export_20200101_000000.csv"
    old.write_text("old", encoding="utf-8")
    os.utime(old, (0, 0))  # mtime 置为纪元 → 过期
    fname = dl.save_export_file("id,标题\r\n1,测试\r\n", "harvest_export_20261006_093005.csv")
    raw = (tmp_exports / fname).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # utf-8-sig BOM，Excel 直开不乱码
    assert not old.exists()                 # 超过 24h 的历史导出被顺手清理


def test_save_export_file_rejects_bad_name(tmp_exports):
    with pytest.raises(ValueError):
        dl.save_export_file("x", "../evil.csv")


def test_download_routes(tmp_exports):
    app = FastAPI()
    dl.register_download_routes(app)
    fname = dl.save_export_file("a,b\r\n", "harvest_export_20261006_093005.csv")
    client = TestClient(app)

    r = client.get(f"/downloads/{fname}")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.headers["content-type"].startswith("text/csv")

    # 越界/可执行后缀 → 400；不存在 → 404（提示重新导出）
    assert client.get("/downloads/..%2Ftieba_mecha.db").status_code in (400, 404)
    assert client.get("/downloads/x.py").status_code == 400
    assert client.get("/downloads/harvest_export_20990101_000000.csv").status_code == 404


def test_download_route_not_shadowed_by_flet_static_mount(tmp_exports):
    """真实 flet app 工厂回归：flet 把 FletStaticFiles mount 在 "/" 且按注册顺序匹配，
    下载路由若注册在 mount 之后会被整个吞掉——register_download_routes 必须插队首。"""
    from flet.fastapi import app as flet_app

    fa = flet_app(session_handler=lambda page: None)
    dl.register_download_routes(fa)
    fname = dl.save_export_file("x,y\r\n", "harvest_export_20261006_093005.csv")
    client = TestClient(fa)

    r = client.get(f"/downloads/{fname}")
    assert r.status_code == 200, "下载路由被 flet 静态 mount 遮蔽"
    assert "attachment" in r.headers.get("content-disposition", "")
    # flet 自有静态资源不受影响（首页可服务）
    assert client.get("/").status_code == 200
