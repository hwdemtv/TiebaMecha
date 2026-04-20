"""Database models for TiebaMecha"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy declarative base"""
    pass


class Account(Base):
    """贴吧账号"""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="账号名称/备注")
    bduss: Mapped[str] = mapped_column(String(200), nullable=False, comment="加密存储的BDUSS")
    stoken: Mapped[str] = mapped_column(String(200), default="", comment="加密存储的STOKEN")
    user_id: Mapped[int] = mapped_column(Integer, default=0, comment="用户ID")
    user_name: Mapped[str] = mapped_column(String(100), default="", comment="贴吧用户名")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否为当前使用账号")
    proxy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="关联代理ID")
    cuid: Mapped[str] = mapped_column(String(100), default="", comment="唯一设备标识")
    user_agent: Mapped[str] = mapped_column(String(255), default="", comment="浏览器标识")
    status: Mapped[str] = mapped_column(String(20), default="unknown", comment="账号状态: active/expired/error/suspended_proxy")
    last_verified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后验证时间")
    post_weight: Mapped[int] = mapped_column(Integer, default=5, comment="发帖权重 1–10，用于加权随机抽样")
    suspended_reason: Mapped[str] = mapped_column(String(200), default="", comment="挂起原因（代理失效自动填充）")
    is_maint_enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否开启拟人化自动养号")
    last_maint_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后一次养号维护时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
    
    # 索引定义 - 优化查询性能
    __table_args__ = (
        Index("ix_accounts_is_active", "is_active"),  # 查找活跃账号
        Index("ix_accounts_status", "status"),  # 按状态筛选
        Index("ix_accounts_proxy_id", "proxy_id"),  # 查找绑定特定代理的账号
    )


class Forum(Base):
    """关注的贴吧"""

    __tablename__ = "forums"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fid: Mapped[int] = mapped_column(Integer, nullable=False, comment="贴吧ID")
    fname: Mapped[str] = mapped_column(String(100), nullable=False, comment="贴吧名称")
    is_sign_today: Mapped[bool] = mapped_column(Boolean, default=False, comment="今日是否已签到")
    last_sign_status: Mapped[str] = mapped_column(String(20), default="pending", comment="签到状态: pending/success/failure")
    sign_count: Mapped[int] = mapped_column(Integer, default=0, comment="连续签到天数")
    level: Mapped[int] = mapped_column(Integer, default=0, comment="用户在该吧的等级")
    last_sign_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后签到日期")
    history_total: Mapped[int] = mapped_column(Integer, default=0, comment="历史累积签到次数")
    history_success: Mapped[int] = mapped_column(Integer, default=0, comment="历史签到成功次数")
    history_failed: Mapped[int] = mapped_column(Integer, default=0, comment="历史签到失败次数")
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="关联账号ID")
    is_post_target: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否允许作为发贴目标")
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否在 UI 中隐藏且跳过签到")
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否被该吧吧务封禁")
    ban_reason: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="封禁原因")

    __table_args__ = (
        UniqueConstraint("fid", "account_id", name="uq_forum_fid_account"),
        Index("ix_forums_account_id", "account_id"),  # 按账号查询贴吧
    )


class SignLog(Base):
    """签到日志"""

    __tablename__ = "sign_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    forum_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="贴吧ID")
    fname: Mapped[str] = mapped_column(String(100), nullable=False, comment="贴吧名称")
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, comment="是否成功")
    message: Mapped[str] = mapped_column(String(200), default="", comment="签到结果信息")
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="签到时间")
    
    __table_args__ = (
        Index("ix_sign_logs_signed_at", "signed_at"),  # 按时间排序查询
    )


class CrawlTask(Base):
    """爬取任务"""

    __tablename__ = "crawl_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="任务类型: threads/user/posts")
    target: Mapped[str] = mapped_column(String(200), nullable=False, comment="目标: 贴吧名/用户ID等")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="状态: pending/running/completed/failed")
    result_path: Mapped[str] = mapped_column(String(500), default="", comment="结果文件路径")
    total_count: Mapped[int] = mapped_column(Integer, default=0, comment="已爬取数量")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="完成时间")
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="关联账号ID")
    
    __table_args__ = (
        Index("ix_crawl_tasks_created_at", "created_at"),  # 按时间排序
        Index("ix_crawl_tasks_account_id", "account_id"),  # 按账号查询
    )


