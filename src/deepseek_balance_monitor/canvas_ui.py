from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import math
import random
import time
from typing import Any, Callable

from .peak import SHANGHAI, is_peak_hour, peak_label
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

BUBBLE_WINDOW = 100
BUBBLE_RADIUS = 40
SWIM_AMPLITUDE = 50
BOB_AMPLITUDE = 8
FRAME_MS = 50
DRAG_SAFETY_MS = 3000
RIPPLE_MAX_RADIUS = 52
TRANSPARENT_COLOR = "#010101"
WATER_DEEP, WATER_RIM, RIPPLE_COLOR = "#0a1628", "#1a4a6e", "#1a5a8a"

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
        clock: Callable[[], float] = time.monotonic,
        random_source: random.Random | None = None,
        whale_frames: tuple[Any, ...] = (),
    ) -> None:
        self._root = root
        self._config = config
        self._callbacks = callbacks
        self._now = now
        self._clock = clock
        self._state = PanelState()
        self._low_balance_warning = Decimal(config.low_balance_warning)
        self._collapsed = False
        self._closed = False
        self._initial_bubble_position = bool(config.collapsed)
        self._normal_position = (
            40 if config.window_x is None else config.window_x,
            80 if config.window_y is None else config.window_y,
        )
        self._anchor_x = self._normal_position[0]
        self._anchor_y = self._normal_position[1]
        self._swim_started = self._clock()
        self._swim_timer: Any | None = None
        self._click_ripple_timer: Any | None = None
        self._drag_safety_timer: Any | None = None
        self._age_timer: Any | None = None
        self._fade_timer: Any | None = None
        self._click_ripple_started: float | None = None
        self._dragging = False
        self._pressed = False
        self._drag_start: tuple[int, int] | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._whale_frames = tuple(whale_frames[:18])
        source = random_source if random_source is not None else random.Random()
        self._particles = tuple(
            (
                source.uniform(-28, 28),
                source.uniform(-24, 24),
                source.uniform(1.0, 2.5),
                source.uniform(0, math.tau),
            )
            for _ in range(5)
        )
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

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    @property
    def anchor(self) -> tuple[int, int]:
        return self._anchor_x, self._anchor_y

    def render(self, state: PanelState, *, now: datetime | None = None) -> None:
        if not isinstance(state, PanelState):
            raise TypeError("state must be a PanelState")
        self._state = state
        if self._collapsed:
            self._draw_bubble(max(0.0, self._clock() - self._swim_started))
            return
        draw_panel(
            self._canvas,
            state,
            now=self._now() if now is None else now,
            low_balance_warning=self._low_balance_warning,
        )
        self._schedule_age_refresh()

    def set_collapsed(self, collapsed: bool, animate: bool = True) -> None:
        if self._closed:
            return
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        if collapsed:
            self._enter_bubble(animate=animate)
        else:
            self._exit_bubble()
        self._callbacks.set_collapsed(collapsed)

    def stop_animation(self) -> None:
        self._cancel_timer("_swim_timer")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._dragging = False
        self._pressed = False
        self._click_ripple_started = None
        for name in (
            "_swim_timer",
            "_click_ripple_timer",
            "_drag_safety_timer",
            "_age_timer",
            "_fade_timer",
        ):
            self._cancel_timer(name)

    def _enter_bubble(self, *, animate: bool) -> None:
        self._cancel_timer("_age_timer")
        if self._initial_bubble_position:
            self._initial_bubble_position = False
            self._anchor_x, self._anchor_y = self._clamp_bubble(*self._normal_position)
        else:
            panel_x, panel_y = self._root.winfo_x(), self._root.winfo_y()
            self._normal_position = panel_x, panel_y
            self._anchor_x, self._anchor_y = self._clamp_bubble(
                panel_x + FULL_WIDTH // 2 - BUBBLE_WINDOW // 2,
                panel_y + FULL_HEIGHT // 2 - BUBBLE_WINDOW // 2,
            )
        self._collapsed = True
        self._root.geometry(
            f"{BUBBLE_WINDOW}x{BUBBLE_WINDOW}+{self._anchor_x}+{self._anchor_y}"
        )
        self._canvas.config(
            width=BUBBLE_WINDOW,
            height=BUBBLE_WINDOW,
            bg=TRANSPARENT_COLOR,
        )
        try:
            self._root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        except Exception:
            pass
        self._canvas.bind("<ButtonPress-1>", self._bubble_press)
        self._canvas.bind("<B1-Motion>", self._bubble_drag)
        self._canvas.bind("<ButtonRelease-1>", self._bubble_release)
        if animate:
            self._start_fade()
        self._start_swim()

    def _exit_bubble(self) -> None:
        self.stop_animation()
        self._cancel_timer("_click_ripple_timer")
        self._cancel_drag_safety()
        self._cancel_timer("_fade_timer")
        self._dragging = False
        self._pressed = False
        self._click_ripple_started = None
        try:
            self._root.attributes("-transparentcolor", "")
            self._root.attributes("-alpha", self._config.opacity)
        except Exception:
            pass
        target_x = self._anchor_x + BUBBLE_WINDOW // 2 - FULL_WIDTH // 2
        target_y = self._anchor_y + BUBBLE_WINDOW // 2 - FULL_HEIGHT // 2
        target_x, target_y = self._clamp_panel(target_x, target_y)
        self._normal_position = target_x, target_y
        self._collapsed = False
        self._root.geometry(f"{FULL_WIDTH}x{FULL_HEIGHT}+{target_x}+{target_y}")
        self._canvas.config(width=FULL_WIDTH, height=FULL_HEIGHT, bg=BG_DARK)
        try:
            self._root.configure(bg=BG_DARK)
        except Exception:
            pass
        self.render(self._state)

    def _start_swim(self) -> None:
        self.stop_animation()
        if self._closed or not self._collapsed or self._dragging:
            return
        self._swim_started = self._clock()
        self._swim_frame()

    def _swim_frame(self) -> None:
        self._cancel_timer("_swim_timer")
        if self._closed or not self._collapsed or self._dragging:
            return
        elapsed = max(0.0, self._clock() - self._swim_started)
        dx = round(math.sin(elapsed * 0.7) * SWIM_AMPLITUDE)
        dy = round(math.sin(elapsed * 1.1) * BOB_AMPLITUDE)
        x, y = self._clamp_bubble(self._anchor_x + dx, self._anchor_y + dy)
        self._root.geometry(f"{BUBBLE_WINDOW}x{BUBBLE_WINDOW}+{x}+{y}")
        self._draw_bubble(elapsed)
        self._swim_timer = self._root.after(FRAME_MS, self._swim_frame)

    def _draw_bubble(self, elapsed: float) -> None:
        self._canvas.delete("all")
        center_x = center_y = BUBBLE_WINDOW // 2
        peak = is_peak_hour(self._now())
        glow_radius = BUBBLE_RADIUS + 6 + round(math.sin(elapsed * 0.5) * 3)
        self._canvas.create_oval(
            center_x - glow_radius,
            center_y - glow_radius,
            center_x + glow_radius,
            center_y + glow_radius,
            outline=_fade_color(CYBER_1, 0.18),
            width=2,
            tags="glow",
        )
        self._canvas.create_oval(
            center_x - BUBBLE_RADIUS,
            center_y - BUBBLE_RADIUS,
            center_x + BUBBLE_RADIUS,
            center_y + BUBBLE_RADIUS,
            fill=PEAK_FILL if peak else WATER_DEEP,
            outline=WATER_RIM,
            width=2,
            tags="bubble-body",
        )
        for base_radius in (8.0, 24.0, 40.0):
            radius = (base_radius + elapsed * 15.0) % RIPPLE_MAX_RADIUS
            fade = 1.0 - radius / RIPPLE_MAX_RADIUS
            self._canvas.create_oval(
                center_x - radius,
                center_y - radius * 0.7,
                center_x + radius,
                center_y + radius * 0.7,
                outline=_fade_color(RIPPLE_COLOR, fade * 0.6),
                width=1,
                tags="water-ripple",
            )
        for particle_x, particle_y, radius, phase in self._particles:
            y = ((particle_y - elapsed * 8.0 + phase * 3.0 + 35.0) % 70.0) - 35.0
            fade = max(0.15, 1.0 - abs(y) / 38.0)
            color = _fade_color(CYBER_1, fade * 0.7)
            self._canvas.create_oval(
                center_x + particle_x - radius,
                center_y + y - radius,
                center_x + particle_x + radius,
                center_y + y + radius,
                fill=color,
                outline="",
                tags="particle",
            )
        self._draw_whale(center_x, center_y, elapsed)
        self._draw_click_ripple(center_x, center_y)
        arc_radius = BUBBLE_RADIUS - 3
        self._canvas.create_arc(
            center_x - arc_radius,
            center_y - arc_radius,
            center_x + arc_radius,
            center_y + arc_radius,
            start=210,
            extent=120,
            outline=PEAK_WARN if peak else self._status_color(),
            width=2,
            style="arc",
            tags="status-ring",
        )

    def _draw_whale(self, center_x: int, center_y: int, elapsed: float) -> None:
        frames = self._whale_frames
        if frames:
            if self._pressed and len(frames) >= 18:
                frames = frames[9:18]
            elif len(frames) >= 9:
                frames = frames[:9]
            index = round((math.sin(elapsed * 2.0) + 1.0) * (len(frames) - 1) / 2.0)
            self._canvas.create_image(
                center_x,
                center_y - 2,
                image=frames[index],
                anchor="center",
                tags="whale",
            )
            return
        self._canvas.create_text(
            center_x,
            center_y - 2,
            text="DS",
            fill=TEXT_MAIN,
            font=("Consolas", 13 if self._pressed else 15, "bold"),
            anchor="center",
            tags="whale-fallback",
        )

    def _draw_click_ripple(self, center_x: int, center_y: int) -> None:
        started = self._click_ripple_started
        if started is None:
            return
        age = max(0.0, self._clock() - started)
        if age > 0.5:
            return
        radius = 12 + age * 70
        self._canvas.create_oval(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            outline=_fade_color(CYBER_1, max(0.1, 1.0 - age * 2.0)),
            width=2,
            tags="click-ripple",
        )

    def _bubble_press(self, event: Any) -> None:
        if self._closed or not self._collapsed:
            return
        self.stop_animation()
        self._cancel_drag_safety()
        self._dragging = True
        self._pressed = True
        self._drag_start = int(event.x), int(event.y)
        self._drag_origin = self._root.winfo_x(), self._root.winfo_y()
        self._click_ripple_started = self._clock()
        self._draw_bubble(max(0.0, self._clock() - self._swim_started))
        self._schedule_click_ripple()
        self._drag_safety_timer = self._root.after(
            DRAG_SAFETY_MS,
            self._recover_lost_release,
        )

    def _bubble_drag(self, event: Any) -> None:
        if not self._dragging or self._drag_start is None or self._drag_origin is None:
            return
        x = self._drag_origin[0] + int(event.x) - self._drag_start[0]
        y = self._drag_origin[1] + int(event.y) - self._drag_start[1]
        self._anchor_x, self._anchor_y = self._clamp_bubble(x, y)
        self._root.geometry(
            f"{BUBBLE_WINDOW}x{BUBBLE_WINDOW}+{self._anchor_x}+{self._anchor_y}"
        )
        self._draw_bubble(max(0.0, self._clock() - self._swim_started))

    def _bubble_release(self, event: Any) -> None:
        if not self._dragging or self._drag_start is None:
            return
        start_x, start_y = self._drag_start
        dx = abs(int(event.x) - start_x)
        dy = abs(int(event.y) - start_y)
        self._cancel_drag_safety()
        self._dragging = False
        self._pressed = False
        self._drag_start = None
        self._drag_origin = None
        if dx < 8 and dy < 8:
            self.set_collapsed(False, animate=True)
            return
        self._callbacks.save_position(self._anchor_x, self._anchor_y, True)
        self._start_swim()

    def _recover_lost_release(self) -> None:
        self._drag_safety_timer = None
        if not self._dragging:
            return
        self._dragging = False
        self._pressed = False
        self._drag_start = None
        self._drag_origin = None
        if self._collapsed and not self._closed:
            self._start_swim()

    def _schedule_click_ripple(self) -> None:
        self._cancel_timer("_click_ripple_timer")
        self._click_ripple_timer = self._root.after(FRAME_MS, self._click_ripple_frame)

    def _click_ripple_frame(self) -> None:
        self._click_ripple_timer = None
        if self._closed or not self._collapsed or self._click_ripple_started is None:
            return
        age = self._clock() - self._click_ripple_started
        if age > 0.5:
            self._click_ripple_started = None
            return
        self._draw_bubble(max(0.0, self._clock() - self._swim_started))
        self._schedule_click_ripple()

    def _schedule_age_refresh(self) -> None:
        self._cancel_timer("_age_timer")
        if self._closed or self._collapsed:
            return
        self._age_timer = self._root.after(1000, self._refresh_age)

    def _refresh_age(self) -> None:
        self._age_timer = None
        if not self._closed and not self._collapsed:
            self.render(self._state)

    def _start_fade(self) -> None:
        self._cancel_timer("_fade_timer")
        self._fade_frame(0)

    def _fade_frame(self, step: int) -> None:
        self._fade_timer = None
        if self._closed or not self._collapsed:
            return
        alpha = min(1.0, 0.15 + self._config.opacity * (step / 6.0))
        try:
            self._root.attributes("-alpha", alpha)
        except Exception:
            return
        if step < 6:
            self._fade_timer = self._root.after(30, lambda: self._fade_frame(step + 1))

    def _cancel_drag_safety(self) -> None:
        self._cancel_timer("_drag_safety_timer")

    def _cancel_timer(self, name: str) -> None:
        timer = getattr(self, name)
        if timer is None:
            return
        setattr(self, name, None)
        try:
            self._root.after_cancel(timer)
        except Exception:
            pass

    def _clamp_bubble(self, x: int, y: int) -> tuple[int, int]:
        margin = 20
        maximum_x = max(margin, self._root.winfo_screenwidth() - BUBBLE_WINDOW - margin)
        maximum_y = max(margin, self._root.winfo_screenheight() - BUBBLE_WINDOW - margin)
        return (
            max(margin, min(int(x), maximum_x)),
            max(margin, min(int(y), maximum_y)),
        )

    def _clamp_panel(self, x: int, y: int) -> tuple[int, int]:
        maximum_x = max(0, self._root.winfo_screenwidth() - FULL_WIDTH)
        maximum_y = max(0, self._root.winfo_screenheight() - FULL_HEIGHT)
        return max(0, min(int(x), maximum_x)), max(0, min(int(y), maximum_y))

    def _status_color(self) -> str:
        display = self._state.display
        if display is None:
            return TEXT_DIM
        if not display.available or display.total_balance <= self._low_balance_warning:
            return STAT_RED
        return STAT_GREEN

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


def _fade_color(color: str, factor: float) -> str:
    bounded = max(0.0, min(1.0, float(factor)))
    foreground = color.lstrip("#")
    background = WATER_DEEP.lstrip("#")
    red, green, blue = (int(foreground[index : index + 2], 16) for index in (0, 2, 4))
    base_red, base_green, base_blue = (
        int(background[index : index + 2], 16) for index in (0, 2, 4)
    )
    return (
        f"#{round(base_red + (red - base_red) * bounded):02x}"
        f"{round(base_green + (green - base_green) * bounded):02x}"
        f"{round(base_blue + (blue - base_blue) * bounded):02x}"
    )
