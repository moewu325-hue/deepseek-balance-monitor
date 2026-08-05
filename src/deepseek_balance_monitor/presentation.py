from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .api import currency_symbol, select_currency
from .models import BalanceSnapshot


ESTIMATED_DECREASE_LABEL = "今日余额减少（估算）"


@dataclass(frozen=True)
class BalanceDisplay:
    currency: str
    total_balance: Decimal
    topped_up_balance: Decimal
    balance_text: str
    topped_up_text: str
    available: bool


def build_balance_display(snapshot: BalanceSnapshot, preferred: str) -> BalanceDisplay:
    try:
        selected = select_currency(snapshot, preferred)
    except ValueError as error:
        raise ValueError("首选币种不可用，无法确定显示金额。") from error
    currency = _normalize_currency(selected.currency)
    return BalanceDisplay(
        currency=currency,
        total_balance=selected.total_balance,
        topped_up_balance=selected.topped_up_balance,
        balance_text=format_currency_amount(currency, selected.total_balance),
        topped_up_text=format_currency_amount(currency, selected.topped_up_balance),
        available=snapshot.is_available,
    )


def format_estimated_decrease(currency: str, amount: Decimal) -> str:
    return f"{ESTIMATED_DECREASE_LABEL}：{format_currency_amount(currency, amount)}"


def format_currency_amount(currency: str, amount: Decimal) -> str:
    code = _normalize_currency(currency)
    value = _finite_decimal(amount)
    rendered = f"{value:.2f}"
    symbol = currency_symbol(code)
    if symbol == code:
        return f"{code} {rendered}"
    return f"{symbol}{rendered}"


def _normalize_currency(currency: str) -> str:
    if not isinstance(currency, str):
        raise ValueError("币种代码无效。")
    normalized = currency.strip().upper()
    if not normalized or not normalized.isalnum():
        raise ValueError("币种代码无效。")
    return normalized


def _finite_decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("金额无效。")
    return value
