# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.1/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-05-06

### Fixed
- **UI & 布局优化**
  - 修复登录与设置密码页面在小窗口下底部按钮无法点击的问题（支持自动滚动与响应式布局）。
  - 优化初始化引导页面的垂直间距，提升小屏幕适配度。

### Changed
- **版本升级 (Version Bump)**
  - 更新基础版本号至 1.3.0。

## [1.2.0] - 2026-05-05

### Added
- **矩阵协作顶帖功能 (Matrix Collaborative Bump)**
  - 实现矩阵协作顶帖功能，优化账户隔离逻辑与代理设置。
  - 支持矩阵轮换循环自顶模式，增加封顶/到期处理逻辑。
  - 增加批量存活探测进度提示，优化代理容灾逻辑。
- **存活分析增强 (Enhanced Survival Analysis)**
  - 存活分析详情弹窗增加删除物料功能。
  - 增加阵亡原因和发布时间范围筛选，支持贴吧筛选下拉框。
  - 列表优化为卡片视图，提升信息展示效率。
- **本土作战自动判定 (Post Target Auto-Detection)**
  - 本土作战 (is_post_target) 改为自动判定，减少人工干预。
- **持久化存储优化**
  - 实现批量发帖任务流水持久化存储与 UI 自动加载。
  - 优化重构矩阵发帖调度逻辑，支持账号重试与阵地轮替。

### Fixed
- **UI & 兼容性修复**
  - 修复全域任务队列中文贴吧名显示为 Unicode 转义的问题。
  - 修复火力配置与安全配置弹窗点击时的 `AssertionError` 崩溃。
  - 修复 `Container` 不支持 `max_height` 参数的问题，改用 `height`。
  - 修复存活分析页面统计卡片在数据加载后不更新的问题。

## [1.1.3] - 2026-04-29

### Added
- **全域战略吧库分页 (Strategic Library Pagination)**
  - 全域战略吧库列表新增分页功能（每页 20 条），支持上/下翻页导航。

### Changed
- **性能优化 (Performance Optimization)**
  - 修复批量补齐关注的 N+1 查询问题：新增 `get_accounts_not_following_any_forums` 批量查询方法，查询次数显著降低。
  - 优化 `bulk_update_target_group` 批量插入逻辑。

### Fixed
- **UI 逻辑修复**
  - 修复搜索框清除逻辑的 UI 耦合问题。
  - 消除 `follow_forums_bulk` 中重复的账号查询调用。

## [1.1.2] - 2026-04-12

### Added
- **战术火力配置工作流 (Artillery Tactical Workflow)**
  - 引入二级向导配置模式：【安全原初打法配置】 -> 【配置火力抛射靶场】。
  - 实现“本地自留区”与“全域轰炸组”的页签化管理。
- **风控情报分析引擎增强 (Enhanced Risk Intelligence)**
  - 升级发帖拦截细节弹窗，支持展示出战账号 ID 和攻坚目标吧名。
  - 接入“战术指导”系统，针对拦截提供精准应对建议。

### Changed
- **文案引擎增强**
  - 优化 AI 改写输出逻辑，强制执行文案与链接间的双空行分隔。

### Fixed
- **兼容性与布局 (Hotfix)**
  - 修复 `Flet 0.23.2` 兼容性问题（`Badge` 参数、`Alignment` 常量、`Checkbox` 参数）。
  - 优化全域 Web 布局，修复“全域签到”页面拉伸异常及“矩阵发帖终端”布局比例。
  - 修复 AI 优化器在高负载下的文本截断问题。

## [1.1.1] - 2026-04-10

### Added
- **矩阵发帖 UI 状态记忆**
  - 引入持久化状态集，解决切换页面导致选中状态重置的问题。

### Changed
- **打包稳定性提升**
  - 显式补齐 `aiohttp_socks`、`httpx`、`rich` 等直引依赖。
  - 将 Flet 锁定为 `0.23.2` 稳定版，确保 API 兼容性。
  - 同步更新便携版启动器版本显示为 `v1.1.1`。

### Fixed
- **自顶计数重置**
  - 修复 `MaterialPool` 物料重置或重发时 `bump_count` 累计未清零的问题。
- **便携版协议冲突 (Hotfix)**
  - 修复 `Receive loop error: 'text'` 错误（通过回滚 Web 协议栈解决）。
- **SOCKS 代理与探测修复**
  - 修复 SOCKS 代理缺少 `socksio` 依赖的问题。
  - 修复用户信息探测返回 301、`user.level` 缺失等系列 Bug。

## [1.1.0] - 2026-04-01

### Added
- **Flet 兼容性补丁**
  - 增加对 Flet 0.84+ 的兼容支持。

## [0.1.0] - 2026-04-01

### Added
- **矩阵发帖终端 (Matrix Post Terminal)**
  - 向导式战术配置、多账号调度策略（轮询/随机/加权）。
  - 全域阵地管理，支持一键全局取关与清理。
  - AI 智能改写（对接智谱/DeepSeek/OpenAI）。
- **拟人化风控引擎 (BionicDelay™)**
  - 对数正态分布延迟、生理节律权重、RPM 限流器。
- **智能自动回帖 (Auto-Bump)**
  - 自动转入自顶序列，后台守护进程定时巡检。
- **其他核心功能**
  - 账号指纹隔离、全域签到引擎、拟人化养号 (BioWarming™)。
  - 全域通知中心、自动更新检测、自动化规则引擎。
  - CLI 命令行工具与 Flet Web 控制台。

### Security
- AES-256-GCM 加密存储 BDUSS/STOKEN 凭证。

---

## 未来规划

### [1.4.0] - TBD
- 云控中心（多节点统一管理）
- 数据分析仪表盘
- 移动端响应式适配
- 插件市场

---

[1.3.0]: https://github.com/hwdemtv/TiebaMecha/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/hwdemtv/TiebaMecha/compare/v1.1.3...v1.2.0
[1.1.3]: https://github.com/hwdemtv/TiebaMecha/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/hwdemtv/TiebaMecha/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/hwdemtv/TiebaMecha/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/hwdemtv/TiebaMecha/compare/v0.1.0...v1.1.0
[0.1.0]: https://github.com/hwdemtv/TiebaMecha/releases/tag/v0.1.0
