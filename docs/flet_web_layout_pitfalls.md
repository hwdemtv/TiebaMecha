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
| 首次进入页面默认 Tab 的行区空白，工具栏/筛选正常；切走再切回（触发 tab 重建）后恢复 | 默认选中 Tab 的列表仍是 scrollable Column，挂载后首次动态填充即中招 | 列表改 `ft.ListView`（案例：账号档案中心列表，本条为 05e1701 的漏网之鱼） |
| 列表总高只有几像素、行整行消失 | 行内 `Text` 带 expand 且处于垂直 Column（滚动列表无界高度） | 移除该 Text 的 expand（同 05e1701） |
| 下拉框/输入框标签上半截被裁 | Tab 内容顶到 Tabs 边界 | 内容包 padding 容器（c636123） |
| 进度条显示时变成粗条 | ProgressBar 带 expand 且在 Column 里（纵向拉伸） | 移入 Row 横向撑满 |
| 点击无反应，悬停提示以 `tooltip {message: ...}` 原文显示在页面上 | `ft.Tooltip` 对象赋给控件的 `tooltip` 属性，被当作子控件渲染成覆盖层（显示原文 + 拦截点击） | `tooltip` 一律赋纯字符串（见 五-1） |
| AlertDialog 打开后只有一片空白，标题/内容/按钮全无；服务端无任何报错 | content 里 `scroll+tight Column` / `max_height` 约束在对话框无界高度下布局冲突，Flutter 整体渲染失败 | 固定 `height`（按行数自适应）+ scroll，去掉 tight 与 max_height（见 五-2） |
| 切换/提交完成后整页变暗、所有点击失效，F5 才恢复 | 对话框 close 与页面重载（build+update）竞态，barrier 残留；自动化环境的事件重放会放大 | 处理器入口先同步 close；宿主页面改"原位 load_data 重载"，不做整页重建（见 五-3） |
| 某页面一进就白屏，服务端 Traceback: `ModuleNotFoundError: ...web.web` | 相对导入多写一层：`pages/posts/x.py` 深一层包，`...web.components` 展开成 `tieba_mecha.web.web.components` | 按包层级数点数：`pages/x.py` 用 `..components`，`pages/posts/x.py` 用 `...components` |
| 芯片/头部已显示切换成功，页面里"默认当前账号"的控件还停在旧账号 | 下拉填充是"保留用户已选"策略，切号后旧值仍是合法 id，回落逻辑不触发 | 切号回调里先把该控件 `value=None` 再 load_data，让回落逻辑选中新活跃账号（案例：帖子管理发布账号下拉） |

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

## 五、对话框与交互层陷阱（账号切换芯片实战，2026-09-19）

> 来源：全局账号切换芯片（`web/components/account_switcher.py`）接入
> 指挥中心/全域签到/帖子管理三页的浏览器实测。本轮问题与布局无关，
> 全部集中在 **tooltip、对话框生命周期、事件重放、相对导入** 四类。

### 规则

1. **`tooltip` 一律赋纯字符串，禁止赋 `ft.Tooltip` 对象**。
   Flet 0.23 中 Container.tooltip 接收 Tooltip 对象时会把它当作
   子控件渲染：页面上出现 `tooltip {message: ...}` 原文，且该覆盖层
   会拦截 underneath 的 `on_click`（表现为"点击完全没反应"）。
   字符串形式由框架包装成 Flutter Tooltip，无此问题。

2. **AlertDialog 的 content 禁用 `scroll+tight Column` 与
   `BoxConstraints(max_height)` 组合**。对话框给内容的是无界高度约束，
   滚动 Column 在其中布局冲突 → **整个对话框（含标题、按钮、遮罩之外
   的全部内容）渲染为空白**。服务端无任何异常，浏览器 console 也没有
   可靠输出。修复模式：固定 `height`（按行数自适应、设上限）+ scroll，
   不用 tight、不用 max_height。

3. **切换/选择类面板禁止用 AlertDialog + page.open/close，改用 PopupMenuButton**。
   Flet 0.23 web 上 AlertDialog 存在客户端状态不同步：即使同步
   `page.close` 先行、随后才重载页面，barrier 仍可能以空壳形式残留
   挡死整页（F5 才能解）——锁、offstage 摘除、延迟重载均无法根治
   （真实浏览器复现，2026-09-19）。
   **根治：改用 `ft.PopupMenuButton` 承载面板**（与 Dropdown 同一原生
   渲染路径，框架自行开关，无遮罩无生命周期竞态）。AlertDialog 仅保留
   给"打开→处理→关闭"不伴随页面刷新的纯表单场景（如账号页添加账号）。

4. **弹窗/写操作处理器必须防重入，且判重要用会话级状态**。
   自动化或事件重放会让同一点击触发两次处理器；页面重载又会创建新
   组件实例，实例级 `self._dialog is not None` 判重挡不住跨实例重复开面板。
   项目模式：模块级 `_dialog_locks: dict[session_id, bool]` 按会话分桶，
   open 前检查、open 后置位、close 时复位。

5. **页面模块的相对导入按包层级数点数，改完先 `python -m py_compile`**。
   `pages/x.py` 与 `pages/posts/x.py` 差一层：
   - `pages/x.py` 引 components → `from ..components...`
   - `pages/posts/x.py` 引 components → `from ...components...`
   写错不会在导入时爆，而是在**页面 build() 时**爆 ModuleNotFoundError
   （白屏 + 服务端 Traceback，特征：报错路径出现重复段如 `.web.web`）。

### 排查手段（本轮实测有效）

- **服务端打点是唯一可靠的观测**：Flet web 布局/渲染异常不会出现在
  服务端日志；在事件处理器每个 return 分支前加
  `print("[标记] ...", file=sys.stderr, flush=True)`，一次重启即可定位
  卡在哪个分支。注意：后台起服务时不要把输出重定向到 /dev/null
  （本轮因此多绕三轮）。
- **点击是否触达的判定**：悬停后 tooltip 出现 = 事件到达控件；
  页面跳转 = 处理器执行。两者都没有 → 点击丢失（自动化环境常见，
  见下），不是处理器问题。
- **自动化点击会丢失/重放**（仅自动化环境，真实鼠标无此问题）：
  - 页面启动白屏窗口期（约 10–15s，主题未应用、字体未加载）内的
    点击会被静默吞掉——每次操作前先截图确认页面处于预期状态；
  - 同一点击可能派发两次事件——写操作处理器必须防重入；
  - 规避手法：先 `move` 到目标坐标停顿再 `click`；失败就换坐标微调重试。
- **验证切换是否真实生效直接查库**，不轻信 UI：
  `sqlite3 data/tieba_mecha.db "SELECT id,name,is_active FROM accounts"`。
- **每次改码必须重启 flet 服务**（无热重载），且浏览器会话失效需重新
  走登录页（"暂不设置，直接进入"通道，不会在库里留密码）。
- **调试结束记得删光打点**：残留的 ChipDebug 类 print 会污染下次排查。
