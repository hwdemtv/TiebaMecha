"""
TiebaMecha 打包器 v2.0 - 绿色便携版构建脚本 (Portable Distribution)
作者: Antigravity AI
功能: 自动集成 3.11 嵌入版 Python，安装依赖并打包为独立目录。
"""

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# --- 项目配置 ---
PROJECT_NAME = "TiebaMecha_Portable"
PYTHON_VERSION = "3.11.9"
PYTHON_ZIP_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"

def get_root():
    return Path(__file__).parent.parent.absolute()

# --- 国内镜像与源配置 ---
PYTHON_MIRRORS = [
    f"https://mirrors.huaweicloud.com/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip",
    f"https://pypi.tuna.tsinghua.edu.cn/python-ftp/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip",
    "https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip" 
]
PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"

def build_portable():
    root = get_root()
    dist_dir = root / "dist"
    portable_dir = dist_dir / PROJECT_NAME
    runtime_dir = portable_dir / "_runtime"
    
    # 1. 清理环境
    print(f"[1/6] 准备分发目录: {portable_dir}")
    if portable_dir.exists():
        shutil.rmtree(portable_dir)
    portable_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(exist_ok=True)

    # 2. 获取嵌入式 Python
    zip_path = dist_dir / "python_embed.zip"
    resource_zip = root / "scripts" / "resources" / "python_embed.zip"
    
    if not zip_path.exists():
        # 优先从本地资源目录查找
        if resource_zip.exists():
            print(f"[2/6] 检测到本地内置资源，正在复制: {resource_zip}")
            shutil.copy2(resource_zip, zip_path)
        else:
            print(f"[2/6] 正在获取 Python {PYTHON_VERSION} 嵌入版...")
            success = False
            for url in PYTHON_MIRRORS:
                try:
                    print(f"   尝试从源下载: {url.split('/')[2]} ...")
                    urllib.request.urlretrieve(url, zip_path)
                    success = True
                    break
                except Exception as e:
                    print(f"   该源不可用: {e}")
            
            if not success:
                print("   [ERROR] 所有下载源均失败，请手动下载并将文件放入 scripts/resources/python_embed.zip")
                return

    print("   正在解压 Python 运行时...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(runtime_dir)

    # 3. 配置 Python _pth 文件 (这是嵌入版加载 site-packages 的关键)
    print("[3/6] 配置嵌入式环境路径...")
    # 获取主要版本号，如 3.11 -> 311
    ver_tag = "".join(PYTHON_VERSION.split(".")[:2])
    pth_file = runtime_dir / f"python{ver_tag}._pth"
    
    # 无论是否存在都重新写入，确保路径正确
    new_content = [
        f"python{ver_tag}.zip",
        ".",
        "site-packages",
        "import site",  # 启用 site 模块以支持 .pth 文件
    ]
    pth_file.write_text("\n".join(new_content))

    # 4. 安装依赖
    print("[4/6] 注入应用依赖 (pip install --target)...")
    sp_dir = runtime_dir / "site-packages"
    sp_dir.mkdir(exist_ok=True)
    
    # 定义核心依赖列表 (确保与 pyproject.toml 一致)
    deps = [
        "aiotieba",
        "flet==0.23.2",  # 锁定已知稳定版本
        "typer>=0.9.0",
        "sqlalchemy[asyncio]>=2.0",
        "aiosqlite>=0.19.0",
        "apscheduler>=3.10",
        "cryptography>=41.0",
        "python-dotenv>=1.0.0",
        "aiohttp>=3.8.0",
        "aiohttp_socks>=0.8.0",
        "httpx>=0.25.0",
        "rich>=13.0",
        "pydantic>=2.0",
    ]
    
    try:
        print(f"   正在安装: {', '.join(deps[:3])} 等 (使用清华源)...")
        subprocess.run([
            sys.executable, "-m", "pip", "install", 
            "-i", PIP_INDEX_URL,
            "--target", str(sp_dir),
            *deps
        ], check=True, capture_output=True)
        print("   依赖注入成功。")
            
    except subprocess.CalledProcessError as e:
        print(f"   依赖安装失败: {e.stderr.decode('utf-8', errors='ignore')}")
        return

    # 填充业务源码与辅助工具
    print("[5/6] 同步 TiebaMecha 业务核心源码与初始化工具...")
    shutil.copytree(root / "src" / "tieba_mecha", portable_dir / "tieba_mecha")
    
    # 填充各种模板
    tmpls = {
        "launcher.py": "launcher.py",
        "setup_env.py": "setup_env.py",
    }
    for src_name, dst_name in tmpls.items():
        src_p = root / "scripts" / "templates" / src_name
        if src_p.exists():
            shutil.copy2(src_p, portable_dir / dst_name)
    
    # 填充配置文件与文档
    for f in [".env.example", "README.md"]:
        if (root / f).exists():
            shutil.copy2(root / f, portable_dir / f)

    # 复制网盘精准配对导入模板
    template_file = root / "网盘精准配对导入模板.csv"
    if template_file.exists():
        shutil.copy2(template_file, portable_dir / "网盘精准配对导入模板.csv")

    # 创建 data 目录
    (portable_dir / "data").mkdir(exist_ok=True)

    # 6. 生成用户启动与初始化入口 (BAT)
    print("[6/6] 生成用户便捷入口 (启动机甲.bat & 首次运行配置.bat)...")
    
    # 启动脚本
    run_bat = f"""@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo [TiebaMecha] 正在唤醒核心引擎...
_runtime\\python.exe launcher.py
if %ERRORLEVEL% neq 0 pause
endlocal
"""
    (portable_dir / "启动机甲.bat").write_text(run_bat, encoding="utf-8")

    # 初始化脚本
    setup_bat = f"""@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo [TiebaMecha] 正在执行首次环境握手...
_runtime\\python.exe setup_env.py
endlocal
"""
    (portable_dir / "首次运行(生成密钥).bat").write_text(setup_bat, encoding="utf-8")

    print("\n" + "=" * 50)
    print(f" [DONE] 绿色便携版打包完成！")
    print(f" 存储路径: {portable_dir}")
    print(f" 请将上述目录打包发送给用户，点击 '启动.bat' 即可。")
    print("=" * 50)

if __name__ == "__main__":
    try:
        build_portable()
    except Exception as e:
        print(f"\n[FATAL] 打包脚本崩溃: {e}")
        import traceback
        traceback.print_exc()
