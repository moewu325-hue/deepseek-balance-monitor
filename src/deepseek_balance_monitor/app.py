from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
import os
from pathlib import Path
from queue import Empty, Full, Queue
import sys
import threading
from typing import Any, Callable
import webbrowser

from .api import ApiClient
from .controller import BalanceController, ControllerEventKind, ControllerUpdate
from .canvas_ui import (
    BG_DARK,
    CanvasCallbacks,
    CanvasMonitorView,
    PanelState,
    load_whale_frames,
)
from .credentials import (
    CredentialCandidate,
    CredentialResolver,
    DiscoveryResult,
    WindowsCredentialStore,
)
from .models import ProbeResult, ProbeStatus
from .presentation import (
    ESTIMATED_DECREASE_LABEL,
    BalanceDisplay,
    build_balance_display,
    format_estimated_decrease,
)
from .settings import SettingsEvent, SettingsWorker
from .startup import StartupManager
from .storage import (
    AppConfig,
    AppPaths,
    UsageBaseline,
    UsageLog,
    UsageRecord,
    load_config,
    load_usage_log,
    save_usage_log_atomic,
)


HISTORY_RETENTION_LIMIT = 10_000


def _load_whale_ui_frames(root: Any) -> tuple[Any, ...]:
    try:
        from PIL import ImageTk
    except Exception:
        return ()

    candidates = (
        Path(__file__).resolve().parents[2] / "assets" / "deepseek_icon.ico",
        Path(sys.prefix) / "share" / "deepseek-balance-monitor" / "deepseek_icon.ico",
    )
    for icon_path in candidates:
        if not icon_path.is_file():
            continue
        frames = load_whale_frames(
            icon_path,
            photo_factory=lambda image: ImageTk.PhotoImage(image, master=root),
        )
        if frames:
            return frames
    return ()
_CLICK_INTERVAL_MS = 400


@dataclass(frozen=True)
class UsageUpdate:
    log: UsageLog
    estimated_decrease: Decimal


class ManualDecision(str, Enum):
    ACCEPTED_VERIFIED = "accepted_verified"
    ACCEPTED_UNVERIFIED = "accepted_unverified"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ManualProbeOutcome:
    decision: ManualDecision
    discovery: DiscoveryResult | None = field(default=None, repr=False)
    persisted: bool = False
    message: str = ""


@dataclass(frozen=True)
class UsageRecorderEvent:
    currency: str | None = None
    estimated_decrease: Decimal | None = None
    message: str = ""


@dataclass(frozen=True)
class _BootstrapEvent:
    kind: str
    value: Any = field(default=None, repr=False)


class RapidClickTracker:
    def __init__(self) -> None:
        self._previous_ms: int | None = None

    def register(self, event_time_ms: int) -> bool:
        previous = self._previous_ms
        self._previous_ms = event_time_ms
        if previous is None:
            return False
        elapsed = event_time_ms - previous
        if 0 <= elapsed <= _CLICK_INTERVAL_MS:
            self._previous_ms = None
            return True
        return False


class _ManualRecoveryResolver:
    def discover(self, excluded: frozenset[str]) -> DiscoveryResult:
        return DiscoveryResult()


