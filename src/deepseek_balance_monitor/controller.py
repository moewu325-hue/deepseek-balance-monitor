from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from queue import Full, Queue
import threading
from typing import Any

from .credentials import CredentialResolver, DiscoveryResult
from .models import FetchResult, ProbeStatus


class ControllerEventKind(str, Enum):
    DISCOVERY = "discovery"
    FETCH = "fetch"
    ERROR = "error"


@dataclass(frozen=True)
class ControllerUpdate:
    kind: ControllerEventKind
    verified: bool = False
    probe_status: ProbeStatus | None = None
    fetch: FetchResult | None = None
    message: str | None = None


_REFRESH = object()
_STOP = object()
_UNSET = object()


class BalanceController:
    def __init__(
        self,
        *,
        api_client: Any,
        resolver: CredentialResolver,
        initial_discovery: DiscoveryResult | None = None,
    ) -> None:
        self.updates: Queue[ControllerUpdate] = Queue()
        self._commands: Queue[object] = Queue(maxsize=1)
        self._api_client = api_client
        self._resolver = resolver
        self._initial_discovery: DiscoveryResult | object = (
            _UNSET if initial_discovery is None else initial_discovery
        )
        self._excluded: set[str] = set()
        self._current_key: str | None = None
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._closed = False

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Controller is closed")
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._worker,
                name="DeepSeekBalanceWorker",
                daemon=True,
            )
            self._thread.start()

    def request_refresh(self) -> None:
        with self._lifecycle_lock:
            if self._closed or self._thread is None:
                raise RuntimeError("Controller is not running")
            try:
                self._commands.put_nowait(_REFRESH)
            except Full:
                pass

    def close(self, timeout: float | None = 2.0) -> None:
        with self._lifecycle_lock:
            first_close = not self._closed
            if first_close:
                self._closed = True
                self._stop_requested.set()
                self._current_key = None
                self._initial_discovery = _UNSET
            thread = self._thread
            if first_close and thread is not None:
                try:
                    self._commands.put_nowait(_STOP)
                except Full:
                    pass
        if thread is None:
            return
        if thread is not threading.current_thread():
            thread.join(timeout)

    def _worker(self) -> None:
        try:
            if self._stop_requested.is_set():
                return
            startup = self._resolve_startup()
            if self._stop_requested.is_set() or not self._apply_discovery(startup):
                return
            if not self._stop_requested.is_set() and self._current_key is not None:
                self._fetch_current()
            while True:
                command = self._commands.get()
                if command is _STOP or self._stop_requested.is_set():
                    return
                if command is _REFRESH and self._current_key is not None:
                    self._fetch_current()
        except Exception:
            self._publish_error()

    def _resolve_startup(self) -> DiscoveryResult:
        if self._initial_discovery is _UNSET:
            result = self._resolver.discover(frozenset())
        else:
            result = self._initial_discovery
            self._initial_discovery = _UNSET
        return result

    def _apply_discovery(self, result: DiscoveryResult) -> bool:
        update = ControllerUpdate(
            ControllerEventKind.DISCOVERY,
            verified=result.verified,
            probe_status=result.probe.status if result.probe is not None else None,
        )
        with self._lifecycle_lock:
            if self._stop_requested.is_set():
                return False
            self._current_key = result.candidate.key if result.candidate is not None else None
            self.updates.put(update)
            return True

    def _publish_update(self, update: ControllerUpdate) -> bool:
        with self._lifecycle_lock:
            if self._stop_requested.is_set():
                return False
            self.updates.put(update)
            return True

    def _fetch_current(self) -> None:
        if self._stop_requested.is_set():
            return
        key = self._current_key
        if key is None:
            return
        result = self._api_client.fetch_balance(key)
        if self._stop_requested.is_set():
            return
        published = result
        if result.error is not None and key in result.error:
            published = FetchResult(
                result.snapshot,
                error="余额请求失败。",
                status_code=result.status_code,
            )
        if not self._publish_update(
            ControllerUpdate(ControllerEventKind.FETCH, fetch=published)
        ):
            return
        if result.status_code != 401:
            return

        self._excluded.add(key)
        self._current_key = None
        if self._stop_requested.is_set():
            return
        replacement = self._resolver.discover(frozenset(self._excluded))
        if self._stop_requested.is_set() or not self._apply_discovery(replacement):
            return
        if not self._stop_requested.is_set() and self._current_key is not None:
            self._fetch_current()

    def _publish_error(self) -> None:
        self._publish_update(
            ControllerUpdate(
                ControllerEventKind.ERROR,
                message="运行任务失败，请稍后重试。",
            )
        )
