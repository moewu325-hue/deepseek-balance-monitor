from datetime import datetime, timedelta
from decimal import Decimal
import math
import random
import re
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
            (
                "oval",
                *coordinates,
                options.get("fill"),
                options.get("outline"),
                options.get("tags"),
            )
        )
        return len(self.calls)

    def create_arc(self, *coordinates: int, **options: object) -> int:
        self.calls.append(("arc", *coordinates, options.get("outline")))
        return len(self.calls)

    def create_image(self, x: int, y: int, **options: object) -> int:
        self.calls.append(("image", x, y, options.get("image")))
        return len(self.calls)

    def config(self, **options: object) -> None:
        self.calls.append(("config", tuple(sorted(options.items()))))

    def bind(self, event: str, _callback: object) -> None:
        self.calls.append(("bind", event))


class FakeRoot:
    def __init__(self) -> None:
        self.last_size = (255, 215)
        self.last_position = (300, 200)
        self.after_delays: list[int] = []
        self.after_callbacks: dict[str, object] = {}
        self.cancelled: list[str] = []
        self.attribute_calls: list[tuple[object, ...]] = []
        self.configure_calls: list[dict[str, object]] = []
        self._after_index = 0

    def geometry(self, geometry: str) -> None:
        match = re.fullmatch(r"(?:(\d+)x(\d+))?\+(\d+)\+(\d+)", geometry)
        if match is None:
            raise AssertionError(f"unexpected geometry: {geometry}")
        width, height, x, y = match.groups()
        if width is not None and height is not None:
            self.last_size = int(width), int(height)
        self.last_position = int(x), int(y)

    def winfo_x(self) -> int:
        return self.last_position[0]

    def winfo_y(self) -> int:
        return self.last_position[1]

    def winfo_screenwidth(self) -> int:
        return 1920

    def winfo_screenheight(self) -> int:
        return 1080

    def after(self, delay: int, callback: object) -> str:
        self._after_index += 1
        timer = f"after-{self._after_index}"
        self.after_delays.append(delay)
        self.after_callbacks[timer] = callback
        return timer

    def after_cancel(self, timer: str) -> None:
        self.cancelled.append(timer)
        self.after_callbacks.pop(timer, None)

    def attributes(self, *values: object) -> None:
        self.attribute_calls.append(values)

    def configure(self, **options: object) -> None:
        self.configure_calls.append(options)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class Event:
    def __init__(self, x: int, y: int, *, x_root: int | None = None, y_root: int | None = None) -> None:
        self.x = x
        self.y = y
        self.x_root = x if x_root is None else x_root
        self.y_root = y if y_root is None else y_root


class CallbackLog:
    def __init__(self) -> None:
        self.collapsed: list[bool] = []
        self.positions: list[tuple[int, int, bool]] = []
        self.exit_calls = 0
        self.refresh_calls = 0
        self.console_calls = 0
        self.autostart: list[bool] = []

    def build(self) -> CanvasCallbacks:
        return CanvasCallbacks(
            refresh=self._refresh,
            open_console=self._console,
            request_exit=self._exit,
            set_collapsed=self.collapsed.append,
            save_position=lambda x, y, collapsed: self.positions.append((x, y, collapsed)),
            set_autostart=self.autostart.append,
        )

    def _exit(self) -> None:
        self.exit_calls += 1

    def _refresh(self) -> None:
        self.refresh_calls += 1

    def _console(self) -> None:
        self.console_calls += 1


class FakeVariable:
    def __init__(self, value: bool = False) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = bool(value)


class FakeMenu:
    def __init__(self, _root: object, **_options: object) -> None:
        self.entries: list[dict[str, object]] = []
        self.popup: tuple[int, int] | None = None

    def add_command(self, **options: object) -> None:
        self.entries.append({"kind": "command", **options})

    def add_checkbutton(self, **options: object) -> None:
        self.entries.append({"kind": "checkbutton", **options})

    def add_separator(self) -> None:
        self.entries.append({"kind": "separator"})

    def tk_popup(self, x: int, y: int) -> None:
        self.popup = x, y

    def grab_release(self) -> None:
        pass


class FakeTk:
    def __init__(self) -> None:
        self.menus: list[FakeMenu] = []

    def BooleanVar(self, master: object = None, value: bool = False) -> FakeVariable:
        return FakeVariable(value)

    def Menu(self, root: object, **options: object) -> FakeMenu:
        menu = FakeMenu(root, **options)
        self.menus.append(menu)
        return menu


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


