from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from queue import Queue
import threading
from typing import Callable, Protocol

from .storage import AppConfig, save_config_atomic


class StartupControl(Protocol):
    def is_enabled(self) -> bool: ...

    def set_enabled(self, enabled: bool) -> None: ...


@dataclass(frozen=True)
class SettingsEvent:
    config: AppConfig
    startup_changed: bool = False
    error: str = ""


class SettingsWorker:
    def __init__(
        self,
        path: Path,
        *,
        save_config: Callable[[Path, AppConfig], None] = save_config_atomic,
        startup: StartupControl,
    ) -> None:
        self.updates: Queue[SettingsEvent] = Queue()
        self._path = Path(path)
        self._save_config = save_config
        self._startup = startup
        self._condition = threading.Condition()
        self._pending_config: AppConfig | None = None
        self._pending_startup: tuple[bool, AppConfig] | None = None
        self._startup_inflight = False
        self._known_auto_start: bool | None = None
        self._thread: threading.Thread | None = None
        self._closing = False

    def start(self) -> None:
        with self._condition:
            if self._closing:
                raise RuntimeError("Settings worker is closed")
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._worker,
                name="DeepSeekSettingsWriter",
                daemon=True,
            )
            self._thread.start()

    def request_save(self, config: AppConfig) -> None:
        self._validate_config(config)
        with self._condition:
            self._require_running()
            self._pending_config = config
            self._condition.notify()

    def request_autostart(self, enabled: bool, config: AppConfig) -> None:
        self._validate_config(config)
        with self._condition:
            self._require_running()
            if self._pending_startup is not None or self._startup_inflight:
                raise RuntimeError("Autostart change already pending")
            self._pending_startup = bool(enabled), config
            self._condition.notify()

    def close(self, timeout: float | None = 2.0) -> None:
        with self._condition:
            self._closing = True
            thread = self._thread
            self._condition.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while (
                    not self._closing
                    and self._pending_config is None
                    and self._pending_startup is None
                ):
                    self._condition.wait()
                if (
                    self._closing
                    and self._pending_config is None
                    and self._pending_startup is None
                ):
                    return
                startup_command = self._pending_startup
                config = self._pending_config
                self._pending_startup = None
                self._pending_config = None
                if startup_command is not None:
                    self._startup_inflight = True

            if startup_command is None:
                if config is not None:
                    self._apply_save(config)
                continue

            enabled, startup_config = startup_command
            try:
                self._apply_startup(enabled, config or startup_config)
            finally:
                with self._condition:
                    self._startup_inflight = False
                    self._condition.notify_all()

    def _apply_save(self, config: AppConfig) -> None:
        if self._known_auto_start is not None:
            config = replace(config, auto_start=self._known_auto_start)
        try:
            self._save_config(self._path, config)
        except Exception:
            self.updates.put(SettingsEvent(config, error="设置保存失败。"))
        else:
            self.updates.put(SettingsEvent(config))

    def _apply_startup(self, enabled: bool, config: AppConfig) -> None:
        previous = self._actual_startup(config.auto_start)
        try:
            self._startup.set_enabled(enabled)
            actual = self._startup.is_enabled()
            if actual != enabled:
                raise OSError("Autostart state mismatch")
            updated = replace(config, auto_start=actual)
            self._save_config(self._path, updated)
        except Exception:
            try:
                self._startup.set_enabled(previous)
            except Exception:
                pass
            actual = self._actual_startup(previous)
            updated = replace(config, auto_start=actual)
            try:
                self._save_config(self._path, updated)
            except Exception:
                pass
            self._known_auto_start = actual
            self.updates.put(
                SettingsEvent(
                    updated,
                    startup_changed=True,
                    error="开机自启动设置失败。",
                )
            )
            return

        self._known_auto_start = actual
        self.updates.put(SettingsEvent(updated, startup_changed=True))

    def _actual_startup(self, fallback: bool) -> bool:
        try:
            return bool(self._startup.is_enabled())
        except Exception:
            return bool(fallback)

    def _require_running(self) -> None:
        if self._thread is None or self._closing:
            raise RuntimeError("Settings worker is not running")

    @staticmethod
    def _validate_config(config: AppConfig) -> None:
        if not isinstance(config, AppConfig):
            raise TypeError("config must be an AppConfig")
