"""UI 冒烟：物料池采集视图 + 养号设置采集行的构建与联动（不发真请求）"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


class _FakePage:
    def __init__(self):
        self.run_task = MagicMock()
        self.open = MagicMock()
        self.close = MagicMock()
        self.launch_url = MagicMock()
        self.web = None  # 桌面模式；web 模式测试里置 object()
        self.overlay = []


def _harvested_material(**over):
    base = dict(
        id=101, title="4K修复版合集整理", content="楼主正文",
        status="harvested", ai_status="none",
        source_tid=888, source_fname="电影吧",
        source_link_url="https://pan.baidu.com/s/1H?pwd=hh99",
        source_link_note="提取码 hh99", source_link_type="baidu",
        link_url=None, last_error=None, posted_account_id=None, posted_fname=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def bp():
    from tieba_mecha.web.pages.batch_post_page import BatchPostPage

    mock_db = MagicMock()
    mock_db.get_materials_status_counts = AsyncMock(return_value={"pending": 3, "success": 1, "failed": 0, "harvested": 2})
    mock_db.get_materials_by_status_paginated = AsyncMock(return_value=([], 0))
    mock_db.promote_harvested = AsyncMock(return_value=(True, "已放行至待发池"))
    page = BatchPostPage(page=_FakePage(), db=mock_db)
    return page


class TestHarvestViewControls:
    def test_controls_exist_after_init(self, bp):
        # 注：全套跑批时 flet 可能被先行模块装桩，断言只用桩控件也有的属性（value/visible）
        # （构造 kwarg 如 visible=False 桩控件不回读，初始隐藏由刷新测试的切换行为覆盖）
        assert bp._material_view == "schedule"
        # 并排双视图按钮存在
        assert bp._view_btn_schedule is not None and bp._view_btn_harvest is not None
        # 表格宿主行存在（桩控件不回读 controls 构造 kwarg，初始内容由排期池刷新测试覆盖）
        assert bp._table_row is not None
        # 批量按钮引用化：四个引用都在（刷新测试会实际读写这些属性）
        for btn in (bp._bulk_delete_btn, bp._bulk_reset_btn, bp._bulk_bump_btn, bp._bulk_ai_btn):
            assert btn is not None
        assert bp._material_bulk_actions is not None

    @pytest.mark.asyncio
    async def test_refresh_switches_to_harvest_view(self, bp):
        mat = _harvested_material()
        bp.db.get_materials_by_status_paginated = AsyncMock(return_value=([mat], 1))
        bp._material_view = "harvest"

        await bp._refresh_material_table()

        assert len(bp._material_table.rows) == 1
        assert "🌾采集(2)" in bp._stats_text.value
        # 静态单表：rows 数据填充（列结构永不改动，flet 动态换表 patch 不可靠）
        # 采集视图隐藏排期池专属批量按钮
        assert bp._bulk_reset_btn.visible is False
        assert bp._bulk_ai_btn.visible is False
        assert bp._bulk_delete_btn.visible is True
        # 查询口径带 harvested
        call_kwargs = bp.db.get_materials_by_status_paginated.call_args.kwargs
        assert call_kwargs["statuses"] == ["harvested"]
        # 表头文案随视图联动（同一列两视图语义不同：采集=别人的原链，排期池=自有链）
        assert bp._col_source_label.value == "来源吧"
        assert bp._col_link_label.value == "原链(悬停看提取码)"

    @pytest.mark.asyncio
    async def test_refresh_schedule_view_restores_buttons(self, bp):
        bp._material_view = "schedule"
        await bp._refresh_material_table()
        assert bp._bulk_reset_btn.visible is True
        # 排期池视图：同一张表填排期池行（切换=换数据不换结构）
        assert bp._material_table.rows == []
        # 表头切回排期池口径（自有链≠原链）
        assert bp._col_source_label.value == "来源"
        assert bp._col_link_label.value == "网盘链接(楼中楼首评)"

    def test_harvest_row_states_pure(self, bp):
        # 行状态判定抽成纯函数（不依赖 flet 控件内部结构，桩环境下同样可测）
        from tieba_mecha.core.harvest import (
            HARVEST_STATE_CONTENT_ONLY,
            HARVEST_STATE_PENDING_TRANSFER,
            HARVEST_STATE_TRANSFERRED,
            harvest_state,
        )

        assert harvest_state("https://pan.baidu.com/s/1H", None) == HARVEST_STATE_PENDING_TRANSFER
        assert harvest_state("https://pan.baidu.com/s/1H", "https://pan.baidu.com/s/1OWN") == HARVEST_STATE_TRANSFERRED
        assert harvest_state(None, None) == HARVEST_STATE_CONTENT_ONLY
        assert harvest_state("", "https://pan.baidu.com/s/1OWN") == HARVEST_STATE_CONTENT_ONLY
        # 行构造在桩控件下也不得抛异常（字段映射/图标名错误会在这里炸）
        bp._build_harvest_row(_harvested_material())
        bp._build_harvest_row(_harvested_material(link_url="https://pan.baidu.com/s/1OWN?pwd=wn22"))
        bp._build_harvest_row(_harvested_material(source_link_url="", source_link_type=None))

    @pytest.mark.asyncio
    async def test_approve_calls_promote_gate(self, bp):
        await bp._on_harvest_approve(101, allow_no_link=False)
        bp.db.promote_harvested.assert_awaited_once_with(101, allow_no_link=False)

    @pytest.mark.asyncio
    async def test_select_all_scope_follows_view(self, bp):
        bp.db.get_material_ids_by_status = AsyncMock(return_value=[101, 102])
        bp._material_view = "harvest"
        e = SimpleNamespace(data="true")
        await bp._on_material_select_all(e)
        kwargs = bp.db.get_material_ids_by_status.call_args.kwargs
        assert kwargs["statuses"] == ["harvested"]

        bp._material_view = "schedule"
        await bp._on_material_select_all(SimpleNamespace(data="false"))
        assert bp._selected_material_ids == set()

    @pytest.mark.asyncio
    async def test_export_button_visibility_follows_view(self, bp):
        # 导出按钮是采集视图专属：刷新联动可见性（桩控件走属性赋值断言）
        mat = _harvested_material()
        bp.db.get_materials_by_status_paginated = AsyncMock(return_value=([mat], 1))

        bp._material_view = "harvest"
        await bp._refresh_material_table()
        assert bp._export_harvest_btn.visible is True

        bp._material_view = "schedule"
        await bp._refresh_material_table()
        assert bp._export_harvest_btn.visible is False

    @pytest.mark.asyncio
    async def test_export_click_writes_file_and_opens_dialog(self, bp, tmp_path, monkeypatch):
        import tieba_mecha.web.downloads as dl

        monkeypatch.setattr(dl, "EXPORTS_DIR", tmp_path)
        mat = _harvested_material()
        bp.db.get_materials_for_export = AsyncMock(return_value=[mat])
        bp.page.web = object()  # web 模式 → 弹窗带下载按钮（点击才 launch_url，此处只验处理器本身）

        await bp._on_export_harvest_click(None)

        bp.page.open.assert_called_once()
        written = list(tmp_path.glob("harvest_export_*.csv"))
        assert len(written) == 1
        text = written[0].read_text(encoding="utf-8-sig")
        assert "源链" in text and mat.source_link_url in text

    @pytest.mark.asyncio
    async def test_export_click_empty_shows_warning_no_dialog(self, bp):
        bp.db.get_materials_for_export = AsyncMock(return_value=[])
        await bp._on_export_harvest_click(None)
        bp.page.open.assert_not_called()

    @pytest.mark.asyncio
    async def test_view_click_switch_and_noop(self, bp):
        """双按钮切换：换视图重置页码清选择；同视图重复点击为 no-op"""
        bp.db.get_materials_by_status_paginated = AsyncMock(return_value=([], 0))
        bp.db.get_materials_status_counts = AsyncMock(return_value={"pending": 3, "failed": 1, "success": 0, "harvested": 5})
        bp._material_page = 2
        bp._selected_material_ids.add(9)

        await bp._on_material_view_click("harvest")
        assert bp._material_view == "harvest"
        assert bp._material_page == 1 and bp._selected_material_ids == set()
        # 静态单表：切换只换 rows（采集 0 条 → 空 rows）
        assert bp._material_table.rows == []
        # 同视图再点：no-op
        bp._material_page = 3
        await bp._on_material_view_click("harvest")
        assert bp._material_page == 3

        await bp._on_material_view_click("schedule")
        assert bp._material_view == "schedule"

    def test_update_view_buttons_text_and_state(self, bp):
        # 计数+✓前缀进按钮文本；激活态 opacity 明暗切换
        # （0.23.2 按钮 bgcolor/color 构造参数不存在会 build 即炸——本测试同时咬住"不许再用"）
        bp._update_view_buttons(pending=3, failed=1, harvested=5)
        assert bp._view_btn_schedule.text == "✓ ⏳ 排期池(4)"
        assert bp._view_btn_schedule.opacity == 1.0
        assert bp._view_btn_harvest.text == "🌾 采集待审(5)"
        assert bp._view_btn_harvest.opacity == 0.5

        bp._material_view = "harvest"
        bp._update_view_buttons(pending=3, failed=1, harvested=5)
        assert bp._view_btn_harvest.text == "✓ 🌾 采集待审(5)"
        assert bp._view_btn_harvest.opacity == 1.0
        assert bp._view_btn_schedule.opacity == 0.5

    def test_view_buttons_build_kwargs_valid(self):
        """构造参数必须是 flet 0.23.2 按钮真实存在的 kwarg（bgcolor 事故回归）"""
        import inspect
        import flet as ft

        valid = set(inspect.signature(ft.FilledButton.__init__).parameters)
        valid |= set(inspect.signature(ft.OutlinedButton.__init__).parameters)
        for kw in ("bgcolor", "color"):
            assert kw not in valid, f"若 {kw} 已成为合法参数可解除本断言，但当前传入即 build 炸"


class TestSettingsHarvestFields:
    def test_maint_fields_and_switch(self):
        from tieba_mecha.web.pages.settings import SettingsPage

        sp = SettingsPage.__new__(SettingsPage)  # 跳过 __init__ 的 db/异步依赖
        sp._init_maint_fields()
        for key in ("maint_harvest_min_reply", "maint_harvest_min_agree",
                    "maint_harvest_max_per_cycle", "maint_harvest_max_age_days"):
            assert key in sp.maint_fields
        assert hasattr(sp, "maint_harvest_switch")
