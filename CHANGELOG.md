# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.1/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.3] - 2026-04-29

- **全域战略吧库性能优化 (Strategic Library Performance)**
  - 修复批量补齐关注的 N+1 查询问题：新增 `get_accounts_not_following_any_forums` 批量查询方法，将逐吧循环查询优化为单次查询 + 单次批量操作，查询次数从 2N 降至 2。
  - 全域战略吧库列表新增分页功能（每页 20 条），支持上/下翻页导航，筛选变化自动重置页码，避免大量贴吧时的渲染性能问题。
  - 修复搜索框清除逻辑的 UI 耦合问题：搜索框改为实例属性直接引用，不再依赖 widget 树遍历。
  - 优化 `bulk_update_target_group` 批量插入：使用 `session.add_all()` 替代逐个 `session.add()`。
  - 消除 `follow_forums_bulk` 中重复的 `get_accounts()` 调用，复用首次查询结果。

## [1.1.2] - 2026-04-12

- **战术火力配置工作流 (Artillery Tactical Workflow)**
  - 引入二级向导配置模式：【安全原初打法配置】->【配置火力抛射靶场】。
  - 实现“本地自留区”与“全域轰炸组”的页签化管理，支持实时搜索与过滤勾选。
  - 支持在火力配置界面直接执行“批量取消关注并全局清理”阵地，实现战术级清场。

- **风控情报分析引擎增强 (Enhanced Risk Intelligence)**
  - 升级发帖拦截细节弹窗，支持展示：出战账号 ID、攻坚目标吧名。
  - 接入“战术指导”系统：针对“用户没有权限”、“由于吧务设置”等拦截提供精准的实战应对建议（如：全域签到提权、AI 深度改写等）。

- **文案引擎防抽增强**
  - 优化 AI 改写输出逻辑：强制执行文案与链接间的双空行分隔。
  - 支持内容池零宽字符自动混淆深度调节（内部逻辑优化）。

- **稳定性修复 (Hotfix)**
  - 修复 `Flet 0.23.2` 兼容性问题：修正 `ft.Badge` 参数及 `ft.Alignment` 常量引用，修复 `Checkbox` 参数 `label_size` 无效导致的崩溃。
  - 优化项目全域 Web 布局：修复“全域签到”页面垂直拉伸异常，优化“矩阵发帖终端”三栏布局比例为 2:6:2 以提升大屏幕适配度。
  - 修复战术图标库映射不全导致的 UI 渲染异常。
  - 修复 AI 优化器在高负载下的文本截断问题。

## [1.1.1] - 2026-04-10

- **矩阵发帖 UI 状态记忆**
  - 在 `BatchPostPage` 中引入持久化状态集，解决切换页面导致账号/贴吧选中状态重设为全选的问题。
  - 支持手动操作与全选操作的实时状态同步。

- **自顶计数智能重置**
  - 修复 `MaterialPool` 物料重置或重发时 `bump_count` 累计未清零的问题。
  - 确保每一条新出的“战报”都从 0 次自顶开始。

### Changed
- **打包稳定性提升**
  - 在 `pyproject.toml` 和 `build_portable.py` 中显式补齐 `aiohttp_socks`、`httpx`、`rich` 等直引依赖。
  - 将 Flet 锁定为 `0.23.2` 稳定版，防止因 0.84.0+ API 变更（如 `Badge` 的 `text` 属性移除）导致的兼容性崩溃。
  - 同步更新便携版启动器版本显示为 `v1.1.1`。
  - 优化 `build_copy_venv.py` 虚拟环境复制打包，修正绝对路径问题。

### Fixed (Hotfix)
- **便携版协议冲突修复**
  - 修复 `Receive loop error: 'text'` 错误：通过回滚 Web 协议栈（Uvicorn 0.29.0, FastAPI 0.110.0, Starlette 0.36.3）解决了与 Flet 0.23.2 的 WebSocket 通信冲突。
  - 修复 `No module named 'aiohttp_socks'` 报错。

- **SOCKS 代理支持修复 (Hotfix)**
  - 修复了使用 SOCKS 代理时由于缺少 `socksio` 依赖导致的 `ImportError`，确保 `httpx` 连接稳定性。

- **用户信息探测修复**
  - 修复带登录态时用户名查询返回 301 的问题（改用无账号客户端获取 portrait）
  - 修复 `user.level` 属性不存在的问题（改用 `glevel`）
  - 修复 `get_user_posts` 异步迭代器错误
  - 修复 `user_name` 显示为 `-` 的问题

- **数据库路径修复**
  - 修复 `DEFAULT_DB_PATH` 在不同运行环境下路径计算错误
  - 支持 PyInstaller 打包环境、源码开发环境、便携版环境

- **测试修复**
  - 修复测试中 BDUSS 长度验证问题
  - 修复 `get_account_credentials` 返回值解包问题
  - 修复 `aiohttp.ClientSession` mock 配置问题

## [1.1.0] - 2026-04-01

### Added
- Flet 0.84+ 兼容性补丁

## [0.1.0] - 2026-04-01

### Added
- **矩阵发帖终端 (Matrix Post Terminal)**
  - **向导式战术配置**：从安全原初打法到火力抛射配置的二级向导流。
  - **多账号调度策略**：轮询/随机/加权三种模式。
  - **全域阵地管理**：页签化管理本地关注吧与外部目标吧，支持**一键全局取关与清理**。
  - **AI 智能改写**：对接智谱/DeepSeek/OpenAI，支持强制空行分隔防抽处理。
  - **战术情报引擎**：发帖拦截后提供保姆级的实战操作建议与账号定位。

- **拟人化风控引擎 (BionicDelay™)**
  - 高斯分布延迟，拒绝固定间隔
  - 生理节律权重（凌晨自动降频）
  - 内部速率墙（RPM 限流器）

- **智能自动回帖 (Auto-Bump)**
  - 发帖成功后自动转入自顶序列
  - 后台守护进程定时巡检
  - 回帖记录追踪

- **账号指纹隔离**
  - 独立 CUID 和 User-Agent 分配
  - 代理绑定与自动熔断
  - 挂起账号自动隔离

- **全域签到引擎**
  - 批量签到所有关注贴吧
  - 签到历史统计追踪
  - 自动同步关注列表

- **拟人化养号 (BioWarming™)**
  - 自动模拟真人浏览、翻页、阅读
  - 随机点赞互动
  - 4 小时周期性自动运行

- **全域通知中心**
  - 本地事件实时推送
  - 远程广播同步
  - Web 界面通知铃铛

- **自动更新检测**
  - 基于 GitHub Releases API
  - 更新日志展示

- **自动化规则引擎**
  - 关键词/正则匹配监控
  - 自动删帖/通知模式

- **CLI 命令行工具**
  - 账号管理（添加/切换/验证/刷新）
  - 签到管理（执行/同步/统计）
  - 帖子管理（列表/搜索/删除/加精）
  - 数据爬取（帖子/用户/历史）

- **Web 控制台**
  - Flet 构建的现代化 UI
  - 指挥中心仪表盘
  - 全功能管理页面

- **Docker 部署支持**
  - 官方镜像支持
  - docker-compose 一键部署

### Security
- AES-256-GCM 加密存储 BDUSS/STOKEN 凭证
- 环境变量隔离敏感配置

---

## 未来规划

### [0.2.0] - TBD
- 云控中心（多节点统一管理）
- 数据分析仪表盘
- 移动端响应式适配
- 插件市场

---

[0.1.0]: https://github.com/hwdemtv/TiebaMecha/releases/tag/v0.1.0
