"""AI SEO Optimizer for TiebaMecha"""
import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime
import aiohttp
from typing import Tuple, Optional
from ..db.crud import Database
from .auth import require_pro

# --- SEO Optimization Constants ---
# 关键词密度上限：单个关键词在全文中最多出现次数
_KEYWORD_DENSITY_MAX = 3

# URL 正则：用于改写前提取/保护链接
_URL_PATTERN = re.compile(r'https?://[^\s<>"\')\]，。、！]+')

# 长尾词修饰词库：前缀 + 后缀组合，覆盖常见搜索意图
_LONG_TAIL_PREFIXES = [
    "最新", "实用", "免费", "完整", "保姆级", "手把手教你",
    "2026年", "小白必看", "超详细", "一键", "自动",
]

_LONG_TAIL_SUFFIXES = [
    "教程", "攻略", "指南", "实战", "方案", "工具",
    "合集", "推荐", "分享", "干货", "入门", "进阶",
    "脚本", "源码", "资源", "大全", "速成", "避坑",
]

logger = logging.getLogger(__name__)


def _encrypt_api_key(value: str) -> str:
    """加密 API Key 后存储"""
    if not value:
        return value
    try:
        from .account import encrypt_value
        return encrypt_value(value)
    except Exception:
        logger.warning("API Key 加密失败，使用明文存储")
        return value


def _decrypt_api_key(value: str) -> str:
    """解密 API Key，兼容旧版明文存储"""
    if not value:
        return value
    try:
        from .account import decrypt_value
        return decrypt_value(value)
    except Exception:
        # 旧版明文或非加密数据，直接返回
        return value

# 可重试的 HTTP 状态码
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY = 3  # 秒

# AI 调用最小间隔（秒），避免密集触发 API 限流
_AI_CALL_INTERVAL_MIN = 1.0
_AI_CALL_INTERVAL_MAX = 3.0

# 全局上次 AI 调用时间戳
_last_ai_call_time: float = 0.0

# 并发锁，保护全局时间戳的读写
_rate_limit_lock = asyncio.Lock()

