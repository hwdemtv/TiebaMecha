import asyncio
import hashlib
import json
import logging
import os
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
        """获取硬件指纹 (支持多平台与 Docker)"""
        if self._hwid: return self._hwid

        # 优先从 DB 缓存读取，避免重复采集
        if not self.db:
            self.db = await get_db()
        cached = await self.db.get_setting("device_id", "")
        if cached:
            self._hwid = cached
            return self._hwid

        raw_id = ""
        try:
            import platform
            system = platform.system()
            
            if system == "Windows":
                # Windows: 优先使用 PowerShell 获取主板/CPU ID
                try:
                    # 使用封装好的命令，增加 -NoProfile 提升启动速度并略微增强安全性
                    cmd_board = 'powershell -NoProfile -Command "Get-CimInstance Win32_BaseBoard | Select-Object -ExpandProperty SerialNumber"'
                    cmd_cpu = 'powershell -NoProfile -Command "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty ProcessorId"'
                    
                    async def run_cmd_async(cmd):
                        try:
                            # 使用 asyncio 异步执行，防止阻塞事件循环
                            p = await asyncio.create_subprocess_shell(
                                cmd, 
                                stdout=subprocess.PIPE, 
                                stderr=subprocess.PIPE
                            )
                            # 增加 5 秒超时控制，防止 WMI/CIM 服务挂起导致全程序假死
                            stdout, _ = await asyncio.wait_for(p.communicate(), timeout=5.0)
                            return stdout.decode().strip()
                        except Exception:
                            return ""

                    res_board = await run_cmd_async(cmd_board)
                    res_cpu = await run_cmd_async(cmd_cpu)
                    
                    if res_board and len(res_board) > 3:
                        raw_id = f"{res_board}-{res_cpu}"
                except Exception as e:
                    logging.debug(f"[AUTH] Windows 硬件采集探测跳过: {e}")

            elif system == "Linux":
                # Linux/Docker: 增加 product_uuid 作为备选方案
                for p in ["/etc/machine-id", "/var/lib/dbus/machine-id", "/sys/class/dmi/id/product_uuid"]:
                    if os.path.exists(p):
                        try:
                            with open(p, "r") as f:
                                content = f.read().strip()
                                if content:
                                    raw_id = content
                                    break
                        except Exception:
                            continue
            
            # 通用降级方案: MAC 地址 (uuid.getnode)
            if not raw_id or len(raw_id) < 5:
                import uuid
                mac = uuid.getnode()
                raw_id = f"MAC-{mac}"
                
            # 统一 SHA256 混淆并截取 32 位大写
            self._hwid = hashlib.sha256(raw_id.encode()).hexdigest()[:32].upper()
            logging.info(f"[AUTH] 设备唯一标识生成成功: {self._hwid[:8]}***")
            # 缓存到 DB，避免重启后重复采集
            try:
                await self.db.set_setting("device_id", self._hwid)
            except Exception:
                pass
            return self._hwid
            
        except Exception as e:
            logging.error(f"无法生成硬件 ID (Critical Error): {e}")
            return "UNKNOWN-HARDWARE-ID-ERR"

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
        
        # 设置浏览器风格的 User-Agent 以绕过 Cloudflare 机器人检测 (Section 6.1)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        connector = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            for server in servers:
                try:
                    # 如果 URL 包含 https://，则补全 api 路径
                    base_url = server.rstrip("/")
                    url = f"{base_url}/api/v1/auth/verify"
                    
                    payload = {
                        "license_key": license_key,
                        "device_id": hwid,
                        "product_id": "tieba_mecha",
                        "mode": "check"
                    }
                    
                    async with session.post(url, json=payload, timeout=10) as resp:
                        # 兼容模式：200 OK, 404 (查无此码), 403 (设备限制) 均视为业务层可解析状态 (Section 6.2)
                        if resp.status in [200, 403, 404]:
                            try:
                                data = await resp.json()
                                if data.get("success"):
                                    self.status = AuthStatus.PRO
                                    self.license_info = data.get("info", {})
                                    logging.info(f"授权同步成功 ({server})")
                                    return True
                                else:
                                    # 提取真实的业务提示（如：卡密不存在、已被禁用等），降级为 WARNING
                                    msg = data.get("message") or data.get("msg") or "未知授权错误"
                                    logging.warning(f"授权业务提示 ({server}): {msg}")
                            except Exception:
                                logging.debug(f"无法解析服务器返回的 JSON 内容 ({server}): HTTP {resp.status}")
                                logging.error(f"服务器错误 ({server}): HTTP {resp.status}")
                        else:
                            # 真正的系统级错误 (如 500, 502 等)
                            logging.error(f"服务器异常 ({server}): HTTP {resp.status}")
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
        lm = await get_auth_manager()
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

_manager: LicenseManager | None = None
_manager_lock = asyncio.Lock()

async def get_auth_manager(db=None) -> LicenseManager:
    global _manager
    if _manager is None:
        async with _manager_lock:
            if _manager is None:
                _manager = LicenseManager(db=db)
    return _manager
