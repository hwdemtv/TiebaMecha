"""Web pages package：仅重导出高频页面，其余由 app.py 懒加载。"""

from .accounts import AccountsPage
from .dashboard import DashboardPage
from .posts import PostsPage
from .sign import SignPage

__all__ = [
    "DashboardPage",
    "AccountsPage",
    "SignPage",
    "PostsPage",
]
