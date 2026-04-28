# 🤖 TiebaMecha (贴吧机甲)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-hwdemtv%2FTiebaMecha-black.svg)](https://github.com/hwdemtv/TiebaMecha)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://hub.docker.com/r/hwdemtv/tieba-mecha)

**赛博机甲风格的一站式百度贴吧自动化管理平台。**

集成了 **拟人化风控引擎 (BionicDelay™)**、**矩阵发帖终端**、**智能自动回帖 (Auto-Bump)** 与 **全域通知中心**，旨在为用户提供最安全、最高效的贴吧运营体验。

![alt text](src/tieba_mecha/assets/icon.png)

> 💡 **核心优势**：通过高斯分布延迟、生理节律权重、零宽字符混淆等多重反风控技术，最大化降低被系统检测和封禁的风险。

---

### 📚 技术文档
- 📦 **[桌面分发指南](docs/deployment/distribution-guide.md)**：了解如何打包绿色便携版。
- 🐳 **[Docker 部署手册](docs/deployment/linux-deployment.md)**：在 Linux 云服务器上实现 24h 挂机。

---

## 🌟 核心特性

### 1. 矩阵发帖终端 (Matrix Post Terminal)
- **战术向导配置**：二级向导流 —— 安全原初打法配置 → 火力抛射靶场，页签化管理本地关注吧与外部目标吧
- **多号轮询/随机/加权**：支持数十个账号同步作业，内置多种调度算法防止单点封禁
- **多贴吧轰炸**：单次任务可投递至多个目标贴吧，全自动轮换
- **AI 智能附魔**：深度集成大语言模型（智谱/DeepSeek/OpenAI 兼容），6 种人格预设（正常、资源帝、随性、萌新、技术、热心），发帖前自动对标题与正文进行语义改写
- **战术情报引擎**：发帖拦截后自动分析原因并提供实战应对建议（签到提权、AI 深度改写等）
- **全域同步**：支持全设备账号关注贴吧的一键同步，快速构建目标池

### 2. 拟人化风控引擎 (BionicDelay™)
- **高斯分布延迟**：拒绝机械的固定间隔，发帖延迟遵循正态演化，极致模拟真实人类操作
- **生理节律权重**：根据现实时间（如深夜 01:00 - 07:00）自动调整操作频率，符合生物作息规律
- **内部速率墙**：可配置的 RPM 限流器，防止触发平台高频风控

### 3. 智能自动回帖 (Auto-Bump)
- **热度守护**：成功发帖后自动转入"自顶"序列，后台守护进程定时巡检，自动维护帖子置于首页
- **时光溯洄记录**：详细记录每一次"回帖顶起"的时间与次数，TID 链接直达原贴验证
- **手动回炉**：支持一键重置物料状态，重新投入发帖队列

### 4. 账号指纹隔离
- **独立设备指纹**：为每个账号动态分配 CUID 和 User-Agent，实现物理级别的账号隔离
- **代理枢纽**：支持为特定账号绑定高匿代理 IP，确保多号环境下的 IP 干净度
- **自动熔断**：代理连续失败达到阈值时自动禁用，关联账号自动挂起隔离

### 5. 全域签到引擎
- **一键签到**：批量签到所有关注的贴吧，支持自定义延迟间隔
- **统计追踪**：记录签到历史、连续天数、等级变化
- **自动同步**：从账号自动拉取关注的贴吧列表

### 6. 拟人化养号 (BioWarming™) [ENHANCED]
- **深度模拟**：自动模拟真人进入贴吧浏览、翻页、停顿阅读。
- **冷启动支持 (Cold Start)**：针对 0 关注的新号，自动启用「公域发现池」进行破冰探索，确保新号不因无关注数据而停滞。
- **动态行为权重**：根据账号发育程度自动调整，新号自动进入“潜行模式”，显著延长停顿时间并将点赞等高危互动概率降至极低。
- **破冰自动关注**：在公域探索中模拟真人兴趣，低概率自动关注优质版块，主动构建账号画像标签。
- **守护进程调度**：后台 4 小时周期性自动运行，全自动维护账号权重。

