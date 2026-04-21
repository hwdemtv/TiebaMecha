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

    # 预设人格配置库
    PERSONA_PROMPTS = {
        "normal": {
            "name": "标准 SEO (默认)",
            "description": "平衡关键词排名与阅读体验，适合正式发布。",
            "system": "你是一个温和且专业的贴吧内容创作者，擅长平衡 SEO 关键词与口语化表达。",
            "examples": "标题：Python 自动化办公实战，这一套库就够了！\n内容：最近在研究 Python 自动化，整理了几个真心好用的库。分享给大家，希望能帮到想提高效率的朋友。链接在下面，自取哈。"
        },
        "resource_god": {
            "name": "资源大神",
            "description": "语气高冷但内容极其丰富，强调资源的稀缺性。",
            "system": "你是一个资深资源分享大神，混迹各大论坛多年，语气专业、精炼，从不废话，极其厌恶营销话术。",
            "examples": "标题：【罕见镜像】某国外付费站点的全套模型数据，低调自取\n内容：费了不少劲从国外站扒下来的，全网目前应该没几个活着的链接。别问怎么弄的，懂的自然懂。建议尽快保存，随时可能挂掉。"
        },
        "casual": {
            "name": "随缘路人",
            "description": "极其简短、口语化，像随手记录，去 AI 痕迹最强。",
            "system": "你是一个普通贴吧用户，发帖只是为了随手记录或简单分享，语气极其随意，标题通常很短，不使用任何成套的逻辑词。",
            "examples": "标题：这东西居然还有人用？有点离谱\n内容：翻旧硬盘找出来的，居然还能跑。我也用不上了，扔出来给有需要的人吧。链接看下面。"
        },
        "newbie": {
            "name": "好奇萌新",
            "description": "谦逊、多互动，适合冷启动账号，提高贴子活跃度。",
            "system": "你是一个刚进贴吧的新人，态度诚恳且好奇，喜欢向大家请教并分享自己发现的好东西。",
            "examples": "标题：求教！刚找到的这个资源有人见过吗？感觉好厉害\n内容：新人报道！刚才整理网盘发现的一套神仙资源，不知道大家有用过没？感觉挺厉害的，直接放出来给大家研究研究，求大佬指点！"
        }
    }

    @require_pro
    async def optimize_post(self, title: str, content: str, persona: str = "normal") -> Tuple[bool, str, str, str]:
        """
        优化帖子内容
        返回: (是否成功, 优化后的标题, 优化后的内容, 错误信息)
        """
        config = await self._get_config()
        if not config["api_key"]:
            return False, title, content, "请先在设置中配置 AI API Key"

        # 获取人格配置
        p_config = self.PERSONA_PROMPTS.get(persona, self.PERSONA_PROMPTS["normal"])
        
        system_prompt = (
            f"{p_config['system']}\n\n"
            "【通用创作准则】\n"
            "1. 标题必须在 5-31 字之间，核心关键词前置。\n"
            "2. 严禁出现以下 AI 常用虚词：综上所述、不得不说、毫无疑问、总之、总的来说、不仅如此、另一方面、此外、因此。\n"
            "3. 绝对禁止营销腔：亲爱的小伙伴们、你还在等什么、赶紧收藏、评论区见、建议收藏、点赞关注。\n"
            "4. 完整保留所有链接和关键数据，格式严禁修改。\n"
            "5. 内容中最多使用 1 个 emoji，严禁使用分点符号（如📌、🎯、✅）。\n\n"
            "【风格示例】\n"
            f"{p_config['examples']}"
        )

        user_prompt = (
            f"请根据选定人格（{p_config['name']}）改写以下帖子：\n\n"
            f"原始标题：{title}\n\n"
            f"原始内容：{content}\n\n"
            "要求：\n"
            "1. 标题 SEO 友好，内容口语化、去 AI 化痕迹。\n"
            "2. 描述文字与链接之间保持 2 个换行符，确保链接独立。\n"
            "3. 即使原文内容较长，改写时也要尽量精炼，贴合人格设定。\n\n"
            "请直接返回 JSON：{\"title\": \"...\", \"content\": \"...\"}"
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
            "temperature": 0.85,
            "response_format": {"type": "json_object"}
        }

        try:
            import re
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=30) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return False, title, content, f"API 请求失败 ({resp.status}): {error_text}"
                    
                    result = await resp.json()
                    content_str = result['choices'][0]['message']['content']
                    
                    # 鲁棒性解析
                    content_str = content_str.strip()
                    if content_str.startswith("```"):
                        content_str = re.sub(r'^```(?:json)?\s*|\s*```$', '', content_str, flags=re.MULTILINE | re.IGNORECASE).strip()

                    try:
                        parsed = json.loads(content_str)
                    except json.JSONDecodeError:
                        # 兜底：尝试正则提取
                        t_m = re.search(r'"title":\s*"(.*?)"', content_str, re.S)
                        c_m = re.search(r'"content":\s*"(.*?)"', content_str, re.S)
                        if t_m and c_m:
                            parsed = {"title": t_m.group(1), "content": c_m.group(1).replace("\\n", "\n")}
                        else:
                            return False, title, content, f"AI 返回格式不规范: {content_str[:50]}..."
                    
                    return True, parsed.get("title", title), parsed.get("content", content), ""
        except Exception as e:
            return False, title, content, f"优化异常: {str(e)}"

    @require_pro
    async def generate_bump_content(self, title: str, persona: str = "normal") -> Tuple[bool, str, str]:
        """
        生成自顶回复内容（简短拟人化）
        返回: (是否成功, 回复内容, 错误信息)
        """
        config = await self._get_config()
        if not config["api_key"]:
            return False, "", "请先在设置中配置 AI API Key"

        # 获取人格配置
        p_config = self.PERSONA_PROMPTS.get(persona, self.PERSONA_PROMPTS["normal"])

        system_prompt = (
            f"{p_config['system']}\n\n"
            "【自顶回复准则】\n"
            "1. 回复必须控制在 5-25 字之间，口语化、去AI痕迹。\n"
            "2. 严禁使用套话：综上所述、不得不说、首先其次总之、值得注意的是。\n"
            "3. 严禁营销腔：亲爱的小伙伴、赶紧收藏点赞、评论区见。\n"
            "4. 语气自然随意，像真实用户在浏览帖子后的随手评论。\n"
            "5. 可以偶尔带一点emoji，但最多1个。\n\n"
            "【风格示例】\n"
            f"{p_config['examples']}"
        )

        user_prompt = (
            f"请根据人格（{p_config['name']}）为以下帖子生成一条简短的回帖评论：\n\n"
            f"帖子标题：{title}\n\n"
            "要求：\n"
            "1. 5-25字，口语化，像真实用户随手评论。\n"
            "2. 不要暴露资源或营销意图。\n"
            "3. 直接返回评论文本，不需要JSON。\n\n"
            "示例回复：路过、看了、顶、收藏了、写得不错👍、mark一下"
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
            "temperature": 0.9,
            "max_tokens": 50
        }

        try:
            import re
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=20) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return False, "", f"API 请求失败 ({resp.status})"

                    result = await resp.json()
                    content = result['choices'][0]['message']['content'].strip()
                    # 清理可能的引号包裹
                    content = re.sub(r'^["\'"]|["\'"]$', '', content)
                    return True, content, ""
        except Exception as e:
            return False, "", f"生成异常: {str(e)}"

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
