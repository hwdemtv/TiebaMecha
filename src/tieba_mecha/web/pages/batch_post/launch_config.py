"""LaunchConfig：批量发帖任务的结构化配置快照。

此前账号选择、靶场选择、物料与各策略项散落在几十个控件 .value 与
页面实例状态上（self._selected_account_ids 等），UI 与状态强耦合。
向导化、启动摘要、干跑预检、复制上一任务都需要一份可序列化的配置对象，
本模块是该对象的单源定义。纯数据 + 校验，不依赖 UI。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime


class LaunchConfigError(ValueError):
    """配置收集/解析失败（对应原页面各处 Snackbar 拦截文案）。"""


@dataclass
class LaunchConfig:
    """一次批量发帖任务的完整配置。

    fnames 语义与引擎一致：本地自留区 + 全域轰炸组合并去重后的目标列表；
    material_ids 为 None 表示执行时从全局 pending 池取料（现状行为）。
    """

    account_ids: list[int] = field(default_factory=list)
    local_fnames: list[str] = field(default_factory=list)
    global_fnames: list[str] = field(default_factory=list)
    strategy: str = "round_robin"
    pairing_mode: str = "random"
    post_count: int = 0
    delay_min: float = 120.0
    delay_max: float = 600.0
    use_ai: bool = False
    ai_persona: str = "normal"
    # 调度
    use_schedule: bool = False
    schedule_type: str = "once"          # once/daily/weekly/interval
    schedule_time: datetime | None = None
    interval_hours: int = 0
    schedule_day_of_week: int | None = None
    reset_strategy: str = "new_only"
    # 物料：None = 全部待发（保留字段，向导化后由物料勾选填充）
    material_ids: list[int] | None = None

    def get_fnames(self) -> list[str]:
        """合并两组目标并去重（保持先后顺序）。"""
        seen: set[str] = set()
        merged: list[str] = []
        for fn in [*self.local_fnames, *self.global_fnames]:
            if fn and fn not in seen:
                seen.add(fn)
                merged.append(fn)
        return merged

    def to_dict(self) -> dict:
        data = asdict(self)
        data["schedule_time"] = (
            self.schedule_time.strftime("%Y-%m-%d %H:%M") if self.schedule_time else None
        )
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "LaunchConfig":
        raw_st = data.get("schedule_time")
        schedule_time = (
            datetime.strptime(raw_st, "%Y-%m-%d %H:%M") if raw_st else None
        )
        return cls(
            account_ids=list(data.get("account_ids") or []),
            local_fnames=list(data.get("local_fnames") or []),
            global_fnames=list(data.get("global_fnames") or []),
            strategy=data.get("strategy") or "round_robin",
            pairing_mode=data.get("pairing_mode") or "random",
            post_count=int(data.get("post_count") or 0),
            delay_min=float(data.get("delay_min") or 120.0),
            delay_max=float(data.get("delay_max") or 600.0),
            use_ai=bool(data.get("use_ai")),
            ai_persona=data.get("ai_persona") or "normal",
            use_schedule=bool(data.get("use_schedule")),
            schedule_type=data.get("schedule_type") or "once",
            schedule_time=schedule_time,
            interval_hours=int(data.get("interval_hours") or 0),
            schedule_day_of_week=data.get("schedule_day_of_week"),
            reset_strategy=data.get("reset_strategy") or "new_only",
            material_ids=list(data["material_ids"]) if data.get("material_ids") else None,
        )
