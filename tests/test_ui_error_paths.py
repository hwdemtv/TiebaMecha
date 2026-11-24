"""界面处理器失败路径回归（2026-11-24 全项目体检整改）

两处 NameError 长期无人发现，共同点是 flet 处理器里的异常不会自动冒泡到界面：
- 账号页「智能权重计算」：import 被留在包装方法里，使用它的平级方法一点击即 NameError，
  自 2026-09-16（7abb103）起两个入口静默失效两个多月；
- 发帖页「保存自顶配置」：except 分支里的 log_error 没导入，保存失败时错误处理自身崩溃，
  用户看不到「保存配置失败」。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 模块顶层导入：保证被测页面绑定到真实 flet（见 test_access_control 的说明）
from tieba_mecha.web.pages import accounts as accounts_mod  # noqa: E402
from tieba_mecha.web.pages.batch_post_page import BatchPostPage  # noqa: E402


class _FakePage:
    def __init__(self):
        self.controls = []
        self.overlay = []
        self.update = MagicMock()
        self.open = MagicMock()
        self.close = MagicMock()
        self.run_task = MagicMock()


def _texts(root, out=None):
    out = [] if out is None else out
    if isinstance(getattr(root, "value", None), str):
        out.append(root.value)
    for attr in ("controls", "actions"):
        for child in getattr(root, attr, None) or []:
            _texts(child, out)
    content = getattr(root, "content", None)
    if content is not None and not isinstance(content, (str, list)):
        _texts(content, out)
    return out


def _accounts_page(db):
    page = _FakePage()
    ap = accounts_mod.AccountsPage(page=page, db=db)
    ap._show_snackbar = MagicMock()
    ap.load_data = AsyncMock()
    return ap, page


async def _seed(db, name="w_acc", weight=5):
    from tieba_mecha.core.account import add_account

    acc = await add_account(db=db, name=name, bduss="a" * 192, stoken="b" * 64)
    await db.add_forum(fid=1, fname="weight_forum", account_id=acc.id)
    if weight != 5:
        await db.update_account(acc.id, post_weight=weight)
    return acc


async def test_auto_calculate_weights_full_recalc_runs_end_to_end(db):
    """菜单「全量重算」：真实计算器跑通，弹出报告，忙标志复位"""
    acc = await _seed(db)
    ap, page = _accounts_page(db)
    assert ap._busy is False

    await ap._auto_calculate_weights(None, False)   # 修复前：此处抛 NameError

    page.open.assert_called_once()
    report = "\n".join(_texts(page.open.call_args[0][0]))
    assert "权重智能计算完成" in report and "共分析 1 个账号" in report
    assert ap._busy is False
    acc_after = await db.get_account(acc.id)
    assert acc_after.post_weight is not None
    assert "智能权重计算失败" not in " ".join(str(c.args[0]) for c in ap._show_snackbar.call_args_list)


async def test_auto_calculate_weights_incremental_recalc_runs(db):
    """菜单「增量计算」入口同样可用"""
    await _seed(db)
    ap, page = _accounts_page(db)
    await ap._auto_calculate_weights(None, True)
    page.open.assert_called_once()
    assert "权重智能计算完成" in "\n".join(_texts(page.open.call_args[0][0]))


async def test_auto_calculate_weights_failure_is_shown_not_swallowed(db, monkeypatch):
    """计算过程抛异常：用户看得到失败提示，忙标志复位（可重试）"""
    await _seed(db)
    ap, page = _accounts_page(db)

    async def _boom(self, e, incremental=False):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(accounts_mod.AccountsPage, "_auto_calculate_weights_impl", _boom)
    await ap._auto_calculate_weights(None, False)   # 不向 flet 冒泡

    page.open.assert_not_called()
    msgs = [str(c.args[0]) for c in ap._show_snackbar.call_args_list]
    assert any("智能权重计算失败" in m and "database is locked" in m for m in msgs)
    assert ap._busy is False


async def test_auto_calculate_weights_rejects_overlapping_run(db):
    await _seed(db)
    ap, page = _accounts_page(db)
    ap._busy = True
    await ap._auto_calculate_weights(None, False)
    page.open.assert_not_called()
    assert ap._busy is True   # 不动别人正在进行的操作
    assert "已有操作正在执行" in str(ap._show_snackbar.call_args.args[0])


async def test_save_bump_config_failure_reaches_the_user():
    """保存自顶配置失败：except 分支自己不再崩溃，「保存配置失败」提示能送达"""
    mock_db = MagicMock()
    mock_db.get_materials_status_counts = AsyncMock(return_value={"pending": 0, "success": 0, "failed": 0, "harvested": 0})
    mock_db.get_materials_by_status_paginated = AsyncMock(return_value=([], 0))
    bp = BatchPostPage(page=_FakePage(), db=mock_db)
    bp._show_snackbar = MagicMock()

    await bp._save_bump_config(None)   # 页面尚未 _init_controls：读取输入框即失败，走 except 分支。
                                       # 修复前这里抛 NameError: log_error

    msgs = [str(c.args[0]) for c in bp._show_snackbar.call_args_list]
    assert any(m.startswith("保存配置失败") for m in msgs)


# ---------- 火力配置弹窗：空降集合 ----------

def _batch_page(db):
    from unittest.mock import MagicMock

    mock_db = db if db is not None else MagicMock()
    bp = BatchPostPage(page=_FakePage(), db=mock_db)
    bp._show_snackbar = MagicMock()
    return bp


async def test_parachute_set_is_computed_with_forum_names():
    """所选账号都没关注的本地吧 = 空降。修复前漏传吧名列表，TypeError 被吞，恒为空集"""
    calls = []

    async def _followed(account_ids, fnames):
        calls.append((account_ids, fnames))             # 真实签名：必须带吧名列表
        return ["A", "C"]

    bp = _batch_page(MagicMock())
    bp.db.get_fnames_followed_by_accounts = _followed
    bp._selected_account_ids = {2, 1}
    local = [{"fname": "A"}, {"fname": "B"}, {"fname": "C"}]

    assert await bp._compute_parachute_fnames(local) == {"B"}
    assert calls == [([1, 2], ["A", "B", "C"])]


async def test_parachute_set_empty_without_selected_accounts():
    bp = _batch_page(MagicMock())
    bp.db.get_fnames_followed_by_accounts = AsyncMock(side_effect=AssertionError("不应查询"))
    bp._selected_account_ids = set()

    assert await bp._compute_parachute_fnames([{"fname": "A"}]) == set()


async def test_parachute_set_matches_preflight_semantics_end_to_end(db):
    """与预检同一口径：所选账号中只要有人关注就不算空降"""
    from tieba_mecha.core.account import add_account

    acc1 = await add_account(db=db, name="p1", bduss="a" * 192, stoken="b" * 64, verify=False)
    acc2 = await add_account(db=db, name="p2", bduss="c" * 192, stoken="d" * 64, verify=False)
    await db.add_forum(fid=701, fname="甲", account_id=acc1.id)
    await db.add_forum(fid=702, fname="乙", account_id=acc2.id)
    await db.add_forum(fid=703, fname="丙", account_id=acc1.id)
    await db.add_forum(fid=704, fname="丁", account_id=999)           # 999 不在所选账号里

    bp = _batch_page(db)
    local = await db.get_all_unique_forums()
    bp._selected_account_ids = {acc1.id, acc2.id}
    both = await bp._compute_parachute_fnames(local)

    bp._selected_account_ids = {acc1.id}
    only1 = await bp._compute_parachute_fnames(local)

    assert both == {"丁"}                        # 甲丙账号1关注、乙账号2关注 → 只剩丁是空降
    assert only1 == {"乙", "丁"}                 # 只选账号 1 时，乙也成了空降


async def test_parachute_query_failure_is_logged_not_silently_empty():
    bp = _batch_page(MagicMock())
    bp._selected_account_ids = {1}

    async def _boom(*args):
        raise RuntimeError("database is locked")

    bp.db.get_fnames_followed_by_accounts = _boom
    log_error = AsyncMock()
    with patch("tieba_mecha.core.logger.log_error", log_error):
        assert await bp._compute_parachute_fnames([{"fname": "A"}]) == set()

    assert "空降集合计算失败" in str(log_error.await_args.args[0])
