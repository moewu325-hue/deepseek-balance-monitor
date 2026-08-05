from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def to_shanghai(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(SHANGHAI)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(SHANGHAI)


def is_peak_hour(now: datetime | None = None) -> bool:
    local = to_shanghai(now)
    minutes = local.hour * 60 + local.minute
    return 9 * 60 <= minutes < 12 * 60 or 14 * 60 <= minutes < 18 * 60


def peak_label(now: datetime | None = None) -> tuple[str, bool]:
    peak = is_peak_hour(now)
    if peak:
        return "▲ 高峰期 · 双倍消耗", True
    return "● 非高峰 · 正常计费", False