class GuiBootstrap:
    def __init__(
        self,
        root: Any,
        *,
        resolver: Any,
        api_client: Any,
        store: Any,
        prompt_manual: Callable[[], tuple[str, bool] | None],
        show_error: Callable[[str], None],
        show_notice: Callable[[str], None],
        on_ready: Callable[[DiscoveryResult], None],
        on_cancel: Callable[[], None],
        poll_interval_ms: int = 25,
    ) -> None:
        self._root = root
        self._resolver = resolver
        self._api_client = api_client
        self._store = store
        self._prompt_manual = prompt_manual
        self._show_error = show_error
        self._show_notice = show_notice
        self._on_ready = on_ready
        self._on_cancel = on_cancel
        self._poll_interval_ms = poll_interval_ms
        self._main_thread_id = threading.get_ident()
        self._events: Queue[_BootstrapEvent] = Queue()
        self._workers: list[threading.Thread] = []
        self._closed = threading.Event()
        self._started = False
        self._completed = False

    def start(self) -> None:
        self._assert_main_thread()
        if self._started:
            return
        self._started = True
        self._spawn(self._discover_normal, "DeepSeekCredentialDiscovery")
        self._root.after(self._poll_interval_ms, self._poll)

    def start_manual(self) -> None:
        self._assert_main_thread()
        if self._started:
            return
        self._started = True
        self._request_manual()
        if not self._completed and not self._closed.is_set():
            self._root.after(self._poll_interval_ms, self._poll)

    def close(self, timeout: float | None = 0.0) -> None:
        self._closed.set()
        self._completed = True
        if timeout is not None and timeout <= 0:
            return
        for worker in tuple(self._workers):
            if worker is not threading.current_thread():
                worker.join(timeout)

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        worker = threading.Thread(target=target, name=name, daemon=True)
        self._workers.append(worker)
        worker.start()

    def _discover_normal(self) -> None:
        try:
            result = self._resolver.discover(frozenset())
        except Exception:
            result = DiscoveryResult(diagnostics=("凭据发现失败。",))
        self._publish(_BootstrapEvent("discovery", result))

    def _verify_manual(self, key: str, persist: bool) -> None:
        outcome = verify_manual_credential(
            self._api_client,
            self._store,
            key,
            persist=persist,
        )
        self._publish(_BootstrapEvent("manual", outcome))

    def _publish(self, event: _BootstrapEvent) -> None:
        if not self._closed.is_set():
            self._events.put(event)

    def _poll(self) -> None:
        self._assert_main_thread()
        if self._closed.is_set() or self._completed:
            return
        try:
            event = self._events.get_nowait()
        except Empty:
            self._root.after(self._poll_interval_ms, self._poll)
            return
        if event.kind == "discovery":
            discovery = event.value
            if discovery.candidate is not None:
                if not discovery.verified:
                    self._show_notice("当前无法验证自动发现的凭据，将仅用于本次运行。")
                self._finish(discovery)
                return
            self._request_manual()
        elif event.kind == "manual":
            outcome = event.value
            if outcome.decision is ManualDecision.REJECTED:
                self._show_error(outcome.message or "凭据不可用，请重新输入。")
                self._request_manual()
            elif outcome.discovery is not None:
                if outcome.message:
                    self._show_notice(outcome.message)
                self._finish(outcome.discovery)
        if not self._completed and not self._closed.is_set():
            self._root.after(self._poll_interval_ms, self._poll)

    def _request_manual(self) -> None:
        self._assert_main_thread()
        if self._closed.is_set():
            return
        result = self._prompt_manual()
        if result is None:
            self._completed = True
            self._on_cancel()
            return
        key, persist = result
        self._spawn(
            lambda: self._verify_manual(key, bool(persist)),
            "DeepSeekManualCredentialProbe",
        )

    def _finish(self, discovery: DiscoveryResult) -> None:
        if self._closed.is_set() or self._completed:
            return
        self._completed = True
        self._on_ready(discovery)

    def _assert_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise RuntimeError("Tk 操作必须在主线程执行。")


_HISTORY_STOP = object()


class UsageRecorder:
    def __init__(
        self,
        path: Path,
        *,
        load_log: Callable[[Path], UsageLog] = load_usage_log,
        save_log: Callable[[Path, UsageLog], None] = save_usage_log_atomic,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self.updates: Queue[UsageRecorderEvent] = Queue()
        self._path = Path(path)
        self._load_log = load_log
        self._save_log = save_log
        self._now = now
        self._commands: Queue[object] = Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def start(self) -> None:
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("历史记录器已关闭。")
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._worker,
                name="DeepSeekUsageWriter",
                daemon=True,
            )
            self._thread.start()

    def record(self, currency: str, total_balance: Decimal) -> None:
        normalized = _normalize_currency(currency)
        total = _finite_decimal(total_balance)
        with self._lock:
            if self._closed.is_set() or self._thread is None:
                raise RuntimeError("历史记录器未运行。")
            command = (normalized, total)
            try:
                self._commands.put_nowait(command)
            except Full:
                try:
                    self._commands.get_nowait()
                except Empty:
                    pass
                self._commands.put_nowait(command)

    def close(self, timeout: float | None = 2.0) -> None:
        with self._lock:
            first_close = not self._closed.is_set()
            self._closed.set()
            thread = self._thread
            if first_close and thread is not None:
                try:
                    self._commands.put_nowait(_HISTORY_STOP)
                except Full:
                    pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _worker(self) -> None:
        try:
            current: UsageLog | None = self._load_log(self._path)
        except Exception:
            current = None
        while True:
            command = self._commands.get()
            if command is _HISTORY_STOP:
                return
            currency, total = command
            if current is None:
                event = UsageRecorderEvent(message="历史记录更新失败。")
            else:
                try:
                    calculated = update_usage(current, currency, total, self._now())
                    self._save_log(self._path, calculated.log)
                except Exception:
                    event = UsageRecorderEvent(message="历史记录更新失败。")
                else:
                    current = calculated.log
                    event = UsageRecorderEvent(currency, calculated.estimated_decrease)
            if not self._closed.is_set():
                self.updates.put(event)
            if self._closed.is_set() and self._commands.empty():
                return


