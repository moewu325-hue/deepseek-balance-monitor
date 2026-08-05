# Local UI Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce the legacy 255×215 balance panel and animated 100×100 whale bubble on top of the secure credential, balance, currency, storage, and shutdown architecture.

**Architecture:** Keep `BalanceController`, `CredentialResolver`, and `UsageRecorder` as the trusted backend. Add isolated peak-schedule, autostart, settings-worker, and Canvas-view modules; make `app.py` compose them and translate sanitized controller events into immutable view state. All network and filesystem work stays off the Tk thread, while all Tk/Canvas calls stay on it.

**Tech Stack:** Python 3.10+, tkinter/Canvas, Pillow 12.3.0, `zoneinfo`, `threading`, `queue`, `unittest`, Windows Script Host startup entry.

## Global Constraints

- Normal credential discovery remains exactly `DEEPSEEK_API_KEY` followed by Windows Credential Manager target `DeepSeekBalanceMonitor.ApiKey`.
- Manual keys remain masked and memory-only by default; no key may enter config, history, UI diagnostics, startup files, or logs.
- The balance endpoint, TLS verification, redirect policy, timeout contract, and strict response schema remain unchanged.
- Display exactly one selected currency; never sum different currencies.
- Peak time is based only on `Asia/Shanghai`: `[09:00, 12:00)` and `[14:00, 18:00)` every day.
- Peak status is informational; do not multiply balance, topped-up balance, or estimated daily decrease.
- Full panel size is 255×215; bubble window is 100×100 with an 80px body and 44px whale.
- All exit paths ask `确定要退出 DeepSeek 余额监控吗？` and only a confirmed exit starts shutdown.
- Autostart defaults to false and is changed only by explicit user action.
- No production network, credential write, config write, usage write, or startup write may run on the Tk thread.
- The old whale icon is user-approved for redistribution despite unverified provenance; NOTICE must state that accurately.
- Existing single-instance mutex and full worker quiescence before mutex release remain mandatory.

---

## File Map

**Create**

- `src/deepseek_balance_monitor/peak.py` — Beijing-time peak calculation.
- `src/deepseek_balance_monitor/startup.py` — dedicated Windows autostart entry.
- `src/deepseek_balance_monitor/settings.py` — coalesced background config/autostart worker.
- `src/deepseek_balance_monitor/presentation.py` — immutable balance and Canvas view models plus currency formatting.
- `src/deepseek_balance_monitor/canvas_ui.py` — Canvas rendering, menus, hit testing, animation, click and drag behavior.
- `tests/test_peak.py` — boundary and foreign-timezone tests.
- `tests/test_startup.py` — exact-entry, idempotence, encoding, and secret-safety tests.
- `tests/test_settings.py` — background-thread, coalescing, rollback, and close-drain tests.
- `tests/test_canvas_ui.py` — recording-Canvas render and interaction tests.

**Modify**

- `src/deepseek_balance_monitor/app.py` — replace widget drawing with Canvas composition and integrate settings/startup/confirmation.
- `tests/test_ui_logic.py` — retain orchestration, credential recovery, usage, and shutdown coverage against the new view boundary.
- `assets/deepseek_icon.ico` — replace current generated gauge with the approved legacy whale asset.
- `NOTICE` — remove the generated-art claim and record unverified whale provenance.
- `README.md`, `README_en.md`, `SECURITY.md` — document UI, Beijing peak schedule, explicit autostart, and asset risk.
- `tests/test_repository_hygiene.py` — update icon expectations and replace the obsolete “no peak feature” gate.

## Task 1: Beijing Peak Schedule

**Files:**
- Create: `src/deepseek_balance_monitor/peak.py`
- Create: `tests/test_peak.py`

**Interfaces:**
- Consumes: aware `datetime` values or `None`.
- Produces: `SHANGHAI: ZoneInfo`, `to_shanghai(now: datetime | None) -> datetime`, `is_peak_hour(now: datetime | None = None) -> bool`, `peak_label(now: datetime | None = None) -> tuple[str, bool]`.

- [ ] **Step 1: Write failing boundary and timezone tests**

