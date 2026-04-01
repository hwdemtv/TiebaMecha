# 🤖 TiebaMecha (贴吧机甲)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-hwdemtv%2FTiebaMecha-black.svg)](https://github.com/hwdemtv/TiebaMecha)

**赛博机甲风格的一站式百度贴吧自动化管理平台。**
集成了 **拟人化风控引擎**、**矩阵发帖终端** 与 **智能自动回帖 (Auto-Bump)**，旨在为用户提供最安全、最高效的贴吧运营体验。

---

## 🌟 核心特性

### 1. 矩阵发帖终端 (Matrix Post Terminal)
- **多号轮询/随机/加权**：支持数十个账号同步作业，内置多种调度算法防止单点封禁
- **多贴吧轰炸**：单次任务可投递至多个目标贴吧，全自动轮换
- **AI 智能附魔**：深度集成大语言模型（智谱/DeepSeek/OpenAI 兼容），发帖前自动对标题与正文进行语义改写，确保每条信息在算法眼中都是"原创"
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

### 6. 拟人化养号 (BioWarming™) [NEW]
- **深度模拟**：自动模拟真人进入贴吧浏览、翻页、停顿阅读
- **随机互动**：在浏览过程中随机执行点赞（Agree）交互，建立健康活跃画像
- **守护进程调度**：后台 4 小时周期性自动运行，全自动维护账号权重

---

## 🚀 快速开始

### 方式一：便携版（推荐新手）

1. 下载 `TiebaMecha-portable.zip` 并解压
2. 运行 `首次运行配置.bat` 设置加密密钥
3. 运行 `启动Web界面.bat`
4. 浏览器访问 http://localhost:9006

### 方式二：源码安装（开发者）

```bash
# 克隆仓库
git clone https://github.com/hwdemtv/TiebaMecha.git
cd TiebaMecha

# 安装依赖
pip install -e .
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
| **数据爬取** | 帖子爬取、用户画像、历史记录 |
| **自动化规则** | 关键词监控、自动删帖、正则匹配 |
| **拟人化养号** | 自动浏览、随性点赞、权重维护、定时调度 | ✅ |
| **矩阵发帖** | 物料池管理、AI 改写、定时任务、自动回帖 | ✅ |
| **插件系统** | 扩展功能加载与管理 | ✅ |
| **系统设置** | AI 配置、全局参数、安全选项 |

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

---

## 📁 项目结构

```
TiebaMecha/
├── src/tieba_mecha/
│   ├── cli/                 # 命令行接口
│   │   └── main.py          # Typer CLI 入口
│   ├── core/                # 核心业务逻辑
│   │   ├── account.py       # 账号管理与凭证加解密
│   │   ├── sign.py          # 签到引擎
│   │   ├── post.py          # 帖子操作
│   │   ├── batch_post.py    # 矩阵发帖核心
│   │   ├── ai_optimizer.py  # AI SEO 优化器
│   │   ├── daemon.py        # 后台守护进程
│   │   ├── proxy.py         # 代理管理
│   │   └── ...
│   ├── db/                  # 数据库层
│   │   ├── models.py        # SQLAlchemy 模型
│   │   └── crud.py          # 异步 CRUD 操作
│   └── web/                 # Web UI
│       ├── app.py           # Flet 主应用
│       ├── components/      # UI 组件
│       └── pages/           # 页面模块
├── data/                    # 数据目录（不纳入版本控制）
│   └── tieba_mecha.db       # SQLite 数据库
├── plugins/                 # 插件目录
├── tests/                   # 测试用例
├── scripts/                 # 实用脚本
├── .env.example             # 环境变量模板
├── pyproject.toml           # 项目配置
└── README.md
```

---

## 🔗 技术栈

| 组件 | 技术 |
|------|------|
| **核心 API** | [aiotieba](https://github.com/Starry-S/aiotieba) - 异步贴吧 API 库 |
| **Web UI** | [Flet](https://flet.dev/) - Flutter for Python |
| **数据库** | SQLAlchemy 2.0 + aiosqlite |
| **CLI** | Typer + Rich |
| **加密** | cryptography (AES-256-GCM) |
| **AI** | OpenAI 兼容 API (智谱/DeepSeek/...) |

---

## 🔌 插件开发

TiebaMecha 支持插件扩展。在 `plugins/` 目录下创建 Python 文件：

```python
# plugins/my_plugin.py
from tieba_mecha.core.plugin_loader import PluginBase

class MyPlugin(PluginBase):
    name = "我的插件"
    description = "插件描述"

    async def on_load(self):
        print("插件已加载")

    async def on_post_success(self, tid: int, fname: str):
        """发帖成功后的回调"""
        print(f"帖子发布成功: {tid} @ {fname}")
```

---

## ❓ 常见问题

### 发帖后不显示？
- 检查账号是否被限流或封禁
- 确认内容不含敏感词
- 尝试更换 IP 或等待一段时间

### 账号验证失败？
- BDUSS 可能已过期，重新登录获取
- 检查网络连接和代理设置

### AI 改写不生效？
- 确认已在设置中配置 API Key
- 检查 API 余额和网络连接

### 数据库迁移错误？
- 程序启动时会自动迁移缺失的列
- 如有问题，可删除 `data/tieba_mecha.db` 重新初始化

---

## ⚠️ 免责声明

本工具仅供学习研究与个人效率提升使用。在使用过程中请严格遵守百度贴吧平台规范，严禁从事违法违规活动。因使用不当造成的风险由用户自行承担。

---

## 📜 开源协议

[MIT License](LICENSE)

---

## 🙏 致谢

- [aiotieba](https://github.com/Starry-S/aiotieba) - 强大的异步贴吧 API 库
- [Flet](https://flet.dev/) - 优雅的 Python UI 框架