class PostCache(Base):
    """帖子缓存 (用于批量操作)"""

    __tablename__ = "post_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tid: Mapped[int] = mapped_column(Integer, nullable=False, comment="主题帖ID")
    pid: Mapped[int] = mapped_column(Integer, nullable=False, comment="回复ID")
    fname: Mapped[str] = mapped_column(String(100), nullable=False, comment="贴吧名称")
    title: Mapped[str] = mapped_column(String(500), default="", comment="帖子标题")
    author_id: Mapped[int] = mapped_column(Integer, default=0, comment="作者ID")
    author_name: Mapped[str] = mapped_column(String(100), default="", comment="作者名称")
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否选中")
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="缓存时间")
    
    __table_args__ = (
        Index("ix_post_cache_fname", "fname"),  # 按贴吧名称查询
        Index("ix_post_cache_cached_at", "cached_at"),  # 按缓存时间排序
    )


class Setting(Base):
    """全局设置"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True, comment="设置键")
    value: Mapped[str] = mapped_column(Text, default="", comment="设置值(JSON/String)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )


class Proxy(Base):
    """代理服务器"""

    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host: Mapped[str] = mapped_column(String(100), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(255), default="")
    password: Mapped[str] = mapped_column(String(255), default="")
    protocol: Mapped[str] = mapped_column(String(10), default="http")  # http/socks5
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    
    __table_args__ = (
        Index("ix_proxies_is_active", "is_active"),  # 查找可用代理
    )


class AutoRule(Base):
    """自动化规则 (删帖/监控)"""

    __tablename__ = "auto_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fname: Mapped[str] = mapped_column(String(100), nullable=False, comment="贴吧名")
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="keyword/regex")
    pattern: Mapped[str] = mapped_column(String(500), nullable=False, comment="规则内容")
    action: Mapped[str] = mapped_column(String(20), default="delete", comment="动作: delete/notify")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class BatchPostTask(Base):
    """批量/定时发帖任务"""

    __tablename__ = "batch_post_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fname: Mapped[str] = mapped_column(String(100), nullable=False, comment="目标贴吧")
    titles_json: Mapped[str] = mapped_column(Text, nullable=False, comment="标题池 JSON")
    contents_json: Mapped[str] = mapped_column(Text, nullable=False, comment="内容池 JSON")
    accounts_json: Mapped[str] = mapped_column(Text, nullable=False, comment="账号 ID 列表 JSON")
    fnames_json: Mapped[str] = mapped_column(Text, default="[]", comment="目标贴吧列表 JSON（多贴吧支持）")
    strategy: Mapped[str] = mapped_column(String(20), default="round_robin", comment="发帖策略: round_robin/random/weighted")
    delay_min: Mapped[float] = mapped_column(Float, default=60.0)
    delay_max: Mapped[float] = mapped_column(Float, default=300.0)

    use_ai: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否启用 AI 改写")
    schedule_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="计划执行时间")
    interval_hours: Mapped[int] = mapped_column(Integer, default=0, comment="重复执行间隔(小时)，0表示不重复")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="pending/running/completed/failed")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (
        Index("ix_batch_post_tasks_created_at", "created_at"),  # 按时间排序
    )


class MaterialPool(Base):
    """全局物料池 (打通短链同步、AI 预改写、矩阵发帖状态反馈)"""

    __tablename__ = "material_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="发送用标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="发送用正文 (可含图片/短链)")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="状态: pending/success/failed")
    
    ai_status: Mapped[str] = mapped_column(String(20), default="none", comment="AI改写状态: none/rewritten")
    original_title: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="改写前原标题 (供重置使用)")
    original_content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="改写前原内容 (供重置使用)")
    
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="上次执行时间")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="发送失败原因/错误日志")
    
    # --- 自动回帖(自顶) 增强字段 ---
    posted_tid: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="发帖成功后的线程ID")
    posted_fname: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="发布所在的贴吧")
    posted_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="精确发帖时间")
    posted_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="发布该物料的账号ID")
    is_auto_bump: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否开启自动回帖")
    bump_count: Mapped[int] = mapped_column(Integer, default=0, comment="已回帖(自顶)次数")
    last_bumped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后一次回帖时间")
    
    # --- 自顶模式配置 (扩展字段) ---
    # bump_mode: once=次数上限(默认), scheduled=定时周期, matrix_loop=矩阵轮换循环
    bump_mode: Mapped[str] = mapped_column(String(20), default="once", comment="自顶模式: once/scheduled/matrix_loop")
    # 定时周期模式
    bump_hour: Mapped[int] = mapped_column(Integer, default=10, comment="每日自顶时间(小时 0-23)")
    bump_duration_days: Mapped[int] = mapped_column(Integer, default=0, comment="自顶持续天数(0=永久)")
    bump_start_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="自顶开始日期")
    # 矩阵轮换模式
    bump_account_ids: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="轮换账号ID列表(JSON数组)")
    bump_account_index: Mapped[int] = mapped_column(Integer, default=0, comment="当前轮换到第几个账号")
    bump_last_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="上次执行日期(用于每日一次判断)")

    survival_status: Mapped[str] = mapped_column(String(20), default="unknown", comment="存活状态: unknown/alive/dead")
    death_reason: Mapped[str] = mapped_column(String(100), default="", comment="被删原因: deleted_by_user/auto_removed/banned_by_mod/error")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后存活检测时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="注入时间")
    
    __table_args__ = (
        Index("ix_material_pool_status", "status"),  # 高频筛选
        Index("ix_material_pool_created_at", "created_at"),  # 按时间排序
    )

class TargetPool(Base):
    """全局靶场大池 (全域贴吧营销组)"""

    __tablename__ = "target_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fname: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, comment="贴吧名称")
    post_group: Mapped[str] = mapped_column(String(200), default="", comment="发帖分组标签(e.g. 'IT,资源')")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="状态：可用 / 已自动熔断屏蔽")

    # 统计与自动熔断
    success_count: Mapped[int] = mapped_column(Integer, default=0, comment="历史破防成功数")
    fail_count: Mapped[int] = mapped_column(Integer, default=0, comment="连续拦截失败数(满阈值熔断)")
    last_fail_reason: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="最近被拦截的原因说明")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="录入时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="状态更新时间"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后一次空降轰炸时间")

    __table_args__ = (
        Index("ix_target_pool_post_group", "post_group"),
        Index("ix_target_pool_is_active", "is_active"),
    )


class Notification(Base):
    """系统通知"""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False, comment="通知类型")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="标题")
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="内容")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已读")
    action_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="操作链接")
    extra_json: Mapped[str] = mapped_column(Text, default="{}", comment="扩展数据 JSON")
    source: Mapped[str] = mapped_column(String(50), default="local", comment="来源: local/remote")
    remote_id: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="远程通知ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")

    __table_args__ = (
        Index("ix_notifications_is_read", "is_read"),
        Index("ix_notifications_created_at", "created_at"),
        Index("ix_notifications_source", "source"),
    )


class ThreadRecord(Base):
    """帖子管理监控记录"""

    __tablename__ = "thread_records"

    tid: Mapped[int] = mapped_column(Integer, primary_key=True, comment="主题帖ID")
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="帖子标题")
    author_name: Mapped[str] = mapped_column(String(100), default="", comment="作者名称")
    author_id: Mapped[int] = mapped_column(Integer, default=0, comment="作者ID")
    reply_num: Mapped[int] = mapped_column(Integer, default=0, comment="回复数")
    text: Mapped[str] = mapped_column(Text, default="", comment="正文摘要")
    fname: Mapped[str] = mapped_column(String(100), nullable=False, comment="贴吧名称")
    is_good: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否精品")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )

    __table_args__ = (
        Index("ix_thread_records_fname", "fname"),
        Index("ix_thread_records_updated_at", "updated_at"),
    )


class CaptchaEvent(Base):
    """验证码事件记录"""

    __tablename__ = "captcha_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="关联账号ID")
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="关联任务ID")
    event_type: Mapped[str] = mapped_column(String(50), default="captcha", comment="事件类型: captcha/rate_limit/block")
    reason: Mapped[str] = mapped_column(String(100), default="", comment="触发原因")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="状态: pending/resolved")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="触发时间")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="解决时间")
    resolved_by: Mapped[str] = mapped_column(String(50), default="", comment="解决方式: manual/auto/timeout")
    notes: Mapped[str] = mapped_column(Text, default="", comment="备注信息")

    __table_args__ = (
        Index("ix_captcha_events_account_id", "account_id"),
        Index("ix_captcha_events_status", "status"),
        Index("ix_captcha_events_created_at", "created_at"),
    )

class BatchPostLog(Base):
    """批量发帖流水日志 (持久化存储)"""

    __tablename__ = "batch_post_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="关联任务ID")
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="关联账号ID")
    account_name: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="账号名称")
    fname: Mapped[str] = mapped_column(String(100), nullable=False, comment="贴吧名称")
    title: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="帖子标题")
    tid: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="成功后的帖子ID")
    status: Mapped[str] = mapped_column(String(20), default="success", comment="发送结果: success/error/skipped")
    message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误详情或备注")
    data_json: Mapped[str] = mapped_column(Text, default="{}", comment="扩展业务数据 JSON")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="记录时间")

    __table_args__ = (
        Index("ix_batch_post_logs_created_at", "created_at"),
        Index("ix_batch_post_logs_task_id", "task_id"),
    )
