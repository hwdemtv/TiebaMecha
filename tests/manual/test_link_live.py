"""链接存活性检测的真网实测（手工用例，不在常规门禁内）。

会真实请求第三方网盘：验证 link_check 对无效分享链的判死逻辑在真实站点上仍然成立。
常规门禁以 `pytest --ignore=tests/manual` 运行，不会执行本文件；
需要时手动执行：pytest tests/manual/test_link_live.py

断网/风控环境下 check_link_alive fail-open 为未知，两种结果都接受（不脆）。
"""

import pytest


@pytest.mark.asyncio
async def test_check_link_alive_real_quark_dead():
    """真网实测（2026-11-03）：夸克假分享 id 走 token API 判死。"""
    from tieba_mecha.core.link_check import check_link_alive

    v = await check_link_alive("https://pan.quark.cn/s/fake123abcde", "quark", timeout=8)
    if v.alive is None:
        assert "检测异常" in v.detail or "未知" in v.detail  # 断网等环境问题：fail-open
    else:
        assert v.alive is False


@pytest.mark.asyncio
async def test_check_link_alive_real_baidu_dead():
    """真网实测（2026-10-06）：百度无效分享链返回 404 → 判死。"""
    from tieba_mecha.core.link_check import check_link_alive

    v = await check_link_alive("https://pan.baidu.com/s/1zzZZzzZZzz-ZzZZzZZzZZz", "baidu", timeout=8)
    if v.alive is None:
        assert "检测异常" in v.detail  # 断网等环境问题：fail-open
    else:
        assert v.alive is False
