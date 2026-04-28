import re
import random
from typing import Optional

# 定义常用的零宽字符集合
ZERO_WIDTH_CHARS = ["\u200b", "\u200c", "\u200d", "\ufeff"]

# 表情与符号库
RANDOM_SYMBOLS = [
    "(๑•̀ㅂ•́)و✧", " (´▽`) ", " (*´∀`)~♥", " O(∩_∩)O ", " (•̀ᴗ•́)و ", 
    " ✨ ", " 🚀 ", " ✅ ", " ☘️ ", " ❄️ ", " ☕ ", " ⚓ "
]

class Obfuscator:
    """内容风控干扰器：利用不可见字符打破哈希重合度，并规避敏感触发词"""
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @staticmethod
    async def from_db(db) -> 'Obfuscator':
        """从数据库加载配置并初始化混淆器"""
        config = {
            "density": float(await db.get_setting("obfuscator_density", "0.1")),
            "use_symbols": await db.get_setting("obfuscator_symbols", "true") == "true",
            "use_spacing": await db.get_setting("obfuscator_spacing", "true") == "true",
            "use_shuffling": await db.get_setting("obfuscator_shuffling", "true") == "true",
        }
        return Obfuscator(config)

    def obfuscate_all(self, text: str) -> str:
        """根据配置执行全流程混淆"""
        if not text:
            return text
        
        # 1. 语义乱序
        if self.config.get("use_shuffling", True):
            text = self.semantic_shuffling(text)
        
        # 2. 注入随机符号
        if self.config.get("use_symbols", True):
            text = self.inject_random_symbols(text)
        
        # 3. 拟人化间距
        if self.config.get("use_spacing", True):
            text = self.humanize_spacing(text)
        
        # 4. 零宽字符注入 (最后一步，因为它插入的是不可见干扰)
        density = self.config.get("density", 0.1)
        text = self.inject_zero_width_chars(text, density=density)
        
        return text

    @staticmethod
    def inject_zero_width_chars(text: str, density: float = 0.1) -> str:
        """在中文/日文/韩文之间随机注入零宽字符，避开英文和 URL 链接。"""
        if not text or density <= 0:
            return text

        url_pattern = re.compile(r'https?://[^\s<>"\')\]，。、！]+')
        urls = url_pattern.findall(text)
        url_strings = [u[0] if isinstance(u, tuple) else u for u in urls]
        
        placeholder = "|||__TIEBAMECHA_URL_PLACEHOLDER_{}__|||"
        temp_text = text
        for i, url in enumerate(url_strings):
            temp_text = temp_text.replace(url, placeholder.format(i))
            
        chars = list(temp_text)
        obfuscated_chars = []
        
        for i, char in enumerate(chars):
            obfuscated_chars.append(char)
            if '\u4e00' <= char <= '\u9fff' and i < len(chars) - 1:
                if random.random() < density:
                    obfuscated_chars.append(random.choice(ZERO_WIDTH_CHARS))
                    
        obfuscated_text = "".join(obfuscated_chars)
        for i, url in enumerate(url_strings):
            obfuscated_text = obfuscated_text.replace(placeholder.format(i), url)
            
        return obfuscated_text

    @staticmethod
    def humanize_spacing(text: str) -> str:
        """随机插入换行和空格，改变整体段落签名的 Hash"""
        if not text:
            return text
        paragraphs = text.split('\n')
        new_paragraphs = []
        for p in paragraphs:
            if p.strip() and random.random() < 0.2:
                p += " " * random.randint(1, 3)
            new_paragraphs.append(p)
        return "\n".join(new_paragraphs)

    @staticmethod
    def inject_random_symbols(text: str) -> str:
        """在文本开头、结尾或段落间随机插入表情或符号"""
        if not text or random.random() > 0.5:
            return text

        symbol = random.choice(RANDOM_SYMBOLS)
        pos = random.choice(["start", "end", "both"])

        if pos == "start":
            return f"{symbol} {text}"
        elif pos == "end":
            return f"{text} {symbol}"
        else:
            return f"{symbol} {text} {symbol}"

    @staticmethod
    def semantic_shuffling(text: str) -> str:
        """段落级语序打乱"""
        if not text:
            return text

        connectors = {'因此', '所以', '但是', '然而', '另外', '此外', '而且', '不过', '总之', '于是', '否则', '接着'}
        paragraphs = text.split('\n')
        result = []
        for p in paragraphs:
            sentences = re.split(r'(?<=[。！？])', p)
            sentences = [s for s in sentences if s.strip()]

            if len(sentences) >= 3:
                i = random.randint(0, len(sentences) - 2)
                s1 = sentences[i]
                s2 = sentences[i + 1]
                if (not any(c in s1 for c in connectors) and not any(c in s2 for c in connectors)):
                    sentences[i], sentences[i + 1] = sentences[i + 1], sentences[i]

            result.append(''.join(sentences))
        return '\n'.join(result)
