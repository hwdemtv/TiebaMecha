"""帖子管理页（三场景）：发布新帖 / 我的帖子 / 批量操作与分析。

app.py 的懒加载约定：`.pages.posts` 包需导出 PostsPage。
"""

from .page import PostsPage

__all__ = ["PostsPage"]
