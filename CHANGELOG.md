# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-01-01

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
