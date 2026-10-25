"""设置页守护任务页签单表化修复测试（2026-10-25）。

旧形态 7 张卡纵向叠高 ~530px，超出 flet web 页签 ~450px 绘制截断线，
第 7 张卡（应用更新自动检测）自上线起长期隐形；改为 DataTable 单表
（总高 ~320px 稳进安全区），触发/开关收进行内操作列。
"""

from types import SimpleNamespace

import flet as ft

from tieba_mecha.web.pages.settings import SettingsPage


class _FakePage:
    def run_task(self, handler, *args, **kwargs):
        pass


JOB_IDS = [
    "global_sign_job", "global_monitor_job", "batch_post_job",
    "update_check_job", "auth_check_job", "auto_bump_job", "biowarming_job",
]


def make_sp(jobs=None):
    sp = SettingsPage.__new__(SettingsPage)  # 跳过 __init__ 的 db/异步依赖
    sp.page = _FakePage()
    sp.JOB_FUNCS = {jid: None for jid in JOB_IDS}
    if jobs is None:
        jobs = [
            {"id": jid, "name": f"守护{idx}", "next_run": "2026-10-26 06:45:00",
             "status": "运行中" if idx % 2 else "已暂停"}
            for idx, jid in enumerate(JOB_IDS, 1)
        ]
    sp._jobs_info = jobs
    return sp


def collect_texts(control, out=None):
    if out is None:
        out = []
    if isinstance(control, ft.Text):
        out.append(control.value)
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for c in child:
                collect_texts(c, out)
        elif isinstance(child, ft.Control):
            collect_texts(child, out)
    # DataTable 树走 rows → cells → content，不在 controls 里
    rows = getattr(control, "rows", None)
    if isinstance(rows, list):
        for row in rows:
            cells = getattr(row, "cells", None)
            if isinstance(cells, list):
                for cell in cells:
                    collect_texts(cell, out)
    return out


def test_daemon_tab_single_table_contains_all_jobs():
    """7 个守护任务全部落进单表行——含此前隐形的 update_check_job"""
    sp = make_sp()
    controls = sp._build_daemon_cards()
    assert len(controls) == 1
    table = controls[0]
    assert isinstance(table, ft.DataTable)
    assert len(table.rows) == 7
    texts = collect_texts(table)
    for idx in range(1, 8):
        assert f"守护{idx}" in texts


def test_daemon_tab_total_height_within_tab_render_limit():
    """结构性高度断言：表头 + 7×行高上限须低于页签 ~450px 截断线"""
    sp = make_sp()
    table = sp._build_daemon_cards()[0]
    total = table.heading_row_height + len(table.rows) * table.data_row_max_height
    assert total < 450, f"守护表总高 {total}px 超出页签绘制截断线"


def test_daemon_tab_rows_keep_trigger_and_switch():
    """行内操作列保留触发按钮与运行开关（旧卡片的两项操作不回退）"""
    sp = make_sp()
    table = sp._build_daemon_cards()[0]
    for row in table.rows:
        op_cell = row.cells[3]
        buttons = [c for c in op_cell.content.controls if isinstance(c, ft.TextButton)]
        switches = [c for c in op_cell.content.controls if isinstance(c, ft.Switch)]
        assert len(buttons) == 1 and buttons[0].text == "触发"
        assert len(switches) == 1


def test_daemon_tab_stable_order_update_check_row_fourth():
    """行序按 JOB_FUNCS 定义序稳定：乱序注入后 update_check 恒在第 4 行"""
    shuffled = list(reversed(JOB_IDS))
    jobs = [{"id": jid, "name": jid, "next_run": "N/A", "status": "运行中"} for jid in shuffled]
    sp = make_sp(jobs=jobs)
    table = sp._build_daemon_cards()[0]
    first_cell_texts = collect_texts(table.rows[3].cells[0])
    assert any("update_check_job" in t for t in first_cell_texts)


def test_daemon_tab_empty_jobs_placeholder():
    sp = make_sp(jobs=[])
    controls = sp._build_daemon_cards()
    assert len(controls) == 1
    texts = collect_texts(controls[0])
    assert any("暂无守护任务" == t for t in texts)