class _NullSettings:
    def __init__(self) -> None:
        self.updates: Queue[SettingsEvent] = Queue()

    def start(self) -> None:
        pass

    def request_save(self, config: AppConfig) -> None:
        self.updates.put(SettingsEvent(config))

    def request_autostart(self, enabled: bool, config: AppConfig) -> None:
        updated = replace(config, auto_start=bool(enabled))
        self.updates.put(SettingsEvent(updated, startup_changed=True))

    def close(self, timeout: float | None = 2.0) -> None:
        pass


class BubbleApp:
    def __init__(
        self,
        root: Any,
        *,
        controller: BalanceController,
        config: AppConfig,
        history: UsageRecorder,
        controller_factory: Callable[[DiscoveryResult], BalanceController] | None = None,
        recovery_factory: Callable[
            [Callable[[DiscoveryResult], None], Callable[[], None]], Any
        ]
        | None = None,
        tk_module: Any | None = None,
        settings: Any | None = None,
        startup: Any | None = None,
        view: Any | None = None,
        ask_yes_no: Callable[..., bool] | None = None,
        show_error: Callable[..., None] | None = None,
    ) -> None:
        if tk_module is None:
            import tkinter as tk_module

        self._root = root
        self._controller = controller
        self._history = history
        self._settings = settings if settings is not None else _NullSettings()
        self._controller_factory = controller_factory
        self._recovery_factory = recovery_factory
        self._recovery: Any | None = None
        self._retired_recoveries: list[Any] = []
        self._retired_controllers: list[BalanceController] = []
        self._recovery_active = False
        self._main_thread_id = threading.get_ident()
        self._closed = False
        self._started = False
        self._confirming_exit = False
        self._autostart_pending = False
        self._pending_autostart_value: bool | None = None
        self._poll_timer: Any | None = None
        self._refresh_timer: Any | None = None
        self._startup_sync_needed = False
        self._ask_yes_no = ask_yes_no or _default_ask_yes_no
        self._show_error = show_error or _default_show_error

        if startup is not None:
            try:
                actual_startup = bool(startup.is_enabled())
            except Exception:
                actual_startup = config.auto_start
            if actual_startup != config.auto_start:
                config = replace(config, auto_start=actual_startup)
                self._startup_sync_needed = True
        self._config = config
        self._view_state = PanelState(status_text="CONNECTING...")

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", config.opacity)
        root.configure(bg=BG_DARK)
        x = 40 if config.window_x is None else config.window_x
        y = 80 if config.window_y is None else config.window_y
        root.geometry(f"255x215+{x}+{y}")

        callbacks = CanvasCallbacks(
            refresh=self._request_refresh,
            open_console=self._open_console,
            request_exit=self.request_exit,
            set_collapsed=self._set_collapsed,
            save_position=self._save_position,
            set_autostart=self._set_autostart,
        )
        if view is None:
            view = CanvasMonitorView(
                root,
                config=config,
                callbacks=callbacks,
                tk_module=tk_module,
                whale_frames=_load_whale_ui_frames(root),
            )
        self._view = view
        self._view.set_autostart_state(config.auto_start, pending=False)
        self._view.render(self._view_state)

    def start(self) -> None:
        self._assert_main_thread()
        if self._closed or self._started:
            return
        self._started = True
        self._settings.start()
        if self._startup_sync_needed:
            self._settings.request_save(self._config)
            self._startup_sync_needed = False
        self._history.start()
        self._controller.start()
        self._poll_timer = self._root.after(40, self._poll_updates)
        self._refresh_timer = self._root.after(
            max(self._config.refresh_interval, 1) * 1000,
            self._refresh,
        )

    def request_exit(self) -> None:
        self._assert_main_thread()
        if self._closed or self._confirming_exit:
            return
        self._confirming_exit = True
        try:
            try:
                confirmed = bool(
                    self._ask_yes_no(
                        "退出确认",
                        "确定要退出 DeepSeek 余额监控吗？",
                        parent=self._root,
                    )
                )
            except Exception:
                confirmed = False
        finally:
            self._confirming_exit = False
        if confirmed:
            self.close()

    def close(self) -> None:
        self._assert_main_thread()
        if self._closed:
            return
        self._closed = True
        self._cancel_root_timer("_poll_timer")
        self._cancel_root_timer("_refresh_timer")
        try:
            self._view.close()
        except Exception:
            pass
        if self._recovery is not None:
            self._recovery.close(timeout=0.0)
        self._controller.close(timeout=0.2)
        self._history.close(timeout=0.2)
        self._settings.close(timeout=0.2)
        try:
            self._root.destroy()
        except Exception:
            pass

    def wait_for_shutdown(self) -> None:
        self._assert_main_thread()
        for recovery in tuple(self._retired_recoveries):
            recovery.close(timeout=None)
        if self._recovery is not None:
            self._recovery.close(timeout=None)
        for controller in tuple(self._retired_controllers):
            controller.close(timeout=None)
        self._controller.close(timeout=None)
        self._history.close(timeout=None)
        self._settings.close(timeout=None)

    def apply_controller_update(self, update: ControllerUpdate) -> None:
        self._assert_main_thread()
        if self._closed:
            return
        if update.kind is ControllerEventKind.DISCOVERY:
            if not update.verified and update.probe_status is None:
                self._begin_manual_recovery()
                return
            status = (
                "凭据已验证，正在读取余额…"
                if update.verified
                else "凭据暂未验证，正在尝试读取余额…"
            )
            self._render_status(status)
            return
        if update.kind is ControllerEventKind.ERROR:
            self._render_status("运行任务失败，请稍后重试。", error="运行任务失败。")
            return
        if update.kind is not ControllerEventKind.FETCH or update.fetch is None:
            return
        fetched = update.fetch
        if fetched.snapshot is None:
            message = (
                "账户已认证，余额暂不可用。"
                if fetched.status_code == 402
                else "余额读取失败，请稍后重试。"
            )
            self._render_status(message, error=message)
            return
        try:
            display = build_balance_display(fetched.snapshot, self._config.currency)
        except ValueError:
            message = "首选币种不可用，无法确定显示金额。"
            self._render_status(message, error=message)
            return
        self._view_state = replace(
            self._view_state,
            display=display,
            status_text="余额已更新",
            error_text="",
            refreshed_at=datetime.now().astimezone(),
        )
        self._view.render(self._view_state)
        self._history.record(display.currency, display.total_balance)

    def _begin_manual_recovery(self) -> None:
        self._assert_main_thread()
        if self._closed or self._recovery_active:
            return
        if self._recovery_factory is None or self._controller_factory is None:
            self._render_status(
                "未找到可用凭据，请重新启动后设置。",
                error="未找到可用凭据。",
            )
            return
        self._recovery_active = True
        self._render_status("凭据已失效，请重新输入。", error="凭据需要更新。")
        self._recovery = self._recovery_factory(self._replace_controller, self.close)
        self._recovery.start_manual()

    def _replace_controller(self, discovery: DiscoveryResult) -> None:
        self._assert_main_thread()
        if self._closed or not self._recovery_active:
            return
        recovery = self._recovery
        if recovery is not None:
            recovery.close(timeout=0.0)
            self._retired_recoveries.append(recovery)
        old_controller = self._controller
        try:
            if self._controller_factory is None:
                raise RuntimeError("Controller factory is unavailable")
            replacement = self._controller_factory(discovery)
            old_controller.close(timeout=0.2)
            self._retired_controllers.append(old_controller)
            self._controller = replacement
            replacement.start()
        except Exception:
            self._recovery = None
            self._recovery_active = False
            self._render_status(
                "凭据恢复失败，请重新启动后重试。",
                error="凭据恢复失败。",
            )
            return
        self._recovery = None
        self._recovery_active = False
        self._render_status("凭据已更新，正在读取余额…")

    def _poll_updates(self) -> None:
        self._assert_main_thread()
        self._poll_timer = None
        if self._closed:
            return
        while True:
            try:
                update = self._controller.updates.get_nowait()
            except Empty:
                break
            self.apply_controller_update(update)
            if self._closed:
                return
        while True:
            try:
                event = self._history.updates.get_nowait()
            except Empty:
                break
            self._apply_history_event(event)
            if self._closed:
                return
        while True:
            try:
                event = self._settings.updates.get_nowait()
            except Empty:
                break
            self._apply_settings_event(event)
            if self._closed:
                return
        self._poll_timer = self._root.after(40, self._poll_updates)

    def _apply_history_event(self, event: UsageRecorderEvent) -> None:
        self._assert_main_thread()
        display = self._view_state.display
        if (
            event.currency is not None
            and event.estimated_decrease is not None
            and display is not None
            and event.currency.upper() == display.currency
        ):
            self._view_state = replace(
                self._view_state,
                estimated_decrease=event.estimated_decrease,
            )
            self._view.render(self._view_state)
        elif event.message:
            self._show_generic_error(event.message)

    def _apply_settings_event(self, event: SettingsEvent) -> None:
        self._assert_main_thread()
        self._config = event.config
        if event.startup_changed:
            self._autostart_pending = False
            self._pending_autostart_value = None
        shown_autostart = (
            self._pending_autostart_value
            if self._autostart_pending and self._pending_autostart_value is not None
            else self._config.auto_start
        )
        self._view.set_autostart_state(
            shown_autostart,
            pending=self._autostart_pending,
        )
        if event.error:
            self._show_generic_error(event.error)

    def _refresh(self) -> None:
        self._assert_main_thread()
        self._refresh_timer = None
        if self._closed:
            return
        self._request_refresh()
        if not self._closed:
            self._refresh_timer = self._root.after(
                max(self._config.refresh_interval, 1) * 1000,
                self._refresh,
            )

    def _request_refresh(self) -> None:
        self._assert_main_thread()
        if self._closed:
            return
        try:
            self._controller.request_refresh()
        except RuntimeError:
            return

    def _open_console(self) -> None:
        self._assert_main_thread()
        try:
            webbrowser.open("https://platform.deepseek.com/usage")
        except Exception:
            self._show_generic_error("无法打开 DeepSeek 控制台。")

    def _set_collapsed(self, collapsed: bool) -> None:
        self._assert_main_thread()
        self._save_position(
            self._root.winfo_x(),
            self._root.winfo_y(),
            bool(collapsed),
        )

    def _save_position(self, x: int, y: int, collapsed: bool) -> None:
        self._assert_main_thread()
        if self._closed:
            return
        self._config = replace(
            self._config,
            window_x=int(x),
            window_y=int(y),
            collapsed=bool(collapsed),
        )
        try:
            self._settings.request_save(self._config)
        except RuntimeError:
            pass

    def _set_autostart(self, enabled: bool) -> None:
        self._assert_main_thread()
        if self._closed or self._autostart_pending:
            return
        self._autostart_pending = True
        self._pending_autostart_value = bool(enabled)
        self._view.set_autostart_state(bool(enabled), pending=True)
        try:
            self._settings.request_autostart(bool(enabled), self._config)
        except RuntimeError:
            self._autostart_pending = False
            self._pending_autostart_value = None
            self._view.set_autostart_state(self._config.auto_start, pending=False)
            self._show_generic_error("开机自启动设置失败。")

    def _render_status(self, status: str, *, error: str = "") -> None:
        self._view_state = replace(
            self._view_state,
            status_text=status,
            error_text=error,
        )
        self._view.render(self._view_state)

    def _show_generic_error(self, message: str) -> None:
        try:
            self._show_error("DeepSeek 余额监控", message, parent=self._root)
        except Exception:
            pass

    def _cancel_root_timer(self, name: str) -> None:
        timer = getattr(self, name, None)
        if timer is None:
            return
        setattr(self, name, None)
        try:
            self._root.after_cancel(timer)
        except Exception:
            pass

    def _assert_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise RuntimeError("Tk 操作必须在主线程执行。")


