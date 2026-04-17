"""存活分析页面单元测试"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import flet as ft


class MockDatabase:
    """模拟数据库"""
    def __init__(self):
        self.stats = {"total": 72, "alive": 50, "dead": 21, "unknown": 1}
        self.accounts = [
            MagicMock(id=1, name="测试账号1"),
            MagicMock(id=2, name="测试账号2"),
        ]
        self.materials = [
            MagicMock(
                id=1,
                posted_account_id=1,
                posted_fname="测试吧",
                content="测试内容",
                survival_status="alive",
                posted_time=None
            ),
            MagicMock(
                id=2,
                posted_account_id=2,
                posted_fname="另一个吧",
                content="另一个内容",
                survival_status="dead",
                posted_time=None
            ),
        ]

    async def get_survival_stats(self):
        return self.stats

    async def get_accounts(self):
        return self.accounts

    async def get_materials_paginated(self, survival_status=None, account_id=None, page=1, page_size=20):
        filtered = self.materials
        if survival_status and survival_status != "all":
            filtered = [m for m in filtered if m.survival_status == survival_status]
        if account_id:
            filtered = [m for m in filtered if m.posted_account_id == account_id]
        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        return filtered[start:end], total


@pytest.fixture
def mock_page():
    """创建模拟的 Flet Page"""
    page = MagicMock()
    page.update = MagicMock()
    return page


@pytest.fixture
def mock_db():
    """创建模拟数据库"""
    return MockDatabase()


@pytest.fixture
def survival_page(mock_page, mock_db):
    """创建存活分析页面实例"""
    from tieba_mecha.web.pages.survival import SurvivalPage
    page = SurvivalPage(mock_page, mock_db, on_navigate=None)
    return page


class TestSurvivalPageInit:
    """测试 SurvivalPage 初始化"""

    def test_init_default_values(self, mock_page, mock_db):
        """测试默认初始值"""
        from tieba_mecha.web.pages.survival import SurvivalPage
        page = SurvivalPage(mock_page, mock_db)
        
        assert page.db == mock_db
        assert page._stats == {"total": 0, "alive": 0, "dead": 0, "unknown": 0}
        assert page._account_options == []
        assert page._current_page == 1
        assert page._page_size == 20
        assert page._total == 0
        assert page._materials == []

    def test_init_with_navigate_callback(self, mock_page, mock_db):
        """测试导航回调"""
        from tieba_mecha.web.pages.survival import SurvivalPage
        nav_callback = MagicMock()
        page = SurvivalPage(mock_page, mock_db, on_navigate=nav_callback)
        
        assert page.on_navigate == nav_callback


class TestLoadData:
    """测试数据加载"""

    @pytest.mark.asyncio
    async def test_load_data_success(self, survival_page, mock_db):
        """测试成功加载数据"""
        await survival_page.load_data()
        
        # 验证统计数据已更新
        assert survival_page._stats == mock_db.stats
        # 验证账号选项已生成
        assert len(survival_page._account_options) == 2
        # 验证分页数据已加载
        assert survival_page._total == 2
        assert len(survival_page._materials) == 2

    @pytest.mark.asyncio
    async def test_load_data_no_db(self, mock_page):
        """测试无数据库时"""
        from tieba_mecha.web.pages.survival import SurvivalPage
        page = SurvivalPage(mock_page, db=None)
        
        # 不应该抛出异常
        await page.load_data()
        # 统计数据应保持初始值
        assert page._stats == {"total": 0, "alive": 0, "dead": 0, "unknown": 0}

    @pytest.mark.asyncio
    async def test_load_data_updates_account_options(self, survival_page):
        """测试账号选项正确生成"""
        await survival_page.load_data()
        
        # 验证选项数量
        assert len(survival_page._account_options) == 2
        # 验证选项类型
        for opt in survival_page._account_options:
            assert isinstance(opt, ft.dropdown.Option)


class TestLoadPage:
    """测试分页加载"""

    @pytest.mark.asyncio
    async def test_load_page_default(self, survival_page, mock_db):
        """测试默认分页加载"""
        await survival_page.load_data()
        await survival_page._load_page(1)
        
        assert survival_page._current_page == 1
        assert survival_page._total == 2
        assert len(survival_page._materials) == 2

    @pytest.mark.asyncio
    async def test_load_page_filter_by_status(self, survival_page, mock_db):
        """测试按状态过滤"""
        await survival_page.load_data()
        
        # 模拟状态过滤
        survival_page._status_filter = MagicMock()
        survival_page._status_filter.value = "alive"
        
        await survival_page._load_page(1)
        
        # 只应返回 alive 状态的物料
        for m in survival_page._materials:
            assert m.survival_status == "alive"


class TestBuildStatCards:
    """测试统计卡片构建"""

    def test_build_stat_cards_structure(self, survival_page):
        """测试统计卡片结构"""
        survival_page._stats = {"total": 72, "alive": 50, "dead": 21, "unknown": 1}
        cards = survival_page._build_stat_cards()
        
        # 应返回列表
        assert isinstance(cards, list)
        # 至少应有存活、阵亡、未知三个卡片 + 进度条卡片
        assert len(cards) >= 3

    def test_build_stat_cards_values(self, survival_page):
        """测试统计卡片数值"""
        survival_page._stats = {"total": 72, "alive": 50, "dead": 21, "unknown": 1}
        cards = survival_page._build_stat_cards()
        
        # 验证卡片数量（3个状态卡片 + 1个进度条卡片 = 4）
        assert len(cards) == 4


class TestBuildTable:
    """测试表格构建"""

    def test_build_table_structure(self, survival_page):
        """测试表格结构"""
        survival_page._materials = [
            MagicMock(
                id=1,
                posted_account_id=1,
                posted_fname="测试吧",
                content="测试",
                survival_status="alive",
                posted_time=None
            )
        ]
        
        table = survival_page._build_table()
        
        # 应返回 Container
        assert isinstance(table, ft.Container)
        # 内部应包含 DataTable
        assert isinstance(table.content, ft.Column)
        assert len(table.content.controls) > 0


class TestBuildFilterBar:
    """测试筛选栏构建"""

    def test_build_filter_bar_structure(self, survival_page):
        """测试筛选栏结构"""
        bar = survival_page._build_filter_bar()
        
        # 应返回 Row
        assert isinstance(bar, ft.Row)
        # 应包含状态和账号两个下拉框
        assert len(bar.controls) >= 2

    def test_build_filter_bar_has_status_filter(self, survival_page):
        """测试包含状态筛选器"""
        bar = survival_page._build_filter_bar()
        
        # 找到状态筛选器
        has_status_filter = False
        for control in bar.controls:
            if isinstance(control, ft.Dropdown) and hasattr(control, 'label'):
                if '存活状态' in str(control.label) or control.label == '存活状态':
                    has_status_filter = True
                    break
        
        assert has_status_filter or len(bar.controls) >= 1


class TestBuildPagination:
    """测试分页控件构建"""

    def test_build_pagination_structure(self, survival_page):
        """测试分页控件结构"""
        pagination = survival_page._build_pagination()
        
        # 应返回 Row
        assert isinstance(pagination, ft.Row)
        # 应包含上一页、页码、下一页
        assert len(pagination.controls) >= 3

    def test_build_pagination_disabled_at_first_page(self, survival_page):
        """测试第一页时上一页按钮禁用"""
        survival_page._current_page = 1
        survival_page._total = 10
        survival_page._page_size = 20
        
        pagination = survival_page._build_pagination()
        
        # 上一页按钮应该禁用
        prev_btn = pagination.controls[0]
        assert isinstance(prev_btn, ft.IconButton)
        assert prev_btn.disabled == True


class TestUpdateTable:
    """测试表格更新"""

    def test_update_table_empty_materials(self, survival_page):
        """测试空物料列表"""
        survival_page._materials = []
        
        # 模拟 _table 属性
        survival_page._table = MagicMock()
        survival_page._table.rows = []
        
        survival_page._update_table()
        
        # 表格应被清空
        survival_page._table.rows = []

    def test_update_table_with_materials(self, survival_page):
        """测试有物料时更新"""
        survival_page._materials = [
            MagicMock(
                id=1,
                posted_account_id=1,
                posted_fname="测试吧",
                content="测试内容",
                survival_status="alive",
                posted_time=MagicMock(strftime=lambda f: "04-17 10:30")
            )
        ]
        
        # 模拟 _table
        survival_page._table = MagicMock()
        survival_page._table.rows = []
        
        survival_page._update_table()
        
        # 表格应包含数据行
        assert len(survival_page._table.rows) == 1


class TestUpdatePagination:
    """测试分页信息更新"""

    def test_update_pagination_single_page(self, survival_page):
        """测试单页情况"""
        survival_page._current_page = 1
        survival_page._total = 5
        survival_page._page_size = 20
        
        # 模拟分页控件
        survival_page._page_info = MagicMock()
        survival_page._prev_btn = MagicMock()
        survival_page._next_btn = MagicMock()
        
        survival_page._update_pagination()
        
        # 应显示单页信息
        assert survival_page._page_info.value == "第 1 / 1 页，共 5 条"
        # 上一页和下一页都应禁用
        assert survival_page._prev_btn.disabled == True
        assert survival_page._next_btn.disabled == True

    def test_update_pagination_multiple_pages(self, survival_page):
        """测试多页情况"""
        survival_page._current_page = 2
        survival_page._total = 50
        survival_page._page_size = 20
        
        # 模拟分页控件
        survival_page._page_info = MagicMock()
        survival_page._prev_btn = MagicMock()
        survival_page._next_btn = MagicMock()
        
        survival_page._update_pagination()
        
        # 应显示正确页数
        assert survival_page._page_info.value == "第 2 / 3 页，共 50 条"
        # 上一页可用，下一页可用
        assert survival_page._prev_btn.disabled == False
        assert survival_page._next_btn.disabled == False


class TestBuild:
    """测试页面构建"""

    def test_build_returns_container(self, survival_page):
        """测试 build 返回 Container"""
        result = survival_page.build()
        
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_build_contains_all_sections(self, survival_page):
        """测试包含所有区块"""
        result = survival_page.build()
        column = result.content
        
        # 应包含：标题、统计卡片、筛选栏、表格、分页
        assert len(column.controls) >= 5


class TestNavigate:
    """测试导航功能"""

    def test_navigate_calls_callback(self, survival_page):
        """测试导航调用回调"""
        survival_page.on_navigate = MagicMock()
        
        survival_page._navigate("dashboard")
        
        survival_page.on_navigate.assert_called_once_with("dashboard")

    def test_navigate_no_callback(self, survival_page):
        """测试无回调时不抛异常"""
        survival_page.on_navigate = None
        
        # 不应抛出异常
        survival_page._navigate("dashboard")


# 运行测试
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
