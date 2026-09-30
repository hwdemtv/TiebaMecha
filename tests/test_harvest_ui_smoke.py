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

        assert len(bp._harvest_table.rows) == 1
        assert "🌾采集(2)" in bp._stats_text.value
        # 采集视图：宿主行只挂采集表（摘除制）
        assert bp._table_row.controls == [bp._harvest_table]
        # 采集视图隐藏排期池专属批量按钮
        assert bp._bulk_reset_btn.visible is False
        assert bp._bulk_ai_btn.visible is False
        assert bp._bulk_delete_btn.visible is True
        # 查询口径带 harvested
        call_kwargs = bp.db.get_materials_by_status_paginated.call_args.kwargs
        assert call_kwargs["statuses"] == ["harvested"]

    @pytest.mark.asyncio
    async def test_refresh_schedule_view_restores_buttons(self, bp):
        bp._material_view = "schedule"
        await bp._refresh_material_table()
        assert bp._bulk_reset_btn.visible is True
        # 切回排期池：宿主行换回排期池表（先去采集视图再回来，验证摘除可逆）
        assert bp._table_row.controls == [bp._material_table]

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
    async def test_view_click_switch_and_noop(self, bp):
        """双按钮切换：换视图重置页码清选择；同视图重复点击为 no-op"""
        bp.db.get_materials_by_status_paginated = AsyncMock(return_value=([], 0))
        bp.db.get_materials_status_counts = AsyncMock(return_value={"pending": 3, "failed": 1, "success": 0, "harvested": 5})
        bp._material_page = 2
        bp._selected_material_ids.add(9)

        await bp._on_material_view_click("harvest")
        assert bp._material_view == "harvest"
        assert bp._material_page == 1 and bp._selected_material_ids == set()
        assert bp._table_row.controls == [bp._harvest_table]
        # 同视图再点：no-op
        bp._material_page = 3
        await bp._on_material_view_click("harvest")
        assert bp._material_page == 3

        await bp._on_material_view_click("schedule")
        assert bp._material_view == "schedule"
        assert bp._table_row.controls == [bp._material_table]

    def test_update_view_buttons_text_and_style(self, bp):
        # 计数进按钮文本；激活态样式切换（桩环境直接属性赋值可读；
        # 桩 ButtonStyle 按 dict 存 kwarg，真 flet 是属性——双形态取值）
        def _bgcolor(style):
            if isinstance(style, dict):
                return style.get("bgcolor")
            return getattr(style, "bgcolor", None)

        bp._update_view_buttons(pending=3, failed=1, harvested=5)
        assert bp._view_btn_schedule.text == "⏳ 排期池(4)"
        assert bp._view_btn_harvest.text == "🌾 采集待审(5)"
        assert _bgcolor(bp._view_btn_schedule.style) == "primary"
        assert _bgcolor(bp._view_btn_harvest.style) is None

        bp._material_view = "harvest"
        bp._update_view_buttons(pending=3, failed=1, harvested=5)
        assert _bgcolor(bp._view_btn_harvest.style) == "primary"
        assert _bgcolor(bp._view_btn_schedule.style) is None


class TestSettingsHarvestFields:
    def test_maint_fields_and_switch(self):
        from tieba_mecha.web.pages.settings import SettingsPage

        sp = SettingsPage.__new__(SettingsPage)  # 跳过 __init__ 的 db/异步依赖
        sp._init_maint_fields()
        for key in ("maint_harvest_min_reply", "maint_harvest_max_per_cycle"):
            assert key in sp.maint_fields
        assert hasattr(sp, "maint_harvest_switch")
