# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.1/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  - 多账号轮询/随机/加权三种调度策略
  - 多贴吧批量投递支持
  - AI 智能改写（智谱/DeepSeek/OpenAI 兼容）
  - 全局物料池管理

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
