"""Web pages"""

from .accounts import AccountsPage
from .crawl import CrawlPage
from .dashboard import DashboardPage
from .posts import PostsPage
from .sign import SignPage

__all__ = [
    "DashboardPage",
    "AccountsPage",
    "SignPage",
    "PostsPage",
    "CrawlPage",
]
