from datetime import datetime, timedelta
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from deepseek_balance_monitor.canvas_ui import (
    CanvasCallbacks,
    CanvasMonitorView,
    PanelState,
)
from deepseek_balance_monitor.presentation import BalanceDisplay
from deepseek_balance_monitor.storage import AppConfig


class RecordingCanvas:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def delete(self, tag: str) -> None:
        self.calls.append(("delete", tag))

    def create_text(self, x: int, y: int, **options: object) -> int:
        self.calls.append(
            (
                "text",
                x,
                y,
                options.get("text"),
                options.get("fill"),
                options.get("font"),
            )
        )
        return len(self.calls)

    def create_oval(self, *coordinates: int, **options: object) -> int:
        self.calls.append(
            ("oval", *coordinates, options.get("fill"), options.get("outline"))
        )
        return len(self.calls)

    def create_arc(self, *coordinates: int, **options: object) -> int:
        self.calls.append(("arc", *coordinates, options.get("outline")))
        return len(self.calls)

    def create_image(self, x: int, y: int, **options: object) -> int:
        self.calls.append(("image", x, y, options.get("image")))
        return len(self.calls)


class FakeRoot:
    pass


def callbacks() -> CanvasCallbacks:
    return CanvasCallbacks(
        refresh=lambda: None,
        open_console=lambda: None,
        request_exit=lambda: None,
        set_collapsed=lambda _value: None,
        save_position=lambda _x, _y, _collapsed: None,
        set_autostart=lambda _value: None,
    )


def display(
    currency: str = "CNY",
    total: str = "8.50",
    topped: str = "4.25",
    *,
    available: bool = True,
) -> BalanceDisplay:
    symbol = {"CNY": "¥", "USD": "$"}.get(currency, f"{currency} ")
    return BalanceDisplay(
        currency=currency,
        total_balance=Decimal(total),
        topped_up_balance=Decimal(topped),
        balance_text=f"{symbol}{Decimal(total):.2f}",
        topped_up_text=f"{symbol}{Decimal(topped):.2f}",
        available=available,
    )


class FullPanelRenderTests(unittest.TestCase):
    def make_view(self, canvas: RecordingCanvas | None = None) -> tuple[CanvasMonitorView, RecordingCanvas]:
        recording = canvas or RecordingCanvas()
        view = CanvasMonitorView(
            FakeRoot(),
            config=AppConfig(low_balance_warning="5"),
            callbacks=callbacks(),
            canvas=recording,
        )
        return view, recording

    def test_peak_panel_matches_legacy_coordinates_and_copy(self) -> None:
        view, canvas = self.make_view()
        now = datetime(2026, 8, 5, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        view.render(
            PanelState(
                display=display(),
                estimated_decrease=Decimal("1.25"),
                status_text="ONLINE",
                refreshed_at=now - timedelta(seconds=7),
            ),
            now=now,
        )
        self.assertEqual(view.normal_size, (255, 215))
        self.assertIn(
            ("text", 127, 58, "¥ 8.50", "#00e5ff", ("Consolas", 22, "bold")),
            canvas.calls,
        )
        self.assertIn(
            (
                "text",
                127,
                124,
                "▲ 高峰期 · 双倍消耗",
                "#ff5252",
                ("Consolas", 9, "bold"),
            ),
            canvas.calls,
        )
        self.assertIn(
            ("text", 127, 86, "充值: ¥4.25", "#2979ff", ("Consolas", 9)),
            canvas.calls,
        )
        self.assertIn(
            (
                "text",
                127,
                104,
                "今日余额减少（估算）: ¥1.2500",
                "#ffab00",
                ("Consolas", 9),
            ),
            canvas.calls,
        )
        self.assertIn(
            ("text", 127, 148, "● 7s ago", "#4a6d8c", ("Consolas", 8)),
            canvas.calls,
        )
        self.assertEqual(view.hit_test(221, 12), "collapse")
        self.assertEqual(view.hit_test(242, 12), "exit")
        self.assertIsNone(view.hit_test(200, 200))

    def test_non_peak_copy_is_green(self) -> None:
        view, canvas = self.make_view()
        now = datetime(2026, 8, 5, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        view.render(PanelState(display=display()), now=now)
        self.assertIn(
            (
                "text",
                127,
                124,
                "● 非高峰 · 正常计费",
                "#00e676",
                ("Consolas", 9, "bold"),
            ),
            canvas.calls,
        )

    def test_loading_panel_keeps_both_top_right_buttons(self) -> None:
        view, canvas = self.make_view()
        now = datetime(2026, 8, 5, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        view.render(PanelState(status_text="CONNECTING..."), now=now)
        self.assertIn(
            ("text", 127, 92, "DEEPSEEK MONITOR", "#00e5ff", ("Consolas", 12, "bold")),
            canvas.calls,
        )
        self.assertIn(
            ("text", 127, 117, "CONNECTING...", "#4a6d8c", ("Consolas", 9)),
            canvas.calls,
        )
        self.assertIn(("text", 221, 12, "◉", "#7c4dff", ("Consolas", 8)), canvas.calls)
        self.assertIn(("text", 242, 12, "✕", "#ff1744", ("Consolas", 8)), canvas.calls)

    def test_error_panel_uses_generic_copy_and_retains_last_balance(self) -> None:
        view, canvas = self.make_view()
        now = datetime(2026, 8, 5, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        raw_error = "C:/private/path raw-credential-value"
        view.render(
            PanelState(
                display=display(),
                error_text=raw_error,
                refreshed_at=now - timedelta(seconds=9),
            ),
            now=now,
        )
        rendered = repr(canvas.calls)
        self.assertIn("余额同步失败，请重试。", rendered)
        self.assertIn("上次余额: ¥8.50", rendered)
        self.assertIn("同步失败 · 9s ago", rendered)
        self.assertNotIn(raw_error, rendered)
        self.assertNotIn("private", rendered)

    def test_unknown_currency_keeps_code_prefix_and_never_sums(self) -> None:
        view, canvas = self.make_view()
        now = datetime(2026, 8, 5, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        view.render(PanelState(display=display("EUR", "4", "2")), now=now)
        rendered = repr(canvas.calls)
        self.assertIn("EUR 4.00", rendered)
        self.assertIn("充值: EUR 2.00", rendered)
        self.assertNotIn("12.50", rendered)

    def test_low_and_unavailable_statuses_are_red(self) -> None:
        now = datetime(2026, 8, 5, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        low_view, low_canvas = self.make_view()
        low_view.render(PanelState(display=display(total="4")), now=now)
        self.assertIn(
            ("text", 127, 24, "DEEPSEEK  LOW", "#ff1744", ("Consolas", 10, "bold")),
            low_canvas.calls,
        )

        offline_view, offline_canvas = self.make_view()
        offline_view.render(PanelState(display=display(available=False)), now=now)
        self.assertIn(
            (
                "text",
                127,
                24,
                "DEEPSEEK  OFFLINE",
                "#ff1744",
                ("Consolas", 10, "bold"),
            ),
            offline_canvas.calls,
        )


if __name__ == "__main__":
    unittest.main()