class AIOptimizer:
    """基于 LLM 的贴吧帖子 SEO 优化器"""

    def __init__(self, db: Database):
        self.db = db
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建复用的 aiohttp ClientSession"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """关闭并释放 ClientSession"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _wait_for_rate_limit(self):
        """在 AI 调用前自动等待，确保两次调用之间有 1~3 秒间隔，避免 API 限流"""
        global _last_ai_call_time
        async with _rate_limit_lock:
            now = time.monotonic()
            elapsed = now - _last_ai_call_time
            # 先更新时间戳，防止并发协程读到相同的旧值
            _last_ai_call_time = now
            # 随机目标间隔，增加自然感
            target_interval = random.uniform(_AI_CALL_INTERVAL_MIN, _AI_CALL_INTERVAL_MAX)
            if elapsed < target_interval:
                wait = target_interval - elapsed
                logger.debug(f"AI 调用限流等待 {wait:.1f}s")
                await asyncio.sleep(wait)

    async def _get_config(self) -> dict:
        """获取 AI 配置"""
        api_key = _decrypt_api_key(await self.db.get_setting("ai_api_key", ""))
        # 默认使用智谱 AI
        base_url = await self.db.get_setting("ai_base_url", "https://open.bigmodel.cn/api/paas/v4/")
        model = await self.db.get_setting("ai_model", "glm-4-flash")
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": model
        }

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
        },
        "tech_expert": {
            "name": "技术老炮",
            "description": "自信略带傲慢，善用行话和缩写，像在技术社区摸爬滚打多年的老手。",
            "system": (
                "你是一个技术圈的老鸟，语气自信到略带傲慢，喜欢用技术术语和行话。"
                "说话直接不绕弯，偶尔吐槽小白问题，但分享的内容确实干货满满。"
                "对低质量内容毫不客气，对好东西会真诚推荐。"
            ),
            "examples": (
                "标题：这API都2026年了还在用？给你整套现代化方案\n"
                "内容：看到有人还在用那套古早API，属实绷不住了。"
                "这套新方案我线上跑了半年，延迟降了40%，直接上干货。"
                "不懂的先去翻文档，别上来就问。链接放下面了。"
            )
        },
        "warm_netizen": {
            "name": "热心网友",
            "description": "热情但不油腻，像真诚分享好东西的邻家大哥/大姐。",
            "system": (
                "你是一个热心但有分寸的网友，分享时真心实意，但不会过分吹捧。"
                "语气温暖自然，像朋友间推荐好物，绝不过度热情到让人起疑。"
                "用\"我试过了\"\"亲测\"等真实体验感词汇，但不滥用。"
            ),
            "examples": (
                "标题：试了一圈终于找到靠谱的，分享给有需要的朋友\n"
                "内容：之前找这个找了好久，踩了不少坑，这个确实好用。"
                "用了差不多两个月了，没什么问题。链接在下面，需要的自取。"
                "有问题可以回帖问我，看到会回。"
            )
        }
    }

    async def _auto_select_persona(self) -> str:
        """
        根据当前时段自动选择人格，避免同一账号长期使用同一风格。
        白天用轻松人格，晚上用专业人格，深夜用高冷人格。
        """
        hour = datetime.now().hour
        if 9 <= hour <= 14:
            pool = ["casual", "warm_netizen", "newbie"]
        elif 15 <= hour <= 19:
            pool = ["normal", "casual", "warm_netizen"]
        elif 20 <= hour <= 23:
            pool = ["normal", "tech_expert", "resource_god"]
        else:
            pool = ["resource_god", "tech_expert"]
        return random.choice(pool)

    @require_pro
    async def optimize_post(self, title: str, content: str, persona: str = None) -> Tuple[bool, str, str, str]:
        """
        优化帖子内容
        返回: (是否成功, 优化后的标题, 优化后的内容, 错误信息)
        """
        # persona 为 None 时自动根据时段轮换选择
        if persona is None:
            persona = await self._auto_select_persona()
        await self._wait_for_rate_limit()
        config = await self._get_config()
        if not config["api_key"]:
            return False, title, content, "请先在设置中配置 AI API Key"

        # 获取人格配置
        p_config = self.PERSONA_PROMPTS.get(persona, self.PERSONA_PROMPTS["normal"])
        
        system_prompt = (
            f"{p_config['system']}\n\n"

            "【标题 SEO 规则】\n"
            "1. 标题必须在 5-31 字之间，核心关键词前置。\n"
            "2. 长尾词注入：在标题中自然融入一个长尾关键词变体"
            "（如\"Python自动化\"→\"Python自动化办公脚本\"）。\n"
            "3. 情绪触发词：标题中适当使用一个情绪词"
            "（如\"居然\"\"真的\"\"终于\"\"后悔没早知道\"），但不要堆砌。\n"
            "4. 数字入标题：当适用时，在标题中加入具体数字"
            "（如\"3个\"\"10款\"\"99%的人不知道\"），提升点击率。\n\n"

            "【内容结构 SEO 规则】\n"
            "5. 核心关键词必须出现在内容第一段（前50字内）。\n"
            "6. 正文中自然嵌入 1-2 个相关关键词变体，"
            "分布在不同段落，不要集中堆砌。\n"
            "7. 单个关键词在全文中最多出现 3 次（含标题），超过视为堆砌。\n\n"

            "【去 AI 痕迹规则】\n"
            "8. 严禁以下句式模板：\"是...的\"连续使用、\"不仅...而且\"、"
            "\"首先...其次...最后\"、\"一方面...另一方面\"。用自然口语替代。\n"
            "9. 段落节奏：长短句交替，每段最多 4 行。"
            "避免连续 3 句以上相同句式长度。\n"
            "10. 口语化填充：适当使用语气词（\"嘛\"\"哈\"\"吧\"）、"
            "省略主语、反问句（\"这谁顶得住？\"），让文字有呼吸感。\n"
            "11. 严禁出现以下 AI 常用虚词：综上所述、不得不说、毫无疑问、"
            "总之、总的来说、不仅如此、另一方面、此外、因此。\n"
            "12. 绝对禁止营销腔：亲爱的小伙伴们、你还在等什么、赶紧收藏、"
            "评论区见、建议收藏、点赞关注。\n\n"

            "【通用规则】\n"
            "13. 完整保留所有链接和关键数据，格式严禁修改。"
            "原文中的公众号名称、频道ID、群号等引流标识一律完整保留。\n"
            "14. 内容中最多使用 1 个 emoji，严禁使用分点符号（如📌、🎯、✅）。\n\n"

            "【风格示例】\n"
            f"{p_config['examples']}"
        )

        # ── 长尾词注入：程序化生成候选词供 AI 参考 ──
        long_tail_candidates = self._generate_long_tail_keywords(title)
        long_tail_hint = ""
        if long_tail_candidates:
            candidates_str = "、".join(long_tail_candidates)
            long_tail_hint = (
                f"\n【长尾关键词参考】改写标题时，请从以下候选词中选择或参考使用：\n"
                f"{candidates_str}\n"
                f"可以选择其中一个融入标题，也可以自行创造类似的长尾变体。\n"
            )

        user_prompt = (
            f"请根据选定人格（{p_config['name']}）改写以下帖子：\n\n"
            f"原始标题：{title}\n\n"
            f"原始内容：{content}\n\n"
            "要求：\n"
            "1. 标题 SEO 友好，融入长尾关键词、情绪词或数字（如适用）。\n"
            "2. 核心关键词在第一段自然出现，正文中再自然嵌入 1-2 个变体。\n"
            "3. 内容口语化、去 AI 化痕迹，长短句交替，有呼吸感。\n"
            "4. 描述文字与链接之间保持 2 个换行符，确保链接独立。\n"
            "5. 即使原文内容较长，改写时也要尽量精炼，贴合人格设定。\n"
            "6. 单个关键词全文最多出现 3 次，避免堆砌。\n"
            f"{long_tail_hint}"
            "请直接返回 JSON：{\"title\": \"...\", \"content\": \"...\"}"
        )

        # ── 链接保护：改写前提取所有 URL，替换为占位符 ──
        original_urls = _URL_PATTERN.findall(content)
        url_placeholders = {}
        protected_content = content
        for i, url_match in enumerate(original_urls):
            placeholder = f"__TIEBAMECHA_LINK_{i}__"
            url_placeholders[placeholder] = url_match
            protected_content = protected_content.replace(url_match, placeholder, 1)

        # 如果内容含 URL，用占位符版本重建 user_prompt
        if original_urls:
            user_prompt = (
                f"请根据选定人格（{p_config['name']}）改写以下帖子：\n\n"
                f"原始标题：{title}\n\n"
                f"原始内容：{protected_content}\n\n"
                "要求：\n"
                "1. 标题 SEO 友好，融入长尾关键词、情绪词或数字（如适用）。\n"
                "2. 核心关键词在第一段自然出现，正文中再自然嵌入 1-2 个变体。\n"
                "3. 内容口语化、去 AI 化痕迹，长短句交替，有呼吸感。\n"
                "4. 描述文字与链接之间保持 2 个换行符，确保链接独立。\n"
                "5. 即使原文内容较长，改写时也要尽量精炼，贴合人格设定。\n"
                "6. 单个关键词全文最多出现 3 次，避免堆砌。\n"
                f"{long_tail_hint}"
                "7. 【重要】内容中的 __TIEBAMECHA_LINK_N__ 占位符必须原样保留，不可修改、删除或合并。\n\n"
                "请直接返回 JSON：{\"title\": \"...\", \"content\": \"...\"}"
            )

        url = f"{config['base_url'].rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json"
        }
        # 基础请求体（不含 response_format，后续按需添加）
        data = {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.85,
        }
        # response_format 并非所有兼容 API 都支持，首次尝试带，失败后去掉重试
        data_with_format = {**data, "response_format": {"type": "json_object"}}

        last_error = ""
        use_format = True

        for attempt in range(DEFAULT_MAX_RETRIES + 1):
            try:
                req_data = data_with_format if use_format else data
                session = await self._get_session()
                async with session.post(url, headers=headers, json=req_data, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            last_error = f"API 请求失败 ({resp.status}): {error_text[:200]}"

                            # 422 通常是 response_format 不被支持，去掉后重试
                            if resp.status == 422 and use_format:
                                use_format = False
                                logger.warning("response_format 不被支持，切换为纯文本模式重试")
                                continue

                            if resp.status in _RETRYABLE_STATUS and attempt < DEFAULT_MAX_RETRIES:
                                delay = DEFAULT_RETRY_DELAY * (attempt + 1)
                                logger.warning(f"AI 改写请求 {resp.status}，第 {attempt+1} 次重试，等待 {delay}s...")
                                await asyncio.sleep(delay)
                                continue
                            return False, title, content, last_error
                        
                        result = await resp.json()
                        # 安全地提取内容，检查嵌套键是否存在
                        choices = result.get('choices', [])
                        if not choices or not isinstance(choices, list):
                            last_error = f"API 返回格式异常: 缺少 choices 字段"
                            if attempt < DEFAULT_MAX_RETRIES:
                                await asyncio.sleep(DEFAULT_RETRY_DELAY)
                                continue
                            return False, title, content, last_error

                        first_choice = choices[0]
                        message = first_choice.get('message', {})
                        content_str = message.get('content', '')
                        if not content_str:
                            last_error = f"API 返回内容为空"
                            if attempt < DEFAULT_MAX_RETRIES:
                                await asyncio.sleep(DEFAULT_RETRY_DELAY)
                                continue
                            return False, title, content, last_error
                        
                        # 鲁棒性解析
                        content_str = content_str.strip()
                        if content_str.startswith("```"):
                            content_str = re.sub(r'^```(?:json)?\s*|\s*```$', '', content_str, flags=re.MULTILINE | re.IGNORECASE).strip()

                        try:
                            parsed = json.loads(content_str)
                        except json.JSONDecodeError:
                            # 兜底：尝试正则提取（支持转义引号和换行）
                            t_m = re.search(r'"title":\s*"((?:[^"\\]|\\.)*)"', content_str, re.S)
                            c_m = re.search(r'"content":\s*"((?:[^"\\]|\\.)*)"', content_str, re.S)
                            if t_m and c_m:
                                parsed = {"title": t_m.group(1), "content": c_m.group(1).replace("\\n", "\n")}
                            else:
                                return False, title, content, f"AI 返回格式不规范: {content_str[:50]}..."
                        
                        # ── 链接保护：从占位符恢复原始 URL ──
                        optimized_content = parsed.get("content", content)
                        for placeholder, original_url in url_placeholders.items():
                            if placeholder in optimized_content:
                                optimized_content = optimized_content.replace(placeholder, original_url)
                            else:
                                # URL 被 AI 丢失，追加到内容末尾
                                optimized_content = optimized_content.rstrip() + f"\n\n{original_url}"

                        optimized_title = parsed.get("title", title)

                        # ── 关键词密度检查 ──
                        optimized_title, optimized_content = self._enforce_keyword_density(
                            optimized_title, optimized_content, title
                        )

                        return True, optimized_title, optimized_content, ""
            except asyncio.TimeoutError:
                last_error = f"AI 请求超时 (60s)"
                if attempt < DEFAULT_MAX_RETRIES:
                    delay = DEFAULT_RETRY_DELAY * (attempt + 1)
                    logger.warning(f"AI 改写超时，第 {attempt+1} 次重试，等待 {delay}s...")
                    await asyncio.sleep(delay)
                    continue
            except Exception as e:
                return False, title, content, f"优化异常: {str(e)}"

        return False, title, content, last_error

    @require_pro
    async def generate_bump_content(self, title: str, persona: str = "normal") -> Tuple[bool, str, str]:
        """
        生成自顶回复内容（简短拟人化）
        返回: (是否成功, 回复内容, 错误信息)
        """
        await self._wait_for_rate_limit()
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
            "5. 可以偶尔带一点emoji，但最多1个。\n"
            "6. 【重要】你必须生成 3 条完全不同的回复，用换行分隔，每条一行。\n"
            "   3 条回复的语气、长度、用词要尽量多样化。\n"
            "   示例格式：\n"
            "   看了，写得不错\n"
            "   mark一下\n"
            "   顶\n\n"
            "【风格示例】\n"
            f"{p_config['examples']}"
        )

        user_prompt = (
            f"请根据人格（{p_config['name']}）为以下帖子生成 3 条简短的回帖评论：\n\n"
            f"帖子标题：{title}\n\n"
            "要求：\n"
            "1. 每条 5-25字，口语化，像真实用户随手评论。\n"
            "2. 不要暴露资源或营销意图。\n"
            "3. 3 条之间要有明显差异，不要换汤不换药。\n"
            "4. 每条独占一行，不要编号、不要引号、不要JSON。\n\n"
            "直接返回 3 行文本，每行一条评论。"
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

        last_error = ""
        for attempt in range(DEFAULT_MAX_RETRIES + 1):
            try:
                session = await self._get_session()
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            last_error = f"API 请求失败 ({resp.status}): {error_text[:200]}"
                            if resp.status in _RETRYABLE_STATUS and attempt < DEFAULT_MAX_RETRIES:
                                delay = DEFAULT_RETRY_DELAY * (attempt + 1)
                                logger.warning(f"自顶回复请求 {resp.status}，第 {attempt+1} 次重试，等待 {delay}s...")
                                await asyncio.sleep(delay)
                                continue
                            return False, "", last_error

                        result = await resp.json()
                        raw_content = result['choices'][0]['message']['content'].strip()
                        # 清理可能的引号包裹
                        raw_content = re.sub(r'^["\']|["\']$', '', raw_content)

                        # 解析 3 条候选回复，随机选取 1 条
                        candidates = [
                            line.strip() for line in raw_content.split('\n')
                            if line.strip() and len(line.strip()) >= 2
                        ]
                        if not candidates:
                            candidates = [raw_content]

                        chosen = random.choice(candidates)
                        return True, chosen, ""
            except asyncio.TimeoutError:
                last_error = "AI 请求超时 (30s)"
                if attempt < DEFAULT_MAX_RETRIES:
                    delay = DEFAULT_RETRY_DELAY * (attempt + 1)
                    logger.warning(f"自顶回复超时，第 {attempt+1} 次重试，等待 {delay}s...")
                    await asyncio.sleep(delay)
                    continue
            except Exception as e:
                return False, "", f"生成异常: {str(e)}"
        return False, "", last_error

    def _enforce_keyword_density(
        self, optimized_title: str, optimized_content: str, original_title: str
    ) -> Tuple[str, str]:
        """
        关键词密度检查：若单个关键词在全文中出现超过 3 次，自动从第二段开始删除多余出现。
        标题和第一段（SEO 锚点）始终保留不变。
        """
        keywords = self._extract_keywords(original_title)
        if not keywords:
            return optimized_title, optimized_content

        full_text = optimized_title + optimized_content
        for keyword in keywords:
            count = full_text.count(keyword)
            if count <= _KEYWORD_DENSITY_MAX:
                continue

            excess = count - _KEYWORD_DENSITY_MAX
            paragraphs = optimized_content.split('\n\n')
            modified = False
            for i in range(1, len(paragraphs)):  # 跳过第一段
                if excess <= 0:
                    break
                if keyword in paragraphs[i]:
                    paragraphs[i] = paragraphs[i].replace(keyword, '', 1)
                    excess -= 1
                    modified = True
            if modified:
                optimized_content = '\n\n'.join(paragraphs)
                logger.debug(
                    f"关键词密度调整: '{keyword}' 从 {count} 次降至 {_KEYWORD_DENSITY_MAX} 次"
                )

        return optimized_title, optimized_content

    @staticmethod
    def _extract_keywords(title: str) -> list:
        """
        从标题中提取潜在关键词。按中英文标点分割，保留 >=2 字符的片段，过滤停用词。
        """
        parts = re.split(r'[，。、！？；：\s,.\-!?;:|/\\]+', title)
        stop_words = {'的', '了', '在', '是', '和', '与', '或', '这', '那', '有', '也', '都'}
        return [p for p in parts if len(p) >= 2 and p not in stop_words]

    @staticmethod
    def _generate_long_tail_keywords(title: str, max_candidates: int = 3) -> list:
        """
        从标题中提取核心词，组合预设修饰词库生成长尾关键词候选。
        返回最多 max_candidates 个候选，供 AI 参考使用。

        示例：
            输入: "Python自动化办公"
            输出: ["Python自动化办公教程", "最新Python自动化办公实战", "免费Python自动化办公脚本"]
        """
        # 提取核心词（取最长的连续片段）
        parts = re.split(r'[，。、！？；：\s,.\-!?;:|/\\]+', title)
        core = max(parts, key=len) if parts else title
        # 如果核心词太短，用原标题
        if len(core) < 2:
            core = title

        # 如果核心词本身已经很长（>8字），只加后缀不加前缀
        candidates = []
        if len(core) > 8:
            # 只加后缀
            suffix_pool = random.sample(_LONG_TAIL_SUFFIXES, min(max_candidates, len(_LONG_TAIL_SUFFIXES)))
            candidates = [f"{core}{s}" for s in suffix_pool]
        else:
            # 前缀+后缀组合
            prefix_pool = random.sample(_LONG_TAIL_PREFIXES, min(2, len(_LONG_TAIL_PREFIXES)))
            suffix_pool = random.sample(_LONG_TAIL_SUFFIXES, min(2, len(_LONG_TAIL_SUFFIXES)))
            for p in prefix_pool:
                for s in suffix_pool:
                    candidates.append(f"{p}{core}{s}")
                    if len(candidates) >= max_candidates:
                        break
                if len(candidates) >= max_candidates:
                    break

        return candidates[:max_candidates]

    async def test_connection(self, api_key: str = "", base_url: str = "", model: str = "") -> Tuple[bool, str]:
        """
        测试 AI 接口连通性

        Returns:
            (是否成功, 消息/延迟)
        """
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

        start_time = time.monotonic()
        last_error = ""
        for attempt in range(DEFAULT_MAX_RETRIES + 1):
            try:
                session = await self._get_session()
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        latency = int((time.monotonic() - start_time) * 1000)
                        if resp.status == 200:
                            return True, f"连接成功 (延迟: {latency}ms)"
                        else:
                            error_text = await resp.text()
                            last_error = f"API 响应错误 ({resp.status}): {error_text[:100]}"
                            if resp.status in _RETRYABLE_STATUS and attempt < DEFAULT_MAX_RETRIES:
                                delay = DEFAULT_RETRY_DELAY * (attempt + 1)
                                await asyncio.sleep(delay)
                                continue
                            return False, last_error
            except Exception as e:
                last_error = f"网络连接异常: {str(e)[:100]}"
                if attempt < DEFAULT_MAX_RETRIES:
                    await asyncio.sleep(DEFAULT_RETRY_DELAY * (attempt + 1))
                    continue
        return False, last_error