```python
from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from deepseek_balance_monitor.peak import is_peak_hour, peak_label, to_shanghai


class PeakScheduleTests(unittest.TestCase):
    def test_exact_beijing_boundaries(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        cases = {
            datetime(2026, 8, 5, 8, 59, 59, tzinfo=tz): False,
            datetime(2026, 8, 5, 9, 0, 0, tzinfo=tz): True,
            datetime(2026, 8, 5, 11, 59, 59, tzinfo=tz): True,
            datetime(2026, 8, 5, 12, 0, 0, tzinfo=tz): False,
            datetime(2026, 8, 5, 13, 59, 59, tzinfo=tz): False,
            datetime(2026, 8, 5, 14, 0, 0, tzinfo=tz): True,
            datetime(2026, 8, 5, 17, 59, 59, tzinfo=tz): True,
            datetime(2026, 8, 5, 18, 0, 0, tzinfo=tz): False,
        }
        for moment, expected in cases.items():
            with self.subTest(moment=moment):
                self.assertIs(is_peak_hour(moment), expected)

    def test_foreign_timezone_is_converted_to_beijing(self) -> None:
        self.assertTrue(is_peak_hour(datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)))
        self.assertEqual(to_shanghai(datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)).hour, 9)

    def test_naive_injected_time_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            is_peak_hour(datetime(2026, 8, 5, 9, 0))

    def test_labels_are_exact(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        self.assertEqual(peak_label(datetime(2026, 8, 5, 9, 0, tzinfo=tz)), ("▲ 高峰期 · 双倍消耗", True))
        self.assertEqual(peak_label(datetime(2026, 8, 5, 12, 0, tzinfo=tz)), ("● 非高峰 · 正常计费", False))
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_peak -v`  
Expected: import failure for `deepseek_balance_monitor.peak`.

- [ ] **Step 3: Implement the pure schedule**

```python
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
    return ("▲ 高峰期 · 双倍消耗", True) if peak else ("● 非高峰 · 正常计费", False)
```

- [ ] **Step 4: Run GREEN and compile**

Run: `python -m unittest tests.test_peak -v && python -m compileall -q src/deepseek_balance_monitor/peak.py`  
Expected: 4 tests pass, exit 0.

- [ ] **Step 5: Commit**

```powershell
git add src/deepseek_balance_monitor/peak.py tests/test_peak.py
git commit -m "feat: add Beijing peak schedule"
```

## Task 2: Explicit Windows Autostart Entry

**Files:**
- Create: `src/deepseek_balance_monitor/startup.py`
- Create: `tests/test_startup.py`

**Interfaces:**
- Consumes: environment mapping and the current Python executable path.
- Produces: `StartupManager.entry_path`, `StartupManager.is_enabled() -> bool`, and `StartupManager.set_enabled(enabled: bool) -> None`.
- Side effect: only `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DeepSeekBalanceMonitor.vbs`.

- [ ] **Step 1: Write failing tests using a temporary APPDATA**

```python
from pathlib import Path
import tempfile
import unittest

from deepseek_balance_monitor.startup import StartupManager


class StartupManagerTests(unittest.TestCase):
    def test_default_is_disabled_and_enable_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = StartupManager(
                {"APPDATA": temporary},
                Path(r"C:\Python\python.exe"),
                exists=lambda path: path.name.lower() == "pythonw.exe",
            )
            self.assertFalse(manager.is_enabled())
            manager.set_enabled(True)
            first = manager.entry_path.read_bytes()
            manager.set_enabled(True)
            self.assertEqual(manager.entry_path.read_bytes(), first)
            self.assertTrue(manager.is_enabled())
            decoded = first.decode("utf-16")
            self.assertIn(r'C:\Python\pythonw.exe', decoded)
            self.assertIn("-m deepseek_balance_monitor", decoded)
            self.assertNotRegex(decoded, r"sk-[A-Za-z0-9_-]{16,}")

    def test_disable_removes_only_the_owned_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = StartupManager({"APPDATA": temporary}, Path(r"C:\Python\python.exe"))
            manager.entry_path.parent.mkdir(parents=True)
            other = manager.entry_path.parent / "OtherApp.vbs"
            other.write_text("safe", encoding="utf-8")
            manager.set_enabled(True)
            manager.set_enabled(False)
            self.assertFalse(manager.entry_path.exists())
            self.assertEqual(other.read_text(encoding="utf-8"), "safe")

    def test_missing_appdata_fails_before_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "APPDATA"):
            StartupManager({}, Path(r"C:\Python\python.exe"))
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_startup -v`  
Expected: import failure for `deepseek_balance_monitor.startup`.

- [ ] **Step 3: Implement exact-path atomic VBS management**

