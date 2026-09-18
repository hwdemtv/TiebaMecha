# 批量发帖页"步骤流"重构规划

> **进度记录（2026-09-19）**：以下阶段已落地并推送，全部测试通过：
> - ✅ 预检闭环（770b63d）：`web/pages/batch_post/launch_config.py`（LaunchConfig 状态对象）+ `preflight.py`（PreflightService，口径与引擎一致）+ 启动前摘要确认弹窗 + "干跑预检"按钮 + 0-10 风险评分。
> - ✅ 贴吧风险可视化与默认禁选（7cb3da5）：`get_forum_risk_stats` 聚合（覆盖账号/发帖/删帖率/封禁），本地自留区与全域组徽标展示，⛔封禁禁选、🔴高风险（删帖率≥50%且样本≥3）默认禁选。
> - ✅ 物料导入预检（7cb3da5）：`scan_import_pairs` 纯函数（空内容/缺标题/超长/重复/含链接），批量粘贴与文件导入共用预览确认弹窗（去重导入/全量导入/取消）。
> - ✅ 复制上一任务 + 日志默认只看异常/关键（94795bd）：任务行"复制配置"，once 任务复制为立即执行；流水默认过滤 `key`，纯文本公告归入 key，修复 DB `skip` 与运行时 `skipped` 标识不一致。
> - ⏸ **暂停：任务中心独立页（Phase 1）与四步向导化（Phase 2）**。原因：检测到另一会话正在并发重构 web/pages/（posts.py → posts/ 包、帖子详情抽屉、我的帖子统一视图），任务中心/向导化需改动的 pages/__init__.py、app.py、batch_post_page.py 布局与其高度重叠，为避免互相踩踏而中止。恢复执行前请先确认并发会话已结束并合并其工作。
>
> 后续恢复时注意：新增控件需同步补 `tests/test_log_view.py` 的假 ft 桩成员；流水筛选新增 `key` 值（默认），其匹配逻辑在 `BatchPostPage._log_matches_filter`。

## 现状（原始规划背景）

- `web/pages/batch_post_page.py` 约 3600 行，单页同时承担 **配置（策略/靶场/账号）+ 执行（启动战役）+ 审计（队列/归档/流水）** 三类职责，三栏布局信息密度过高，13 个弹窗中有 2 个重复实现。
- 目标：按"配置步骤化 + 运行独立化"拆分，视觉风格（深色/青色/状态标签）保持不变。

## 一、目标信息架构

```
侧栏（执行中心组）
├─ 批量发帖        → 向导式配置页（4 步）
└─ 发帖运行中心     → 任务队列 / 已发归档 / 运行日志（独立页）

批量发帖向导（Stepper）：
  步骤 1  目标与兵力   —— 选账号（现右栏账号池）+ 选靶场（现"配置火力抛射靶场"弹窗内容上浮）
  步骤 2  物料准备     —— 录入/粘贴/文件导入 + 搜索/批量操作（现"物料排期池"Tab 的待发子集）
  步骤 3  AI 与排期    —— AI改写/人格化、定时计划/循环模式、自顶配置（现左栏内容）
  步骤 4  确认发射     —— LaunchConfig 汇总清单（账号 n、靶场 m、物料 k、AI 状态、时间计划）
                          + confirm_async 二次确认 → 启动战役
```

要点：
- **运行中心独立**后，向导页不再有底部 4 Tab，首屏即步骤 1。
- 靶场二级 Tab（本地自留区/全域轰炸组）在步骤 1 内保留为分段选择，不再是弹窗。
- 已发归档的"存活探测/自顶控制"能力随归档一起迁往运行中心，不丢失。

## 二、核心前置：LaunchConfig 状态对象（Phase 0）

现状痛点：账号选择、靶场选择、物料、各配置项散落在 `self._selected_account_ids`、`self._temp_local_fnames`、`self._temp_global_fnames`、几十个控件 `.value` 上，UI 与状态耦合。

新增 `web/pages/batch_post/launch_config.py`：

