# Flet Web 布局陷阱与排查手册

> 来源：帖子管理页三场景重构后的三轮实际 UI 故障（提交 30a69a5 / c636123 / 05e1701）。
> 三次故障的共同点：**单元测试全绿，但真实浏览器渲染异常**——布局问题测试桩测不出来，
> 必须浏览器实测。本文沉淀根因、症状速查和修复规则，供后续 UI 开发规避。

## 一、核心规则（写 UI 前先读这五条）

1. **`expand` 只能用在"有界主轴"容器里**。
   `expand` 是沿父容器主轴的弹性占位：
   - Row 的主轴是横向 → 子项 expand 合法（横向撑满）
   - Column 的主轴是纵向 → 子项 expand = 纵向弹性
   - **滚动容器（scroll=AUTO/ALWAYS 的 Column 等）给子项的是无界主轴约束**
     ——无界约束下弹性布局是 Flutter 未定义行为，整块塌缩为 0 高度。

2. **`wrap=True` 的 Row/Column（即 Flutter 的 Wrap）内禁止任何 expand 子项**。
   Wrap 流式布局不支持弹性子项，必触发布局异常。

3. **垂直 Column 里的 `Text` 不可加 expand**（哪怕想让它"占满宽度"——
   那是横向诉求，纵向 expand 语义完全不同）。横向占满在 Column 内是默认行为，
   省略号截断只需要 `max_lines=1 + overflow=ELLIPSIS`（列宽约束天然有界）。

4. **Tabs 内容里的动态列表必须用 `ft.ListView`，禁止用 scrollable Column**。
   Flet web 缺陷：Tab 处于非选中状态期间，向 scrollable Column 动态添加的
   子控件，切回选中后不会被渲染（最小复现证实：静态挂载正常；动态填充时
   四种行结构全部消失；切 Tab、二次 update 均无法补救）。
   ListView 是专用虚拟化滚动容器，动态添加可靠（运行中心流水列表即先例）。

5. **Tab 内容包一层 padding 容器**（`ft.padding.only(top=6, left=4, right=4)`），
   否则下拉框/输入框的浮动标签会被 Tabs 边界裁掉上半截。

## 二、症状 → 病因速查表

| 症状 | 根因 | 修复 |
|---|---|---|
| 页面/表单只剩第一个控件，其余全部消失 | 滚动列容器内的子控件带 `expand`（纵向弹性塌缩） | 移除该 expand（案例：发布页账号/贴吧下拉，30a69a5） |
| 整个 Tab 渲染为灰色空块 | `wrap=True` 行内有 `expand` 子项（Wrap 布局异常） | 改固定宽度（案例：关键字输入框，c636123） |
| 列表分页/统计正常，但行区空白；静态内容（卡片/提示）正常 | Tabs 非选中期间向 scrollable Column 动态填充 | 列表改 `ft.ListView`（案例：批量/我的帖子列表，05e1701） |
| 列表总高只有几像素、行整行消失 | 行内 `Text` 带 expand 且处于垂直 Column（滚动列表无界高度） | 移除该 Text 的 expand（同 05e1701） |
| 下拉框/输入框标签上半截被裁 | Tab 内容顶到 Tabs 边界 | 内容包 padding 容器（c636123） |
| 进度条显示时变成粗条 | ProgressBar 带 expand 且在 Column 里（纵向拉伸） | 移入 Row 横向撑满 |

## 三、排查方法论（这次实测有效的流程）

1. **先分清"没进 DOM"还是"进了 DOM 但 0 尺寸"**——处理方式完全不同：
   ```js
   // 浏览器控制台（或在 playwright evaluate 里）：
   const sems = [...document.querySelectorAll('flt-semantics[role="checkbox"]')];
   sems.slice(0, 3).map(n => { const r = n.getBoundingClientRect();
     return { y: r.y, h: r.height, w: r.width }; });
   ```
   - 数量为 0 → 控件没被 diff/挂载（案例 3：动态填充丢失）→ 换 ListView
   - 存在但 h=0 → 布局塌缩（案例 1/2 与标题 Text）→ 找无界约束下的 expand

2. **最小复现 + 单变量二分**。把可疑结构剥成独立小页（`ft.run` 一个几十行的
   脚本），同屏并排对照多个变体，一次只改一个变量。本轮用此法 10 分钟锁定
   "标题 Text 的 expand"——纯代码推演半小时无果。

3. **对照已知正常的相似结构**。同一页面里"我的帖子"卡片渲染正常、
   "批量"行不渲染，diff 两者的控件树差异即嫌疑清单。

4. **两个实验陷阱**（都真实踩过）：
   - **Tabs 默认选中第一个 Tab**：非选中 Tab 的内容根本不渲染，
     会把"tab 没选中"误判成"布局塌缩"。实验必须 `selected_index` 指到目标 Tab。
   - **把实验容器放进 scroll 外层**：外层滚动又引入一层无界约束，全组塌缩，
     实验作废。对照实验的外层必须是有界固定结构。

5. **Flet web release 构建的布局异常是静默的**：console.error 拿不到
   "RenderFlex children have non-zero flex"这类报错，别依赖日志，靠 DOM 测量。

6. **自动化操作的两个干扰**（不影响真实鼠标）：
   - Flet canvas 重渲染后第一次点击会丢失——先 hover（move）再 click；
   - 页面刷新后无障碍遮罩/登录页时序——实验脚本每步等待控件实际出现。

## 四、修复模式（项目惯例）

- **动态填充的列表一律 `ft.ListView`**（运行中心流水、帖子批量列表、
  我的帖子列表均已如此），不用 scrollable Column。
- 静态表单/滚动列内的控件不给 expand；需要宽度用固定 `width`。
- Tab 内容统一包 padding 容器。
- 修改布局后：跑页面级单测（`test_posts_page.py` 等，需同步补假 ft 桩成员）
  + **本地起服务浏览器实测目标页面**，两道都过才算修复。
- 已知非问题（勿误修）：`detail.py`/`kv_row`/流水卡片里 Text 的 expand
  都在 Row 内（横向），是合法用法。