```python
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Callable, Mapping


ENTRY_NAME = "DeepSeekBalanceMonitor.vbs"


class StartupManager:
    def __init__(
        self,
        env: Mapping[str, str],
        executable: Path,
        *,
        exists: Callable[[Path], bool] = Path.exists,
    ) -> None:
        appdata = env.get("APPDATA")
        if not isinstance(appdata, str) or not appdata.strip():
            raise ValueError("APPDATA is required")
        self.entry_path = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / ENTRY_NAME
        executable = Path(executable)
        pythonw = executable.with_name("pythonw.exe")
        self._pythonw = pythonw if exists(pythonw) else executable

    def _content(self) -> str:
        executable = str(self._pythonw).replace('"', '""')
        command = f'"{executable}" -m deepseek_balance_monitor'
        escaped = command.replace('"', '""')
        return f'Set shell = CreateObject("WScript.Shell")\r\nshell.Run "{escaped}", 0, False\r\n'

    def is_enabled(self) -> bool:
        try:
            return self.entry_path.read_text(encoding="utf-16") == self._content()
        except (FileNotFoundError, OSError, UnicodeError):
            return False

    def set_enabled(self, enabled: bool) -> None:
        if not enabled:
            try:
                self.entry_path.unlink()
            except FileNotFoundError:
                pass
            return
        self.entry_path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=f".{ENTRY_NAME}.", suffix=".tmp", dir=self.entry_path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-16", newline="") as stream:
                stream.write(self._content())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.entry_path)
            if not self.is_enabled():
                raise OSError("Autostart read-back failed")
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
```

- [ ] **Step 4: Add fault-injection tests before final GREEN**

Add tests that patch `os.replace` to raise and assert an existing entry remains byte-for-byte unchanged, and that invalid UTF-16 content makes `is_enabled()` false without exposing content in the exception.

Run: `python -m unittest tests.test_startup -v`  
Expected: all startup tests pass, exit 0.

- [ ] **Step 5: Commit**

```powershell
git add src/deepseek_balance_monitor/startup.py tests/test_startup.py
git commit -m "feat: add explicit Windows autostart"
```

## Task 3: Background Settings and Autostart Worker

**Files:**
- Create: `src/deepseek_balance_monitor/settings.py`
- Create: `tests/test_settings.py`
- Modify: `src/deepseek_balance_monitor/storage.py`

**Interfaces:**
- Consumes: `AppConfig`, `save_config_atomic`, and `StartupManager`.
- Produces: `SettingsEvent(config: AppConfig, startup_changed: bool = False, error: str = "")`; `SettingsWorker.start()`, `request_save(config)`, `request_autostart(enabled, config)`, `close(timeout)` and `updates`.

- [ ] **Step 1: Write failing thread-boundary and rollback tests**

```python
from dataclasses import replace
from pathlib import Path
from queue import Empty
import threading
import unittest

from deepseek_balance_monitor.settings import SettingsWorker
from deepseek_balance_monitor.storage import AppConfig


class SettingsWorkerTests(unittest.TestCase):
    def test_config_save_runs_off_caller_thread_and_latest_pending_wins(self) -> None:
        caller = threading.get_ident()
        saved: list[tuple[int, AppConfig]] = []
        worker = SettingsWorker(Path("config.json"), save_config=lambda path, config: saved.append((threading.get_ident(), config)), startup=FakeStartup())
        worker.start()
        worker.request_save(AppConfig(window_x=1))
        worker.request_save(AppConfig(window_x=2))
        worker.close(timeout=None)
        self.assertTrue(saved)
        self.assertNotEqual(saved[-1][0], caller)
        self.assertEqual(saved[-1][1].window_x, 2)

    def test_failed_autostart_reverts_to_actual_state_and_reports_generic_error(self) -> None:
        startup = FakeStartup(enabled=False, failure=OSError("C:/private/path"))
        worker = SettingsWorker(Path("config.json"), save_config=lambda path, config: None, startup=startup)
        worker.start()
        worker.request_autostart(True, AppConfig(auto_start=False))
        event = worker.updates.get(timeout=2)
        worker.close(timeout=None)
        self.assertFalse(event.config.auto_start)
        self.assertEqual(event.error, "开机自启动设置失败。")
        self.assertNotIn("private", repr(event))
```

