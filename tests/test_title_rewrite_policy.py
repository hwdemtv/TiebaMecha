"""标题不改写口径回归测试（2026-09-22 内容池整改）。

发帖时 AI 改写只采纳正文：片名即检索价值，标题侧长尾词注入是营销模式源头。
标题必须保持入库的《片名》（年份）规范格式。
"""

import inspect


def test_post_time_rewrite_keeps_stored_title():
    """发帖链路不得用 AI 改写标题覆盖入库标题。"""
    from tieba_mecha.core import batch_post

    source = inspect.getsource(batch_post.BatchPostManager)
    assert "content = opt_c" in source, "应只采纳改写正文"
    assert "title, content = opt_t, opt_c" not in source, "不得整体采纳改写结果（会覆盖标题）"
