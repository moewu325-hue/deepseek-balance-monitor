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
        self.entry_path = (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / ENTRY_NAME
        )
        executable = Path(executable)
        pythonw = executable.with_name("pythonw.exe")
        self._pythonw = pythonw if exists(pythonw) else executable

    def _content(self) -> str:
        command = f'"{self._pythonw}" -m deepseek_balance_monitor'
        escaped = command.replace('"', '""')
        return (
            'Set shell = CreateObject("WScript.Shell")\r\n'
            f'shell.Run "{escaped}", 0, False\r\n'
        )

    def is_enabled(self) -> bool:
        try:
            with self.entry_path.open("r", encoding="utf-16", newline="") as stream:
                return stream.read() == self._content()
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
        handle, temporary = tempfile.mkstemp(
            prefix=f".{ENTRY_NAME}.",
            suffix=".tmp",
            dir=self.entry_path.parent,
        )
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