class WhaleAnimationTests(unittest.TestCase):
    def make_view(self) -> tuple[CanvasMonitorView, FakeRoot, RecordingCanvas, FakeClock, CallbackLog]:
        root = FakeRoot()
        canvas = RecordingCanvas()
        clock = FakeClock()
        log = CallbackLog()
        view = CanvasMonitorView(
            root,
            config=AppConfig(window_x=300, window_y=200),
            callbacks=log.build(),
            canvas=canvas,
            clock=clock,
            random_source=random.Random(7),
            now=lambda: datetime(
                2026,
                8,
                5,
                9,
                30,
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ),
        )
        return view, root, canvas, clock, log

    def test_swim_moves_the_whole_window_within_exact_bounds(self) -> None:
        view, root, _canvas, clock, _log = self.make_view()
        view.set_collapsed(True, animate=False)
        clock.value = 0.0
        view._swim_frame()
        self.assertEqual(root.last_size, (100, 100))
        self.assertEqual(root.last_position, view.anchor)

        clock.value = math.pi / (2 * 0.7)
        view._swim_frame()
        self.assertLessEqual(abs(root.last_position[0] - view.anchor[0]), 50)
        self.assertLessEqual(abs(root.last_position[1] - view.anchor[1]), 8)
        self.assertEqual(root.after_delays[-1], 50)

    def test_peak_bubble_has_red_body_three_ripples_five_particles_and_fallback(self) -> None:
        view, _root, canvas, _clock, _log = self.make_view()
        view.set_collapsed(True, animate=False)
        rendered = repr(canvas.calls)
        self.assertIn("#241014", rendered)
        self.assertIn("#ff5252", rendered)
        self.assertIn("DS", rendered)
        oval_calls = [call for call in canvas.calls if call[0] == "oval"]
        self.assertGreaterEqual(len(oval_calls), 10)

    def test_press_pauses_swim_draws_feedback_and_schedules_one_safety_timer(self) -> None:
        view, root, canvas, _clock, _log = self.make_view()
        view.set_collapsed(True, animate=False)
        view._bubble_press(Event(50, 50))
        self.assertTrue(view._dragging)
        self.assertTrue(view._pressed)
        self.assertEqual(root.after_delays.count(3000), 1)
        self.assertIsNone(view._swim_timer)
        self.assertIn("click-ripple", repr(canvas.calls))

    def test_less_than_eight_pixels_is_a_click_and_expands(self) -> None:
        view, root, _canvas, _clock, log = self.make_view()
        view.set_collapsed(True, animate=False)
        view._bubble_press(Event(50, 50))
        view._bubble_release(Event(57, 57))
        self.assertEqual(root.last_size, (255, 215))
        self.assertEqual(log.collapsed[-1], False)
        self.assertFalse(view.collapsed)

    def test_eight_pixels_or_more_is_a_drag_and_persists_anchor(self) -> None:
        view, root, _canvas, _clock, log = self.make_view()
        view.set_collapsed(True, animate=False)
        origin = view.anchor
        view._bubble_press(Event(50, 50))
        view._bubble_drag(Event(58, 50))
        view._bubble_release(Event(58, 50))
        self.assertEqual(view.anchor, (origin[0] + 8, origin[1]))
        self.assertEqual(log.positions[-1], (*view.anchor, True))
        self.assertTrue(view.collapsed)
        self.assertEqual(root.last_size, (100, 100))
        self.assertIsNotNone(view._swim_timer)

    def test_lost_release_recovers_after_exactly_three_seconds(self) -> None:
        view, root, _canvas, _clock, _log = self.make_view()
        view.set_collapsed(True, animate=False)
        view._bubble_press(Event(50, 50))
        timer = view._drag_safety_timer
        self.assertIsNotNone(timer)
        self.assertEqual(root.after_delays.count(3000), 1)
        callback = root.after_callbacks[timer]
        callback()
        self.assertFalse(view._dragging)
        self.assertFalse(view._pressed)
        self.assertIsNotNone(view._swim_timer)

    def test_close_cancels_every_active_timer_and_is_idempotent(self) -> None:
        view, root, _canvas, _clock, _log = self.make_view()
        view.render(PanelState(display=display()))
        view.set_collapsed(True, animate=True)
        view._bubble_press(Event(50, 50))
        self.assertTrue(root.after_callbacks)
        view.close()
        view.close()
        self.assertEqual(root.after_callbacks, {})


class CanvasInteractionTests(unittest.TestCase):
    def make_view(self) -> tuple[CanvasMonitorView, FakeRoot, FakeTk, CallbackLog]:
        root = FakeRoot()
        tk = FakeTk()
        log = CallbackLog()
        view = CanvasMonitorView(
            root,
            config=AppConfig(auto_start=True),
            callbacks=log.build(),
            canvas=RecordingCanvas(),
            tk_module=tk,
        )
        return view, root, tk, log

    def test_top_right_collapse_and_exit_use_the_approved_callbacks(self) -> None:
        view, _root, _tk, log = self.make_view()
        view._normal_press(Event(221, 12))
        view._normal_release(Event(221, 12))
        self.assertEqual(log.collapsed, [True])

        view.set_collapsed(False, animate=False)
        view._normal_press(Event(242, 12))
        view._normal_release(Event(242, 12))
        self.assertEqual(log.exit_calls, 1)

    def test_panel_and_bubble_context_menus_have_exact_commands(self) -> None:
        view, _root, tk, _log = self.make_view()
        view._show_context_menu(Event(1, 1, x_root=400, y_root=300))
        panel_entries = tk.menus[-1].entries
        self.assertEqual(
            [entry.get("label") for entry in panel_entries if "label" in entry],
            ["缩小为气泡", "立即刷新", "DeepSeek 控制台", "开机自启动", "退出"],
        )

        view.set_collapsed(True, animate=False)
        view._show_context_menu(Event(1, 1, x_root=410, y_root=310))
        bubble_entries = tk.menus[-1].entries
        self.assertEqual(
            [entry.get("label") for entry in bubble_entries if "label" in entry],
            ["展开", "立即刷新", "开机自启动", "退出"],
        )

    def test_autostart_menu_is_disabled_while_change_is_pending(self) -> None:
        view, _root, tk, log = self.make_view()
        view.set_autostart_state(True, pending=True)
        view._show_context_menu(Event(1, 1))
        entry = next(
            item for item in tk.menus[-1].entries if item.get("label") == "开机自启动"
        )
        self.assertEqual(entry["state"], "disabled")

        view.set_autostart_state(False, pending=False)
        view._show_context_menu(Event(1, 1))
        entry = next(
            item for item in tk.menus[-1].entries if item.get("label") == "开机自启动"
        )
        variable = entry["variable"]
        variable.set(True)
        entry["command"]()
        self.assertEqual(log.autostart, [True])


if __name__ == "__main__":
    unittest.main()
