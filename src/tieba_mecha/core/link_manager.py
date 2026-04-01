import asyncio
import httpx
import json
from typing import List, Dict, Optional
from ..db.crud import Database

class SmartLinkConnector:
    """连接到 smart-link-manager 的官方 REST API 连接器 (带本地持久化缓存)"""
    
    def __init__(self, db: Database):
        self.db = db
        self.client = httpx.AsyncClient(timeout=10.0)

    async def _get_config(self) -> Dict[str, str]:
        """从 TiebaMecha 设置中获取 API 配置"""
        api_url = await self.db.get_setting("slm_api_url", "https://s.hubinwei.top")
        api_key = await self.db.get_setting("slm_api_key", "")
        
        # 补全 URL 斜杠
        if api_url.endswith("/"):
            api_url = api_url[:-1]
            
        return {
            "url": api_url,
            "key": api_key,
        }

    async def test_connection(self) -> tuple[bool, str]:
        """测试并验证 API 连通性与 Key 的有效性"""
        config = await self._get_config()
        if not config["key"]:
            return False, "API Key (API 令牌) 缺失"
        
        url = f"{config['url']}/api/v1/links"
        headers = {"Authorization": f"Bearer {config['key']}"}
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return True, f"连接成功！监测到 {len(data)} 条短链资产"
                elif resp.status_code == 401:
                    return False, "API 令牌 (Key) 无效或已过期"
                else:
                    return False, f"接口返回异常: {resp.status_code} - {resp.text[:50]}"
        except Exception as e:
            return False, f"网络无法连接至 {config['url']}: {str(e)}"

    async def sync_shortlinks_to_db(self) -> tuple[bool, str]:
        """手动获取短链数据并持久化到本地核心数据库中"""
        config = await self._get_config()
        if not config["key"]:
            return False, "API Key (API 令牌) 缺失，请先至全局设置配置！"
            
        url = f"{config['url']}/api/v1/links"
        headers = {"Authorization": f"Bearer {config['key']}"}
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    await self.db.set_setting("slm_cached_links", json.dumps(data))
                    return True, f"已从云端永久同步 {len(data)} 条短链到本地数据库！"
                elif resp.status_code == 401:
                    return False, "API 令牌 (Key) 无效或已过期"
                else:
                    return False, f"API 异常: {resp.status_code}"
        except Exception as e:
            return False, f"无法连接到接口: {str(e)}"

    async def get_active_shortlinks(self) -> List[Dict]:
        """(离线极速) 直接拉起保存在系统中已持久化的 JSON 链接字典"""
        cached_data = await self.db.get_setting("slm_cached_links", "[]")
        try:
            return json.loads(cached_data)
        except Exception:
            return []

    async def get_shortlinks_with_status(self, db: Database) -> List[Dict]:
        """
        获取带发帖状态的短链列表
        
        Returns:
            [
                {
                    'shortCode': 'KJ8F2',
                    'seoTitle': 'Python 教程',
                    'description': '...',
                    'post_count': 3,
                    'status': '已发'  # 或 '未发'
                },
                ...
            ]
        """
        # 1. 获取所有短链
        all_links = await self.get_active_shortlinks()
        
        # 2. 获取发帖统计
        post_stats = await db.get_material_success_stats()

        # 3. 合并状态
        result = []
        for link in all_links:
            code = link.get('shortCode', '')
            post_count = post_stats.get(code, 0)
            
            result.append({
                **link,
                'post_count': post_count,
                'status': '已发' if post_count > 0 else '未发'
            })
        
        return result

    async def close(self):
        await self.client.aclose()