### 7. 全域通知中心
- **本地通知**：任务完成、账号异常、代理失效等事件实时推送
- **远程广播**：支持从授权中心接收全局公告和紧急通知
- **多渠道展示**：Web 界面通知铃铛 + SnackBar 弹窗双重提醒
- **智能同步**：自动去重，支持强制通知立即展示

### 8. 自动更新检测
- **版本检测**：基于 GitHub Releases API 自动检测新版本
- **更新日志**：完整展示版本更新内容
- **智能节流**：避免频繁请求 API（默认 24 小时检查一次）

### 9. 自动化规则引擎
- **关键词监控**：支持关键词匹配和正则表达式两种模式
- **自动删帖**：监控目标贴吧，自动删除匹配规则的帖子
- **通知模式**：可选择仅通知不删除，适合内容审核场景

### 10. 帖子存活分析 (Survival Analysis)
- **存活追踪**：实时监控已发帖子是否存活，自动检测被删/被屏蔽状态
- **死亡原因分类**：识别并分类帖子失效原因（吧务删除、系统屏蔽、用户举报等）
- **多维筛选**：按账号、贴吧、死亡原因等维度快速过滤分析

### 11. Web 密码认证
- **访问控制**：为 Web 控制台设置访问密码，防止未授权访问
- **登录拦截**：首次访问显示设置密码页面，之后显示登录页面
- **密码管理**：在系统设置中可修改密码或关闭密码保护
- **密码重置**：忘记密码时，通过环境变量 `TIEBA_MECHA_WEB_PASSWORD_RESET=true` 启动应用即可重置

---

## 🚀 快速开始

### 方式一：绿色便携版（推荐 Windows 用户/新手）

