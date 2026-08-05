from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from .peak import SHANGHAI, peak_label
from .presentation import (
    ESTIMATED_DECREASE_LABEL,
    BalanceDisplay,
    format_currency_amount,
)
from .storage import AppConfig


FULL_WIDTH, FULL_HEIGHT = 255, 215
BG_DARK, BG_PANEL = "#060b18", "#0c1430"
CYBER_1, CYBER_2, CYBER_3 = "#00e5ff", "#2979ff", "#7c4dff"
TEXT_MAIN, TEXT_DIM = "#e0f7ff", "#4a6d8c"
STAT_GREEN, STAT_YELLOW, STAT_RED = "#00e676", "#ffab00", "#ff1744"
PEAK_WARN, PEAK_FILL = "#ff5252", "#241014"

_CENTER_X = FULL_WIDTH // 2
_COLLAPSE_CENTER = (221, 12)
_EXIT_CENTER = (242, 12)


@dataclass(frozen=True)
class PanelState:
    display: BalanceDisplay | None = None
    estimated_decrease: Decimal | None = None
    status_text: str = "CONNECTING"
    error_text: str = ""
    refreshed_at: datetime | None = None


@dataclass(frozen=True)
class CanvasCallbacks:
    refresh: Callable[[], None]
    open_console: Callable[[], None]
    request_exit: Callable[[], None]
    set_collapsed: Callable[[bool], None]
    save_position: Callable[[int, int, bool], None]
    set_autostart: Callable[[bool], None]


class CanvasMonitorView:
    normal_size = (FULL_WIDTH, FULL_HEIGHT)

    def __init__(
        self,
        root: Any,
        *,
        config: AppConfig,
        callbacks: CanvasCallbacks,
        tk_module: Any | None = None,
        canvas: Any | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(SHANGHAI),
    ) -> None:
        self._root = root
        self._config = config
        self._callbacks = callbacks
        self._now = now
        self._state = PanelState()
        self._low_balance_warning = Decimal(config.low_balance_warning)
        if canvas is None:
            if tk_module is None:
                import tkinter as tk_module

            canvas = tk_module.Canvas(
                root,
                width=FULL_WIDTH,
                height=FULL_HEIGHT,
                bg=BG_DARK,
                highlightthickness=0,
                cursor="hand2",
            )
            canvas.pack()
        self._canvas = canvas

    @property
    def state(self) -> PanelState:
        return self._state

    def render(self, state: PanelState, *, now: datetime | None = None) -> None:
        if not isinstance(state, PanelState):
            raise TypeError("state must be a PanelState")
        self._state = state
        draw_panel(
            self._canvas,
            state,
            now=self._now() if now is None else now,
            low_balance_warning=self._low_balance_warning,
        )

    @staticmethod
    def hit_test(x: int, y: int) -> str | None:
        if 212 <= x <= 230 and 3 <= y <= 21:
            return "collapse"
        if 233 <= x <= 252 and 3 <= y <= 21:
            return "exit"
        return None


def draw_panel(
    canvas: Any,
    state: PanelState,
    *,
    now: datetime,
    low_balance_warning: Decimal = Decimal("10"),
) -> None:
    canvas.delete("all")
    if state.display is None and not state.error_text:
        _draw_loading_panel(canvas, state)
    elif state.error_text:
        _draw_error_panel(canvas, state, now=now)
    else:
        label, peak = peak_label(now)
        _draw_balance_panel(
            canvas,
            state,
            label=label,
            peak=peak,
            now=now,
            low_balance_warning=low_balance_warning,
        )
    _draw_top_right_buttons(canvas)


def _draw_loading_panel(canvas: Any, state: PanelState) -> None:
    canvas.create_text(
        _CENTER_X,
        92,
        text="DEEPSEEK MONITOR",
        fill=CYBER_1,
        font=("Consolas", 12, "bold"),
        anchor="center",
    )
    canvas.create_text(
        _CENTER_X,
        117,
        text=state.status_text,
        fill=TEXT_DIM,
        font=("Consolas", 9),
        anchor="center",
    )


