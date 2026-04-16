---
name: fix-bump-content-anti-spam
overview: 修改 batch_post.py 中 AutoBumpManager 的自顶回帖文案生成逻辑，移除"分享好物"推广格式，将 AI 改写物料的回帖内容改为更自然的拟人化模板，避免触发贴吧反垃圾机制。
todos:
  - id: rewrite-bump-engine
    content: 重写 batch_post.py 第 691~709 行拟人化文案引擎：替换词库、删除分享好物分支、降低标题提及率
    status: completed
  - id: add-content-tests
    content: 在 test_bump_config.py 新增 TestBumpContentEngine 测试类，验证新文案不包含推广特征
    status: completed
    dependencies:
      - rewrite-bump-engine
  - id: verify-all-tests
    content: 运行全部测试确认语法正确且原有测试未被破坏
    status: completed
    dependencies:
      - rewrite-bump-engine
      - add-content-tests
---

## 产品概述

自顶回帖（Bump）内容被百度反垃圾系统判定为"涉嫌异常行为"并删除。截图显示所有被删回帖均以 `"分享好物：{完整标题}"` 格式呈现，这是 AI 改写物料（`ai_status == "rewritten"`）专属的推广格式分支，具有强烈的广告/推广特征，触发了百度反垃圾机制。

## 核心功能

- **删除有毒分支**：移除 `if material.ai_status == "rewritten"` 的 `"分享好物：{material.title}"` 固定前缀逻辑，让所有物料统一走自然模板词库
- **重构文案引擎**：
- 将 `BUMP_TEMPLATES` 从 12 条扩充至约 20 条，新增纯互动/中性评价类短句
- 移除带有明显推广色彩的模板（如"资源已取"、"资源分享"、"百度一下支持此贴"等）
- 新增自然口语化模板（如"不错不错"、"看了"、"收藏了"、"mark以后看"、"挺有意思的"等）
- **弱化标题提及**：
- 携带标题关键词概率从 40% 降至 20%
- 去掉 `【】` 方括号包裹，改为更自然的半句式提及（如"这个确实不错，{base_text}"）
- 标题截断从 `[:10]` 改为 `[:8]`，减少长标题暴露
- **Emoji 精简**：移除过于花哨的颜文字 emoji，保留少量中性表情

## 技术栈

- Python 3.11, pytest + pytest-asyncio 测试框架
- Flet (flet) UI 框架
- SQLAlchemy ORM

## 实现方案

### 核心改动：`batch_post.py` 第 691~709 行

**修改策略**：将硬编码在方法内部的词库和生成逻辑替换为新的自然化版本。

1. **`BUMP_TEMPLATES` 词库替换** -- 从 12 条改为约 20 条纯互动/中性模板，分为三类：

- 极简互动类（40%）：`"不错不错"`, `"看了"`, `"收藏了"`, `"挺有意思的"`, `"mark了"`, `"可以"`, `"嗯"` 等
- 中性评价类（35%）：`"楼主整理得挺用心的"`, `"这个系列确实还可以"`, `"之前看过类似的，这个也不错"`, `"感谢整理"` 等
- 轻量帮顶类（25%）：`"帮顶一下"`, `"支持下"`, `"顶一个"` 等

2. **`RANDOM_EMOJIS` 精简** -- 移除 `(๑•̀ㅂ• dot)و✧` 等夸张颜文字，保留 `✨`, `👍`, `[赞]` 等少量中性表情

3. **标题提及逻辑改造** -- 

- 概率 0.4 → 0.2
- 格式 `关于【{keyword}】：{base_text}` → `{keyword} 还行，{base_text}` （去掉括号、更口语化）
- 截断长度 `[:10]` → `[:8]`

4. **彻底删除 `ai_status == "rewritten"` 分支** -- 不再有特殊处理，所有物料统一走同一路径

### 测试覆盖

- 在 `tests/test_bump_config.py` 中新增 `TestBumpContentEngine` 类
- 覆盖：模板多样性验证、无"分享好物"输出、标题截断正确性、概率参数合理性
- 复用现有 `db` fixture 和 `AutoBumpManager` 构造方式

### 性能影响

- 无性能影响：仅修改字符串常量和随机选择逻辑，不涉及 I/O 或数据库查询
- 文案生成在每次循环内重新创建列表（当前行为），保持不变

## 目录结构

```
d:/软件开发/TiebaMecha/
├── src/tieba_mecha/core/batch_post.py    # [MODIFY] 第 691~709 行，文案引擎重写
└── tests/test_bump_config.py              # [MODIFY] 新增 TestBumpContentEngine 测试类
```