```python
@dataclass
class LaunchConfig:
    account_ids: set[int]
    local_fnames: list[str]      # 本地自留区
    global_fnames: list[str]     # 全域轰炸组
    material_ids: list[int] | None   # None = 全部待发
    use_ai: bool
    ai_persona: str
    post_count: int
    min_delay: int
    max_delay: int
    schedule_type: str           # once/daily/weekly/interval
    schedule_time: str | None
    interval_hours: int | None
    reset_strategy: str
    bump_config: BumpConfig      # 自顶子配置
```

- 现有持久化 settings keys（`last_selected_account_ids` 等）**保持不变**，LaunchConfig 与 DB 互转，无存储迁移。
- `core/batch_post.py`（战役执行引擎）**零改动**——本次只重构 UI 层。

## 三、分阶段实施（每阶段独立可交付、可回滚）

### Phase 0 — 无 UI 变化的结构拆分（约 1 个会话）
把 4 个巨型方法拆为 mixin/组件模块，`self.*` 引用保持不变，行为等价：
| 现方法 | 行数 | 拆到 |
|---|---|---|
| `_init_controls` | ~490 | `controls.py`（按 组别分函数：strategy/tables/logs） |
| `_open_firepower_dialog` | ~380 | `components/firepower_panel.py`（为步骤 1 复用做准备） |
| `_refresh_material_table` | ~248 | `material_table.py` |
| `_on_start_click` | ~229 | `launcher.py`（校验+汇总+执行循环） |
- 验收：655+ 测试全过；GUI 冒烟走通一次发帖 dry-run；diff 行为等价（对比前后 `LaunchConfig` 序列化）。

### Phase 1 — 发帖运行中心独立（约 0.5 个会话，收益立现）
- 新增 `web/pages/batch_post_center.py`：迁入 任务队列/已发归档/运行日志 三个 Tab 的构建与刷新函数（现成代码搬运）。
- 侧栏"执行中心"组加"发帖运行中心"条目；批量发帖页底部 Tabs 移除，运行中心页提供"发起新任务"按钮跳回向导。
- 验收：归档探测/自顶开关/流水筛选在运行中心全部可用；向导页首屏即步骤 1。

### Phase 2 — 配置向导化（约 1~1.5 个会话）
- 用 `ft.Stepper`（或自绘分段条，规避测试桩缺成员问题）承载 4 步；每步挂 Phase 0 拆出的组件。
- 步骤 1：账号池列表 + 火力面板内嵌（本地自留区/全域轰炸组分段）。
- 步骤 4：LaunchConfig 汇总卡片 + `confirm_async` 确认后调 `launcher.py`。
- 向导状态存 `page.session`（刷新恢复到当前步）；页面实例加入 `_pages_cache` 并实现 `cleanup()`（顺带修 FilePicker overlay 泄漏）。
- 验收：4 步向导走通一次真实低风险任务；中途刷新不丢配置；旧三栏布局代码删除。

### Phase 3 — 打磨与性能（约 0.5 个会话）
- 物料表：单行勾选不再触发"4 次查询 + 双表整表重建"，改局部行更新。
- 流水/归档列表改增量更新；`load_data` 保持 gather 并行（已完成的部分不动）。
- 清理死代码残留与重复"安全原初打法"实现（已删 1 份，面板化后再收编 1 份）。

## 四、测试与回滚策略

1. 每阶段结束跑全量 `pytest tests --ignore=tests/manual`（当前基线 655）。
2. Phase 0 为 `LaunchConfig` 新增单测（与 settings 互转、校验规则）；向导流转加集成测试。
3. 测试桩 `tests/test_log_view.py` 的假 ft 需按新增控件补成员（教训：`CrossAxisAlignment.STRETCH` 曾破 27 个测试）。
4. GUI 冒烟清单（每阶段）：侧栏导航 → 向导 4 步 → 发起任务 → 运行中心看队列/归档/流水 → 账号页跳存活分析。
5. 每阶段单独 commit；Phase 1/2 出问题时可独立 revert，不影响引擎层。

## 五、明确不做的事

- 不改 `core/batch_post.py` 执行引擎与风控组件（熔断/限流/拟人延迟）。
- 不改存储格式与 settings keys。
- 不动赛博视觉风格（配色/组件库复用现有 theme）。