Define `FakeStartup` in the test with `is_enabled()` and `set_enabled()`; use an event in the fake saver to prove accepted work drains before `close(None)` returns.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_settings -v`  
Expected: import failure for `deepseek_balance_monitor.settings`.

- [ ] **Step 3: Implement a condition-based coalescing worker**

```python
@dataclass(frozen=True)
class SettingsEvent:
    config: AppConfig
    startup_changed: bool = False
    error: str = ""


class SettingsWorker:
    def request_save(self, config: AppConfig) -> None:
        with self._condition:
            self._require_running()
            self._pending_config = config
            self._condition.notify()

    def request_autostart(self, enabled: bool, config: AppConfig) -> None:
        with self._condition:
            self._require_running()
            if self._pending_startup is not None:
                raise RuntimeError("Autostart change already pending")
            self._pending_startup = (bool(enabled), config)
            self._condition.notify()

    def _apply_startup(self, enabled: bool, config: AppConfig) -> None:
        try:
            self._startup.set_enabled(enabled)
            actual = self._startup.is_enabled()
            updated = replace(config, auto_start=actual)
            self._save_config(self._path, updated)
            self.updates.put(SettingsEvent(updated, startup_changed=True))
        except Exception:
            actual = self._startup.is_enabled()
            updated = replace(config, auto_start=actual)
            self.updates.put(SettingsEvent(updated, startup_changed=True, error="开机自启动设置失败。"))
```

The worker loop must atomically take one startup command plus the latest pending config, prioritize startup, never drop a startup command, save only immutable `AppConfig`, and exit only after close was requested and both pending slots are empty. Add `with_config(config: AppConfig, **changes) -> AppConfig` to `storage.py` as a thin tested wrapper around `dataclasses.replace` only if it removes repeated replacement code; otherwise use `replace` directly.

- [ ] **Step 4: Run GREEN and storage regression**

Run: `python -m unittest tests.test_settings tests.test_storage -v`  
Expected: all tests pass, no Tk imports or filesystem calls on the caller thread.

- [ ] **Step 5: Commit**

```powershell
git add src/deepseek_balance_monitor/settings.py src/deepseek_balance_monitor/storage.py tests/test_settings.py tests/test_storage.py
git commit -m "feat: persist UI settings off thread"
```

## Task 4: Presentation Models and Full Canvas Panel

**Files:**
- Create: `src/deepseek_balance_monitor/presentation.py`
- Create: `src/deepseek_balance_monitor/canvas_ui.py`
- Create: `tests/test_canvas_ui.py`
- Modify: `src/deepseek_balance_monitor/app.py`
- Modify: `tests/test_ui_logic.py`

**Interfaces:**
- Produces `BalanceDisplay(currency, total_balance, topped_up_balance, balance_text, topped_up_text, available)` and `PanelState(display=None, estimated_decrease=None, status_text="CONNECTING", error_text="", refreshed_at=None)`.
- Produces `CanvasCallbacks(refresh, open_console, request_exit, set_collapsed, save_position, set_autostart)` and `CanvasMonitorView`.
- `app.py` re-exports `BalanceDisplay`, `build_balance_display`, and `format_estimated_decrease` so existing consumers remain compatible.

- [ ] **Step 1: Extend balance presentation with a failing no-sum test**

```python
display = build_balance_display(
    BalanceSnapshot(True, (info("USD", "3.25"), info("CNY", "8.50", topped="4.25"))),
    "CNY",
)
self.assertEqual(display.balance_text, "¥8.50")
self.assertEqual(display.topped_up_text, "¥4.25")
self.assertEqual(display.total_balance, Decimal("8.50"))
self.assertNotIn("11.75", repr(display))
```

Run: `python -m unittest tests.test_ui_logic.BalanceDisplayTests -v`  
Expected: FAIL because `topped_up_text` is absent.

- [ ] **Step 2: Move presentation code without changing semantics**

```python
@dataclass(frozen=True)
class BalanceDisplay:
    currency: str
    total_balance: Decimal
    topped_up_balance: Decimal
    balance_text: str
    topped_up_text: str
    available: bool


def build_balance_display(snapshot: BalanceSnapshot, preferred: str) -> BalanceDisplay:
    selected = select_currency(snapshot, preferred)
    currency = selected.currency.upper()
    return BalanceDisplay(
        currency=currency,
        total_balance=selected.total_balance,
        topped_up_balance=selected.topped_up_balance,
        balance_text=format_currency_amount(currency, selected.total_balance),
        topped_up_text=format_currency_amount(currency, selected.topped_up_balance),
        available=snapshot.is_available,
    )
