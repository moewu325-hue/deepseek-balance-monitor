from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class ProbeStatus(str, Enum):
    VALID = "valid"
    AUTHENTICATED_NO_BALANCE = "authenticated_no_balance"
    INVALID = "invalid"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class BalanceInfo:
    currency: str
    total_balance: Decimal
    granted_balance: Decimal
    topped_up_balance: Decimal


@dataclass(frozen=True)
class BalanceSnapshot:
    is_available: bool
    balance_infos: tuple[BalanceInfo, ...]


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    snapshot: BalanceSnapshot | None = None
    error: str | None = None


@dataclass(frozen=True)
class FetchResult:
    snapshot: BalanceSnapshot | None
    error: str | None = None
    status_code: int | None = None
