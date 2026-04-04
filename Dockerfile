# 使用轻量级 Python 3.11 镜像
FROM python:3.11-slim-bookworm

# 设置环境变量，保持 Python 输出实时显示并指定工作目录
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# 设置工作目录
WORKDIR /app

# 安装底层系统依赖 (Flet 虽然在 Web 运行，但 Python 包内部可能链接某些基础库)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0 \
    libgtk-3-0 \
    libpangocairo-1.0-0 \
    libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
# 优先复制 pyproject.toml 利用 Docker 缓存
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 复制项目源代码
COPY . .

# 创建数据存储目录并设置权限
RUN mkdir -p /app/data && chmod 777 /app/data

# 暴露 Flet 默认端口
EXPOSE 9006

# 启动命令：指定运行在 0.0.0.0 以便外部访问
CMD ["python", "start_web.py", "9006"]
