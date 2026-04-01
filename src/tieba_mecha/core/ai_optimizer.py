"""AI SEO Optimizer for TiebaMecha"""
import json
import aiohttp
from typing import Tuple, Optional
from ..db.crud import Database
from .auth import require_pro

class AIOptimizer:
    """基于 LLM 的贴吧帖子 SEO 优化器"""

    def __init__(self, db: Database):
        self.db = db

    async def _get_config(self) -> dict:
        """获取 AI 配置"""
        api_key = await self.db.get_setting("ai_api_key", "")
        # 默认使用智谱 AI
        base_url = await self.db.get_setting("ai_base_url", "https://open.bigmodel.cn/api/paas/v4/")
        model = await self.db.get_setting("ai_model", "glm-4-flash")
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": model
        }

    @require_pro
    async def optimize_post(self, title: str, content: str) -> Tuple[bool, str, str, str]:
        """
        优化帖子内容
        返回: (是否成功, 优化后的标题, 优化后的内容, 错误信息)
        """
        config = await self._get_config()
        if not config["api_key"]:
            return False, title, content, "请先在设置中配置 AI API Key"

        # 获取自定义提示词，如果没有则使用系统预设
        custom_prompt = await self.db.get_setting("ai_system_prompt", "")
        system_prompt = custom_prompt if custom_prompt else (
            "你是一个专业的百度贴吧 SEO 优化专家，擅长创建高点击率、高权重且具有抗删能力的帖子内容。\n"
            "你的目标是：\n"
            "1. **吸睛标题**：利用好奇心、干货分享或热点词汇重塑标题，增强点击欲望。\n"
            "2. **内容防御（Anti-Deletion）**：采用柔性表达，巧妙避开贴吧敏感词，增加互动引导。使用段落分隔和表情符号增加阅读舒适度。\n"
            "3. **Cyber-Mecha 风格**：如果帖子内容涉及技术、工具或机械，请融入一种冷酷且高效的 '机甲' 调性。\n"
            "4. **SEO 关键词**：在标题前部包含核心关键词，提高搜索热度。\n"
            "5. **回复引导**：在结尾处增加一个让人忍不住想回复的问题。"
        )

        user_prompt = (
            f"请将以下帖子内容进行专业级 SEO 优化：\n\n"
            f"--- 原始标题 ---\n{title}\n\n"
            f"--- 原始内容 ---\n{content}\n\n"
            "--- 要求 ---\n"
            "请直接返回格式化的 JSON，包含 'title' 和 'content' 字段。不要有任何其他解释文字。"
        )

        url = f"{config['base_url'].rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json"
        }
        data = {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.8,
            "response_format": {"type": "json_object"}
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=30) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return False, title, content, f"API 请求失败 ({resp.status}): {error_text}"
                    
                    result = await resp.json()
                    content_str = result['choices'][0]['message']['content']
                    
                    # 尝试解析 JSON
                    try:
                        parsed = json.loads(content_str)
                    except json.JSONDecodeError:
                        # 如果非标准 JSON，尝试正则表达式提取
                        import re
                        title_match = re.search(r'"title":\s*"(.*?)"', content_str, re.S)
                        content_match = re.search(r'"content":\s*"(.*?)"', content_str, re.S)
                        if title_match and content_match:
                            parsed = {
                                "title": title_match.group(1),
                                "content": content_match.group(1).replace("\\n", "\n")
                            }
                        else:
                            return False, title, content, f"AI 返回格式不规范: {content_str[:100]}..."
                    
                    return True, parsed.get("title", title), parsed.get("content", content), ""
        except Exception as e:
            return False, title, content, f"优化过程中发生错误: {str(e)}"

    async def test_connection(self, api_key: str = "", base_url: str = "", model: str = "") -> Tuple[bool, str]:
        """
        测试 AI 接口连通性
        
        Returns:
            (是否成功, 消息/延迟)
        """
        import time
        
        # 如果未传入参数，则从配置加载
        if not api_key:
            config = await self._get_config()
            api_key = config["api_key"]
            base_url = config["base_url"]
            model = config["model"]
            
        if not api_key:
            return False, "未配置 API Key"
            
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        # 极简请求，仅消耗 1-2 tokens
        data = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1
        }
        
        start_time = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=15) as resp:
                    latency = int((time.time() - start_time) * 1000)
                    if resp.status == 200:
                        return True, f"连接成功 (延迟: {latency}ms)"
                    else:
                        error_text = await resp.text()
                        return False, f"API 响应错误 ({resp.status}): {error_text[:100]}"
        except Exception as e:
            return False, f"网络连接异常: {str(e)[:100]}"
