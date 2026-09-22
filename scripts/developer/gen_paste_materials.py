#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""粘贴清单 → 物料种子生成器（2026-09-22 内容池整改配套）

输入格式：每行 `标题部<TAB>网盘链接`（支持 [名][年][类型][国家] 括号格式、
名称+年份+空格+类型 的自由格式）。
输出：JSON 数组 [{title, content, link_url}]，供服务器端装填 + GLM 逐条改写。

规则对齐内容池准入标准：
- 标题清洗：去画质词（1080P/4K/中字/全集）、去【】分组标签、波浪线归一
- 正文：一物一题、真实观影观感口吻、无营销禁词、链接不进正文
- 去重：同 名+年 视为重复只留首条；无法识别片名的残次行剔除
"""
import json
import random
import re
import sys

URL_RE = re.compile(r"^https?://pan\.baidu\.com/s/")
YEAR_RE = re.compile(r"(19|20)\d{2}")
JUNK_TOKENS = ("1080p", "4k", "中英字幕", "英语中字", "中字", "全集")

OPENINGS = [
    "前阵子片荒，翻到这部《{name}》，意外看进去了。",
    "又到了深夜刷片的时间，这次想聊聊《{name}》。",
    "把《{name}》补完了，趁着印象还热，随便写写。",
    "朋友推荐了很久的《{name}》，最近总算排上队看了。",
    "整理硬盘的时候又看到《{name}》，忍不住二刷了一遍。",
]
CLOSINGS = [
    "总体值得一看，找个不被打扰的晚上，安静看完挺好。",
    "评分高低放一边，至少看的过程是投入的，推荐给片荒的朋友。",
    "看完想找人聊聊，有看过的朋友欢迎交流。",
    "可能不是所有人的菜，但合我口味，就这样。",
]
GENERIC = [
    "有些片子的好处在于不端着，平实地把故事讲完，反而容易记住。",
    "观感因人而异，我对这类题材的容忍度一直比较高，看得很顺。",
    "中间有一段戏的调度和表演都很舒服，是全片我印象最深的段落。",
    "看完去翻了点背景资料，拍摄过程中的几件事还挺有意思的。",
    "配乐没有存在感过头，该响的地方响，这点比很多片子做得好。",
]
DETAIL = [
    "片长不算短，但全程没有看表，这点对节奏就是最好的肯定。",
    "几个配角的存在感都很强，没有一个是纯工具人。",
    "取景和美术都在线，画面语言是有想法的。",
    "开场十分钟就把基调立住了，后面基本没有掉下来。",
]
ANGLE_POOLS = {
    "doc": [
        "纪录片的好处是真实自有千钧之力，这个题材本身就足够震撼。",
        "镜头很克制，不煽情，但看完心里久久不能平静。",
    ],
    "horror": [
        "吓人的桥段其实不多，真正瘆人的是那种慢慢渗透的不安感。",
        "恐怖片看多了会发现，最有效的从来不是突然惊吓，是日常里长出来的诡异。",
    ],
    "war": [
        "战争场面只是背景，真正着墨的是普通人的处境，立意高了一层。",
        "历史的重量都压在细节里，看完久久说不出话。",
    ],
    "biography": [
        "人物传记最难的是不写成流水账，这部选的几个切面都挺准。",
        "真实事件的曲折程度远超编剧的想象，光是如实呈现就够精彩。",
    ],
    "music": [
        "音乐部分的现场感做得很足，就算不是为了故事也值得看。",
        "旋律和剧情咬合得很好，几段现场戏看完耳朵怀孕。",
    ],
    "animation": [
        "画面风格很舒服，故事虽然简单，情绪落点却很准。",
        "动画的表达空间是真人给不了的，这部用得很聪明。",
    ],
    "scifi": [
        "设定不算多新鲜，但完成度很高，世界观立住了就能看得很投入。",
        "科幻外壳下讲的还是人的事，这点处理得比很多大制作强。",
    ],
    "romance": [
        "感情戏拍得很克制，越是欲言又止的部分越动人。",
        "不是那种甜腻的爱情片，讲的是错过和遗憾，看完缓了好几天。",
    ],
    "thriller": [
        "悬念一直吊到最后才揭开，中间几次以为猜到了走向，结果都被打了脸。",
        "氛围营造很成熟，紧张感不是靠音效硬堆，而是从剧情缝隙里渗出来的。",
    ],
    "action": [
        "动作场面设计得挺扎实，没有过度依赖慢镜头和爆炸，拳拳到肉。",
        "节奏很快，从头打到尾却不像流水账，剪辑帮了大忙。",
    ],
    "comedy": [
        "笑点不算密集，但胜在不尬，好几个段子是过几天想起来还会笑的那种。",
        "喜剧的底色其实是心酸，这部把分寸拿捏得刚刚好。",
    ],
    "family": [
        "家庭题材最怕说教，这部全程白描，反而更打动人。",
        "柴米油盐里的张力比什么大场面都真实。",
    ],
    "series": [
        "剧集的铺开节奏很舒服，每集留钩子但不刻意，适合慢慢追。",
        "群像戏各有各的弧光，追完像跟一群朋友告了别。",
    ],
    "drama": [
        "剧情本身不复杂，胜在人物立得住，几场对手戏的张力到现在还记得。",
        "节奏偏慢，但情绪是逐渐堆上来的，看完心里堵得慌又觉得值。",
    ],
}
CLASS_KEYWORDS = [
    ("doc", ("纪录",)),
    ("horror", ("恐怖",)),
    ("war", ("战争", "历史")),
    ("biography", ("传记",)),
    ("music", ("音乐", "歌舞")),
    ("animation", ("动画",)),
    ("scifi", ("科幻", "奇幻", "魔幻")),
    ("romance", ("爱情", "浪漫")),
    ("thriller", ("惊悚", "悬疑", "犯罪")),
    ("action", ("动作", "冒险", "西部")),
    ("comedy", ("喜剧",)),
    ("family", ("家庭", "亲情")),
    ("series", ("美剧", "韩剧", "日剧", "泰剧", "港剧", "剧集", "真人秀", "韩综", "综艺", "季")),
]


def classify(text: str) -> str:
    for cls, kws in CLASS_KEYWORDS:
        for kw in kws:
            if kw in text:
                return cls
    return "drama"


def clean_name(raw: str) -> str:
    s = raw.strip()
    s = s.replace("~", "").replace("～", "")
    s = re.sub(r"【[^】]*】", "", s)
    low = s.lower()
    for tok in JUNK_TOKENS:
        idx = low.find(tok)
        if idx >= 0:
            s = s[:idx]
            low = s.lower()
    s = s.strip(" .。_-,，")
    return s.strip()


def parse_line(title_part: str):
    """→ (name, year, meta) 或 None（残次行）"""
    s = title_part.strip()
    if not s:
        return None
    meta_parts = []
    year = None

    if s.startswith("["):
        # 括号格式：[名][年][类型][国家]，尾部可能有裸文本
        chunks = [c.strip() for c in s.replace("[", "]").split("]") if c.strip()]
        name_chunks = []
        for i, c in enumerate(chunks):
            m = YEAR_RE.search(c)
            if i > 0 and m and len(c) <= 6 and not year:
                year = m.group(0)
                continue
            if i > 0 and c in ("全集",):
                continue
            if i == 0:
                name_chunks.append(c)
            else:
                meta_parts.append(c)
        name = clean_name(" ".join(name_chunks))
    else:
        m = YEAR_RE.search(s)
        if m:
            year = m.group(0)
            before, after = s[: m.start()], s[m.end():]
            after = after.replace("(", " ").replace("（", " ")
            parts = [p.strip() for p in re.split(r"\s{2,}|\s", after) if p.strip()]
            if before.strip(" (（-_.，"):
                name = clean_name(before)
                meta_parts = parts
            else:
                # 年份开头（如 "2022 惩罚  阿根廷智利剧情电影"）：年份后首个词是片名
                name = clean_name(parts[0]) if parts else ""
                meta_parts = parts[1:]
        else:
            parts = [p.strip() for p in re.split(r"\s{2,}", s) if p.strip()]
            name = clean_name(parts[0])
            meta_parts = parts[1:]

    if not name or name in ("第",):
        return None
    return name, year, " ".join(meta_parts)


def build_content(name: str, year, meta: str) -> str:
    cls = classify(meta + name)
    fact_bits = []
    if year:
        fact_bits.append(year + "年")
    if meta:
        fact_bits.append(meta)
    fact = f"这部{''.join(fact_bits)}，" if fact_bits else "这部作品，"
    sentences = [
        random.choice(OPENINGS).format(name=name),
        fact + random.choice(ANGLE_POOLS[cls]),
        random.choice(DETAIL),
        random.choice(GENERIC),
        random.choice(DETAIL),
        random.choice(CLOSINGS),
    ]
    if sentences[2] == sentences[4]:
        sentences[4] = random.choice([d for d in DETAIL if d != sentences[2]])
    return "".join(sentences)


def main(in_path: str, out_path: str):
    random.seed(20260922)
    entries, skipped, dup_dropped = [], [], []
    seen = set()
    with open(in_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip("\n").strip()
            if not line:
                continue
            parts = line.split("\t")
            title_part, url = parts[0].strip(), parts[-1].strip()
            if not URL_RE.match(url):
                skipped.append((lineno, title_part[:30], "链接非百度网盘格式"))
                continue
            parsed = parse_line(title_part)
            if not parsed:
                skipped.append((lineno, title_part[:30], "片名无法识别"))
                continue
            name, year, meta = parsed
            key = re.sub(r"[\s\W_]", "", name).lower() + (year or "")
            if key in seen:
                dup_dropped.append((lineno, f"《{name}》" + (f"({year})" if year else "")))
                continue
            seen.add(key)
            title = f"《{name}》（{year}）" if year else f"《{name}》"
            entries.append({
                "title": title,
                "content": build_content(name, year, meta),
                "link_url": url,
            })

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)

    print(f"装填条目: {len(entries)}")
    print(f"剔除残次: {len(skipped)}")
    for lineno, t, r in skipped:
        print(f"  行{lineno} [{t}] {r}")
    print(f"去重丢弃: {len(dup_dropped)}")
    for lineno, t in dup_dropped:
        print(f"  行{lineno} {t}")
    lens = sorted(len(e["content"]) for e in entries)
    if lens:
        print(f"正文长度 min/median/max: {lens[0]}/{lens[len(lens)//2]}/{lens[-1]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
