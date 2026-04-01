# 短链筛选功能实现方案

## 背景

当短链数量过多时，当前的短链选择弹窗存在以下问题：
- 一次性加载全部短链，性能差
- 无搜索功能，难以快速定位
- 无法区分"已发/未发"状态
- 无法按发帖次数筛选

## 目标

实现基于**发帖状态**和**发帖次数**的短链筛选功能。

---

## 方案概述

### 核心改动

| 文件 | 修改内容 |
|------|---------|
| `models.py` | 添加 `short_code` 字段 |
| `crud.py` | 修改 `add_materials_bulk` 方法 + 数据库迁移 |
| `batch_post_page.py` | 传递 short_code + 筛选 UI |
| `link_manager.py` | 添加带状态查询的方法 |

---

## 详细实现

### 1. MaterialPool 模型扩展

**文件**: `src/tieba_mecha/db/models.py`

```python
class MaterialPool(Base):
    """全局物料池"""
    __tablename__ = "material_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="发送用标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="发送用正文")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="状态: pending/success/failed")
    
    # 新增字段
    short_code: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="关联短码")
    
    # AI 改写相关
    ai_status: Mapped[str] = mapped_column(String(20), default="none", comment="AI改写状态")
    original_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # 时间与错误
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    
    __table_args__ = (
        Index("ix_material_pool_status", "status"),
        Index("ix_material_pool_short_code", "short_code"),  # 新增索引
        Index("ix_material_pool_created_at", "created_at"),
    )
```

### 2. CRUD 方法修改

**文件**: `src/tieba_mecha/db/crud.py`

#### 2.1 数据库迁移

```python
# init_db() 中添加
columns = [
    # ... 现有迁移 ...
    ("short_code", "VARCHAR(20) DEFAULT NULL"),
]
```

#### 2.2 批量添加方法

```python
async def add_materials_bulk(self, pairs: list[tuple[str, str, str | None]]) -> int:
    """
    批量添加物料
    
    Args:
        pairs: [(title, content, short_code), ...]
        short_code 可为 None（手动输入的物料无短码关联）
    
    Returns:
        添加成功的条数
    """
    added = 0
    async with self.async_session() as session:
        result = await session.execute(select(MaterialPool.content))
        existing_contents = set(result.scalars().all())
        
        for title, content, short_code in pairs:
            if content not in existing_contents:
                material = MaterialPool(
                    title=title, 
                    content=content, 
                    short_code=short_code
                )
                session.add(material)
                existing_contents.add(content)
                added += 1
        if added > 0:
            await session.commit()
    return added
```

#### 2.3 短链发帖统计查询

```python
async def get_short_code_post_stats(self) -> dict[str, int]:
    """
    获取所有短链的发帖成功次数统计
    
    Returns:
        {short_code: success_count, ...}
    """
    async with self.async_session() as session:
        from sqlalchemy import func
        result = await session.execute(
            select(
                MaterialPool.short_code,
                func.count(MaterialPool.id)
            )
            .where(MaterialPool.short_code.isnot(None))
            .where(MaterialPool.status == "success")
            .group_by(MaterialPool.short_code)
        )
        return {row[0]: row[1] for row in result.all()}
```

### 3. 短链连接器扩展

**文件**: `src/tieba_mecha/core/link_manager.py`

```python
async def get_shortlinks_with_status(self, db: Database) -> List[Dict]:
    """
    获取带发帖状态的短链列表
    
    Returns:
        [
            {
                'shortCode': 'KJ8F2',
                'seoTitle': 'Python 教程',
                'description': '...',
                'post_count': 3,
                'status': '已发'  # 或 '未发'
            },
            ...
        ]
    """
    # 1. 获取所有短链
    all_links = await self.get_active_shortlinks()
    
    # 2. 获取发帖统计
    post_stats = await db.get_short_code_post_stats()
    
    # 3. 合并状态
    result = []
    for link in all_links:
        code = link.get('shortCode', '')
        post_count = post_stats.get(code, 0)
        
        result.append({
            **link,
            'post_count': post_count,
            'status': '已发' if post_count > 0 else '未发'
        })
    
    return result
```

### 4. UI 筛选弹窗

**文件**: `src/tieba_mecha/web/pages/batch_post_page.py`

#### 4.1 弹窗布局

```
┌─────────────────────────────────────────────────────────────┐
│ 📂 短链资产库                               共 328 条        │
├─────────────────────────────────────────────────────────────┤
│ 🔍 搜索短码或标题...                                         │
├─────────────────────────────────────────────────────────────┤
│ 📊 发帖状态: [全部 328] [未发 245] [已发 83]                  │
├─────────────────────────────────────────────────────────────┤
│ ┌────┬──────────┬────────────────────┬────────┬─────────┐  │
│ │ ☐  │ 短码     │ 标题               │ 状态   │ 发帖次数│  │
│ ├────┼──────────┼────────────────────┼────────┼─────────┤  │
│ │ ☐  │ KJ8F2   │ Python 教程包      │ ⏳未发 │ 0       │  │
│ │ ☐  │ ABC123  │ 设计素材合集       │ ✅已发 │ 3       │  │
│ │ ☐  │ XYZ789  │ 学习资料分享       │ ✅已发 │ 12      │  │
│ │ ...│ ...      │ ...                │ ...    │ ...     │  │
│ └────┴──────────┴────────────────────┴────────┴─────────┘  │
├─────────────────────────────────────────────────────────────┤
│ 已选: 0 条    [全选当前] [反选] [清空]   [取消] [确认注入]   │
└─────────────────────────────────────────────────────────────┘
```