```

Import these names into `app.py` at module scope to preserve existing imports.

- [ ] **Step 3: Write recording-Canvas tests for exact panel output**

Use a `RecordingCanvas` that stores `delete`, `create_text`, `create_oval`, `create_arc`, and `create_image` calls as immutable tuples. Assert:

```python
self.assertEqual(view.normal_size, (255, 215))
self.assertIn(("text", 127, 58, "¥ 8.50", "#00e5ff", ("Consolas", 22, "bold")), canvas.calls)
self.assertIn(("text", 127, 124, "▲ 高峰期 · 双倍消耗", "#ff5252", ("Consolas", 9, "bold")), canvas.calls)
self.assertEqual(view.hit_test(221, 12), "collapse")
self.assertEqual(view.hit_test(242, 12), "exit")
self.assertIsNone(view.hit_test(200, 200))
```

Also assert non-peak green copy, loading copy, error copy with last balance, unknown currency prefix, and absence of raw error/key strings.

- [ ] **Step 4: Run RED**

Run: `python -m unittest tests.test_canvas_ui -v`  
Expected: import failure for `CanvasMonitorView`.

- [ ] **Step 5: Implement panel constants, immutable state, and rendering**

```python
FULL_WIDTH, FULL_HEIGHT = 255, 215
BG_DARK, BG_PANEL = "#060b18", "#0c1430"
CYBER_1, CYBER_2, CYBER_3 = "#00e5ff", "#2979ff", "#7c4dff"
TEXT_MAIN, TEXT_DIM = "#e0f7ff", "#4a6d8c"
STAT_GREEN, STAT_YELLOW, STAT_RED = "#00e676", "#ffab00", "#ff1744"
PEAK_WARN, PEAK_FILL = "#ff5252", "#241014"


@dataclass(frozen=True)
class PanelState:
    display: BalanceDisplay | None = None
    estimated_decrease: Decimal | None = None
    status_text: str = "CONNECTING"
    error_text: str = ""
    refreshed_at: datetime | None = None


def draw_panel(canvas: Any, state: PanelState, *, now: datetime) -> None:
    canvas.delete("all")
    label, peak = peak_label(now)
    if state.display is None and not state.error_text:
        canvas.create_text(127, 92, text="DEEPSEEK MONITOR", fill=CYBER_1, font=("Consolas", 12, "bold"))
        canvas.create_text(127, 117, text=state.status_text, fill=TEXT_DIM, font=("Consolas", 9))
    elif state.error_text:
        draw_error_panel(canvas, state, now=now)
    else:
        draw_balance_panel(canvas, state, label=label, peak=peak, now=now)
    draw_top_right_buttons(canvas)
```

Keep all strings and coordinates in named constants or small draw helpers; do not read credentials or call network/storage from the renderer.

- [ ] **Step 6: Run GREEN and legacy logic regression**

Run: `python -m unittest tests.test_canvas_ui tests.test_ui_logic.BalanceDisplayTests tests.test_currency -v`  
Expected: all tests pass; currency no-sum tests remain green.

- [ ] **Step 7: Commit**

```powershell
git add src/deepseek_balance_monitor/presentation.py src/deepseek_balance_monitor/canvas_ui.py src/deepseek_balance_monitor/app.py tests/test_canvas_ui.py tests/test_ui_logic.py
git commit -m "feat: render legacy balance panel safely"
```

## Task 5: Whale Bubble Animation and Click Feedback

**Files:**
- Modify: `src/deepseek_balance_monitor/canvas_ui.py`
- Modify: `tests/test_canvas_ui.py`

**Interfaces:**
- `CanvasMonitorView.set_collapsed(collapsed: bool, animate: bool = True)`.
- `CanvasMonitorView.stop_animation()` is idempotent.
- `CanvasMonitorView.close()` cancels swim, click-ripple, drag-safety, age-refresh, and fade timer IDs.

- [ ] **Step 1: Write failing deterministic animation tests**

Inject `clock: Callable[[], float]`, `random_source`, and a fake root with recorded `after`, `after_cancel`, and `geometry` calls. Assert:

```python
class FakeClock:
    value = 0.0

    def __call__(self) -> float:
        return self.value


clock = FakeClock()
clock.value = 0.0
view.set_collapsed(True, animate=False)
view._swim_frame()
self.assertEqual(root.last_size, (100, 100))
self.assertEqual(root.last_position, view.anchor)

