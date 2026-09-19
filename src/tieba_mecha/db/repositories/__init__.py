"""按领域拆分的 Repository mixin 集合。"""

from .account_repo import AccountRepository
from .forum_repo import ForumRepository
from .setting_repo import SettingRepository
from .proxy_repo import ProxyRepository
from .rule_repo import RuleRepository
from .batch_task_repo import BatchTaskRepository
from .material_repo import MaterialRepository
from .target_pool_repo import TargetPoolRepository
from .captcha_repo import CaptchaRepository
from .notification_repo import NotificationRepository
from .thread_repo import ThreadRepository
from .batch_log_repo import BatchLogRepository
from .bump_log_repo import BumpLogRepository

__all__ = ["AccountRepository", "ForumRepository", "SettingRepository", "ProxyRepository", "RuleRepository", "BatchTaskRepository", "MaterialRepository", "TargetPoolRepository", "CaptchaRepository", "NotificationRepository", "ThreadRepository", "BatchLogRepository", "BumpLogRepository"]
