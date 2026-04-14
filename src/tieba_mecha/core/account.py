import re
import base64
import os
from datetime import datetime
from dataclasses import dataclass

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from dotenv import load_dotenv

from ..db.crud import Database

# 加载环境变量
load_dotenv()

# 加密密钥 (实际使用时应从环境变量或配置文件读取)
_ENCRYPTION_KEY: bytes | None = None


def get_encryption_key() -> bytes:
    """获取加密密钥
    
    安全说明:
        - 必须通过环境变量提供密钥
        - 缺少环境变量时抛出异常,而非使用硬编码默认值
        - 生产环境应使用强随机密钥(至少32字节)
    """
    global _ENCRYPTION_KEY
    if _ENCRYPTION_KEY is None:
        # 从环境变量读取密钥(必须提供)
        salt_str = os.getenv("TIEBA_MECHA_SALT")
        secret_str = os.getenv("TIEBA_MECHA_SECRET_KEY")
        
        if not salt_str or not secret_str:
            raise ValueError(
                "安全配置缺失: 必须设置环境变量 TIEBA_MECHA_SALT 和 TIEBA_MECHA_SECRET_KEY\n"
                "生成方法(示例):\n"
                "  Python: import secrets; print(secrets.token_hex(32))\n"
                "  Linux/Mac: openssl rand -hex 32\n"
                "  或在 .env 文件中设置:\n"
                "    TIEBA_MECHA_SALT=<your-salt-hex>\n"
                "    TIEBA_MECHA_SECRET_KEY=<your-key-hex>"
            )

        if len(secret_str) < 32:
            raise ValueError("安全警告: TIEBA_MECHA_SECRET_KEY 长度必须 >= 32 字符以防暴力破解")

        salt = salt_str.encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600000,  # OWASP 推荐基准
        )
        _ENCRYPTION_KEY = base64.urlsafe_b64encode(kdf.derive(secret_str.encode()))
    return _ENCRYPTION_KEY