clock.value = math.pi / (2 * 0.7)
view._swim_frame()
self.assertLessEqual(abs(root.last_position[0] - view.anchor[0]), 50)
self.assertLessEqual(abs(root.last_position[1] - view.anchor[1]), 8)
self.assertEqual(root.after_delays[-1], 50)
```

The test invokes the real private frame function through the injected clock; no test-only production method is added.

- [ ] **Step 2: Write failing click/drag tests**

Assert press stops swim and draws a ripple, less than 8px movement expands, 8px or more persists a new anchor and resumes, and a lost release schedules exactly one 3000ms recovery callback.

Run: `python -m unittest tests.test_canvas_ui.WhaleAnimationTests -v`  
Expected: FAIL because bubble animation and handlers are absent.

- [ ] **Step 3: Implement bubble drawing and timers**

```python
BUBBLE_WINDOW = 100
BUBBLE_RADIUS = 40
SWIM_AMPLITUDE = 50
BOB_AMPLITUDE = 8
FRAME_MS = 50
DRAG_SAFETY_MS = 3000


def _swim_frame(self) -> None:
    if self._closed or not self._collapsed or self._dragging:
        self._swim_timer = None
        return
    elapsed = self._clock() - self._swim_started
    dx = round(math.sin(elapsed * 0.7) * SWIM_AMPLITUDE)
    dy = round(math.sin(elapsed * 1.1 + 1.2) * BOB_AMPLITUDE)
    x, y = self._clamp_bubble(self._anchor_x + dx, self._anchor_y + dy)
    self._root.geometry(f"{BUBBLE_WINDOW}x{BUBBLE_WINDOW}+{x}+{y}")
    self._draw_bubble(elapsed)
    self._swim_timer = self._root.after(FRAME_MS, self._swim_frame)
```

`_draw_bubble` must draw glow, red/blue body, three ripples, five particles, 44px whale or `DS` fallback, press-scale state, click ripple, and peak/status arc. Cache a bounded set of rotated 44px `PhotoImage` frames rather than creating an unbounded image each frame.

- [ ] **Step 4: Implement exact event behavior**

```python
def _bubble_press(self, event: Any) -> None:
    self.stop_animation()
    self._dragging = True
    self._pressed = True
    self._drag_start = (event.x, event.y)
    self._drag_origin = (self._root.winfo_x(), self._root.winfo_y())
    self._click_ripple_started = self._clock()
    self._drag_safety_timer = self._root.after(DRAG_SAFETY_MS, self._recover_lost_release)


def _bubble_release(self, event: Any) -> None:
    self._cancel_drag_safety()
    dx = abs(event.x - self._drag_start[0])
    dy = abs(event.y - self._drag_start[1])
    self._dragging = False
    self._pressed = False
    if dx < 8 and dy < 8:
        self.set_collapsed(False, animate=True)
    else:
        self._callbacks.save_position(self._anchor_x, self._anchor_y, True)
        self._start_swim()
```

- [ ] **Step 5: Run GREEN and timer-leak coverage**

Run: `python -m unittest tests.test_canvas_ui -v`  
Expected: all renderer/animation/interaction tests pass; every scheduled timer is cancelled by `close()`.

- [ ] **Step 6: Commit**

```powershell
git add src/deepseek_balance_monitor/canvas_ui.py tests/test_canvas_ui.py
git commit -m "feat: add animated whale bubble"
```

## Task 6: App Integration, Menus, Confirmation, and State Memory

**Files:**
- Modify: `src/deepseek_balance_monitor/app.py`
- Modify: `tests/test_ui_logic.py`
- Modify: `src/deepseek_balance_monitor/canvas_ui.py`
- Modify: `tests/test_canvas_ui.py`

**Interfaces:**
- `BubbleApp` keeps its public constructor and `start`, `close`, `wait_for_shutdown`, `apply_controller_update` methods.
- `BubbleApp.request_exit()` is the only callback wired to user-facing exit actions and asks once; `BubbleApp.close()` remains the internal confirmed/cleanup shutdown primitive.
- `CanvasCallbacks` connects refresh, console, confirmed exit, collapse, position save, and autostart changes.

- [ ] **Step 1: Write failing orchestration tests**

Add tests proving:

```python
app.request_exit()
confirm.assert_called_once_with("退出确认", "确定要退出 DeepSeek 余额监控吗？", parent=root)
self.assertFalse(app._closed)  # confirm returned False