def _default_ask_yes_no(title: str, message: str, **options: Any) -> bool:
    from tkinter import messagebox

    return bool(messagebox.askyesno(title, message, **options))


def _default_show_error(title: str, message: str, **options: Any) -> None:
    from tkinter import messagebox

    messagebox.showerror(title, message, **options)


def update_usage(
    log: UsageLog,
    currency: str,
    total_balance: Decimal,
    when: datetime,
) -> UsageUpdate:
    normalized_currency = _normalize_currency(currency)
    normalized_total = _finite_decimal(total_balance)
    baselines = dict(log.baselines)
    baseline = baselines.get(normalized_currency)
    if baseline is None or baseline.date != when.date():
        baseline = UsageBaseline(when.date(), normalized_total)
        baselines[normalized_currency] = baseline
    decrease = max(baseline.total_balance - normalized_total, Decimal("0"))
    records = log.records[-HISTORY_RETENTION_LIMIT:]
    if not records or (
        records[-1].currency != normalized_currency
        or records[-1].total_balance != normalized_total
    ):
        record = UsageRecord(when.isoformat(), normalized_currency, normalized_total)
        records = (*records, record)[-HISTORY_RETENTION_LIMIT:]
    return UsageUpdate(
        UsageLog(baselines, records),
        decrease,
    )


