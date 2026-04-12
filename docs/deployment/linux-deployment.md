# TiebaMecha Linux 部署与运维手册

本文档介绍如何在 Linux 服务器（以 **Ubuntu 24.04** 为例）上部署 TiebaMecha，涵盖 Docker 部署与源码手动安装两种方案，并包含关键的数据安全与维护指南。

---

## 1. 方案选择

| 方案 | 适用场景 | 优势 |
| :--- | :--- | :--- |
| **Docker 部署** | 生产环境、挂机服务器 | 隔离性强、自愈性好（崩溃自动重启）、环境零污染。 |
| **源码手动安装** | 开发者、WSL2、轻量级运行 | 调试方便、不占用磁盘空间（无需镜像）、响应稍快。 |

---

## 2. Docker 部署 (推荐)

确保已安装 [Docker](https://docs.docker.com/get-docker/) 和 [Docker Compose](https://docs.docker.com/compose/install/)。

### 部署步骤
1.  **环境初始化**：
    ```bash
    mkdir -p TiebaMecha && cd TiebaMecha
    cp .env.example .env
    ```
2.  **启动服务**：
    ```bash
    docker-compose up -d
    ```
3.  **查看状态**：`docker-compose ps` 或 `docker-compose logs -f`。

---

## 3. Ubuntu 24.04 源码手动安装

### 第一步：安装系统层依赖
Flet UI 在 Linux 上运行需要底层图形与总线库：
```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 libgtk-3-0 libpangocairo-1.0-0 libdbus-1-3
```

### 第二步：源码下载与环境建立
```bash
git clone https://github.com/hwdemtv/TiebaMecha.git
cd TiebaMecha

# 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装项目
pip install --upgrade pip
pip install -e .
```

### 第三步：安全密钥配置 (必须)
项目启动前必须在 `.env` 中配置加密盐值，否则无法加密存储 Cookie。

1. **生成随机密钥**：
   ```bash
   python3 -c "import secrets; print(f'TIEBA_MECHA_SALT={secrets.token_hex(32)}'); print(f'TIEBA_MECHA_SECRET_KEY={secrets.token_hex(32)}')"
   ```
2. **写入配置**：
   复制并编辑 `.env`：
   ```bash
   cp .env.example .env
   nano .env # 填入生成的两行密钥
   ```

### 第四步：启动与后台运行
```bash
# 直接启动
python3 start_web.py

# 后台持续运行 (推荐)
nohup python3 start_web.py > server.log 2>&1 &
```

---

## 4. 数据迁移 (导入旧数据)

如果你要从 Windows 或其他服务器迁移数据，请按照以下流程：

1.  **拷贝数据库**：将旧环境的 `data/tieba_mecha.db` 拷贝到新环境的 `data/` 目录。
2.  **同步密钥**：**必须**将旧环境 `.env` 文件中的 `TIEBA_MECHA_SALT` 和 `TIEBA_MECHA_SECRET_KEY` 完整复制并覆盖到新环境。
    > [!CAUTION]
    > 如果密钥不同步，数据库中的所有账号 Cookie 将无法解密，导致账号失效。

---

## 5. 版本更新与维护

### 使用 Git 拉取更新
```bash
cd TiebaMecha
git pull origin master
# 重启服务使新代码生效
```

### 手动热修复 (Hotfix)
若仅需修复单个文件，可直接使用 `nano` 编辑对应文件：
```bash
nano src/tieba_mecha/core/auth.py
# 修改后 Ctrl+O 保存，Ctrl+X 退出，重启项目
```

### 常见问题排查
- **硬件 ID 报错**：确保 `src/tieba_mecha/core/auth.py` 中已包含 `import os`。
- **端口占用**：默认端口为 `9006`，可通过 `python3 start_web.py 9007` 修改端口。
- **权限问题**：若提示权限不足，请确保对 `data/` 目录有写入权限：`chmod -R 777 data/`。

---

## 6. 设置开机自启 (Systemd)

为了确保服务器重启后 TiebaMecha 能自动运行，建议配置 systemd 服务。

### 1. 创建服务文件
```bash
sudo nano /etc/systemd/system/tieba-mecha.service
```

### 2. 写入配置
将以下内容粘贴进去（请根据实际 `User` 和 `WorkingDirectory` 修改）：
```ini
[Unit]
Description=TiebaMecha Web Service
After=network.target

[Service]
User=hw
Group=hw
WorkingDirectory=/home/hw/TiebaMecha
ExecStart=/home/hw/TiebaMecha/.venv/bin/python3 start_web.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### 3. 启用服务
```bash
sudo systemctl daemon-reload
sudo systemctl enable tieba-mecha
sudo systemctl start tieba-mecha
```

### 4. 常用管理命令
- **查看状态**：`sudo systemctl status tieba-mecha`
- **实时日志**：`journalctl -u tieba-mecha -f`
- **重启服务**：`sudo systemctl restart tieba-mecha`
- **停止服务**：`sudo systemctl stop tieba-mecha`