#### 4.2 核心代码结构

```python
async def _open_shortlink_dialog(self, e):
    """显示短链选择对话框 (带筛选功能)"""
    
    # 获取带状态的短链列表
    links = await self.connector.get_shortlinks_with_status(self.db)
    
    # 筛选状态
    self._filter_status = "all"  # all / posted / unposted
    
    # 搜索关键词
    self._search_keyword = ""
    
    # UI 组件
    search_field = ft.TextField(
        label="搜索短码或标题",
        on_change=self._on_search_change,
    )
    
    filter_chips = ft.Row([
        ft.Chip("全部", on_click=lambda e: self._set_filter("all")),
        ft.Chip("未发", on_click=lambda e: self._set_filter("unposted")),
        ft.Chip("已发", on_click=lambda e: self._set_filter("posted")),
    ])
    
    # DataTable
    self._link_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("☐")),  # 全选
            ft.DataColumn(ft.Text("短码")),
            ft.DataColumn(ft.Text("标题")),
            ft.DataColumn(ft.Text("状态")),
            ft.DataColumn(ft.Text("发帖次数")),
        ],
        rows=[],
    )
    
    # 渲染表格
    await self._render_filtered_links(links)
```

#### 4.3 筛选逻辑

```python
async def _render_filtered_links(self, links: List[Dict]):
    """根据筛选条件渲染表格"""
    
    filtered = []
    for link in links:
        # 搜索过滤
        if self._search_keyword:
            keyword = self._search_keyword.lower()
            if (keyword not in link.get('shortCode', '').lower() and
                keyword not in link.get('seoTitle', '').lower()):
                continue
        
        # 状态过滤
        if self._filter_status == "posted" and link['post_count'] == 0:
            continue
        if self._filter_status == "unposted" and link['post_count'] > 0:
            continue
        
        filtered.append(link)
    
    # 构建表格行
    rows = []
    for link in filtered:
        status_icon = "✅" if link['post_count'] > 0 else "⏳"
        status_text = "已发" if link['post_count'] > 0 else "未发"
        
        rows.append(ft.DataRow(
            cells=[
                ft.DataCell(ft.Checkbox(data=link, on_change=self._on_checkbox_change)),
                ft.DataCell(ft.Text(link['shortCode'])),
                ft.DataCell(ft.Text(link.get('seoTitle', '（无标题）')[:30])),
                ft.DataCell(ft.Text(f"{status_icon}{status_text}")),
                ft.DataCell(ft.Text(str(link['post_count']))),
            ]
        ))
    
    self._link_table.rows = rows
    self.page.update()
```

#### 4.4 注入时保存 short_code

```python
def on_confirm(_):
    selected = [cb.data for cb in checkboxes if cb.value]
    
    pairs = []
    for link_data in selected:
        code = link_data['shortCode']
        seo_title = link_data.get('seoTitle') or ""
        desc = link_data.get('description') or ""
        
        effective_title = seo_title if seo_title else f"主页输入【{code}】立刻查看网盘资源"
        new_content = f"{desc} 👉 主页搜【{code}】马上查阅" if desc else f"主页搜【{code}】马上查阅"
        
        # 传递 short_code
        pairs.append((effective_title, new_content, code))
    
    async def _bg_task():
        if overwrite_switch.value:
            await self.db.clear_materials()
        added_count = await self.db.add_materials_bulk(pairs)  # 现在支持 3 元组
        # ...
```

---

## 数据流向

```
smart-link-manager API
        │
        ▼
slm_cached_links (JSON 缓存)
        │
        ▼
get_shortlinks_with_status()
        │
        ├── 合并 MaterialPool 发帖统计
        │
        ▼
UI 弹窗展示 (带状态筛选)
        │
        ▼
用户选择 → 保存 short_code 到 MaterialPool
        │
        ▼
发帖成功 → status = "success"
        │
        ▼
下次查询时可统计发帖次数
```

---

## 验证清单

- [ ] `short_code` 字段添加成功
- [ ] 数据库迁移无报错
- [ ] `add_materials_bulk` 支持 3 元组参数
- [ ] 短链注入时 `short_code` 正确保存
- [ ] 筛选弹窗正确显示"已发/未发"状态
- [ ] 筛选功能正常工作
- [ ] 发帖后状态正确更新

---

## 后续扩展

1. **发帖次数筛选**：可添加 `0次` / `1-3次` / `4-10次` / `10次以上` 的细分筛选
2. **时间筛选**：按短链创建时间或最后发帖时间筛选
3. **标签系统**：短链支持标签分类，按标签筛选