def verify_manual_credential(
    api_client: Any,
    store: Any,
    key: str,
    *,
    persist: bool,
) -> ManualProbeOutcome:
    normalized = key.strip()
    if not normalized:
        return ManualProbeOutcome(
            ManualDecision.REJECTED,
            message="请输入有效凭据。",
        )
    try:
        probe = api_client.probe_key(normalized)
    except Exception:
        probe = ProbeResult(ProbeStatus.INDETERMINATE, error="凭据验证暂不可用。")

    if probe.status is ProbeStatus.INVALID:
        return ManualProbeOutcome(
            ManualDecision.REJECTED,
            message="凭据无效，请重新输入。",
        )

    candidate = CredentialCandidate("manual", normalized)
    if probe.status is ProbeStatus.INDETERMINATE:
        return ManualProbeOutcome(
            ManualDecision.ACCEPTED_UNVERIFIED,
            DiscoveryResult(candidate, probe, False),
            message="当前无法验证凭据，将仅在本次运行的内存中使用。",
        )

    if probe.status not in {
        ProbeStatus.VALID,
        ProbeStatus.AUTHENTICATED_NO_BALANCE,
    }:
        return ManualProbeOutcome(
            ManualDecision.REJECTED,
            message="当前无法使用凭据，请稍后重试。",
        )

    discovery = DiscoveryResult(candidate, probe, True)
    if not persist:
        return ManualProbeOutcome(ManualDecision.ACCEPTED_VERIFIED, discovery)
    try:
        store.write_dedicated(normalized)
    except Exception:
        return ManualProbeOutcome(
            ManualDecision.ACCEPTED_VERIFIED,
            discovery,
            message="凭据可用，但保存失败，将仅在本次运行的内存中使用。",
        )
    return ManualProbeOutcome(
        ManualDecision.ACCEPTED_VERIFIED,
        discovery,
        persisted=True,
    )