def _draw_balance_panel(
    canvas: Any,
    state: PanelState,
    *,
    label: str,
    peak: bool,
    now: datetime,
    low_balance_warning: Decimal,
) -> None:
    display = state.display
    if display is None:
        return
    if not display.available:
        status, status_color = "OFFLINE", STAT_RED
    elif display.total_balance <= low_balance_warning:
        status, status_color = "LOW", STAT_RED
    else:
        status, status_color = "ONLINE", STAT_GREEN

    canvas.create_text(
        _CENTER_X,
        24,
        text=f"DEEPSEEK  {status}",
        fill=status_color,
        font=("Consolas", 10, "bold"),
        anchor="center",
    )
    canvas.create_text(
        _CENTER_X,
        58,
        text=_main_balance_text(display),
        fill=CYBER_1,
        font=("Consolas", 22, "bold"),
        anchor="center",
    )
    canvas.create_text(
        _CENTER_X,
        86,
        text=f"充值: {display.topped_up_text}",
        fill=CYBER_2,
        font=("Consolas", 9),
        anchor="center",
    )
    estimated = _estimated_text(display.currency, state.estimated_decrease)
    canvas.create_text(
        _CENTER_X,
        104,
        text=estimated,
        fill=STAT_YELLOW if _positive(state.estimated_decrease) else TEXT_DIM,
        font=("Consolas", 9),
        anchor="center",
    )
    canvas.create_text(
        _CENTER_X,
        124,
        text=label,
        fill=PEAK_WARN if peak else STAT_GREEN,
        font=("Consolas", 9, "bold"),
        anchor="center",
    )
    canvas.create_text(
        _CENTER_X,
        148,
        text=f"● {_age_seconds(state.refreshed_at, now)}s ago",
        fill=TEXT_DIM,
        font=("Consolas", 8),
        anchor="center",
    )


def _draw_error_panel(canvas: Any, state: PanelState, *, now: datetime) -> None:
    canvas.create_text(
        _CENTER_X,
        40,
        text="ERROR",
        fill=STAT_RED,
        font=("Consolas", 14, "bold"),
        anchor="center",
    )
    canvas.create_text(
        _CENTER_X,
        70,
        text="余额同步失败，请重试。",
        fill=STAT_RED,
        font=("Consolas", 8),
        anchor="center",
    )
    if state.display is not None:
        canvas.create_text(
            _CENTER_X,
            100,
            text=f"上次余额: {state.display.balance_text}",
            fill=TEXT_DIM,
            font=("Consolas", 9),
            anchor="center",
        )
    canvas.create_text(
        _CENTER_X,
        130,
        text=f"同步失败 · {_age_seconds(state.refreshed_at, now)}s ago",
        fill=TEXT_DIM,
        font=("Consolas", 8),
        anchor="center",
    )


def _draw_top_right_buttons(canvas: Any) -> None:
    for (x, y), text, color, tag in (
        (_COLLAPSE_CENTER, "◉", CYBER_3, "collapse"),
        (_EXIT_CENTER, "✕", STAT_RED, "exit"),
    ):
        canvas.create_oval(
            x - 7,
            y - 7,
            x + 7,
            y + 7,
            outline=color,
            width=1,
            fill=BG_PANEL,
            tags=tag,
        )
        canvas.create_text(
            x,
            y,
            text=text,
            fill=color,
            font=("Consolas", 8),
            anchor="center",
            tags=tag,
        )


def _main_balance_text(display: BalanceDisplay) -> str:
    text = display.balance_text
    if text.startswith(("¥", "$")):
        return f"{text[0]} {text[1:]}"
    return text


def _estimated_text(currency: str, amount: Decimal | None) -> str:
    if amount is None or not isinstance(amount, Decimal) or not amount.is_finite():
        rendered = "--"
    else:
        precise = f"{amount:.4f}"
        two_decimal = format_currency_amount(currency, amount)
        suffix = f"{amount:.2f}"
        rendered = f"{two_decimal[:-len(suffix)]}{precise}"
    return f"{ESTIMATED_DECREASE_LABEL}: {rendered}"


def _positive(value: Decimal | None) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _age_seconds(refreshed_at: datetime | None, now: datetime) -> int:
    if refreshed_at is None:
        return 0
    try:
        return max(0, int((now - refreshed_at).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return 0