confirm.return_value = True
app.request_exit()
self.assertTrue(app._closed)
app.request_exit()
self.assertEqual(controller.close_calls, 1)
```

Add a test where `apply_controller_update` receives CNY and USD and asserts only the selected CNY `BalanceDisplay` reaches the view. Add a history-event test that updates `estimated_decrease` without multiplying it during peak time.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_ui_logic tests.test_canvas_ui -v`  
Expected: confirmation/view/settings integration tests fail because callbacks are not wired.

- [ ] **Step 3: Compose the new view and background settings worker**

```python
self._settings = settings
self._view_state = PanelState()
self._view = CanvasMonitorView(
    root,
    config=config,
    callbacks=CanvasCallbacks(
        refresh=self._request_refresh,
        open_console=lambda: webbrowser.open("https://platform.deepseek.com/usage"),
        request_exit=self.request_exit,
        set_collapsed=self._set_collapsed,
        save_position=self._save_position,
        set_autostart=self._set_autostart,
    ),
    tk_module=tk_module,
)
```

Start `SettingsWorker` before accepting view-side persistence requests. On startup, compare `StartupManager.is_enabled()` to `config.auto_start`; reflect actual state in the immutable config and queue a config save if they differ.

- [ ] **Step 4: Translate controller/history events into immutable view state**

```python
display = build_balance_display(fetched.snapshot, self._config.currency)
self._view_state = replace(
    self._view_state,
    display=display,
    status_text="余额已更新",
    error_text="",
    refreshed_at=datetime.now().astimezone(),
)
self._view.render(self._view_state)
self._history.record(display.currency, display.total_balance)
```

On errors, retain `display`, set only generic `error_text/status_text`, and render. On `SettingsEvent`, update immutable config and menu state on the Tk thread; show only the generic event error.

- [ ] **Step 5: Implement confirmed shutdown and right-click menus**

```python
def request_exit(self) -> None:
    self._assert_main_thread()
    if self._closed or self._confirming_exit:
        return
    self._confirming_exit = True
    try:
        confirmed = self._ask_yes_no(
            "退出确认",
            "确定要退出 DeepSeek 余额监控吗？",
            parent=self._root,
        )
    finally:
        self._confirming_exit = False
    if confirmed:
        self.close()
```

`close()` must stop the view first, close controller/history/settings with short nonblocking joins, destroy Tk, and leave `wait_for_shutdown()` to join every current and retired worker with `None`. `run_app` must use an internal noninteractive cleanup path only after `mainloop` has already ended; it must never display a confirmation from `finally`.

Menus must contain the approved commands. Disable the autostart item while a change is pending and restore it only on a `SettingsEvent`.

- [ ] **Step 6: Run GREEN plus thread/shutdown suites**

Run: `python -m unittest tests.test_ui_logic tests.test_canvas_ui tests.test_settings tests.test_runtime_recovery tests.test_windows -v`  
Expected: all tests pass; no Tk mutation is observed from a worker thread; confirmed close drains accepted settings/history work.

- [ ] **Step 7: Commit**

```powershell
git add src/deepseek_balance_monitor/app.py src/deepseek_balance_monitor/canvas_ui.py tests/test_ui_logic.py tests/test_canvas_ui.py
git commit -m "feat: integrate legacy monitor interactions"
```

## Task 7: Whale Asset and Public Release Contract

**Files:**
- Replace: `assets/deepseek_icon.ico`
- Modify: `NOTICE`
- Modify: `README.md`
- Modify: `README_en.md`
- Modify: `SECURITY.md`
- Modify: `tests/test_repository_hygiene.py`

**Interfaces:**
- `assets/deepseek_icon.ico` is the exact approved 225×225 transparent legacy ICO.
- The wheel continues to contain the asset at `share/deepseek-balance-monitor/deepseek_icon.ico`.

- [ ] **Step 1: Change repository tests first**

Replace the generated-art notice assertion with:

```python
def test_icon_notice_records_unverified_user_approved_provenance(self) -> None:
    text = _read_index_text("NOTICE")
    self.assertIn("user-provided legacy whale asset", text)
    self.assertIn("redistribution authorization has not been independently verified", text)
    self.assertNotIn("OpenAI's built-in image generation", text)
```

