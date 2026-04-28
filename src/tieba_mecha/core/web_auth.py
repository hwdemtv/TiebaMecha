"""Web 密码认证管理 - PBKDF2 哈希存储"""

from __future__ import annotations

import hashlib
import os
import base64

# 密码哈希在 settings 表中的 key
_PASSWORD_HASH_KEY = "web_password_hash"
# PBKDF2 迭代次数（与项目其他加密保持一致量级）
_ITERATIONS = 600_000
_SALT_LENGTH = 32


def hash_password(password: str) -> str:
    """对密码进行 PBKDF2-HMAC-SHA256 哈希，返回 salt$hash 格式"""
    salt = os.urandom(_SALT_LENGTH)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    """验证密码是否与存储的哈希匹配"""
    try:
        salt_b64, hash_b64 = stored.split("$", 1)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
        return dk == expected
    except Exception:
        return False


async def is_password_set(db) -> bool:
    """检查是否已设置 Web 密码"""
    hashed = await db.get_setting(_PASSWORD_HASH_KEY, "")
    return bool(hashed)


async def set_password(db, password: str) -> None:
    """存储密码哈希到数据库"""
    hashed = hash_password(password)
    await db.set_setting(_PASSWORD_HASH_KEY, hashed)


async def check_password(db, password: str) -> bool:
    """检查输入的密码是否正确"""
    hashed = await db.get_setting(_PASSWORD_HASH_KEY, "")
    if not hashed:
        return False
    return verify_password(password, hashed)


async def clear_password(db) -> None:
    """清除密码（关闭认证）"""
    await db.set_setting(_PASSWORD_HASH_KEY, "")
