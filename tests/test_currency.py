from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
import unittest

from deepseek_balance_monitor.api import currency_symbol, select_currency
from deepseek_balance_monitor.models import BalanceInfo, BalanceSnapshot


def balance(currency: str, total: str) -> BalanceInfo:
    return BalanceInfo(
        currency=currency,
        total_balance=Decimal(total),
        granted_balance=Decimal("0"),
        topped_up_balance=Decimal(total),
    )


class CurrencySelectionTests(unittest.TestCase):
    def test_uses_only_currency_when_preference_is_absent(self):
        snapshot = BalanceSnapshot(True, (balance("USD", "4.20"),))

        self.assertEqual("USD", select_currency(snapshot, "CNY").currency)

    def test_selects_preferred_currency(self):
        snapshot = BalanceSnapshot(
            True,
            (balance("CNY", "10.00"), balance("USD", "4.20")),
        )

        selected = select_currency(snapshot, "USD")

        self.assertEqual("USD", selected.currency)
        self.assertEqual(Decimal("4.20"), selected.total_balance)

    def test_rejects_ambiguous_fallback_when_preference_is_absent(self):
        snapshot = BalanceSnapshot(
            True,
            (balance("USD", "4.20"), balance("EUR", "3.10")),
        )

        with self.assertRaisesRegex(ValueError, "Preferred currency is unavailable"):
            select_currency(snapshot, "CNY")

    def test_unknown_currency_symbol_is_its_code(self):
        self.assertEqual("EUR", currency_symbol("EUR"))

    def test_known_currency_symbols(self):
        self.assertEqual("¥", currency_symbol("CNY"))
        self.assertEqual("$", currency_symbol("USD"))

    def test_selection_never_sums_balances_across_currencies(self):
        snapshot = BalanceSnapshot(
            True,
            (balance("CNY", "10.00"), balance("USD", "4.20")),
        )

        self.assertEqual(Decimal("10.00"), select_currency(snapshot, "CNY").total_balance)

    def test_balance_models_are_immutable(self):
        info = balance("USD", "4.20")

        with self.assertRaises(FrozenInstanceError):
            info.currency = "CNY"


if __name__ == "__main__":
    unittest.main()
