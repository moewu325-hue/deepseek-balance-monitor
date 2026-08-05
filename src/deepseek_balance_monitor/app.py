from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
import os
from pathlib import Path
from queue import Empty, Full, Queue
import threading
from typing import Any, Callable

from .api import ApiClient
from .controller import BalanceController, ControllerEventKind, ControllerUpdate
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
    ) -> None:
        if tk_module is None:
            import tkinter as tk_module

        self._root = root
        self._controller = controller
        self._config = config
        self._history = history
        self._controller_factory = controller_factory
        self._recovery_factory = recovery_factory
        self._recovery: Any | None = None
        self._retired_recoveries: list[Any] = []
        self._retired_controllers: list[BalanceController] = []
        self._recovery_active = False
        self._tk = tk_module
        self._main_thread_id = threading.get_ident()
        self._closed = False
        self._tracker = RapidClickTracker()
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._dragged = False
        self._balance_var = tk_module.StringVar(master=root, value="--")
        self._status_var = tk_module.StringVar(master=root, value="正在读取余额…")
        self._usage_var = tk_module.StringVar(
            master=root,
            value=f"{ESTIMATED_DECREASE_LABEL}：--",
        )
        self._build_widgets()

    def start(self) -> None:
        self._assert_main_thread()
        self._history.start()
        self._controller.start()
        self._root.after(40, self._poll_updates)
        self._root.after(max(self._config.refresh_interval, 1) * 1000, self._refresh)

    def close(self) -> None:
        self._assert_main_thread()
        if self._closed:
            return
        self._closed = True
        if self._recovery is not None:
            self._recovery.close(timeout=0.0)
        self._controller.close(timeout=0.2)
        self._history.close(timeout=0.2)
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

    def apply_controller_update(self, update: ControllerUpdate) -> None:
        self._assert_main_thread()
        if self._closed:
            return
        if update.kind is ControllerEventKind.DISCOVERY:
            if not update.verified and update.probe_status is None:
                self._begin_manual_recovery()
                return
            if update.verified:
                self._status_var.set("凭据已验证，正在读取余额…")
            else:
                self._status_var.set("凭据暂未验证，正在尝试读取余额…")
            return
        if update.kind is ControllerEventKind.ERROR:
            self._status_var.set("运行任务失败，请稍后重试。")
            return
        if update.kind is not ControllerEventKind.FETCH or update.fetch is None:
            return
        fetched = update.fetch
        if fetched.snapshot is None:
            if fetched.status_code == 402:
                self._status_var.set("账户已认证，余额暂不可用。")
            else:
                self._status_var.set("余额读取失败，请稍后重试。")
            return
        try:
            display = build_balance_display(fetched.snapshot, self._config.currency)
        except ValueError:
            self._status_var.set("首选币种不可用，无法确定显示金额。")
            return
        self._balance_var.set(display.balance_text)
        self._status_var.set("余额已更新")
        self._history.record(display.currency, display.total_balance)

    def _begin_manual_recovery(self) -> None:
        self._assert_main_thread()
        if self._closed or self._recovery_active:
            return
        if self._recovery_factory is None or self._controller_factory is None:
            self._status_var.set("未找到可用凭据，请重新启动后设置。")
            return
        self._recovery_active = True
        self._status_var.set("凭据已失效，请重新输入。")
        self._recovery = self._recovery_factory(
            self._replace_controller,
            self.close,
        )
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
            replacement = self._controller_factory(discovery)
            old_controller.close(timeout=0.2)
            self._retired_controllers.append(old_controller)
            self._controller = replacement
            replacement.start()
        except Exception:
            self._recovery = None
            self._recovery_active = False
            self._status_var.set("凭据恢复失败，请重新启动后重试。")
            return
        self._recovery = None
        self._recovery_active = False
        self._status_var.set("凭据已更新，正在读取余额…")

    def _build_widgets(self) -> None:
        tk = self._tk
        root = self._root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", self._config.opacity)
        root.configure(bg="#07131a")
        x = 40 if self._config.window_x is None else self._config.window_x
        y = 80 if self._config.window_y is None else self._config.window_y
        root.geometry(f"+{x}+{y}")

        self._shell = tk.Frame(
            root,
            bg="#07131a",
            highlightbackground="#20d9ff",
            highlightthickness=1,
            padx=10,
            pady=8,
        )
        self._shell.pack()
        self._content = tk.Frame(self._shell, bg="#07131a")
        self._content.pack()
        self._title_label = tk.Label(
            self._content,
            text="DEEPSEEK // BALANCE",
            fg="#20d9ff",
            bg="#07131a",
            font=("Segoe UI", 8, "bold"),
        )
        self._title_label.pack(anchor="w")
        self._balance_label = tk.Label(
            self._content,
            textvariable=self._balance_var,
            fg="#f25cff",
            bg="#07131a",
            font=("Segoe UI", 20, "bold"),
        )
        self._balance_label.pack(anchor="w")
        self._usage_label = tk.Label(
            self._content,
            textvariable=self._usage_var,
            fg="#b5cbd6",
            bg="#07131a",
            font=("Segoe UI", 8),
        )
        self._usage_label.pack(anchor="w")
        self._status_label = tk.Label(
            self._content,
            textvariable=self._status_var,
            fg="#6f8792",
            bg="#07131a",
            font=("Segoe UI", 7),
        )
        self._status_label.pack(anchor="w")
        self._close_button = tk.Button(
            self._content,
            text="×",
            command=self.close,
            bd=0,
            fg="#f25cff",
            bg="#07131a",
            activebackground="#112630",
            activeforeground="#ffffff",
        )
        self._close_button.place(relx=1.0, x=0, y=0, anchor="ne")
        self._compact = tk.Label(
            self._shell,
            text="DS",
            fg="#20d9ff",
            bg="#07131a",
            font=("Segoe UI", 10, "bold"),
        )
        for widget in (
            self._shell,
            self._content,
            self._title_label,
            self._balance_label,
            self._usage_label,
            self._status_label,
            self._compact,
        ):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
        self._collapsed = not self._config.collapsed
        self._set_collapsed(self._config.collapsed)

    def _poll_updates(self) -> None:
        self._assert_main_thread()
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
        if self._closed:
            return
        while True:
            try:
                event = self._history.updates.get_nowait()
            except Empty:
                break
            if event.currency is not None and event.estimated_decrease is not None:
                self._usage_var.set(
                    format_estimated_decrease(event.currency, event.estimated_decrease)
                )
            elif event.message:
                self._status_var.set(event.message)
            if self._closed:
                return
        if not self._closed:
            self._root.after(40, self._poll_updates)

    def _refresh(self) -> None:
        self._assert_main_thread()
        if self._closed:
            return
        try:
            self._controller.request_refresh()
        except RuntimeError:
            return
        self._root.after(max(self._config.refresh_interval, 1) * 1000, self._refresh)

    def _drag_start(self, event: Any) -> None:
        self._assert_main_thread()
        self._drag_origin = (
            event.x_root,
            event.y_root,
            self._root.winfo_x(),
            self._root.winfo_y(),
        )
        self._dragged = False

    def _drag_move(self, event: Any) -> None:
        self._assert_main_thread()
        if self._drag_origin is None:
            return
        start_x, start_y, window_x, window_y = self._drag_origin
        dx = event.x_root - start_x
        dy = event.y_root - start_y
        self._dragged = self._dragged or abs(dx) > 2 or abs(dy) > 2
        self._root.geometry(f"+{window_x + dx}+{window_y + dy}")

    def _drag_end(self, event: Any) -> None:
        self._assert_main_thread()
        self._drag_origin = None
        if not self._dragged and self._tracker.register(int(event.time)):
            self._set_collapsed(not self._collapsed)

    def _set_collapsed(self, collapsed: bool) -> None:
        self._assert_main_thread()
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if collapsed:
            self._content.pack_forget()
            self._compact.pack()
        else:
            self._compact.pack_forget()
            self._content.pack()

    def _assert_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise RuntimeError("Tk 操作必须在主线程执行。")


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
        bubble = BubbleApp(
            root,
            controller=controller,
            config=config,
            history=history,
            controller_factory=create_recovery_controller,
            recovery_factory=create_recovery,
            tk_module=tk,
        )
        active_app.append(bubble)
        root.protocol("WM_DELETE_WINDOW", bubble.close)
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