Change the ICO test to require `(225, 225)` and alpha range `(0, 255)`. Replace `test_public_source_has_no_timed_pricing_feature` with tests asserting `peak.py` contains the two exact Beijing intervals, UI copy contains both approved labels, and `presentation.py`/`update_usage` never multiply a monetary value by 2.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_repository_hygiene -v`  
Expected: failures for old icon dimensions/notice and missing peak release contract.

- [ ] **Step 3: Replace the binary asset exactly**

```powershell
$source = (Resolve-Path -LiteralPath 'D:\桌面\deepseek_icon.ico').Path
$target = (Resolve-Path -LiteralPath 'assets').Path + '\deepseek_icon.ico'
Copy-Item -LiteralPath $source -Destination $target -Force
```

Verify before continuing:

```powershell
Get-FileHash -Algorithm SHA256 'D:\桌面\deepseek_icon.ico','assets\deepseek_icon.ico'
```

Expected: both hashes are identical.

- [ ] **Step 4: Update NOTICE and bilingual documentation**

NOTICE must say the icon is a user-provided legacy whale asset selected for UI parity, its original author/license and redistribution authorization were not independently verified, and DeepSeek trademarks remain owned by their owner. README files must document panel/bubble controls, Beijing peak schedule, explicit autostart, confirmation exit, and that peak display does not recalculate balance.

SECURITY must add the dedicated startup entry as an explicit user-authorized persistence surface and state its exact path/name and secret-free content rule.

- [ ] **Step 5: Run GREEN and wheel gate**

Run: `python -m unittest tests.test_repository_hygiene -v`  
Expected: all release tests pass, offline wheel includes the 225×225 whale icon and updated NOTICE.

- [ ] **Step 6: Commit**

```powershell
git add assets/deepseek_icon.ico NOTICE README.md README_en.md SECURITY.md tests/test_repository_hygiene.py
git commit -m "docs: ship approved whale interface asset"
```

## Task 8: Full Regression and Windows Smoke

**Files:**
- No production changes expected.
- If a failure is found, add a failing regression test to the owning task's test file before changing production code, then make a separate fix commit.

- [ ] **Step 1: Run the complete locked test suite**

```powershell
$py = '.\.superpowers\sdd\task6-lock-venv-2\Scripts\python.exe'
& $py -m unittest discover -s tests -v
```

Expected: all existing 176 tests plus every new test pass with zero failures/errors.

- [ ] **Step 2: Run build and dependency checks**

```powershell
& $py -m pip check
& $py -m compileall -q src deepseek_monitor.py
& $py -c "import deepseek_balance_monitor; import deepseek_balance_monitor.canvas_ui; import deepseek_balance_monitor.startup"
& $py -m deepseek_balance_monitor --help
```

Expected: every command exits 0; `pip check` prints `No broken requirements found.`

- [ ] **Step 3: Run real credential/config checks without printing secrets or balances**

```powershell
& $py -m deepseek_balance_monitor --check
& $py -c "import os; from deepseek_balance_monitor.storage import AppPaths, load_config; c=load_config(AppPaths.from_environment(os.environ).config_path); print(len(c.__dataclass_fields__), c.auto_start)"
```

Expected: check exits 0 or the documented non-secret missing/indeterminate code for current external state; config reports exactly 9 fields.

- [ ] **Step 4: Run Windows UI smoke against the real Tk window**

Start the new app with an exact owned PID. Verify:

1. Last saved full/bubble mode is restored.
2. Full panel is 255×215 and both `◉`/`✕` are at top right.
3. Peak copy/color agrees with current Beijing time.
4. `◉` produces a 100×100 whale bubble.
5. Whale bubble swims ±50px/±8px; press shows ripple; drag changes anchor; click expands.
6. Right-click refresh works and repeated refreshes coalesce.
7. Autostart enable creates only `DeepSeekBalanceMonitor.vbs`, contains no key, and disable removes it.
8. Exit “No” leaves the process alive; exit “Yes” ends normally.
9. A second instance exits 0 without starting another Tk window.

Do not stop or modify the legacy monitor process. Any smoke-test process must be tracked by exact PID and normally closed; force cleanup only that owned PID if the test itself fails.

- [ ] **Step 5: Verify repository state**

```powershell
git diff --check
git status --short --branch
git log --oneline --decorate -12
```

Expected: no diff-check output, clean worktree, only intentional feature commits.

- [ ] **Step 6: Request final independent review**

Review `main..HEAD` for Critical/Important findings, specifically: secrets in UI/startup, Tk-thread I/O, peak timezone boundaries, currency summation, timer leaks, unconfirmed exits, autostart over-deletion, and mutex release before settings/history shutdown. Fix every Critical/Important finding with a RED→GREEN regression before handoff.
