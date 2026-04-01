import asyncio
import hashlib
import json
import logging
import subprocess
from enum import IntEnum
from functools import wraps
from typing import Optional, List

import aiohttp

# 这里的 import 如果没有 aiohttp 的话，需要稍后检查 pyproject.toml
# TODO: 以后可以增加 JWT 校验逻辑 (pyjwt 加解密)

async def get_db():
    from ..db.crud import get_db as get_db_original
    return await get_db_original()

# 默认授权服务器列表 (支持容灾)
DEFAULT_LICENSE_SERVERS = [
    "https://km.hwdemtv.com",
    "https://kami.hwdemtv.com",
    "https://hw-license-center.hwdemtv.workers.dev"
]

class AuthStatus(IntEnum):
    FREE = 0
    PRO = 1
    EXPIRED = 2
    ERROR = 3

class LicenseManager:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db=None):
        if getattr(self, "_initialized", False): return
        self.db = db
        self.status = AuthStatus.FREE
        self.license_info = {}
        self._hwid = None
        self._initialized = True

    async def get_hwid(self) -> str:
        """获取硬件指纹 (基于主板序列号与 CPU ID)"""
        if self._hwid: return self._hwid
        
        try:
            # 现代 Win11 推荐使用 Get-CimInstance
            # 使用 powershell 直接执行
            cmd_board = 'powershell -Command "Get-CimInstance Win32_BaseBoard | Select-Object -ExpandProperty SerialNumber"'
            cmd_cpu = 'powershell -Command "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty ProcessorId"'
            
            proc_board = subprocess.run(cmd_board, capture_output=True, text=True, shell=True)
            proc_cpu = subprocess.run(cmd_cpu, capture_output=True, text=True, shell=True)
            
            raw_id = f"{proc_board.stdout.strip()}-{proc_cpu.stdout.strip()}"
            # 若获取失败，降级使用 uuid.getnode (MAC)
            if not proc_board.stdout.strip() or len(raw_id) < 5:
                import uuid
                raw_id = str(uuid.getnode())
                
            # SHA256 混淆
            self._hwid = hashlib.sha256(raw_id.encode()).hexdigest()[:32].upper()
            return self._hwid
        except Exception as e:
            logging.error(f"无法生成硬件 ID: {e}")
            return "UNKNOWN-HARDWARE-ID"

    async def check_local_status(self) -> AuthStatus:
        """检查本地缓存的授权状态 (离线校验逻辑)"""
        if not self.db: self.db = await get_db()
        
        license_key = await self.db.get_setting("license_key", "")
        if not license_key:
            self.status = AuthStatus.FREE
        else:
            # TODO: 校验本地 JWT 缓存。暂定默认为 PRO。
            self.status = AuthStatus.PRO
            
        return self.status

    async def verify_online(self) -> bool:
        """多节点在线验证授权"""
        if not self.db: self.db = await get_db()
        
        license_key = await self.db.get_setting("license_key", "")
        if not license_key:
            self.status = AuthStatus.FREE
            return False
            
        hwid = await self.get_hwid()
        custom_server = await self.db.get_setting("license_server_url", "")
        
        # 构造探测列表：用户自定义优先，随后是内置容灾节点
        servers = []
        if custom_server: 
            # 如果是逗号分隔的列表也支持
            servers.extend([s.strip() for s in custom_server.split(",") if s.strip()])
        
        # 补充默认节点
        for d_server in DEFAULT_LICENSE_SERVERS:
            if d_server not in servers:
                servers.append(d_server)
        
        async with aiohttp.ClientSession() as session:
            for server in servers:
                try:
                    # 如果 URL 包含 https://，则补全 api 路径
                    base_url = server.rstrip("/")
                    url = f"{base_url}/api/v1/verify"
                    
                    payload = {
                        "license_key": license_key,
                        "device_id": hwid,
                        "product": "TiebaMecha"
                    }
                    
                    async with session.post(url, json=payload, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("success"):
                                self.status = AuthStatus.PRO
                                self.license_info = data.get("info", {})
                                return True
                            else:
                                logging.warning(f"授权无效 ({server}): {data.get('message')}")
                        else:
                            logging.error(f"服务器错误 ({server}): HTTP {resp.status}")
                except Exception as e:
                    logging.warning(f"无法连接至授权服务器 {server}: {str(e)}")
                    continue
        
        # 所有节点均失败，如果有 license_key 但在线验证失败，标记为 ERROR 并维持现状
        self.status = AuthStatus.ERROR
        return False

def require_pro(f):
    """Pro 权限功能拦截装饰器"""
    @wraps(f)
    async def wrapper(*args, **kwargs):
        lm = get_auth_manager()
        # 如果当前状态不是 PRO，尝试读取本地缓存
        if lm.status != AuthStatus.PRO:
            await lm.check_local_status()
            
        if lm.status != AuthStatus.PRO:
            # 自动通知 UI (由于 decorator 往往在 Core，通知逻辑需安全引入)
            try:
                from .notification import get_notification_manager
                nm = get_notification_manager()
                if nm:
                    await nm.push(
                        type="system_alert",
                        title="需要 Pro 授权",
                        message="该功能仅对 Pro 用户开放，请在设置中配置有效的许可证密钥以解锁完整功能。",
                    )
            except Exception:
                pass
            raise PermissionError("Pro license required for this feature")
        return await f(*args, **kwargs)
    return wrapper

_manager = None

def get_auth_manager(db=None) -> LicenseManager:
    global _manager
    if not _manager:
        _manager = LicenseManager(db=db)
    return _manager
