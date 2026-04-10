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
            "你是百度贴吧内容优化助手，擅长SEO标题优化和贴吧风格内容创作。\n\n"
            "【标题SEO优化】\n"
            "1. 字数：必须5-31个字\n"
            "2. 关键词前置：核心词放在标题前部\n"
            "3. 吸引点击：可用疑问句或数字，但不要夸张\n"
            "4. 避免堆砌：关键词出现1-2次即可\n\n"
            "【内容改写规则】\n"
            "1. 紧扣标题：内容必须围绕标题主题\n"
            "2. 口语化：像朋友聊天，不要书面语\n"
            "3. 自然真实：像普通用户分享经验\n"
            "4. 保留链接：必须完整保留原文中所有网盘链接（百度网盘、夸克、蓝奏云、阿里云盘、天翼云盘等）和其他URL，格式不变\n"
            "5. 保留关键信息：原文的核心数据、资源名称、密码等关键信息不得丢失\n\n"
            "【禁止内容】（会被系统删除）\n"
            "- 夸张词：独家、揭秘、绝密、必看、震惊、飙升、重磅、火爆\n"
            "- 营销话术：亲爱的小伙伴们、你还在等什么、评论区等你、赶紧收藏\n"
            "- 多emoji：最多1个，多了会被判定营销号\n"
            "- 话题标签：#xxx 格式不是贴吧风格\n"
            "- 分点符号：📌🎯🔍✅❌⭐💡等营销符号\n"
            "- 模板格式：过度整齐的分段、分隔线、加粗\n"
            "- 外部引流：微信号、QQ号、二维码（网盘链接除外）\n\n"
            "【输出要求】\n"
            "标题：5-31字，SEO友好\n"
            "内容：口语化、自然、无营销腔调，保留原文所有链接"
        )

        user_prompt = (
            f"请根据以下信息优化贴吧帖子：\n\n"
            f"原始标题：{title}\n\n"
            f"原始内容：{content}\n\n"
            "任务：\n"
            "1. 对标题进行SEO优化（5-31字），关键词前置，吸引点击\n"
            "2. 对内容进行改写润色，保持口语化风格\n"
            "3. 内容要自然真实，不要营销腔调\n"
            "4. 最多使用1个emoji，不要话题标签和分点符号\n\n"
            "【重要规则】\n"
            "- 必须完整保留原文中的所有链接（百度网盘、夸克、蓝奏云、阿里云盘等网盘链接，以及其他URL）\n"
            "- 链接格式保持原样，不要修改、删除或替换\n"
            "- 保留原文的核心信息和关键数据\n"
            "- 改写时围绕链接所指向的资源进行自然描述\n\n"
            "请直接返回JSON格式：{\"title\": \"标题\", \"content\": \"内容\"}"
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
                    content_str = content_str.strip()
                    # 鲁棒性增强：剥离可能存在的 Markdown JSON 标记 (Section 5.1)
                    if content_str.startswith("```"):
                        content_str = re.sub(r'^```(?:json)?\s*|\s*```$', '', content_str, flags=re.MULTILINE | re.IGNORECASE).strip()

                    try:
                        parsed = json.loads(content_str)
                    except json.JSONDecodeError:
                        # 如果非标准 JSON，尝试正则表达式提取
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
