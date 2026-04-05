"""
TiebaMecha .env 一键生成器 (Portable Edition)
用于在非系统开发环境下快速初始化加密密钥。
"""

import os
import secrets
from pathlib import Path

def setup_env():
    # 定位到当前运行根目录
    base_dir = Path(__file__).parent.absolute()
    env_file = base_dir / ".env"
    
    print("=" * 50)
    print("   TiebaMecha 环境初始化工具")
    print("=" * 50)
    
    if env_file.exists():
        print(f"\n[已存在] 检测到已有的 .env 配置文件。")
        print("为了保护您的现有账号数据和加密配置，脚本不会进行覆盖。")
        print("如果需要重新生成，请先备份并手动删除现有的 .env 文件。")
        input("\n按回车键退出...")
        return

    print("\n[初始化] 正在为您生成高强度安全加密密钥...")
    
    # 生成各 64 位（32 字节十六进制）随机密钥
    salt = secrets.token_hex(32)
    secret_key = secrets.token_hex(32)
    
    env_content = f"""# TiebaMecha 核心配置文件 (自动生成)
# ⚠️ 请妥善保管本文件，丢失密钥将导致已存储的账号凭证无法解密。

# 核心安全密钥 (加密 BDUSS/STOKEN 使用)
TIEBA_MECHA_SALT={salt}
TIEBA_MECHA_SECRET_KEY={secret_key}

# 网络与界面配置
# TIEBA_MECHA_HOST=127.0.0.1
# TIEBA_MECHA_PORT=9006
"""
    
    try:
        env_file.write_text(env_content, encoding="utf-8")
        print("\n[成功] .env 配置文件已生成！")
        print(f"路径: {env_file}")
        print("\n提示: 现在您可以运行 '启动机甲.bat' 访问 Web 界面了。")
    except Exception as e:
        print(f"\n[失败] 写入文件时发生错误: {e}")
    
    input("\n按回车键继续...")

if __name__ == "__main__":
    setup_env()