def show_manual_key_dialog(
    root: Any,
    *,
    tk_module: Any | None = None,
) -> tuple[str, bool] | None:
    if tk_module is None:
        import tkinter as tk_module

    window = tk_module.Toplevel(root)
    window.title("DeepSeek 凭据")
    window.resizable(False, False)
    try:
        owner_state = root.state()
    except Exception:
        owner_state = "normal"
    if owner_state != "withdrawn":
        window.transient(root)
    window.grab_set()
    window.columnconfigure(0, weight=1)
    key_variable = tk_module.StringVar(master=window, value="")
    persist_variable = tk_module.BooleanVar(master=window, value=False)
    error_variable = tk_module.StringVar(master=window, value="")
    result: tuple[str, bool] | None = None

    def cancel() -> None:
        key_variable.set("")
        window.destroy()

    def submit() -> None:
        nonlocal result
        key = str(key_variable.get()).strip()
        if not key:
            error_variable.set("请输入有效凭据。")
            return
        result = (key, bool(persist_variable.get()))
        key_variable.set("")
        window.destroy()

    tk_module.Label(
        window,
        text="未自动发现可用凭据，请手动输入。",
        padx=16,
        pady=8,
    ).grid(row=0, column=0, columnspan=2, sticky="w")
    entry = tk_module.Entry(
        window,
        textvariable=key_variable,
        show="*",
        width=42,
    )
    entry.grid(row=1, column=0, columnspan=2, padx=16, sticky="ew")
    entry.focus_set()
    tk_module.Checkbutton(
        window,
        text="保存到 Windows 凭据管理器",
        variable=persist_variable,
    ).grid(row=2, column=0, columnspan=2, padx=12, pady=6, sticky="w")
    tk_module.Label(
        window,
        textvariable=error_variable,
        fg="#b00020",
    ).grid(row=3, column=0, columnspan=2, padx=16, sticky="w")
    tk_module.Button(window, text="取消", command=cancel).grid(
        row=4, column=0, padx=8, pady=12, sticky="e"
    )
    tk_module.Button(window, text="使用", command=submit).grid(
        row=4, column=1, padx=8, pady=12, sticky="w"
    )
    window.protocol("WM_DELETE_WINDOW", cancel)
    try:
        root.wait_window(window)
        return result
    finally:
        key_variable.set("")