def encrypt_value(value: str) -> str:
    """加密字符串"""
    fernet = Fernet(get_encryption_key())
    return fernet.encrypt(value.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    """解密字符串"""
    fernet = Fernet(get_encryption_key())
    return fernet.decrypt(encrypted.encode()).decode()


@dataclass
class AccountInfo:
    """账号信息"""

    id: int
    name: str
    user_id: int
    user_name: str
    is_active: bool
    status: str = "unknown"
    cuid: str = ""
    user_agent: str = ""
    proxy_id: int | None = None
    post_weight: int = 5
    is_maint_enabled: bool = False
    last_maint_at: datetime | None = None


async def add_account(
    db: Database,
    name: str,
    bduss: str,
    stoken: str = "",
    proxy_id: int | None = None,
    verify: bool = True,
) -> AccountInfo:
    """
    添加账号

    Args:
        db: 数据库实例
        name: 账号名称/备注
        bduss: BDUSS
        stoken: STOKEN
        proxy_id: 关联代理ID
        verify: 是否验证账号并获取真实用户信息 (默认 True)

    Returns:
        AccountInfo: 账号信息
    """
    # 加密敏感信息
    encrypted_bduss = encrypt_value(bduss)
    encrypted_stoken = encrypt_value(stoken) if stoken else ""

    # 验证账号并获取真实用户信息
    user_id = 0
    user_name = ""
    status = "unknown"

    if verify:
        # 在验证阶段应用指纹（如果有）
        valid, uid, uname, error = await verify_account(bduss, stoken)
        if valid:
            user_id = uid
            user_name = uname
            status = "active"
        else:
            if "封禁" in error or "屏蔽" in error:
                status = "banned"
            else:
                status = f"invalid: {error[:50]}" if error else "invalid"

    account = await db.add_account(
        name=name,
        bduss=encrypted_bduss,
        stoken=encrypted_stoken,
        user_id=user_id,
        user_name=user_name,
        proxy_id=proxy_id,
    )

    # 更新状态
    if status != "unknown":
        await db.update_account_status(account.id, status)

    return AccountInfo(
        id=account.id,
        name=account.name,
        user_id=account.user_id,
        user_name=account.user_name,
        is_active=account.is_active,
        status=status,
        cuid=account.cuid,
        user_agent=account.user_agent,
        proxy_id=account.proxy_id,
        post_weight=getattr(account, 'post_weight', 5),
        is_maint_enabled=account.is_maint_enabled,
        last_maint_at=account.last_maint_at,
    )


async def get_account_credentials(db: Database, account_id: int | None = None) -> tuple[int, str, str, int | None, str, str] | None:
    """
    获取账号凭证 (解密后的 BDUSS、STOKEN 以及关联代理 ID)

    Args:
        db: 数据库实例
        account_id: 账号ID，None 则获取当前活跃账号

    Returns:
        (id, BDUSS, STOKEN, proxy_id, cuid, user_agent) 或 None
    """
    if account_id:
        accounts = await db.get_accounts()
        account = next((a for a in accounts if a.id == account_id), None)
    else:
        account = await db.get_active_account()

    if not account:
        return None

    try:
        bduss = decrypt_value(account.bduss)
        stoken = decrypt_value(account.stoken) if account.stoken else ""
        return account.id, bduss, stoken, account.proxy_id, account.cuid, account.user_agent
    except Exception:
        return None


async def verify_account(bduss: str, stoken: str = "", cuid: str = "", ua: str = "") -> tuple[bool, int, str, str]:
    """
    验证账号登录状态

    Args:
        bduss: BDUSS
        stoken: STOKEN

    Returns:
        (是否有效, user_id, user_name, error_msg)
    """
    from .client_factory import create_client

    try:
        # 使用 client_factory 创建客户端，确保指纹被正确应用
        async with await create_client(None, bduss, stoken, cuid=cuid, ua=ua) as client:
            user_info = await client.get_self_info()
            if user_info and user_info.user_id:
                # 优先使用 nick_name（用户昵称），其次 show_name，再 user_name，最后用 user_id
                # UserInfo 对象包含: nick_name, show_name, user_name, log_name 等字段
                display_name = (
                    getattr(user_info, 'nick_name', '')
                    or getattr(user_info, 'show_name', '')
                    or getattr(user_info, 'user_name', '')
                    or f"用户_{user_info.user_id}"
                )
                return True, user_info.user_id, display_name, ""
            else:
                return False, 0, "", "无法获取用户信息，请检查凭证是否有效"
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        if "timeout" in error_msg.lower():
            error_msg = "连接超时，请检查网络设置或代理"
        elif "connection" in error_msg.lower():
            error_msg = "网络连接失败，请检查网络"
        elif "封禁" in error_msg or "屏蔽" in error_msg:
            error_msg = "账号已被百度全吧封禁或屏蔽"

        # 打印错误便于调试
        print(f"验证账号失败: {error_msg}")
        return False, 0, "", error_msg


async def list_accounts(db: Database) -> list[AccountInfo]:
    """列出所有账号"""
    accounts = await db.get_accounts()
    return [
        AccountInfo(
            id=a.id,
            name=a.name,
            user_id=a.user_id,
            user_name=a.user_name,
            is_active=a.is_active,
            status=a.status,
            cuid=a.cuid,
            user_agent=a.user_agent,
            proxy_id=a.proxy_id,
            post_weight=getattr(a, 'post_weight', 5),
            is_maint_enabled=a.is_maint_enabled,
            last_maint_at=a.last_maint_at,
        )
        for a in accounts
    ]


async def switch_account(db: Database, account_id: int) -> bool:
    """切换活跃账号"""
    await db.set_active_account(account_id)
    return True


async def remove_account(db: Database, account_id: int) -> bool:
    """删除账号"""
    return await db.delete_account(account_id)


async def refresh_account(db: Database, account_id: int) -> AccountInfo | None:
    """
    刷新账号信息 (验证登录状态并更新 user_id/user_name)

    Args:
        db: 数据库实例
        account_id: 账号ID

    Returns:
        AccountInfo: 更新后的账号信息，账号不存在返回 None
    """
    # 获取账号
    accounts = await db.get_accounts()
    account = next((a for a in accounts if a.id == account_id), None)
    if not account:
        return None

    # 解密凭证
    try:
        bduss = decrypt_value(account.bduss)
        stoken = decrypt_value(account.stoken) if account.stoken else ""
    except Exception:
        return None

    # 验证账号 (带上已有的指纹)
    valid, user_id, user_name, error = await verify_account(
        bduss, stoken, cuid=account.cuid, ua=account.user_agent
    )

    if valid:
        status = "active"
    else:
        if "封禁" in error or "屏蔽" in error:
            status = "banned"
        else:
            status = f"invalid: {error[:50]}" if error else "invalid"

    # 更新数据库
    updated = await db.update_account(
        account_id,
        user_id=user_id if valid else account.user_id,
        user_name=user_name if valid else account.user_name,
    )
    await db.update_account_status(account_id, status)

    if updated:
        return AccountInfo(
            id=updated.id,
            name=updated.name,
            user_id=updated.user_id,
            user_name=updated.user_name,
            is_active=updated.is_active,
            status=status,
            cuid=updated.cuid,
            user_agent=updated.user_agent,
            proxy_id=updated.proxy_id,
            post_weight=getattr(updated, 'post_weight', 5),
            is_maint_enabled=updated.is_maint_enabled,
            last_maint_at=updated.last_maint_at,
        )
    return None


def parse_cookie(cookie_str: str) -> tuple[str, str]:
    """
    从原始 Cookie 字符串中解析 BDUSS 和 STOKEN

    Args:
        cookie_str: 原始 Cookie 字符串

    Returns:
        (BDUSS, STOKEN)
    """
    bduss = ""
    stoken = ""
    
    # 匹配 BDUSS
    bduss_match = re.search(r'BDUSS=([^;]+)', cookie_str)
    if bduss_match:
        bduss = bduss_match.group(1).strip()
    
    # 匹配 STOKEN
    stoken_match = re.search(r'STOKEN=([^;]+)', cookie_str)
    if stoken_match:
        stoken = stoken_match.group(1).strip()
        
    return bduss, stoken
