from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

import requests

from .models import BalanceInfo, BalanceSnapshot, FetchResult, ProbeResult, ProbeStatus


_BALANCE_URL = "https://api.deepseek.com/user/balance"
_TIMEOUT = (3.05, 6.0)
_AMOUNT_PATTERN = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")


class _BearerAuth(requests.auth.AuthBase):
    def __init__(self, key: str) -> None:
        self._key = key

    def __call__(self, request: Any) -> Any:
        request.headers["Authorization"] = f"Bearer {self._key}"
        return request

    def __repr__(self) -> str:
        return "<_BearerAuth>"


class ApiClient:
    def __init__(self, session: Any | None = None) -> None:
        self._session = session if session is not None else requests.Session()

    def probe_key(self, key: str) -> ProbeResult:
        response, error = self._request(key)
        if error is not None:
            return ProbeResult(ProbeStatus.INDETERMINATE, error=error)
        if response.status_code == 401:
            return ProbeResult(ProbeStatus.INVALID)
        if response.status_code == 402:
            return ProbeResult(ProbeStatus.AUTHENTICATED_NO_BALANCE)
        if response.status_code != 200:
            return ProbeResult(ProbeStatus.INDETERMINATE, error=_http_error(response.status_code))

        snapshot, error = _parse_snapshot(response)
        if error is not None:
            return ProbeResult(ProbeStatus.INDETERMINATE, error=error)
        return ProbeResult(ProbeStatus.VALID, snapshot=snapshot)

    def fetch_balance(self, key: str) -> FetchResult:
        response, error = self._request(key)
        if error is not None:
            return FetchResult(None, error=error)
        if response.status_code != 200:
            return FetchResult(None, error=_http_error(response.status_code), status_code=response.status_code)

        snapshot, error = _parse_snapshot(response)
        return FetchResult(snapshot, error=error, status_code=response.status_code)

    def _request(self, key: str) -> tuple[Any | None, str | None]:
        try:
            response = self._session.get(
                _BALANCE_URL,
                auth=_BearerAuth(key),
                timeout=_TIMEOUT,
                allow_redirects=False,
                verify=True,
            )
        except Exception:
            return None, "Balance request failed"
        return response, None


def select_currency(snapshot: BalanceSnapshot, preferred: str) -> BalanceInfo:
    if not snapshot.balance_infos:
        raise ValueError("Balance response has no currencies")
    normalized_preference = preferred.upper()
    for info in snapshot.balance_infos:
        if info.currency.upper() == normalized_preference:
            return info
    if len(snapshot.balance_infos) == 1:
        return snapshot.balance_infos[0]
    raise ValueError("Preferred currency is unavailable")


def currency_symbol(code: str) -> str:
    return {"CNY": "¥", "USD": "$"}.get(code.upper(), code)


def _parse_snapshot(response: Any) -> tuple[BalanceSnapshot | None, str | None]:
    try:
        payload = response.json()
    except Exception:
        return None, "Balance response was not valid JSON"
    if not isinstance(payload, dict):
        return None, "Balance response has an invalid schema"

    is_available = payload.get("is_available")
    raw_infos = payload.get("balance_infos")
    if not isinstance(is_available, bool) or not isinstance(raw_infos, list):
        return None, "Balance response has an invalid schema"

    infos: list[BalanceInfo] = []
    currencies: set[str] = set()
    for raw_info in raw_infos:
        parsed, error = _parse_balance_info(raw_info)
        if error is not None:
            return None, error
        if parsed.currency in currencies:
            return None, "Balance response has an invalid schema"
        currencies.add(parsed.currency)
        infos.append(parsed)
    if not infos:
        return None, "Balance response has an invalid schema"
    return BalanceSnapshot(is_available, tuple(infos)), None


def _parse_balance_info(raw_info: Any) -> tuple[BalanceInfo | None, str | None]:
    if not isinstance(raw_info, dict):
        return None, "Balance response has an invalid schema"
    currency = raw_info.get("currency")
    if not isinstance(currency, str):
        return None, "Balance response has an invalid schema"
    if not 1 <= len(currency) <= 12 or not currency.isascii() or not currency.isalnum():
        return None, "Balance response has an invalid schema"
    currency = currency.upper()
    try:
        raw_amounts = (
            raw_info["total_balance"],
            raw_info["granted_balance"],
            raw_info["topped_up_balance"],
        )
        if not all(
            isinstance(value, str) and _AMOUNT_PATTERN.fullmatch(value) is not None
            for value in raw_amounts
        ):
            return None, "Balance response has an invalid schema"
        total_balance, granted_balance, topped_up_balance = map(Decimal, raw_amounts)
    except (KeyError, InvalidOperation, ValueError):
        return None, "Balance response has an invalid schema"
    if not all(value.is_finite() for value in (total_balance, granted_balance, topped_up_balance)):
        return None, "Balance response has an invalid schema"
    return BalanceInfo(currency, total_balance, granted_balance, topped_up_balance), None


def _http_error(status_code: int) -> str:
    return f"Balance request returned HTTP {status_code}"