def run_app() -> int:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    api_client = ApiClient()
    store = WindowsCredentialStore()
    resolver = CredentialResolver(api_client, env=os.environ, store=store)
    manual_recovery_resolver = _ManualRecoveryResolver()
    paths = AppPaths.from_environment(os.environ)
    config = load_config(paths.config_path)
    active_app: list[BubbleApp] = []

    def create_controller(discovery: DiscoveryResult) -> BalanceController:
        return BalanceController(
            api_client=api_client,
            resolver=resolver,
            initial_discovery=discovery,
        )

    def create_recovery_controller(discovery: DiscoveryResult) -> BalanceController:
        return BalanceController(
            api_client=api_client,
            resolver=manual_recovery_resolver,
            initial_discovery=discovery,
        )

    def create_recovery(
        on_ready: Callable[[DiscoveryResult], None],
        on_cancel: Callable[[], None],
    ) -> GuiBootstrap:
        return GuiBootstrap(
            root,
            resolver=resolver,
            api_client=api_client,
            store=store,
            prompt_manual=lambda: show_manual_key_dialog(root, tk_module=tk),
            show_error=lambda message: messagebox.showerror(
                "DeepSeek 余额监控", message, parent=root
            ),
            show_notice=lambda message: messagebox.showinfo(
                "DeepSeek 余额监控", message, parent=root
            ),
            on_ready=on_ready,
            on_cancel=on_cancel,
        )

    def cancel() -> None:
        if active_app:
            active_app[0].close()
        else:
            root.destroy()

    def ready(discovery: DiscoveryResult) -> None:
        controller = create_controller(discovery)
        history = UsageRecorder(paths.usage_log_path)
        source_launcher = Path(__file__).resolve().parents[2] / "deepseek_monitor.py"
        startup = StartupManager(
            os.environ,
            Path(sys.executable),
            launcher=source_launcher if source_launcher.is_file() else None,
        )
        settings = SettingsWorker(paths.config_path, startup=startup)
        bubble = BubbleApp(
            root,
            controller=controller,
            config=config,
            history=history,
            controller_factory=create_recovery_controller,
            recovery_factory=create_recovery,
            tk_module=tk,
            settings=settings,
            startup=startup,
            ask_yes_no=messagebox.askyesno,
            show_error=messagebox.showerror,
        )
        active_app.append(bubble)
        root.protocol("WM_DELETE_WINDOW", bubble.request_exit)
        root.deiconify()
        bubble.start()

    bootstrap = GuiBootstrap(
        root,
        resolver=resolver,
        api_client=api_client,
        store=store,
        prompt_manual=lambda: show_manual_key_dialog(root, tk_module=tk),
        show_error=lambda message: messagebox.showerror(
            "DeepSeek 余额监控", message, parent=root
        ),
        show_notice=lambda message: messagebox.showinfo(
            "DeepSeek 余额监控", message, parent=root
        ),
        on_ready=ready,
        on_cancel=cancel,
    )
    root.protocol("WM_DELETE_WINDOW", cancel)
    bootstrap.start()
    try:
        root.mainloop()
    finally:
        bootstrap.close(timeout=None)
        if active_app:
            try:
                active_app[0].close()
            except Exception:
                pass
            active_app[0].wait_for_shutdown()
    return 0


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
