# TiebaMecha

赛博机甲风格的百度贴吧管理工具，支持 CLI 和 Web 双界面。

## 安装

```bash
git clone https://github.com/your-username/TiebaMecha.git
cd TiebaMecha
pip install -e .
```

## 安全配置（必须）

TiebaMecha 使用强加密存储您的 BDUSS/STOKEN 凭证。首次使用前，必须配置加密密钥：

### 步骤 1: 生成密钥

使用以下任一方法生成至少 64 位十六进制字符的随机密钥：

**Python:**
```python
import secrets
print(f"TIEBA_MECHA_SALT={secrets.token_hex(32)}")
print(f"TIEBA_MECHA_SECRET_KEY={secrets.token_hex(32)}")
```

**Linux/Mac:**
```bash
echo "TIEBA_MECHA_SALT=$(openssl rand -hex 32)"
echo "TIEBA_MECHA_SECRET_KEY=$(openssl rand -hex 32)"
```

**Windows PowerShell:**
```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RNGCryptoServiceProvider]::Create().GetBytes($bytes)
"TIEBA_MECHA_SALT=$($bytes | ForEach-Object { $_.ToString('x2') })" -replace '\s',''
```

### 步骤 2: 创建 .env 文件

```bash
# 复制示例文件
cp .env.example .env

# 编辑 .env 文件,填入生成的密钥
notepad .env  # Windows
nano .env     # Linux/Mac
```

`.env` 文件示例:
```env
TIEBA_MECHA_SALT=a1b2c3d4e5f6...（64位十六进制）
TIEBA_MECHA_SECRET_KEY=9f8e7d6c5b4a...（64位十六进制）
```

### 安全说明

⚠️ **重要提示:**
- **绝不要**将 `.env` 文件提交到 Git 或公开分享
- 丢失密钥将导致已加密的凭证无法解密
- 建议定期更换密钥并重新导入账号
- `.env` 已在 `.gitignore` 中,确保不会被意外提交

## 使用方法

### CLI 命令

```bash
# 查看帮助
tieba --help

# 添加账号
tieba account add --bduss YOUR_BDUSS --stoken YOUR_STOKEN

# 查看账号列表
tieba account list

# 签到
tieba sign run              # 签到所有贴吧
tieba sign run "贴吧名"     # 签到指定贴吧

# 帖子管理
tieba post list "贴吧名" --page 1
tieba post search "关键词" --forum "贴吧名"

# 数据爬取
tieba crawl threads "贴吧名" --pages 5
tieba crawl user 123456789

# 启动 Web 界面
tieba web
tieba web --port 3000
```

### Web 界面

```bash
tieba web
```

然后打开浏览器访问 http://localhost:8080

## 功能模块

| 模块 | CLI | Web |
|------|-----|-----|
| 账号管理 | `tieba account` | 账号页面 |
| 批量签到 | `tieba sign` | 签到页面 |
| 帖子管理 | `tieba post` | 帖子页面 |
| 数据爬取 | `tieba crawl` | 爬取页面 |

## UI 风格

采用赛博机甲 (Cyber-Mecha) 风格设计：
- 护眼暗色主题 + 明亮实验舱主题
- HUD 翼板数据展示
- 渐变按钮 + 发光效果
- 流式列表动效

## 获取 BDUSS/STOKEN

1. 登录百度贴吧网页版
2. 打开浏览器开发者工具 (F12)
3. 在 Application → Cookies 中找到 `BDUSS` 和 `STOKEN`

## 依赖

- aiotieba - 贴吧 API 库
- flet - Web UI 框架
- typer - CLI 框架
- sqlalchemy - 数据库 ORM