1. [下载最新的 `TiebaMecha-Portable.zip`](#) 并在本地目录解压。
2. 运行 **`首次运行(生成密钥).bat`**。系统会自动为您生成唯一的加密盐值和访问密钥（仅需在初次使用或重置环境时运行一次）。
3. 运行 **`启动机甲.bat`**。
4. 浏览器将自动打开并访问 [http://localhost:9006](http://localhost:9006)。

**📦 更新与维护：**

1. 下载新版本 `TiebaMecha-Portable.zip`。
2. **保留旧版本文件夹中的 `data/` 目录和 `.env` 文件**。它们包含了您的所有账号数据和加密配置。
3. 将新版本文件解压并覆盖至旧目录，或将保留的文件/文件夹移至新目录即可。

> [!CAUTION]
> **重要警告**：请务必备份您的 `.env` 文件。一旦丢失，您在数据库中存储的所有加密 Cookie 将无法解密并会导致账号失效。

**🔧 配置文件 (.env) 说明：**

在便携版中，主要通过 `首次运行(生成密钥).bat` 自动配置。如果您需要手动调整端口或主界面地址，可以编辑 `.env`：

```bash
# 加密参数 (由初始化脚本自动生成)
TIEBA_MECHA_SALT=...
TIEBA_MECHA_SECRET_KEY=...

# 可选：自定义端口
TIEBA_MECHA_PORT=9006
```

编辑 `.env` 文件推荐使用 **记事本** 或 **VS Code** 等文本编辑工具。

### 方式二：Docker 部署（推荐服务器/挂机用户）

确保已安装 Docker 和 Docker Compose，然后在项目根目录执行：

```bash
# 克隆仓库
git clone https://github.com/hwdemtv/TiebaMecha.git
cd TiebaMecha

# 创建 .env 文件（必须！）
cp .env.example .env
# 编辑 .env 填入加密密钥（参见下方安全配置章节）

# 启动服务
docker-compose up -d
```

**Docker 配置说明：**

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| 端口映射 | Web 服务端口 | `9006:9006` |
| 数据卷 | 数据库持久化 | `./data:/app/data` |
| 日志卷 | 运行日志 | `./logs:/app/logs` |
| 时区 | 容器时区 | `Asia/Shanghai` |
| 健康检查 | 每 30 秒检测一次 | 自动重启 |

访问 `http://localhost:9006` 即可打开 Web 界面。

详见 [Docker 部署手册](docs/deployment/linux-deployment.md)。

### 方式三：源码安装（开发者）

```bash
# 克隆仓库
git clone https://github.com/hwdemtv/TiebaMecha.git
cd TiebaMecha

# 创建虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

# 安装依赖
pip install -e .

# 安装开发依赖（可选）
pip install -e ".[dev]"
```

### 安全配置（必须）

TiebaMecha 使用 AES 加密存储您的 BDUSS/STOKEN 凭证。首次运行前必须配置加密密钥：

**生成密钥（任选一种方式）：**

```python
# Python
import secrets
print(f"TIEBA_MECHA_SALT={secrets.token_hex(32)}")
print(f"TIEBA_MECHA_SECRET_KEY={secrets.token_hex(32)}")
```

```bash
# Linux/Mac
echo "TIEBA_MECHA_SALT=$(openssl rand -hex 32)"
echo "TIEBA_MECHA_SECRET_KEY=$(openssl rand -hex 32)"
```

```powershell
# Windows PowerShell
$bytes = New-Object byte[] 32
[Security.Cryptography.RNGCryptoServiceProvider]::Create().GetBytes($bytes)
"TIEBA_MECHA_SALT=$($bytes | ForEach-Object { $_.ToString('x2') })" -replace '\s',''
```

**配置 .env 文件：**

```bash
# 复制模板
cp .env.example .env

# 编辑填入生成的密钥
# Windows: notepad .env
# Linux/Mac: nano .env
```

⚠️ **重要提示：**
- 绝不要将 `.env` 文件提交到 Git 或公开分享
- 丢失密钥将导致已加密的凭证无法解密
- 建议定期更换密钥并重新导入账号

---

## 🖥 界面与操作

### Web 控制台（推荐）

```bash
python start_web.py
# 或
tieba web --port 9006
```

默认地址：`http://localhost:9006`

| 页面 | 功能 |
|------|------|
| **指挥中心** | 系统概览、账号状态、快捷操作 |
| **账号列表** | 多号导入、存活验证、代理绑定、设备指纹管理 |
| **代理池** | HTTP/SOCKS5 代理管理、健康检测、自动熔断 |
| **全域签到** | 批量签到、统计追踪、贴吧同步 |
| **帖子管理** | 帖子搜索、删除、加精、置顶操作 |
| **帖子存活分析** | 帖子存活追踪、死亡原因分类、按账号/贴吧/原因筛选 |
| **数据爬取** | 帖子爬取、用户画像、历史记录 |
| **自动化规则** | 关键词监控、自动删帖、正则匹配 |
| **拟人化养号** | 自动浏览、随性点赞、权重维护、定时调度 |
| **矩阵发帖** | 战术向导配置、物料池管理、AI 改写、定时任务、自动回帖 |
| **插件系统** | 扩展功能加载与管理 |
| **系统设置** | AI 配置、全局参数、安全选项（Web 密码管理）、定时任务调度 |
| **新手引导** | 首次使用向导，引导完成账号导入与基础配置 |

### 命令行工具（CLI）

适合 VPS 挂机或自动化脚本：

```bash
# 查看帮助
tieba --help

# ========== 账号管理 ==========
tieba account add --bduss YOUR_BDUSS --stoken YOUR_STOKEN --name "备注名"
tieba account list
tieba account switch 1              # 切换活跃账号
tieba account verify                # 验证当前账号
tieba account refresh               # 刷新所有账号状态
tieba account delete 1

# ========== 签到管理 ==========
tieba sign run                      # 签到所有贴吧
tieba sign run "贴吧名"             # 签到指定贴吧
tieba sign sync                     # 同步关注贴吧列表
tieba sign status                   # 查看签到统计

# ========== 帖子管理 ==========
tieba post list "贴吧名" --page 1 --num 20
tieba post search "关键词" --forum "贴吧名"
tieba post delete 12345678 --forum "贴吧名"
tieba post good 12345678 --forum "贴吧名"    # 加精
tieba post good 12345678 --forum "贴吧名" --undo  # 取消加精

# ========== 数据爬取 ==========
tieba crawl threads "贴吧名" --pages 5 --output ./output
tieba crawl user 123456789 --posts
tieba crawl history --limit 20

# ========== Web 服务 ==========
tieba web --port 9006 --host 0.0.0.0
```

---

## 🔧 配置详解

### 环境变量

| 变量名 | 必填 | 说明 | 默认值 |
|--------|:----:|------|--------|
| `TIEBA_MECHA_SALT` | ✅ | 加密盐值（64 位十六进制） | 无 |
| `TIEBA_MECHA_SECRET_KEY` | ✅ | 加密密钥（64 位十六进制） | 无 |
| `TIEBA_MECHA_DB_PATH` | ❌ | 数据库路径 | `data/tieba_mecha.db` |
| `TIEBA_MECHA_LOG_LEVEL` | ❌ | 日志级别 | `INFO` |
| `TIEBA_MECHA_DEBUG` | ❌ | 调试模式 | `false` |
| `TIEBA_MECHA_WEB_PASSWORD_RESET` | ❌ | Web 密码重置（设为 `true` 启动时清除密码） | 无 |

### AI 改写配置

在 Web 界面的「系统设置」页面配置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `ai_api_key` | LLM API 密钥 | 无 |
| `ai_base_url` | API 地址 | 智谱 AI |
| `ai_model` | 模型名称 | glm-4-flash |
| `ai_system_prompt` | 自定义提示词 | 内置 SEO 优化提示词 |

支持的 AI 服务商：
- 智谱 AI (glm-4-flash, glm-4)
- DeepSeek (deepseek-chat)
- OpenAI 兼容接口 (需自定义 base_url)

### 发帖策略

| 策略 | 说明 |
|------|------|
| `round_robin` | 轮询：按顺序依次使用账号 |
| `random` | 随机：每次随机选择一个账号 |
| `weighted` | 加权：根据账号权重 (post_weight) 概率选择 |

### 拟人延迟参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `delay_min` | 最小延迟（秒） | 60 |
| `delay_max` | 最大延迟（秒） | 300 |
| 生理节律 | 凌晨 1-7 点延迟自动增加 1.5-2.2 倍 | 启用 |

### 后台守护进程任务

| 任务 | 执行频率 | 说明 |
|------|----------|------|
| 自动签到 | Cron 表达式可配置 | 按设定时间执行全域签到 |
| 自动监控 | 每 10 分钟 | 扫描目标贴吧，执行自动化规则 |
| 批量发帖轮询 | 每 30 分钟 | 检查并执行到期的定时发帖任务 |
| 自动回帖 (Auto-Bump) | 每 20 分钟 | 维护已发帖子的热度 |
| 拟人养号 (BioWarming) | 每 4 小时 | 模拟真人浏览互动 |
| 授权心跳 | 每 6 小时 | 校验 Pro 授权状态 |
| 更新检测 | 每 12 小时 | 检查 GitHub Releases 新版本 |

---

## 📁 项目结构

```
TiebaMecha/
├── src/tieba_mecha/
│   ├── cli/                 # 命令行接口
│   │   └── main.py          # Typer CLI 入口
│   ├── core/                # 核心业务逻辑
│   │   ├── account.py       # 账号管理与凭证加解密
│   │   ├── ai_optimizer.py  # AI SEO 优化器（6 种人格预设）
│   │   ├── auto_rule.py     # 自动化规则引擎
│   │   ├── batch_post.py    # 矩阵发帖核心 & Auto-Bump
│   │   ├── client_factory.py # 账号级设备指纹客户端工厂
│   │   ├── crawl.py         # 数据爬取引擎
│   │   ├── daemon.py        # 后台守护进程 (APScheduler)
│   │   ├── link_manager.py  # 短链管理连接器
│   │   ├── logger.py        # 异步日志 & UI 实时流
│   │   ├── maintenance.py   # BioWarming 拟人养号引擎
│   │   ├── notification.py  # 全域通知中心
│   │   ├── obfuscator.py    # 零宽字符混淆器
│   │   ├── plugin_loader.py # 插件沙箱加载器
│   │   ├── post.py          # 帖子操作
│   │   ├── proxy.py         # 代理管理
│   │   ├── sign.py          # 签到引擎
│   │   ├── updater.py       # GitHub Releases 更新检测
│   │   ├── auth.py          # 授权 & 硬件指纹验证
│   │   └── web_auth.py      # Web 密码认证 (PBKDF2)
│   ├── db/                  # 数据库层
│   │   ├── models.py        # SQLAlchemy 模型 (15 张表)
│   │   └── crud.py          # 异步 CRUD 操作 (40+ 方法)
│   ├── web/                 # Web UI (Flet)
│   │   ├── app.py           # 主应用 & 路由管理
│   │   ├── components/      # UI 组件 (HUD、通知铃铛、主题等)
│   │   └── pages/           # 页面模块 (13 个页面)
│   └── assets/              # 静态资源 (图标等)
├── data/                    # 数据目录（不纳入版本控制）
│   └── tieba_mecha.db       # SQLite 数据库
├── plugins/                 # 插件目录
├── tests/                   # 测试用例 (30+ 自动化测试)
├── scripts/                 # 实用脚本
├── docs/                    # 项目文档
├── .env.example             # 环境变量模板
├── pyproject.toml           # 项目配置
├── Dockerfile               # Docker 镜像定义
├── docker-compose.yml       # Docker Compose 编排
└── README.md
```

---

## 🔗 技术栈

| 组件 | 技术 |
|------|------|
| **核心 API** | [aiotieba](https://github.com/Starry-S/aiotieba) - 异步贴吧 API 库 |
| **Web UI** | [Flet](https://flet.dev/) 0.23.2 - Flutter for Python |
| **数据库** | SQLAlchemy 2.0 + aiosqlite |
| **CLI** | Typer + Rich |
| **加密** | cryptography (AES-256-GCM) |
| **AI** | OpenAI 兼容 API (智谱/DeepSeek/...) |
| **调度** | APScheduler - 后台守护进程 |
| **HTTP** | aiohttp + httpx + aiohttp_socks |
| **校验** | Pydantic 2.0 |

---

## 🛡️ 反风控技术栈

TiebaMecha 内置多重反检测机制，最大程度降低被平台风控的风险：

### 1. 拟人化延迟 (BionicDelay™)
- **高斯分布**：延迟时间遵循正态分布，拒绝机械的固定间隔
- **生理节律**：凌晨 1-7 点自动增加 1.5-2.2 倍延迟，模拟真人作息
- **边界保护**：自动裁剪异常值，避免极端延迟

### 2. 零宽字符混淆 (Obfuscator)
- **零宽注入**：在标题和正文中注入不可见零宽字符，绕过内容指纹检测
- **间距人性化**：智能调整文本间距，模拟人工输入习惯
- **密度可调**：支持自定义混淆密度（0.1-0.5）

### 3. 内部速率墙 (RateLimiter)
- **滑动窗口**：基于滑动时间窗的令牌限流
- **动态阻塞**：超过阈值（默认 15 帖/分钟）自动休眠等待
- **智能唤醒**：避免请求堆叠，平滑流量曲线

### 4. 账号指纹隔离
- **独立 CUID**：每个账号分配唯一设备标识
- **独立 UA**：随机化 User-Agent 指纹
- **代理绑定**：支持账号级代理 IP 隔离

---

## 🔌 插件开发

TiebaMecha 支持插件扩展。在 `plugins/` 目录下创建 Python 文件：

```python
# plugins/my_plugin.py
from tieba_mecha.core.plugin_loader import PluginBase

class MyPlugin(PluginBase):
    name = "我的插件"
    description = "插件描述"
    version = "1.1.1"
    author = "Your Name"

    async def on_load(self):
        """插件加载时调用"""
        print("插件已加载")

    async def on_unload(self):
        """插件卸载时调用"""
        print("插件已卸载")

    async def on_post_success(self, tid: int, fname: str, account_id: int):
        """发帖成功后的回调"""
        print(f"帖子发布成功: TID={tid} @ {fname} by Account#{account_id}")

    async def on_post_failed(self, error: str, fname: str):
        """发帖失败后的回调"""
        print(f"发帖失败: {fname} - {error}")

    async def on_sign_complete(self, fname: str, success: bool):
        """签到完成后的回调"""
        status = "成功" if success else "失败"
        print(f"签到{status}: {fname}")
```

### 可用钩子列表

| 钩子函数 | 触发时机 | 参数 |
|----------|----------|------|
| `on_load()` | 插件加载 | 无 |
| `on_unload()` | 插件卸载 | 无 |
| `on_post_success()` | 发帖成功 | `tid`, `fname`, `account_id` |
| `on_post_failed()` | 发帖失败 | `error`, `fname` |
| `on_sign_complete()` | 签到完成 | `fname`, `success` |
| `on_material_added()` | 物料添加 | `material_id`, `title` |
| `on_account_expired()` | 账号失效 | `account_id`, `reason` |

---

## ❓ 常见问题

### 发帖后不显示？
- 检查账号是否被限流或封禁
- 确认内容不含敏感词
- 尝试更换 IP 或等待一段时间
- 检查目标贴吧是否有发帖门槛（等级/会员限制）

### 账号验证失败？
- BDUSS 可能已过期，重新登录获取
- 检查网络连接和代理设置
- 确认 BDUSS 格式正确（通常以 `FD...` 开头）

### AI 改写不生效？
- 确认已在设置中配置 API Key
- 检查 API 余额和网络连接
- 确认 Base URL 正确（智谱/DeepSeek/OpenAI 格式不同）
- 查看控制台日志确认错误原因

### 数据库迁移错误？
- 程序启动时会自动迁移缺失的列
- 如有问题，可删除 `data/tieba_mecha.db` 重新初始化
- 重要数据请先备份

### 如何获取 BDUSS 和 STOKEN？
1. 登录 [百度贴吧](https://tieba.baidu.com)
2. 按 F12 打开开发者工具
3. 切换到 Application → Cookies
4. 找到 `BDUSS` 和 `STOKEN` 的值

### 代理设置不生效？
- 确认代理格式正确（`http://ip:port` 或 `socks5://ip:port`）
- 检查代理服务器是否在线
- 认证代理需要填写用户名和密码
- 测试代理连通性后再绑定账号

### Docker 容器无法启动？
- 检查 `.env` 文件是否存在且配置正确
- 确认端口 9006 未被占用
- 查看容器日志：`docker logs tieba-mecha`

### 忘记 Web 访问密码？
在 `.env` 文件中添加以下配置后重启应用：
```bash
TIEBA_MECHA_WEB_PASSWORD_RESET=true
```
启动后会自动清除密码并恢复访问。进入**系统设置 → 安全**重新设置密码，然后删除该配置项。

---

## 🗺️ 功能路线图

| 状态 | 功能 | 说明 |
|:----:|------|------|
| ✅ | 矩阵发帖终端 | 多账号、多贴吧、多策略、战术向导配置 |
| ✅ | 拟人化风控引擎 | 高斯延迟、生理节律 |
| ✅ | AI 智能改写 | 智谱/DeepSeek/OpenAI，6 种人格预设 |
| ✅ | 自动回帖 (Auto-Bump) | 帖子热度维护 |
| ✅ | 拟人化养号 (BioWarming™) | 浏览模拟、随机点赞、新号冷启动破冰 |
| ✅ | 帖子存活分析 | 存活追踪、死亡原因分类、多维筛选 |
| ✅ | 战术情报分析 | 拦截原因解析、实战应对建议 |
| ✅ | 全域通知中心 | 本地 + 远程通知 |
| ✅ | 自动更新检测 | GitHub Releases |
| ✅ | 全域签到引擎 | 批量签到、跨账号矩阵签到、贴吧同步 |
| 🔜 | 云控中心 | 多节点统一管理 |
| 🔜 | 数据分析仪表盘 | 可视化统计报表 |
| 🔜 | 移动端适配 | 响应式 Web UI |

---

## ⚠️ 免责声明

本工具仅供学习研究与个人效率提升使用。在使用过程中请严格遵守百度贴吧平台规范，严禁从事违法违规活动。因使用不当造成的风险由用户自行承担。

---

## 📜 开源协议

[MIT License](LICENSE)

---

## 🤝 贡献指南

欢迎贡献代码、报告问题或提出建议！

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

### 开发环境设置

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码格式检查
ruff check src/
```

---

## 📞 支持与反馈

- **问题反馈**：[GitHub Issues](https://github.com/hwdemtv/TiebaMecha/issues)
- **功能建议**：[GitHub Discussions](https://github.com/hwdemtv/TiebaMecha/discussions)
- **更新日志**：[GitHub Releases](https://github.com/hwdemtv/TiebaMecha/releases)

---

## 🙏 致谢

- [aiotieba](https://github.com/Starry-S/aiotieba) - 强大的异步贴吧 API 库
- [Flet](https://flet.dev/) - 优雅的 Python UI 框架
