# TiebaMecha Linux 与 Docker 部署手册

本文档介绍如何在 Linux 服务器或容器化环境中部署 TiebaMecha，实现 24 小时无人值守自动化运行。

---

## 1. 方案选择 (Docker vs 物理机)

### 推荐方案：Docker 部署
Docker 方案具备以下优势：
- **隔离性**：环境独立，不污染宿主机系统。
- **一致性**：解决 Flet 在 Linux 上的底层依赖库问题。
- **自愈性**：支持容器挂死自动重启。

---

## 2. Docker 部署 (一键启动)

### 准备工作
- 安装 [Docker](https://docs.docker.com/get-docker/)。
- 安装 [Docker Compose](https://docs.docker.com/compose/install/)。

### 部署步骤
1.  **准备配置文件**：将项目的 `docker-compose.yml` 和 `Dockerfile` 复制到服务器目录。
2.  **创建数据目录**：
    ```bash
    mkdir -p data logs
    ```
3.  **启动容器**：
    ```bash
    docker-compose up -d
    ```

### 服务管理
- **查看运行状态**：`docker-compose ps`
- **查看实时日志**：`docker-compose logs -f`
- **重启服务**：`docker-compose restart`

---

## 3. Linux 物理机部署 (Ubuntu/Debian)

如果您不希望使用容器，可以手动安装 Python 环境：

### 安装依赖
```bash
sudo apt-get update && sudo apt-get install -y \
    python3-pip python3-venv \
    libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
    libgtk-3-0 libpangocairo-1.0-0 libdbus-1-3
```

### 运行应用
```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装项目
pip install .

# 启动 (后台运行)
nohup python3 start_web.py 9006 > logs/server.log 2>&1 &
```

---

## 4. 关键配置与运维

### 端口访问 (防火墙)
- 请确保服务器防火墙（如 `ufw`, `iptables`）已开放 **9006** 端口。
- 默认为 `http://服务器IP:9006`，建议在正式生产环境前加设 Nginx 反向代理并开启 SSL。

### 授权状态说明
- **HWID 识别**：在 Linux 下，系统会读取 `/etc/machine-id` 或 MAC 地址生成硬件指纹。
- **稳定性**：只要不更换服务器或大规模修改硬件，HWID 将保持唯一且稳定。

### 数据备份
- 核心配置文件：`data/tieba_mecha.db`
- 定期备份整个 `data/` 目录即可实现无损数据迁移。
