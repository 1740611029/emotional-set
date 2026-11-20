"""交易日时段判断。

盘中实时快照链路已于 2026-11-20 随指数总览页下线；本模块只保留交易时段判定，
供调度器在盘中跳过盘后调度动作。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from . import paths  # noqa: F401  # 配置 sys.path

CN = ZoneInfo("Asia/Shanghai")


def trading_session(now: datetime | None = None) -> bool:
    """仅按时间窗判断，节假日由实时源回执二次校验。"""
    now = now or datetime.now(CN)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= minutes <= 11 * 60 + 30) or (13 * 60 <= minutes <= 15 * 60)
