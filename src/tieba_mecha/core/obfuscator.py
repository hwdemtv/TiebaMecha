import re
import random

# 定义常用的零宽字符集合
# \u200b: 零宽空格 (Zero-width space)
# \u200c: 零宽非连接符 (Zero-width non-joiner)
# \u200d: 零宽连接符 (Zero-width joiner)
# \ufeff: 零宽不换行空格 (Zero-width no-break space)
ZERO_WIDTH_CHARS = ["\u200b", "\u200c", "\u200d", "\ufeff"]

# 表情与符号库
RANDOM_SYMBOLS = [
    "(๑•̀ㅂ•́)و✧", " (´▽`) ", " (*´∀`)~♥", " O(∩_∩)O ", " (•̀ᴗ•́)و ", 
    " ✨ ", " 🚀 ", " ✅ ", " ☘️ ", " ❄️ ", " ☕ ", " ⚓ "
]

class Obfuscator:
    """内容风控干扰器：利用不可见字符打破哈希重合度，并规避敏感触发词"""
    
    @staticmethod
    def inject_zero_width_chars(text: str, density: float = 0.1) -> str:  # 保守值：从0.3降至0.1，降低可识别性
        """
        在中文/日文/韩文之间随机注入零宽字符，避开英文和 URL 链接。
        
        Args:
            text: 原始文本
            density: 注入密度，0.0 到 1.0，表示有百分之多少的概率在两个字符间插入。
            
        Returns:
            干扰后的强混淆文本
        """
        if not text:
            return text

        # 匹配非连续英文/数字/标点（即：主要是中日韩表意文字）
        # 我们用正则表达式切分出 "可能包含URL的字母数字块" 和 "其它文字"
        # 汉字范围大致是 \u4e00-\u9fa5
        
        result = []
        # 将连续的英文字母、数字和网址常用标点 (如 http://, .com) 视作安全块，不进行打断
        # 其他字符尝试进行零宽注入
        
        # 按单个字符迭代（但需要避开 http/https 块）
        # 简单策略：利用正则寻找完整的 URL 并先替换为占位符，处理完再换回来。
        url_pattern = re.compile(r'(https?://[^\s]+|[a-zA-Z0-9][-a-zA-Z0-9]{0,62}(\.[a-zA-Z0-9][-a-zA-Z0-9]{0,62})+\.?)')
        urls = url_pattern.findall(text)
        
        # 提取真实的网址字符串
        url_strings = [u[0] if isinstance(u, tuple) else u for u in urls]
        
        placeholder = "|||__TIEBAMECHA_URL_PLACEHOLDER_{}__|||"
        temp_text = text
        for i, url in enumerate(url_strings):
            temp_text = temp_text.replace(url, placeholder.format(i))
            
        # 对非 URL 的部分（逐字）进行零宽注入
        chars = list(temp_text)
        obfuscated_chars = []
        
        for i, char in enumerate(chars):
            obfuscated_chars.append(char)
            # 如果是占位符相关的字符，就一直跳过直到下一个普通字符
            # 严格来说我们只在汉字后面插入
            if '\u4e00' <= char <= '\u9fff' and i < len(chars) - 1:
                if random.random() < density:
                    obfuscated_chars.append(random.choice(ZERO_WIDTH_CHARS))
                    
        obfuscated_text = "".join(obfuscated_chars)
        
        # 还原 URL
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
                # 20%的概率在行末多加一个看不见的空格，或者换行
                p += " " * random.randint(1, 3)
            new_paragraphs.append(p)
            
        return "\n".join(new_paragraphs)

    @staticmethod
    def inject_random_symbols(text: str) -> str:
        """在文本开头、结尾或段落间随机插入表情或符号"""
        if not text or random.random() > 0.5: # 50% 概率不注入，保持自然
            return text
        
        symbol = random.choice(RANDOM_SYMBOLS)
        pos = random.choice(["start", "end", "both"])
        
        if pos == "start":
            return f"{symbol} {text}"
        elif pos == "end":
            return f"{text} {symbol}"
        else:
            return f"{symbol} {text} {symbol}"
